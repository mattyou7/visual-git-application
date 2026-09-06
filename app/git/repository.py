from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import re


class GitRepositoryError(RuntimeError):
    """A local Git repository operation failure."""


@dataclass(frozen=True)
class RepositoryInfo:
    root: Path
    branch: str | None


@dataclass(frozen=True)
class GitFileStatus:
    path: Path
    index_code: str
    worktree_code: str
    original_path: Path | None = None

    @property
    def label(self) -> str:
        if self.index_code == "?" and self.worktree_code == "?":
            return "Untracked"
        if self.index_code == "A" or self.worktree_code == "A":
            return "Added"
        if self.index_code == "D" or self.worktree_code == "D":
            return "Deleted"
        if self.index_code in {"R", "C"} or self.worktree_code in {"R", "C"}:
            return "Renamed"
        return "Modified"

    @property
    def symbol(self) -> str:
        return {
            "Untracked": "?",
            "Added": "+",
            "Deleted": "x",
            "Renamed": "↔",
            "Modified": "●",
        }[self.label]


@dataclass(frozen=True)
class GitCommit:
    hash: str
    short_hash: str
    author: str
    timestamp: str
    subject: str
    path: Path | None = None


@dataclass(frozen=True)
class GitBranch:
    name: str
    current: bool


@dataclass(frozen=True)
class GitMergeResult:
    succeeded: bool
    message: str
    conflicts: list[Path]


@dataclass(frozen=True)
class GitConflictState:
    in_progress: bool
    conflicts: list[Path]


@dataclass(frozen=True)
class GitRemote:
    name: str
    fetch_url: str
    push_url: str


class GitRepositoryService:
    def detect(self, directory: Path) -> RepositoryInfo | None:
        result = self._run(directory, "rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return None
        root = Path(result.stdout.strip()).resolve()
        branch_result = self._run(root, "branch", "--show-current", check=False)
        branch = branch_result.stdout.strip() or None
        return RepositoryInfo(root=root, branch=branch)

    def remotes(self, repository: RepositoryInfo) -> list[GitRemote]:
        result = self._run(repository.root, "remote", "-v", check=True)
        remotes: dict[str, dict[str, str]] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            name, url, direction = fields[:3]
            remotes.setdefault(name, {})[direction] = url
        return [
            GitRemote(name, values.get("(fetch)", ""), values.get("(push)", ""))
            for name, values in remotes.items()
        ]

    def add_remote(self, repository: RepositoryInfo, name: str, url: str) -> None:
        clean_name = name.strip()
        clean_url = url.strip()
        if not clean_name or not clean_url:
            raise GitRepositoryError("Remote name and URL are required.")
        if not self._valid_remote_url(clean_url):
            raise GitRepositoryError("Enter a valid HTTPS, SSH, or SCP-style remote URL.")
        self._run(repository.root, "remote", "add", clean_name, clean_url, check=True)

    def remove_remote(self, repository: RepositoryInfo, name: str) -> None:
        self._run(repository.root, "remote", "remove", name, check=True)

    @staticmethod
    def _valid_remote_url(url: str) -> bool:
        if re.match(r"^[^/@\s]+@[^/:\s]+:.+$", url):
            return True
        parsed = urlparse(url)
        return parsed.scheme in {"https", "http", "ssh"} and bool(parsed.hostname)

    def status(self, repository: RepositoryInfo) -> list[GitFileStatus]:
        result = self._run(
            repository.root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            check=True,
        )
        records = result.stdout.split("\0")
        statuses: list[GitFileStatus] = []
        index = 0
        while index < len(records) and records[index]:
            record = records[index]
            if len(record) < 4:
                index += 1
                continue
            index_code, worktree_code = record[0], record[1]
            path = Path(record[3:])
            original_path = None
            if index_code in {"R", "C"} or worktree_code in {"R", "C"}:
                index += 1
                if index < len(records) and records[index]:
                    original_path = Path(records[index])
            statuses.append(GitFileStatus(path, index_code, worktree_code, original_path))
            index += 1
        return statuses

    def stage(self, repository: RepositoryInfo, paths: list[Path]) -> None:
        if not paths:
            raise GitRepositoryError("Select at least one change to stage.")
        self._run(repository.root, "add", "--", *(path.as_posix() for path in paths), check=True)

    def stage_all(self, repository: RepositoryInfo) -> None:
        self._run(repository.root, "add", "-A", check=True)

    def unstage(self, repository: RepositoryInfo, paths: list[Path]) -> None:
        if not paths:
            raise GitRepositoryError("Select at least one staged change to unstage.")
        self._run(repository.root, "reset", "HEAD", "--", *(path.as_posix() for path in paths), check=True)

    def commit(self, repository: RepositoryInfo, message: str) -> str:
        clean_message = message.strip()
        if not clean_message:
            raise GitRepositoryError("Enter a commit message.")
        merge_state = self.conflict_state(repository)
        if merge_state.in_progress and merge_state.conflicts:
            raise GitRepositoryError("Resolve all merge conflicts before committing.")
        if not merge_state.in_progress and not any(status.index_code != " " for status in self.status(repository)):
            raise GitRepositoryError("There are no staged changes to commit.")
        result = self._run(repository.root, "commit", "-m", clean_message, check=True)
        commit_hash = self._run(repository.root, "rev-parse", "HEAD", check=True).stdout.strip()
        return commit_hash or result.stdout.strip()

    def branches(self, repository: RepositoryInfo) -> list[GitBranch]:
        result = self._run(repository.root, "branch", "--format=%(HEAD)\t%(refname:short)", check=True)
        branches = []
        for line in result.stdout.splitlines():
            current, separator, name = line.partition("\t")
            if separator and name:
                branches.append(GitBranch(name, current == "*"))
        return branches

    def branch_tips(self, repository: RepositoryInfo) -> dict[str, str]:
        result = self._run(repository.root, "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads", check=True)
        return {
            name: commit_hash
            for line in result.stdout.splitlines()
            for name, separator, commit_hash in [line.partition("\t")]
            if separator and name and commit_hash
        }

    def create_branch(self, repository: RepositoryInfo, name: str, start_point: str | None = None) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise GitRepositoryError("Enter a branch name.")
        arguments = ["switch", "-c", clean_name]
        if start_point:
            arguments.append(start_point)
        self._run(repository.root, *arguments, check=True)

    def switch_branch(self, repository: RepositoryInfo, name: str) -> None:
        if self.status(repository):
            raise GitRepositoryError("Commit or stash your changes before switching branches.")
        self._run(repository.root, "switch", name, check=True)

    def delete_branch(self, repository: RepositoryInfo, name: str) -> None:
        self._run(repository.root, "branch", "-d", name, check=True)

    def rename_branch(self, repository: RepositoryInfo, old_name: str, new_name: str) -> None:
        clean_name = new_name.strip()
        if not clean_name:
            raise GitRepositoryError("Enter a new branch name.")
        self._run(repository.root, "branch", "-m", old_name, clean_name, check=True)

    def merge(self, repository: RepositoryInfo, branch: str) -> GitMergeResult:
        if self.status(repository):
            raise GitRepositoryError("Commit or stash your changes before merging.")
        result = self._run(repository.root, "merge", "--no-edit", branch, check=False)
        if result.returncode == 0:
            return GitMergeResult(True, result.stdout.strip() or "Merge completed.", [])
        conflicts_result = self._run(repository.root, "diff", "--name-only", "--diff-filter=U", check=False)
        conflicts = [Path(line) for line in conflicts_result.stdout.splitlines() if line]
        if conflicts:
            return GitMergeResult(False, "Merge conflicts require resolution.", conflicts)
        message = result.stderr.strip() or result.stdout.strip() or "Git could not merge the selected branch."
        raise GitRepositoryError(message)

    def conflict_state(self, repository: RepositoryInfo) -> GitConflictState:
        conflicts_result = self._run(repository.root, "diff", "--name-only", "--diff-filter=U", check=False)
        conflicts = [Path(line) for line in conflicts_result.stdout.splitlines() if line]
        merge_head = repository.root / ".git" / "MERGE_HEAD"
        return GitConflictState(merge_head.exists(), conflicts)

    def use_current(self, repository: RepositoryInfo, path: Path) -> None:
        self._run(repository.root, "checkout", "--ours", "--", path.as_posix(), check=True)

    def use_incoming(self, repository: RepositoryInfo, path: Path) -> None:
        self._run(repository.root, "checkout", "--theirs", "--", path.as_posix(), check=True)

    def mark_resolved(self, repository: RepositoryInfo, path: Path) -> None:
        self._run(repository.root, "add", "--", path.as_posix(), check=True)

    def history(self, repository: RepositoryInfo, limit: int = 100) -> list[GitCommit]:
        result = self._run(
            repository.root,
            "log",
            f"-{limit}",
            "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e",
            check=True,
        )
        return self._parse_commits(result.stdout)

    def graph_history(self, repository: RepositoryInfo, limit: int = 200) -> list[GitCommit]:
        result = self._run(
            repository.root,
            "log",
            "--all",
            f"-{limit}",
            "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e",
            check=True,
        )
        return self._parse_commits(result.stdout)

    def file_history(self, repository: RepositoryInfo, path: Path, limit: int = 100) -> list[GitCommit]:
        result = self._run(
            repository.root,
            "log",
            "--follow",
            f"-{limit}",
            "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e",
            "--",
            path.as_posix(),
            check=True,
        )
        return self._parse_commits(result.stdout)

    def show_file_at_commit(self, repository: RepositoryInfo, commit_hash: str, path: Path) -> str:
        result = self._run(repository.root, "show", f"{commit_hash}:{path.as_posix()}", check=True)
        return result.stdout

    def copy_file_from_commit(self, repository: RepositoryInfo, commit_hash: str, path: Path) -> None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository.root), "show", f"{commit_hash}:{path.as_posix()}"],
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise GitRepositoryError(f"Could not read historical file: {error.strerror or error}") from error
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip() or "Git could not read that historical file."
            raise GitRepositoryError(message)
        destination = repository.root / path
        try:
            destination.write_bytes(result.stdout)
        except OSError as error:
            raise GitRepositoryError(f"Could not write the current file: {error.strerror or error}") from error

    def diff_file_versions(
        self,
        repository: RepositoryInfo,
        path: Path,
        older_commit: str | None = None,
        newer_commit: str | None = None,
    ) -> str:
        older = f"{older_commit}:{path.as_posix()}" if older_commit else "HEAD"
        newer = f"{newer_commit}:{path.as_posix()}" if newer_commit else str(path)
        arguments = ["diff", "--no-ext-diff", "--unified=3", older, newer]
        result = self._run(repository.root, *arguments, check=False)
        if result.returncode not in {0, 1}:
            message = result.stderr.strip() or "Git could not compare these file versions."
            raise GitRepositoryError(message)
        return result.stdout or result.stderr.strip() or "No textual differences."

    @staticmethod
    def _parse_commits(output: str) -> list[GitCommit]:
        commits = []
        for record in output.split("\x1e"):
            fields = record.strip("\n").split("\x1f")
            if len(fields) != 5 or not fields[0]:
                continue
            commits.append(GitCommit(fields[0], fields[1], fields[2], fields[3], fields[4]))
        return commits

    def _run(self, directory: Path, *arguments: str, check: bool) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(directory), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise GitRepositoryError(f"Could not run Git: {error.strerror or error}") from error
        if check and result.returncode != 0:
            message = result.stderr.strip() or "Git reported an unknown error."
            raise GitRepositoryError(message)
        return result
