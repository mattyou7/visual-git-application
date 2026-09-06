# Visual Git Workspace

Visual Git Workspace is a PySide6 desktop file manager that makes local Git repositories easier to understand. The filesystem remains the source of truth for current files, and Git remains the source of truth for repository history.

## Current stage

Step 1 is implemented:

- Open a local Git repository
- Display repository path and current branch
- Browse the repository directory tree
- Refresh the filesystem view
- Create folders
- Rename files and folders
- Delete files and folders with confirmation
- Open files with the operating system
- Detect whether the selected directory belongs to a Git repository
- Copy, cut, and paste files and folders
- Multi-select filesystem items
- Drag and drop items to move them
- Context menus and keyboard shortcuts for common actions
- Filesystem watcher with debounced external-change reconciliation
- Git status indicators for modified, added, untracked, deleted, and renamed files
- Separate staged and working-tree Changes panel
- Stage selected changes
- Stage all changes
- Unstage selected changes
- Commit staged changes locally with an explicit commit message
- Project and file history from real Git commits
- Read-only historical file content inspection
- Textual comparison of historical and current file versions
- Textual comparison between adjacent historical versions
- Explicitly copy a historical file version into the current working file
- Create, switch, rename, and delete local Git branches
- Create a branch from a historical commit
- Read-only visual commit graph with branch-tip labels
- Merge a selected branch into the current branch
- Detect and report merge conflicts without choosing either side
- Explicit conflict resolution actions: Use Current, Use Incoming, Open Conflict Editor, and Mark Resolved
- Provider-neutral remote account model
- macOS Keychain-backed credential storage abstraction
- GitHub Connect/Disconnect flow using browser authorization, state, and PKCE
- Explicit local remote inspection, addition, and removal
- Provider-neutral GitHub repository-creation client with mocked tests
- Explicit GitHub repository-creation action with confirmation

Automatic merge commit, restore/reset semantics, contextual helper guidance, clone, push, pull, and remote synchronization status are not implemented yet.

## Setup

Python 3.9 or newer and Git are required. Create a virtual environment and install dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```sh
.venv/bin/python -m app.main
```

Open a directory that is already a Git repository or a directory inside one. The application does not initialize repositories or execute repository files.

## Tests

The service tests use temporary directories and temporary Git repositories. They never operate on the user's working repository:

```sh
python3 -m unittest discover -s tests -v
```

## Architecture

- `app/gui/`: PySide6 presentation and user interaction
- `app/filesystem/`: ordinary filesystem operations
- `app/git/`: local Git inspection
- `tests/`: service-level tests

## Known limitations

- The directory tree is refreshed manually.
- Commit creates a real local Git commit and never pushes automatically.
- History browsing remains planned for a later milestone.
- History inspection is read-only; selecting an old commit does not change the working file.
- Binary comparisons are delegated to Git's binary-safe diff output rather than treated as text.
- Copying historical content creates an ordinary working-tree modification; it does not rewrite Git history.
- Branch switching is blocked when the working tree or index has changes; commit or stash first.
- The graph is a read-only visualization derived from Git commits and branch refs.
- Merge requires a clean working tree and leaves conflicts for the dedicated resolution workflow.
- Conflict resolution never selects a side automatically; marking a file resolved stages the explicit result but does not commit it.
- Authentication stores tokens through the macOS Keychain abstraction and never logs or displays token values.
- GitHub connection uses a localhost callback with state and PKCE; passwords are entered only on GitHub's authorization page.
- GitHub API requests are explicit service calls; no network operation is triggered automatically by opening a repository.
- Creating a GitHub repository requires explicit confirmation and optionally adds the returned clone URL as `origin`; it never pushes automatically.
- The watcher refreshes the explorer and status after external changes, but it is not the source of truth.
- Opening files uses the operating system's native opener.
- Empty-directory guidance and advanced Git repository structures are not implemented yet.
