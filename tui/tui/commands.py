"""Textual Command Palette provider (`Ctrl+P`) for quick jumps across repos, branches, and actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.content import Content
from textual.fuzzy import Matcher

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
    def action_minimize_all(self) -> None: ...
    def action_restore_all(self) -> None: ...
    def action_explain_selected_check(self) -> None: ...
    def action_open_lines(self) -> None: ...
    def action_open_pull_request(self) -> None: ...
    def action_comment(self) -> None: ...


def _call_project(host: _PaletteHost, project: ProjectInfo) -> Callable[[], None]:
    return lambda: host._on_project_chosen(project)


def _call_branch(host: _PaletteHost, role: str, branch_name: str) -> Callable[[], object]:
    return lambda: host._sync_chosen_branch(role, branch_name)


def _call_profile(host: _PaletteHost, profile_name: str) -> Callable[[], object]:
    return lambda: host.select_profile_by_name(profile_name)


def highlighted(matcher: Matcher, label: str) -> Content:
    """`label` with the letters the query matched marked, read as plain text. Textual's own `highlight` reads it as
    markup: read by review, and reproduced, a repository `my[service]` was listed as `my`, and `x[/old]` raised
    `MarkupError`, which dropped the palette's later hits."""
    content = Content(label)
    score, offsets = matcher.fuzzy_search.match(matcher.query, label)
    if not score:
        return content
    for offset in offsets:
        if not label[offset].isspace():
            content = content.stylize(matcher.match_style, offset, offset + 1)
    return content


class ReviewCommandProvider(Provider):
    """Fuzzy command search over actions, repositories, branches, and profiles."""

    def _entries(self) -> list[tuple[str, Callable[[], object], str]]:
        host = cast("_PaletteHost", self.app)
        items: list[tuple[str, Callable[[], object], str]] = [
            ("Action: Run Review (Ctrl+R)", host.action_run_review, "Execute adk-review with current selections"),
            ("Action: Dry Run (Ctrl+D)", host.action_run_dry, "Validate scope and readiness without running checks"),
            ("Action: Fetch & Refresh Repo (F5)", host.action_refresh_repo, "Run git fetch --prune on active repo"),
            ("Action: Minimize All Panels (Ctrl+T)", host.action_minimize_all, "Minimize every configuration panel"),
            ("Action: Restore All Panels (Ctrl+O)", host.action_restore_all, "Restore every configuration panel"),
            (
                "Action: Explain Selected Finding Check",
                host.action_explain_selected_check,
                "Show documentation for the check in Findings Explorer",
            ),
            (
                "Action: Open Finding's Lines (o)",
                host.action_open_lines,
                "Open the chosen finding's lines in Azure DevOps",
            ),
            (
                "Action: Open Finding in Its Pull Request (p)",
                host.action_open_pull_request,
                "Open the pull request at the finding's file, or at its comment once posted",
            ),
            (
                "Action: Comment on Finding in Its Pull Request (c)",
                host.action_comment,
                "Draft a comment to edit, confirm and post to the finding's lines",
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
                yield Hit(score, highlighted(matcher, label), callback, text=label, help=literal(help_text))
