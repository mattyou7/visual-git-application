from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.git.repository import GitConflictState, GitFileStatus


@dataclass(frozen=True)
class WorkflowGuidance:
    happening: str
    next_step: str
    action_label: str | None = None
    action: str | None = None


class GitWorkflowHelper:
    """Maps local Git state to deterministic, beginner-friendly guidance."""

    def recommend(
        self,
        statuses: Iterable[GitFileStatus],
        conflict_state: GitConflictState,
    ) -> WorkflowGuidance:
        changes = list(statuses)
        if conflict_state.in_progress and conflict_state.conflicts:
            count = len(conflict_state.conflicts)
            return WorkflowGuidance(
                f"A merge is in progress and {count} file{'s' if count != 1 else ''} still need conflict resolution.",
                "Resolve conflicts before Git can finish the merge.",
                "Resolve Conflicts",
                "resolve_conflicts",
            )
        if conflict_state.in_progress:
            return WorkflowGuidance(
                "All merge conflicts are resolved, but the merge has not been recorded yet.",
                "Commit the merge to finish combining the branches.",
                "Commit Merge",
                "commit_merge",
            )

        staged = [status for status in changes if status.index_code not in {" ", "?"}]
        if staged:
            count = len(staged)
            return WorkflowGuidance(
                f"{count} file{'s are' if count != 1 else ' is'} staged and ready to be recorded.",
                "Commit the staged changes to create a local checkpoint.",
                "Commit Changes",
                "commit_changes",
            )

        unstaged = [status for status in changes if status.worktree_code != " " or status.index_code == "?"]
        if unstaged:
            count = len(unstaged)
            return WorkflowGuidance(
                f"{count} file{'s have' if count != 1 else ' has'} changes that are not staged.",
                "Stage the changes so they can be included in a commit.",
                "Stage All",
                "stage_all",
            )

        return WorkflowGuidance(
            "Working tree is clean.",
            "There are no local changes waiting to be staged or committed.",
        )
