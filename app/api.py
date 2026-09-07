from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
import uvicorn

from app.filesystem.operations import FilesystemError, FilesystemService
from app.git.repository import (
    GitBranch,
    GitConflictState,
    GitFileStatus,
    GitMergeResult,
    GitRepositoryError,
    GitRepositoryService,
    RepositoryInfo,
)
from app.git.workflow_helper import GitWorkflowHelper
from app.remote.auth import (
    AuthenticationError,
    GitHubAuthenticator,
    MacOSKeychainCredentialStore,
    RemoteAccount,
)
from app.remote.provider import GitHubProvider, RemoteProviderError


app = FastAPI(title="Visual Git API", version="1.0.0")

filesystem = FilesystemService()
git = GitRepositoryService()
workflow = GitWorkflowHelper()

STATE_DIR = Path.home() / ".visual_git_workspace"
STATE_FILE = STATE_DIR / "state.json"
MAX_RECENT_REPOS = 12
MAX_SEARCH_RESULTS = 500
T = TypeVar("T")


class ApiProblem(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": message}},
    )


@app.exception_handler(ApiProblem)
async def api_problem_handler(_: Request, exc: ApiProblem) -> JSONResponse:
    return fail(exc.code, exc.message, exc.status)


@app.exception_handler(Exception)
async def unexpected_handler(_: Request, exc: Exception) -> JSONResponse:
    # Never expose tracebacks or local implementation details to the browser.
    return fail("INTERNAL_ERROR", "The operation could not be completed.", 500)


def _load_state() -> dict[str, Any]:
    try:
        if not STATE_FILE.exists():
            return {"current_directory": str(Path.home()), "recent_repositories": [], "github_account": None}
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(STATE_FILE)
    except OSError:
        # Persistence is convenience only; it must never break a filesystem/Git operation.
        pass


def _path(value: str, *, must_exist: bool = True, directory: Optional[bool] = None) -> Path:
    clean = value.strip()
    if not clean or "\x00" in clean:
        raise ApiProblem("INVALID_PATH", "A valid path is required.")
    target = Path(clean).expanduser()
    try:
        target = target.resolve(strict=False)
    except OSError as exc:
        raise ApiProblem("INVALID_PATH", "The supplied path could not be resolved.") from exc
    if must_exist and not target.exists():
        raise ApiProblem("PATH_NOT_FOUND", f"Path does not exist: {target}")
    if directory is True and not target.is_dir():
        raise ApiProblem("NOT_A_DIRECTORY", f"Expected a folder: {target}")
    if directory is False and not target.is_file():
        raise ApiProblem("NOT_A_FILE", f"Expected a file: {target}")
    return target


def _current_directory() -> Path:
    state = _load_state()
    value = state.get("current_directory")
    if isinstance(value, str):
        candidate = Path(value).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return Path.home().resolve()


def _set_current_directory(directory: Path) -> None:
    state = _load_state()
    state["current_directory"] = str(directory.resolve())
    _save_state(state)


def _repo_or_error() -> RepositoryInfo:
    repo = git.detect(_current_directory())
    if repo is None:
        raise ApiProblem("NO_REPOSITORY_OPEN", "Open a Git repository first.", 404)
    return repo


def _repo_for_path(path: Path) -> Optional[RepositoryInfo]:
    return git.detect(path if path.is_dir() else path.parent)


def _relative_repo_path(repo: RepositoryInfo, value: str) -> Path:
    target = _path(value)
    try:
        relative = target.relative_to(repo.root)
    except ValueError as exc:
        raise ApiProblem("PATH_OUTSIDE_REPOSITORY", "The path is outside the open Git repository.") from exc
    return relative


def _status_for_path(statuses: list[GitFileStatus]) -> dict[str, GitFileStatus]:
    return {status.path.as_posix(): status for status in statuses}


def _status_label(status: GitFileStatus) -> str:
    return status.code


def _file_item(path: Path, status_map: dict[str, GitFileStatus]) -> dict[str, Any]:
    try:
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        size = stat.st_size if path.is_file() else None
    except OSError:
        modified = None
        size = None

    ext: Optional[str]
    if path.is_dir():
        ext = None
    elif path.name.startswith(".") and path.suffix == "":
        ext = path.name[1:] or None
    else:
        ext = path.suffix[1:] or None

    status = status_map.get(path.as_posix())
    return {
        "id": str(path),
        "name": path.name,
        "type": "folder" if path.is_dir() else "file",
        "ext": ext,
        "size": size,
        "modified": modified,
        "gitStatus": _status_label(status) if status else None,
    }


def _repository_payload(repo: Optional[RepositoryInfo]) -> Optional[dict[str, Any]]:
    if repo is None:
        return None
    return {"root": str(repo.root), "branch": repo.branch}


def _list_files(directory: Path) -> list[dict[str, Any]]:
    repo = _repo_for_path(directory)
    status_map: dict[str, GitFileStatus] = {}
    if repo:
        try:
            status_map = _status_for_path(git.status(repo))
        except GitRepositoryError:
            status_map = {}
    items = filesystem.list_directory(directory)
    return [_file_item(item, status_map) for item in items]


def _changes_payload(repo: RepositoryInfo) -> list[dict[str, Any]]:
    statuses = git.status(repo)
    return [
        {
            "path": status.path.as_posix(),
            "status": _status_label(status),
            "staged": status.index_code not in {" ", "?"},
        }
        for status in statuses
    ]


def _guidance_payload(repo: RepositoryInfo) -> dict[str, Any]:
    statuses = git.status(repo)
    conflict = git.conflict_state(repo)
    guidance = workflow.recommend(statuses, conflict)
    return {
        "happening": guidance.happening,
        "nextStep": guidance.next_step,
        "actionLabel": guidance.action_label,
        "action": guidance.action,
    }


def _git_status_payload(repo: RepositoryInfo, commit: Optional[str] = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "branch": repo.branch,
        "changes": _changes_payload(repo),
        "guidance": _guidance_payload(repo),
    }
    if commit is not None:
        data["commit"] = commit
    return data


def _record_recent(repo: RepositoryInfo) -> None:
    state = _load_state()
    records = state.get("recent_repositories")
    if not isinstance(records, list):
        records = []

    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "path": str(repo.root),
        "name": repo.root.name or str(repo.root),
        "lastOpened": now,
    }
    records = [
        item for item in records
        if not isinstance(item, dict) or item.get("path") != str(repo.root)
    ]
    records.insert(0, entry)
    state["recent_repositories"] = records[:MAX_RECENT_REPOS]
    _save_state(state)


def _recent_payload() -> list[dict[str, Any]]:
    state = _load_state()
    records = state.get("recent_repositories")
    if not isinstance(records, list):
        return []

    result: list[dict[str, Any]] = []
    changed = False
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            changed = True
            continue
        root = Path(item["path"]).expanduser()
        available = root.is_dir()
        branch: Optional[str] = None
        dirty = False
        if available:
            repo = git.detect(root)
            if repo:
                branch = repo.branch
                try:
                    dirty = bool(git.status(repo))
                except GitRepositoryError:
                    dirty = False
        result.append({
            "path": str(root),
            "name": str(item.get("name") or root.name or root),
            "lastOpened": str(item.get("lastOpened") or ""),
            "branch": branch,
            "dirty": dirty,
            "available": available,
        })
    if changed:
        state["recent_repositories"] = records[:MAX_RECENT_REPOS]
        _save_state(state)
    return result


def _locations() -> list[dict[str, str]]:
    home = Path.home()
    candidates = [
        ("home", "Home", home),
        ("desktop", "Desktop", home / "Desktop"),
        ("downloads", "Downloads", home / "Downloads"),
        ("documents", "Documents", home / "Documents"),
    ]
    return [
        {"id": item_id, "label": label, "path": str(path.resolve())}
        for item_id, label, path in candidates
        if path.is_dir()
    ]


def _serialize_branch(branch: GitBranch) -> dict[str, Any]:
    return {"name": branch.name, "current": branch.current}


def _serialize_commit(commit: Any) -> dict[str, Any]:
    return {
        "hash": commit.hash,
        "shortHash": commit.short_hash,
        "author": commit.author,
        "timestamp": commit.timestamp,
        "subject": commit.subject,
    }


def _serialize_remote(remote: Any) -> dict[str, Any]:
    return {
        "name": remote.name,
        "fetchUrl": remote.fetch_url,
        "pushUrl": remote.push_url,
    }


def _serialize_merge(result: GitMergeResult) -> dict[str, Any]:
    return {
        "succeeded": result.succeeded,
        "message": result.message,
        "conflicts": [path.as_posix() for path in result.conflicts],
    }


def _serialize_conflict(state: GitConflictState) -> dict[str, Any]:
    return {
        "inProgress": state.in_progress,
        "conflicts": [path.as_posix() for path in state.conflicts],
    }


def _map_error(exc: Exception) -> ApiProblem:
    if isinstance(exc, (FilesystemError, GitRepositoryError, AuthenticationError, RemoteProviderError)):
        return ApiProblem("OPERATION_FAILED", str(exc), 400)
    return ApiProblem("OPERATION_FAILED", "The operation could not be completed.", 400)


def _github_account() -> Optional[RemoteAccount]:
    value = _load_state().get("github_account")
    if not isinstance(value, dict):
        return None
    provider = value.get("provider")
    host = value.get("host")
    account = value.get("account")
    if all(isinstance(v, str) and v for v in (provider, host, account)):
        return RemoteAccount(provider, host, account)
    return None


def _github_authenticator() -> GitHubAuthenticator:
    client_id = os.getenv("VISUAL_GIT_GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("VISUAL_GIT_GITHUB_CLIENT_SECRET", "")
    return GitHubAuthenticator(
        client_id=client_id,
        client_secret=client_secret,
        credential_store=MacOSKeychainCredentialStore(),
    )


def _github_status() -> dict[str, Any]:
    account = _github_account()
    return {
        "connected": account is not None,
        "username": account.account if account else None,
    }


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, Any]:
    return ok({"ok": True})


# ─── Filesystem ──────────────────────────────────────────────────────────────

@app.get("/api/files")
def get_files(path: str) -> dict[str, Any]:
    directory = _path(path, directory=True)
    _set_current_directory(directory)
    repo = _repo_for_path(directory)
    try:
        items = _list_files(directory)
    except (FilesystemError, GitRepositoryError) as exc:
        raise _map_error(exc)
    return ok({
        "path": str(directory),
        "repository": _repository_payload(repo),
        "items": items,
    })


@app.get("/api/files/tree")
def get_files_tree(path: str, depth: int = Query(1, ge=0, le=8)) -> dict[str, Any]:
    directory = _path(path, directory=True)
    repo = _repo_for_path(directory)

    def build(node: Path, remaining: int) -> dict[str, Any]:
        status_map: dict[str, GitFileStatus] = {}
        if repo:
            try:
                status_map = _status_for_path(git.status(repo))
            except GitRepositoryError:
                pass
        result = _file_item(node, status_map)
        if node.is_dir() and remaining > 0:
            children = []
            for child in filesystem.list_directory(node):
                children.append(build(child, remaining - 1))
            result["children"] = children
        return result

    try:
        return ok(build(directory, depth))
    except (FilesystemError, GitRepositoryError) as exc:
        raise _map_error(exc)


@app.post("/api/files/folder")
def create_folder(payload: dict[str, Any]) -> dict[str, Any]:
    parent = _path(str(payload.get("parent", "")), directory=True)
    name = str(payload.get("name", ""))
    try:
        return ok({"path": str(filesystem.create_folder(parent, name))})
    except FilesystemError as exc:
        raise _map_error(exc)


@app.post("/api/files/rename")
def rename_file(payload: dict[str, Any]) -> dict[str, Any]:
    source = _path(str(payload.get("path", "")))
    new_name = str(payload.get("newName", ""))
    try:
        return ok({"path": str(filesystem.rename(source, new_name))})
    except FilesystemError as exc:
        raise _map_error(exc)


@app.delete("/api/files")
def delete_files(payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ApiProblem("INVALID_REQUEST", "Select at least one file or folder.")
    deleted: list[str] = []
    try:
        for value in paths:
            target = _path(str(value))
            filesystem.delete(target)
            deleted.append(str(target))
    except FilesystemError as exc:
        raise _map_error(exc)
    return ok({"deleted": deleted})


@app.post("/api/files/copy")
def copy_files(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ApiProblem("INVALID_REQUEST", "Sources must be a list.")
    destination = _path(str(payload.get("destination", "")), directory=True)
    try:
        targets = filesystem.copy_items([_path(str(value)) for value in sources], destination)
        return ok({"paths": [str(path) for path in targets]})
    except FilesystemError as exc:
        raise _map_error(exc)


@app.post("/api/files/move")
def move_files(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ApiProblem("INVALID_REQUEST", "Sources must be a list.")
    destination = _path(str(payload.get("destination", "")), directory=True)
    try:
        targets = filesystem.move_items([_path(str(value)) for value in sources], destination)
        return ok({"paths": [str(path) for path in targets]})
    except FilesystemError as exc:
        raise _map_error(exc)


@app.post("/api/files/open")
def open_file(payload: dict[str, Any]) -> dict[str, Any]:
    target = _path(str(payload.get("path", "")))
    try:
        filesystem.open_path(target)
    except FilesystemError as exc:
        raise _map_error(exc)
    return ok({})


@app.get("/api/files/locations")
def get_locations() -> dict[str, Any]:
    return ok(_locations())


@app.get("/api/files/search")
def search_files(query: str, path: Optional[str] = None) -> dict[str, Any]:
    root = _path(path or str(_current_directory()), directory=True)
    needle = query.strip().casefold()
    if not needle:
        return ok([])

    matches: list[dict[str, Any]] = []
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in [*dirs, *files]:
                if needle not in name.casefold():
                    continue
                item = Path(current) / name
                matches.append({
                    "path": str(item),
                    "type": "folder" if item.is_dir() else "file",
                    "name": name,
                })
                if len(matches) >= MAX_SEARCH_RESULTS:
                    return ok(matches)
    except OSError as exc:
        raise ApiProblem("SEARCH_FAILED", f"Could not search this folder: {exc.strerror or exc}", 400) from exc
    return ok(matches)


# ─── Clipboard ───────────────────────────────────────────────────────────────

_CLIPBOARD: dict[str, Any] = {"mode": None, "paths": []}


@app.post("/api/clipboard/copy")
def clipboard_copy(payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ApiProblem("INVALID_REQUEST", "Select at least one file or folder.")
    normalized = [str(_path(str(value))) for value in paths]
    _CLIPBOARD.update(mode="copy", paths=normalized)
    return ok({})


@app.post("/api/clipboard/cut")
def clipboard_cut(payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ApiProblem("INVALID_REQUEST", "Select at least one file or folder.")
    normalized = [str(_path(str(value))) for value in paths]
    _CLIPBOARD.update(mode="cut", paths=normalized)
    return ok({})


@app.post("/api/clipboard/paste")
def clipboard_paste(payload: dict[str, Any]) -> dict[str, Any]:
    destination = _path(str(payload.get("destination", "")), directory=True)
    mode = _CLIPBOARD.get("mode")
    paths = _CLIPBOARD.get("paths") or []
    if mode not in {"copy", "cut"} or not paths:
        raise ApiProblem("CLIPBOARD_EMPTY", "There is nothing to paste.")
    try:
        source_paths = [_path(str(value)) for value in paths]
        targets = filesystem.copy_items(source_paths, destination) if mode == "copy" else filesystem.move_items(source_paths, destination)
    except FilesystemError as exc:
        raise _map_error(exc)
    if mode == "cut":
        _CLIPBOARD.update(mode=None, paths=[])
    return ok({"paths": [str(path) for path in targets]})


@app.get("/api/clipboard")
def get_clipboard() -> dict[str, Any]:
    return ok({"mode": _CLIPBOARD["mode"], "paths": list(_CLIPBOARD["paths"])})


# ─── Repository ──────────────────────────────────────────────────────────────

@app.post("/api/repository/open")
def open_repository(payload: dict[str, Any]) -> dict[str, Any]:
    directory = _path(str(payload.get("path", "")), directory=True)
    _set_current_directory(directory)
    repo = git.detect(directory)
    if repo:
        _record_recent(repo)
    return ok({"path": str(directory), "repository": _repository_payload(repo)})


@app.get("/api/repository/current")
def get_current_repository() -> dict[str, Any]:
    directory = _current_directory()
    repo = git.detect(directory)
    return ok({"path": str(directory), "repository": _repository_payload(repo)})


@app.get("/api/repositories/recent")
def get_recent_repositories() -> dict[str, Any]:
    return ok(_recent_payload())


# ─── Git status / staging / commit ──────────────────────────────────────────

@app.get("/api/git/status")
def get_git_status() -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/stage")
def stage_files(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    paths = payload.get("paths")
    if not isinstance(paths, list):
        raise ApiProblem("INVALID_REQUEST", "Paths must be a list.")
    try:
        git.stage(repo, [_relative_repo_path(repo, str(value)) for value in paths])
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/stage-all")
def stage_all() -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.stage_all(repo)
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/unstage")
def unstage_files(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    paths = payload.get("paths")
    if not isinstance(paths, list):
        raise ApiProblem("INVALID_REQUEST", "Paths must be a list.")
    try:
        git.unstage(repo, [_relative_repo_path(repo, str(value)) for value in paths])
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/commit")
def commit(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    message = str(payload.get("message", ""))
    try:
        commit_hash = git.commit(repo, message)
        return ok(_git_status_payload(repo, commit_hash))
    except GitRepositoryError as exc:
        raise _map_error(exc)


# ─── Branches ────────────────────────────────────────────────────────────────

@app.get("/api/git/branches")
def get_branches() -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok([_serialize_branch(branch) for branch in git.branches(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/branches")
def create_branch(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    name = str(payload.get("name", ""))
    start_point = payload.get("startPoint")
    if start_point is not None:
        start_point = str(start_point)
    try:
        git.create_branch(repo, name, start_point)
        new_repo = git.detect(repo.root)
        if new_repo is None:
            raise ApiProblem("REPOSITORY_NOT_FOUND", "The repository could not be reopened.")
        return ok({
            "branches": [_serialize_branch(branch) for branch in git.branches(new_repo)],
            "repository": _repository_payload(new_repo),
        })
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/branches/switch")
def switch_branch(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    name = str(payload.get("name", ""))
    try:
        git.switch_branch(repo, name)
        new_repo = git.detect(repo.root)
        if new_repo is None:
            raise ApiProblem("REPOSITORY_NOT_FOUND", "The repository could not be reopened.")
        return ok({
            "repository": _repository_payload(new_repo),
            "status": _git_status_payload(new_repo),
        })
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/branches/rename")
def rename_branch(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    old_name = str(payload.get("oldName", ""))
    new_name = str(payload.get("newName", ""))
    try:
        git.rename_branch(repo, old_name, new_name)
        return ok([_serialize_branch(branch) for branch in git.branches(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.delete("/api/git/branches")
def delete_branch(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    name = str(payload.get("name", ""))
    try:
        git.delete_branch(repo, name)
        return ok([_serialize_branch(branch) for branch in git.branches(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


# ─── History / graph ─────────────────────────────────────────────────────────

@app.get("/api/git/history")
def get_history(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok([_serialize_commit(commit) for commit in git.history(repo, limit)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.get("/api/git/graph")
def get_graph(limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok([_serialize_commit(commit) for commit in git.graph_history(repo, limit)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.get("/api/git/file-history")
def get_file_history(path: str, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        relative = _relative_repo_path(repo, path)
        return ok([_serialize_commit(commit) for commit in git.file_history(repo, relative, limit)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.get("/api/git/file-at-commit")
def get_file_at_commit(commit: str, path: str) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        relative = _relative_repo_path(repo, path)
        content = git.show_file_at_commit(repo, commit, relative)
        return ok({"path": relative.as_posix(), "commit": commit, "content": content})
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/copy-file-from-commit")
def copy_file_from_commit(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    commit_hash = str(payload.get("commit", ""))
    try:
        relative = _relative_repo_path(repo, str(payload.get("path", "")))
        git.copy_file_from_commit(repo, commit_hash, relative)
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.get("/api/git/diff")
def get_diff(
    path: str,
    older: Optional[str] = None,
    newer: Optional[str] = None,
) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        relative = _relative_repo_path(repo, path)
        return ok({"diff": git.diff_file_versions(repo, relative, older, newer)})
    except GitRepositoryError as exc:
        raise _map_error(exc)


# ─── Merge / conflicts ───────────────────────────────────────────────────────

@app.post("/api/git/merge")
def merge_branch(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    branch = str(payload.get("branch", ""))
    try:
        return ok(_serialize_merge(git.merge(repo, branch)))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.get("/api/git/conflicts")
def get_conflicts() -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok(_serialize_conflict(git.conflict_state(repo)))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/conflicts/use-current")
def use_current(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.use_current(repo, _relative_repo_path(repo, str(payload.get("path", ""))))
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/conflicts/use-incoming")
def use_incoming(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.use_incoming(repo, _relative_repo_path(repo, str(payload.get("path", ""))))
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/conflicts/mark-resolved")
def mark_resolved(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.mark_resolved(repo, _relative_repo_path(repo, str(payload.get("path", ""))))
        return ok(_git_status_payload(repo))
    except GitRepositoryError as exc:
        raise _map_error(exc)


# ─── Remotes ─────────────────────────────────────────────────────────────────

@app.get("/api/git/remotes")
def get_remotes() -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        return ok([_serialize_remote(remote) for remote in git.remotes(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.post("/api/git/remotes")
def add_remote(payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.add_remote(repo, str(payload.get("name", "")), str(payload.get("url", "")))
        return ok([_serialize_remote(remote) for remote in git.remotes(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


@app.delete("/api/git/remotes/{name}")
def remove_remote(name: str) -> dict[str, Any]:
    repo = _repo_or_error()
    try:
        git.remove_remote(repo, name)
        return ok([_serialize_remote(remote) for remote in git.remotes(repo)])
    except GitRepositoryError as exc:
        raise _map_error(exc)


# ─── Not-yet-implemented Git features ────────────────────────────────────────

@app.post("/api/git/clone")
def clone_not_implemented() -> JSONResponse:
    return fail("NOT_IMPLEMENTED", "Clone is not implemented yet.", 501)


@app.post("/api/git/push")
def push_not_implemented() -> JSONResponse:
    return fail("NOT_IMPLEMENTED", "Push is not implemented yet.", 501)


@app.post("/api/git/pull")
def pull_not_implemented() -> JSONResponse:
    return fail("NOT_IMPLEMENTED", "Pull is not implemented yet.", 501)


# ─── GitHub ──────────────────────────────────────────────────────────────────

@app.get("/api/github/status")
def github_status() -> dict[str, Any]:
    return ok(_github_status())


@app.post("/api/github/connect")
def github_connect() -> dict[str, Any]:
    try:
        result = _github_authenticator().connect()
        state = _load_state()
        state["github_account"] = {
            "provider": result.account.provider,
            "host": result.account.host,
            "account": result.account.account,
        }
        _save_state(state)
        return ok(_github_status())
    except AuthenticationError as exc:
        raise ApiProblem("GITHUB_AUTH_FAILED", str(exc), 400)


@app.post("/api/github/disconnect")
def github_disconnect() -> dict[str, Any]:
    account = _github_account()
    if account is None:
        return ok(_github_status())
    try:
        _github_authenticator().disconnect(account)
        state = _load_state()
        state["github_account"] = None
        _save_state(state)
        return ok(_github_status())
    except AuthenticationError as exc:
        raise ApiProblem("GITHUB_AUTH_FAILED", str(exc), 400)


@app.post("/api/github/repos")
def create_github_repo(payload: dict[str, Any]) -> dict[str, Any]:
    account = _github_account()
    if account is None:
        raise ApiProblem("GITHUB_NOT_CONNECTED", "Authenticate with GitHub before creating a repository.", 400)
    name = str(payload.get("name", ""))
    private = bool(payload.get("private", True))
    try:
        provider = GitHubProvider(
            account,
            token_loader=lambda account: _github_authenticator().credential_store.load(account),
        )
        result = provider.create_repository(name, private)
        return ok({
            "name": result.name,
            "fullName": result.full_name,
            "cloneUrl": result.clone_url,
            "private": result.private,
        })
    except (AuthenticationError, RemoteProviderError) as exc:
        raise ApiProblem("GITHUB_REQUEST_FAILED", str(exc), 400)


if __name__ == "__main__":
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)