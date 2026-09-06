from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from app.gui.main_window import MainWindow
except ImportError:
    QApplication = None

from app.filesystem.operations import FilesystemError, FilesystemService
from app.git.repository import GitRepositoryError, GitRepositoryService, RepositoryInfo
from app.git.repository import GitConflictState, GitFileStatus
from app.git.workflow_helper import GitWorkflowHelper
from app.remote.auth import AuthenticationError, GitHubAuthenticator, GitHubAuthResult, MacOSKeychainCredentialStore, account_from_remote_url
from app.remote.provider import GitHubProvider, RemoteProviderError, RemoteRepository


class FilesystemServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.service = FilesystemService()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_create_rename_and_delete_folder(self) -> None:
        folder = self.service.create_folder(self.root, "analysis")
        renamed = self.service.rename(folder, "results")
        self.assertTrue(renamed.is_dir())
        self.service.delete(renamed)
        self.assertFalse(renamed.exists())

    def test_create_file_and_list_directories_before_files(self) -> None:
        (self.root / "notes.txt").touch()
        folder = self.service.create_folder(self.root, "src")
        self.assertEqual(self.service.list_directory(self.root), [folder, self.root / "notes.txt"])

    def test_copy_and_move_multiple_items(self) -> None:
        source = self.service.create_folder(self.root, "source")
        destination = self.service.create_folder(self.root, "destination")
        move_destination = self.service.create_folder(self.root, "move-destination")
        first_file = source / "first.txt"
        second_file = source / "second.txt"
        first_file.write_text("first")
        second_file.write_text("second")

        copied = self.service.copy_items([first_file, second_file], destination)
        self.assertEqual([path.name for path in copied], ["first.txt", "second.txt"])
        self.assertTrue((destination / "first.txt").exists())
        moved = self.service.move_items([first_file], move_destination)
        self.assertEqual(moved, [move_destination / "first.txt"])
        self.assertFalse(first_file.exists())

    def test_copy_rejects_existing_destination(self) -> None:
        source = self.root / "notes.txt"
        source.write_text("notes")
        destination = self.service.create_folder(self.root, "destination")
        (destination / source.name).write_text("existing")
        with self.assertRaisesRegex(FilesystemError, "already exists"):
            self.service.copy_items([source], destination)

    def test_copy_and_move_nested_folder_preserve_contents(self) -> None:
        source = self.service.create_folder(self.root, "folder_a")
        nested = self.service.create_folder(source, "subfolder")
        (nested / "file1.txt").write_text("content")
        copy_destination = self.service.create_folder(self.root, "copy-destination")
        move_destination = self.service.create_folder(self.root, "move-destination")

        self.service.copy_items([source], copy_destination)
        self.assertEqual((copy_destination / "folder_a/subfolder/file1.txt").read_text(), "content")
        self.service.move_items([source], move_destination)
        self.assertFalse(source.exists())
        self.assertEqual((move_destination / "folder_a/subfolder/file1.txt").read_text(), "content")

    def test_transfer_rejects_moving_folder_into_itself(self) -> None:
        source = self.service.create_folder(self.root, "folder_a")
        nested = self.service.create_folder(source, "subfolder")
        with self.assertRaisesRegex(FilesystemError, "into itself"):
            self.service.move_items([source], nested)

    def test_rename_rejects_existing_name(self) -> None:
        first = self.root / "file1.txt"
        second = self.root / "file2.txt"
        first.write_text("first")
        second.write_text("second")
        with self.assertRaisesRegex(FilesystemError, "already exists"):
            self.service.rename(first, second.name)
        self.assertEqual(first.read_text(), "first")
        self.assertEqual(second.read_text(), "second")

    def test_special_and_long_names_and_empty_directory_are_real(self) -> None:
        folder = self.service.create_folder(self.root, "folder with spaces")
        file_path = folder / ("file-with-dash_and_underscore" + "x" * 80 + ".txt")
        file_path.write_text("content")
        self.assertIn(file_path, self.service.list_directory(folder))
        empty = self.service.create_folder(self.root, "empty_folder")
        self.assertTrue(empty.is_dir())


class GitRepositoryServiceTests(unittest.TestCase):
    def test_status_reports_untracked_modified_deleted_and_staged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            tracked = root / "tracked.txt"
            deleted = root / "deleted.txt"
            tracked.write_text("initial")
            deleted.write_text("deleted")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                check=True,
            )
            tracked.write_text("changed")
            deleted.unlink()
            (root / "new.txt").write_text("new")
            staged = root / "staged.txt"
            staged.write_text("staged")
            subprocess.run(["git", "-C", str(root), "add", "staged.txt"], check=True)

            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            statuses = {status.path.as_posix(): status for status in GitRepositoryService().status(repository)}
            self.assertEqual(statuses["tracked.txt"].label, "Modified")
            self.assertEqual(statuses["deleted.txt"].label, "Deleted")
            self.assertEqual(statuses["new.txt"].label, "Untracked")
            self.assertEqual(statuses["staged.txt"].label, "Added")

    def test_status_reports_git_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "old.txt"
            original.write_text("content")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                check=True,
            )
            original.rename(root / "new.txt")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            statuses = GitRepositoryService().status(repository)
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].label, "Renamed")
            self.assertEqual(statuses[0].original_path, Path("old.txt"))

    def test_stage_and_unstage_selected_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("initial")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                check=True,
            )
            tracked.write_text("changed")
            new_file = root / "new.txt"
            new_file.write_text("new")
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()

            service.stage(repository, [Path("tracked.txt")])
            statuses = {status.path.as_posix(): status for status in service.status(repository)}
            self.assertEqual(statuses["tracked.txt"].index_code, "M")
            self.assertEqual(statuses["tracked.txt"].worktree_code, " ")

            service.unstage(repository, [Path("tracked.txt")])
            statuses = {status.path.as_posix(): status for status in service.status(repository)}
            self.assertEqual(statuses["tracked.txt"].index_code, " ")
            self.assertEqual(statuses["tracked.txt"].worktree_code, "M")

            service.stage_all(repository)
            statuses = {status.path.as_posix(): status for status in service.status(repository)}
            self.assertEqual(statuses["new.txt"].label, "Added")
            self.assertEqual(statuses["tracked.txt"].index_code, "M")

    def test_commit_records_staged_changes_and_rejects_empty_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("initial")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                check=True,
            )
            tracked.write_text("committed change")
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            service.stage_all(repository)
            commit_hash = service.commit(repository, "Record useful checkpoint")
            self.assertEqual(len(commit_hash), 40)
            self.assertEqual(service.status(repository), [])
            subject = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%s"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(subject, "Record useful checkpoint")
            with self.assertRaisesRegex(GitRepositoryError, "commit message"):
                service.commit(repository, "   ")

    def test_project_and_file_history_and_historical_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("version one")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            commit_environment = ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
            subprocess.run([*commit_environment, "commit", "-qm", "Initial version"], check=True)
            tracked.write_text("version two")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run([*commit_environment, "commit", "-qm", "Second version"], check=True)

            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            project_history = service.history(repository)
            file_history = service.file_history(repository, Path("tracked.txt"))
            self.assertEqual([commit.subject for commit in project_history[:2]], ["Second version", "Initial version"])
            self.assertEqual(len(file_history), 2)
            self.assertEqual(service.show_file_at_commit(repository, file_history[0].hash, Path("tracked.txt")), "version two")
            self.assertEqual(service.show_file_at_commit(repository, file_history[1].hash, Path("tracked.txt")), "version one")
            current_diff = service.diff_file_versions(repository, Path("tracked.txt"), file_history[1].hash)
            self.assertIn("+version two", current_diff)
            history_diff = service.diff_file_versions(repository, Path("tracked.txt"), file_history[1].hash, file_history[0].hash)
            self.assertIn("-version one", history_diff)
            self.assertIn("+version two", history_diff)
            service.copy_file_from_commit(repository, file_history[1].hash, Path("tracked.txt"))
            self.assertEqual(tracked.read_text(), "version one")
            self.assertEqual(len(service.history(repository)), 2)

    def test_branch_create_switch_rename_delete_and_dirty_safety(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("initial")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            environment = ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
            subprocess.run([*environment, "commit", "-qm", "initial"], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            default_branch = repository.branch
            self.assertIsNotNone(default_branch)

            service.create_branch(repository, "feature")
            service.switch_branch(repository, "feature")
            self.assertIn("feature", [branch.name for branch in service.branches(repository)])
            service.rename_branch(repository, "feature", "renamed-feature")
            self.assertIn("renamed-feature", [branch.name for branch in service.branches(repository)])
            service.switch_branch(repository, default_branch)
            service.delete_branch(repository, "renamed-feature")
            self.assertNotIn("renamed-feature", [branch.name for branch in service.branches(repository)])

            tracked.write_text("dirty")
            with self.assertRaisesRegex(GitRepositoryError, "before switching"):
                service.switch_branch(repository, "feature")

    def test_branch_tips_are_real_commit_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "tracked.txt").write_text("initial")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            environment = ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
            subprocess.run([*environment, "commit", "-qm", "initial"], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            tips = GitRepositoryService().branch_tips(repository)
            self.assertEqual(tips[repository.branch], subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip())

    def test_merge_success_and_conflict_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("base")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            environment = ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
            subprocess.run([*environment, "commit", "-qm", "base"], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            service.create_branch(repository, "feature")
            (root / "feature.txt").write_text("feature")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run([*environment, "commit", "-qm", "feature"], check=True)
            default_branch = repository.branch
            service.switch_branch(repository, default_branch)
            result = service.merge(repository, "feature")
            self.assertTrue(result.succeeded)
            self.assertTrue((root / "feature.txt").exists())

            service.create_branch(repository, "conflict")
            tracked.write_text("conflict branch")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run([*environment, "commit", "-qm", "conflict branch"], check=True)
            service.switch_branch(repository, default_branch)
            tracked.write_text("main branch")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run([*environment, "commit", "-qm", "main branch"], check=True)
            conflict_result = service.merge(repository, "conflict")
            self.assertFalse(conflict_result.succeeded)
            self.assertEqual(conflict_result.conflicts, [Path("tracked.txt")])

            conflict_state = service.conflict_state(repository)
            self.assertTrue(conflict_state.in_progress)
            service.use_current(repository, Path("tracked.txt"))
            self.assertEqual(tracked.read_text(), "main branch")
            self.assertNotIn("<<<<<<<", tracked.read_text())
            service.mark_resolved(repository, Path("tracked.txt"))
            self.assertFalse(service.conflict_state(repository).conflicts)
            self.assertTrue(service.conflict_state(repository).in_progress)
            merge_commit = service.commit(repository, "Resolve conflict")
            self.assertEqual(len(merge_commit), 40)
            self.assertFalse(service.conflict_state(repository).in_progress)

    def test_detects_repository_root_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            nested = root / "nested"
            nested.mkdir()
            info = GitRepositoryService().detect(nested)
            self.assertIsNotNone(info)
            self.assertEqual(info.root, root.resolve())
            self.assertIn(info.branch, {"main", "master", None})

    def test_returns_none_for_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.assertIsNone(GitRepositoryService().detect(Path(temporary_directory)))


class GitWorkflowHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = GitWorkflowHelper()
        self.no_merge = GitConflictState(False, [])

    def test_untracked_and_modified_files_recommend_staging(self) -> None:
        untracked = self.helper.recommend([GitFileStatus(Path("new.txt"), "?", "?")], self.no_merge)
        modified = self.helper.recommend([GitFileStatus(Path("edited.txt"), " ", "M")], self.no_merge)
        self.assertEqual(untracked.action, "stage_all")
        self.assertEqual(modified.action, "stage_all")
        self.assertIn("not staged", untracked.happening)
        self.assertIn("Stage", modified.next_step)

    def test_staged_files_recommend_committing(self) -> None:
        guidance = self.helper.recommend([GitFileStatus(Path("ready.txt"), "M", " ")], self.no_merge)
        self.assertEqual(guidance.action, "commit_changes")
        self.assertIn("staged", guidance.happening)

    def test_clean_state_explains_nothing_is_waiting(self) -> None:
        guidance = self.helper.recommend([], self.no_merge)
        self.assertIsNone(guidance.action)
        self.assertEqual(guidance.happening, "Working tree is clean.")

    def test_merge_conflict_recommends_resolution(self) -> None:
        guidance = self.helper.recommend([], GitConflictState(True, [Path("conflicted.txt")]))
        self.assertEqual(guidance.action, "resolve_conflicts")
        self.assertIn("conflict", guidance.happening)

    def test_resolved_merge_recommends_merge_commit(self) -> None:
        guidance = self.helper.recommend([], GitConflictState(True, []))
        self.assertEqual(guidance.action, "commit_merge")
        self.assertIn("Commit", guidance.next_step)


class LocalOfflineAcceptanceTests(unittest.TestCase):
    def test_local_filesystem_and_git_workflow_requires_no_remote_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            filesystem = FilesystemService()
            git = GitRepositoryService()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git_command = ["git", "-C", str(root), "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid"]

            tracked = root / "tracked.txt"
            tracked.write_text("version one\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run([*git_command, "commit", "-qm", "Initial version"], check=True)
            repository = git.detect(root)
            self.assertIsNotNone(repository)

            workspace = filesystem.create_folder(root, "workspace")
            inbox = filesystem.create_folder(root, "inbox")
            archive = filesystem.create_folder(root, "archive")
            draft = workspace / "draft.txt"
            draft.write_text("draft\n")
            filesystem.copy_items([draft], inbox)
            moved = filesystem.move_items([inbox / "draft.txt"], archive)[0]
            renamed = filesystem.rename(moved, "renamed-draft.txt")
            filesystem.delete(renamed)

            tracked.write_text("version two\n")  # External editor save.
            self.assertEqual(
                {item.path for item in git.status(repository)},
                {Path("tracked.txt"), Path("workspace/draft.txt")},
            )
            git.stage(repository, [Path("tracked.txt")])
            self.assertEqual(git.status(repository)[0].index_code, "M")
            git.unstage(repository, [Path("tracked.txt")])
            self.assertEqual(git.status(repository)[0].worktree_code, "M")
            git.stage_all(repository)
            second_commit = git.commit(repository, "Local checkpoint")
            self.assertEqual(git.status(repository), [])

            file_history = git.file_history(repository, Path("tracked.txt"))
            project_history = git.history(repository)
            self.assertEqual([commit.subject for commit in project_history[:2]], ["Local checkpoint", "Initial version"])
            self.assertEqual(git.show_file_at_commit(repository, file_history[1].hash, Path("tracked.txt")), "version one\n")
            self.assertIn("+version two", git.diff_file_versions(repository, Path("tracked.txt"), file_history[1].hash))
            git.copy_file_from_commit(repository, file_history[1].hash, Path("tracked.txt"))
            self.assertEqual(tracked.read_text(), "version one\n")
            git.stage_all(repository)
            git.commit(repository, "Restore historical content")

            main_branch = git.detect(root).branch
            self.assertIsNotNone(main_branch)
            git.create_branch(repository, "feature")
            (root / "feature.txt").write_text("feature\n")
            git.stage_all(repository)
            git.commit(repository, "Feature work")
            git.switch_branch(repository, main_branch)
            self.assertTrue(git.merge(repository, "feature").succeeded)
            self.assertIn("feature", git.branch_tips(repository))
            self.assertGreaterEqual(len(git.graph_history(repository)), 4)

            tracked.write_text("dirty\n")
            with self.assertRaisesRegex(GitRepositoryError, "before switching"):
                git.switch_branch(repository, "feature")
            subprocess.run(["git", "-C", str(root), "checkout", "--", "tracked.txt"], check=True)

            git.create_branch(repository, "from-initial", file_history[1].hash)
            self.assertIn("from-initial", [branch.name for branch in git.branches(repository)])
            git.switch_branch(repository, main_branch)
            git.rename_branch(repository, "from-initial", "historical")
            git.delete_branch(repository, "historical")

            git.create_branch(repository, "conflict")
            tracked.write_text("incoming\n")
            git.stage_all(repository)
            git.commit(repository, "Incoming change")
            git.switch_branch(repository, main_branch)
            tracked.write_text("current\n")
            git.stage_all(repository)
            git.commit(repository, "Current change")
            conflict = git.merge(repository, "conflict")
            self.assertFalse(conflict.succeeded)
            self.assertEqual(conflict.conflicts, [Path("tracked.txt")])
            git.use_current(repository, Path("tracked.txt"))
            git.mark_resolved(repository, Path("tracked.txt"))
            self.assertFalse(git.conflict_state(repository).conflicts)


class AuthenticationTests(unittest.TestCase):
    def test_remote_account_parses_https_and_ssh_urls(self) -> None:
        self.assertEqual(account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice").host, "github.com")
        self.assertEqual(account_from_remote_url("GitHub", "git@github.com:example/project.git", "alice").host, "github.com")

    def test_keychain_store_never_exposes_token_in_errors(self) -> None:
        store = MacOSKeychainCredentialStore()
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        with patch("app.remote.auth.sys.platform", "darwin"), patch("app.remote.auth.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "security failed"
            with self.assertRaisesRegex(AuthenticationError, "securely"):
                store.save(account, "secret-token")
            command = run.call_args.args[0]
            self.assertIn("secret-token", command)

    def test_empty_token_is_rejected_before_keychain_access(self) -> None:
        store = MacOSKeychainCredentialStore()
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        with patch("app.remote.auth.subprocess.run") as run:
            with self.assertRaisesRegex(AuthenticationError, "cannot be empty"):
                store.save(account, "  ")
            run.assert_not_called()

    def test_github_connect_uses_pkce_state_and_keychain_without_logging_token(self) -> None:
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        saved = []
        opened = []
        responses = []

        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                import json
                return json.dumps(self.payload).encode()

        responses.extend([FakeResponse({"access_token": "secret-token"}), FakeResponse({"login": "alice"})])

        class FakeCallback:
            port = 43123
            last_state = None

            def __init__(self, _state):
                FakeCallback.last_state = _state

            def start(self):
                return None

            def wait(self, _timeout):
                return "authorization-code"

            def close(self):
                return None

        class FakeStore:
            def save(self, requested, token):
                saved.append((requested, token))

        authenticator = GitHubAuthenticator(
            "client-id",
            "client-secret",
            FakeStore(),
            browser_opener=lambda url: opened.append(url) or True,
            urlopen=lambda *_args, **_kwargs: responses.pop(0),
        )
        with patch("app.remote.auth._OAuthCallbackServer", FakeCallback):
            result = authenticator.connect(timeout=1)
        self.assertEqual(result, GitHubAuthResult(account, "secret-token"))
        self.assertEqual(saved, [(account, "secret-token")])
        query = parse_qs(urlparse(opened[0]).query)
        self.assertEqual(query["state"][0], FakeCallback.last_state)
        self.assertTrue(query["code_challenge"][0])
        self.assertEqual(query["code_challenge_method"], ["S256"])

    def test_github_token_exchange_includes_client_secret_and_sanitizes_error(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                import json
                return json.dumps({
                    "error": "incorrect_client_credentials",
                    "error_description": "The client credentials passed are incorrect.",
                    "access_token": "must-not-appear",
                    "refresh_token": "must-not-appear",
                }).encode()

        authenticator = GitHubAuthenticator(
            "client-id",
            "client-secret",
            credential_store=object(),
            urlopen=lambda *_args, **_kwargs: FakeResponse(),
        )
        with self.assertRaisesRegex(
            AuthenticationError,
            "^GitHub token exchange failed: incorrect_client_credentials — The client credentials passed are incorrect\\.$",
        ) as raised:
            authenticator._exchange_code("authorization-code", "http://127.0.0.1:43123/callback", "code-verifier")
        self.assertNotIn("must-not-appear", str(raised.exception))
        self.assertNotIn("authorization-code", str(raised.exception))

        captured = []
        successful_authenticator = GitHubAuthenticator(
            "client-id",
            "client-secret",
            credential_store=object(),
            urlopen=lambda request_object, **_kwargs: captured.append(request_object) or type(
                "Response",
                (), {"__enter__": lambda self: self, "__exit__": lambda self, *_args: False, "read": lambda self: b'{\"access_token\": \"token\"}'},
            )(),
        )
        self.assertEqual(
            successful_authenticator._exchange_code("authorization-code", "http://127.0.0.1:43123/callback", "code-verifier"),
            "token",
        )
        request_values = parse_qs(captured[0].data.decode())
        self.assertEqual(request_values["client_secret"], ["client-secret"])


class RemoteProviderTests(unittest.TestCase):
    def test_local_remote_add_list_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            service.add_remote(repository, "origin", "https://github.com/example/project.git")
            remotes = service.remotes(repository)
            self.assertEqual(remotes, [
                type(remotes[0])("origin", "https://github.com/example/project.git", "https://github.com/example/project.git")
            ])
            service.remove_remote(repository, "origin")
            self.assertEqual(service.remotes(repository), [])

    def test_invalid_remote_url_is_rejected_without_configuration_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            repository = GitRepositoryService().detect(root)
            self.assertIsNotNone(repository)
            service = GitRepositoryService()
            with self.assertRaisesRegex(GitRepositoryError, "valid"):
                service.add_remote(repository, "origin", "not a remote URL")
            self.assertEqual(service.remotes(repository), [])

    def test_github_repository_creation_uses_mocked_response(self) -> None:
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        provider = GitHubProvider(account, lambda _account: "test-token")
        response = {
            "name": "project",
            "full_name": "alice/project",
            "clone_url": "https://github.com/alice/project.git",
            "private": True,
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                import json
                return json.dumps(response).encode("utf-8")

        with patch("app.remote.provider.request.urlopen", return_value=FakeResponse()) as urlopen:
            result = provider.create_repository("project", private=True)
        self.assertEqual(result, RemoteRepository("project", "alice/project", "https://github.com/alice/project.git", True))
        self.assertIn("Bearer test-token", urlopen.call_args.args[0].headers["Authorization"])

    def test_github_repository_creation_sanitizes_api_error_response(self) -> None:
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        provider = GitHubProvider(account, lambda _account: "test-token")
        response_error = HTTPError(
            "https://api.github.com/user/repos",
            422,
            "Unprocessable Entity",
            None,
            BytesIO(b'{"error":"already_exists","error_description":"Repository already exists.","token":"must-not-appear"}'),
        )
        with patch("app.remote.provider.request.urlopen", side_effect=response_error):
            with self.assertRaisesRegex(
                RemoteProviderError,
                "^GitHub API request failed \\(HTTP 422\\). already_exists — Repository already exists\\.$",
            ) as raised:
                provider.create_repository("project")
        self.assertNotIn("must-not-appear", str(raised.exception))

    def test_github_repository_creation_reports_non_http_failure_without_details(self) -> None:
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        provider = GitHubProvider(account, lambda _account: "test-token")
        with patch("app.remote.provider.request.urlopen", side_effect=URLError("network unavailable test-token")):
            with self.assertLogs("app.remote.provider", level="INFO") as logs:
                with self.assertRaisesRegex(
                    RemoteProviderError,
                    "^GitHub request failed. Check connectivity and authentication\\.$",
                ) as raised:
                    provider.create_repository("project")
        self.assertEqual(
            logs.output,
            [
                "INFO:app.remote.provider:GitHub repository API request has an authenticated token in memory: token_supplied=True",
                "INFO:app.remote.provider:GitHub repository API non-HTTP failure: exception_class=URLError "
                "exception_message=<urlopen error network unavailable <redacted>> hostname=api.github.com path=/user/repos "
                "request_constructed=True urlopen_entered=True response_received=False",
            ],
        )
        self.assertNotIn("test-token", "\n".join(logs.output))
        self.assertNotIn("test-token", str(raised.exception))


@unittest.skipUnless(QApplication is not None, "PySide6 is required for GUI regression tests")
class GitHubRepositoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_authenticated_create_repository_button_opens_dialogs_and_attempts_api_request(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        account = account_from_remote_url("GitHub", "https://github.com/example/project.git", "alice")
        window.repository = RepositoryInfo(Path("/tmp/project"), "main")
        window.github_account = account
        window.show()
        self.application.processEvents()
        create_button = window.create_github_repository_button
        self.assertFalse(create_button.isVisible())

        with patch("app.gui.main_window.QInputDialog.getText", return_value=("project", True)) as get_text, \
             patch("app.gui.main_window.QInputDialog.getItem", return_value=("Private", True)) as get_item, \
             patch("app.gui.main_window.QMessageBox.question", side_effect=[QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No]), \
             patch("app.gui.main_window.QMessageBox.information") as information, \
             patch("app.gui.main_window.GitHubProvider") as provider_class:
            provider_class.return_value.create_repository.return_value = RemoteRepository(
                "project", "alice/project", "https://github.com/alice/project.git", True,
            )
            window.create_github_repository()

        provider_class.assert_called_once()
        get_text.assert_called_once()
        get_item.assert_called_once()
        provider_class.return_value.create_repository.assert_called_once_with("project", private=True)
        information.assert_called_once_with(window, "GitHub Repository Created", "Created alice/project on GitHub.")


@unittest.skipUnless(QApplication is not None, "PySide6 is required for GUI acceptance tests")
class LocalGuiAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_external_save_refreshes_local_git_status_without_remote_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.txt"
            tracked.write_text("initial\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
                check=True,
            )
            window = MainWindow()
            self.addCleanup(window.close)
            window.show()
            window.load_directory(root)
            self.assertIsNone(window.github_account)

            tracked.write_text("external save\n")
            QTest.qWait(800)
            self.application.processEvents()

            self.assertTrue(
                any("tracked.txt" in window.changes_list.item(index).text() for index in range(window.changes_list.count()))
            )
            self.assertEqual(window.workflow_action_button.property("workflow_action"), "stage_all")
            self.assertIn("not staged", window.workflow_message_label.text())

    def test_stage_selected_from_tree_stages_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            untracked = root / "new.txt"
            untracked.write_text("new\n")
            window = MainWindow()
            self.addCleanup(window.close)
            window.show()
            window.load_directory(root)
            self.application.processEvents()
            root_item = window.tree.topLevelItem(0)
            file_item = next(root_item.child(index) for index in range(root_item.childCount()) if root_item.child(index).text(0) == "new.txt")
            window.tree.setCurrentItem(file_item)
            file_item.setSelected(True)
            QTest.qWait(800)
            self.application.processEvents()
            self.assertEqual(window.selected_path(), untracked.resolve())

            window.stage_selected()

            status = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True, check=True).stdout
            self.assertEqual(status, "A  new.txt\n")

    def test_folder_expansion_survives_reconciliation_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "folder" / "nested.txt"
            nested.parent.mkdir()
            nested.write_text("content\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            window = MainWindow()
            self.addCleanup(window.close)
            window.show()
            window.load_directory(root)
            self.application.processEvents()
            root_item = window.tree.topLevelItem(0)
            folder_item = next(root_item.child(index) for index in range(root_item.childCount()) if root_item.child(index).text(0) == "folder")
            folder_item.setExpanded(True)

            QTest.qWait(800)
            self.application.processEvents()

            root_item = window.tree.topLevelItem(0)
            refreshed_folder = next(root_item.child(index) for index in range(root_item.childCount()) if root_item.child(index).text(0) == "folder")
            self.assertTrue(refreshed_folder.isExpanded())
            self.assertEqual(refreshed_folder.child(0).text(0), "nested.txt")

    def test_external_branch_switch_refreshes_branch_summary_and_helper_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "tracked.txt").write_text("initial\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
                check=True,
            )
            original_branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True, check=True
            ).stdout.strip()
            subprocess.run(["git", "-C", str(root), "branch", "other"], check=True)
            window = MainWindow()
            self.addCleanup(window.close)
            window.show()
            window.load_directory(root)

            subprocess.run(["git", "-C", str(root), "switch", "-q", "other"], check=True)
            window._reconcile_external_changes()

            self.assertEqual(window.repository.branch, "other")
            self.assertIn("Branch: other", window.repository_label.text())
            self.assertEqual(window.workflow_message_label.text(), "Working tree is clean.")
            self.assertNotEqual(original_branch, "other")

    def test_file_manager_navigation_and_theme_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "folder"
            nested.mkdir()
            (nested / "file.txt").write_text("content\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            window = MainWindow()
            self.addCleanup(window.close)
            window.show()
            window.load_directory(root)

            self.assertEqual(window.tree.headerItem().text(0), "Name")
            self.assertEqual(window.tree.headerItem().text(1), "Git Status")
            self.assertEqual(window.locations_list.count(), 2)
            self.assertGreater(window.breadcrumb_layout.count(), 0)
            window.theme_selector.setCurrentText("Dark")
            self.assertIn("#1f2329", QApplication.instance().styleSheet())
            window.theme_selector.setCurrentText("Light")
            self.assertIn("#f6f7f9", QApplication.instance().styleSheet())


if __name__ == "__main__":
    unittest.main()
