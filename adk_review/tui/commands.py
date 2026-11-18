"""Textual Command Palette provider (`Ctrl+P`) for quick jumps across repos, branches, and actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.content import Content

from .git_sync import BranchInfo, ProjectInfo
from .options import ProfileOption
from .styles import literal


class _PaletteHost(Protocol):
    """Minimal host interface required by `ReviewCommandProvider` without importing `app.py`."""

    projects: list[ProjectInfo]
    branches: list[BranchInfo]
    profiles: list[ProfileOption]

    def _on_project_chosen(self, project: ProjectInfo) -> None: ...
    def _sync_chosen_branch(self, role: str, branch_name: str) -> object: ...
    def select_profile_by_name(self, name: str) -> None: ...
    def action_run_review(self) -> None: ...
    def action_run_dry(self) -> None: ...
    def action_refresh_repo(self) -> None: ...
    def action_toggle_minimize_all(self) -> None: ...
    def action_explain_selected_check(self) -> None: ...


def _call_project(host: _PaletteHost, project: ProjectInfo) -> Callable[[], None]:
    return lambda: host._on_project_chosen(project)


def _call_branch(host: _PaletteHost, role: str, branch_name: str) -> Callable[[], object]:
    return lambda: host._sync_chosen_branch(role, branch_name)


def _call_profile(host: _PaletteHost, profile_name: str) -> Callable[[], object]:
    return lambda: host.select_profile_by_name(profile_name)


class ReviewCommandProvider(Provider):
    """Fuzzy command search over actions, repositories, branches, and profiles."""

    def _entries(self) -> list[tuple[str, Callable[[], object], str]]:
        host = cast("_PaletteHost", self.app)
        items: list[tuple[str, Callable[[], object], str]] = [
            ("Action: Run Review (Ctrl+R)", host.action_run_review, "Execute adk-review with current selections"),
            ("Action: Dry Run (Ctrl+D)", host.action_run_dry, "Validate scope and readiness without running checks"),
            ("Action: Fetch & Refresh Repo (F5)", host.action_refresh_repo, "Run git fetch --prune on active repo"),
            (
                "Action: Fold / Unfold All Sections (Ctrl+T)",
                host.action_toggle_minimize_all,
                "Collapse or expand all configuration sections",
            ),
            (
                "Action: Explain Selected Finding Check",
                host.action_explain_selected_check,
                "Show documentation for the check in Findings Explorer",
            ),
        ]
        items.extend((f"Repo: {proj.label()}", _call_project(host, proj), proj.path) for proj in host.projects)
        for branch in host.branches[:40]:
            items.append((f"Head Branch: {branch.name}", _call_branch(host, "head", branch.name), branch.label()))
            items.append((f"Base Branch: {branch.name}", _call_branch(host, "base", branch.name), branch.label()))
        items.extend(
            (f"Profile: {prof.label()}", _call_profile(host, prof.name), prof.detail()) for prof in host.profiles
        )
        return items

    async def discover(self) -> Hits:
        """Show core actions and configured repositories before typing."""
        for label, callback, help_text in self._entries()[:12]:
            yield DiscoveryHit(Content(label), callback, text=label, help=literal(help_text))  # no markup: a name's own

    async def search(self, query: str) -> Hits:
        """Fuzzy-match commands, repos, branches, and profiles against `query`."""
        matcher = self.matcher(query)
        for label, callback, help_text in self._entries():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback, text=label, help=literal(help_text))
