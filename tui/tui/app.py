"""Textual front-end shell (`adk-review-tui`) implementing the cascading Chain of Selects."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.command import Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    OptionList,
    RichLog,
    Select,
    SelectionList,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from .clipboard import copy_text
from .commands import ReviewCommandProvider
from .config import RepoPreset, RootDir, TuiConfig, load_tui_config, save_repo_preset
from .findings import (
    FindingRow,
    filter_findings,
    format_finding_detail,
    load_findings_from_json,
)
from .git_sync import (
    BranchInfo,
    ProjectInfo,
    SyncStatus,
    changed_folders,
    discover_projects,
    fast_forward_local_branch,
    fetch_remote,
    inspect_branch_sync,
    list_branches,
    preferred_base_branch,
)
from .modals import DivergedBranchModal, ExplainCheckModal, SyncBranchModal
from .options import (
    OPTION_DESCRIPTIONS,
    ProfileOption,
    ReviewMode,
    ReviewSelection,
    baseline_path,
    build_review_argv,
    fail_on_choices,
    format_cli_command,
    load_profile_options,
    parse_csv_checks,
    parse_csv_paths,
    parse_diff_cap,
    promote_baseline,
    review_command,
)
from .styles import APP_CSS, literal


class AdkReviewTuiApp(App[int]):
    """Interactive Textual front-end shell for `adk-review`."""

    TITLE = "adk-review-tui"
    CSS = APP_CSS
    COMMANDS: ClassVar[set[type[Provider] | Callable[[], type[Provider]]]] = App.COMMANDS | {ReviewCommandProvider}
    # Each action key goes first, wherever the focus is: in a text field Textual's own Ctrl+D deletes and Ctrl+X cuts.
    # None is a key a terminal sends as another, Ctrl+M as Enter, or one the classic Windows console keeps for itself:
    # Ctrl+A, Ctrl+C, Ctrl+F, Ctrl+M and Ctrl+V. Read by review: Ctrl+M folded nothing where a list had the focus.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+r", "run_review", "Run Review", priority=True),
        Binding("ctrl+d", "run_dry", "Dry Run", priority=True),
        Binding("ctrl+x", "stop_review", "Stop Review", priority=True),
        Binding("ctrl+t", "minimize_all", "Minimize All", priority=True),
        Binding("ctrl+o", "restore_all", "Restore All", priority=True),
        Binding("ctrl+y", "copy_active_tab", "Copy Tab", priority=True),
        Binding("f5", "refresh_repo", "Fetch & Refresh"),
        Binding("1", "toggle_step(1)", "Step 1", show=False),
        Binding("2", "toggle_step(2)", "Step 2", show=False),
        Binding("3", "toggle_step(3)", "Step 3", show=False),
        Binding("4", "toggle_step(4)", "Step 4", show=False),
        Binding("5", "toggle_step(5)", "Step 5", show=False),
        Binding("6", "toggle_step(6)", "Step 6", show=False),
        Binding("7", "toggle_step(7)", "Step 7", show=False),
        Binding("q", "quit", "Quit"),
    ]

    #: What a key starts that never runs behind a dialog: a review, a dry run, the sections folded, a tab copied. Not
    #: Stop, which must always reach a running review.
    BEHIND_NO_DIALOG = frozenset({"run_review", "run_dry", "minimize_all", "restore_all", "copy_active_tab"})

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002 -- Textual's
        """No action key but Stop runs while a dialog is open. Measured: once the keys went first, Ctrl+R and Ctrl+D
        started a review behind the question a branch behind its remote raises, which they had not before."""
        if action in {"run_review", "run_dry"} and self._reviewing:
            # Read by review: the review's worker is exclusive, so Ctrl+R or Ctrl+D during a run stopped it and started
            # another, its model calls spent again. Its button was disabled, its key was not.
            return False
        return not (action in self.BEHIND_NO_DIALOG and isinstance(self.screen, ModalScreen))

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.tui_config: TuiConfig = load_tui_config(config_path)
        self.profiles: list[ProfileOption] = load_profile_options()
        self.projects: list[ProjectInfo] = []
        self.branches: list[BranchInfo] = []
        self.findings_rows: list[FindingRow] = []
        self._filtered_findings: list[FindingRow] = []
        self._selected_finding: FindingRow | None = None
        self.selected_root: RootDir = self.tui_config.root_dirs[0]
        self.selected_project: ProjectInfo | None = None
        self.selected_mode: ReviewMode = "branch"
        self.selected_head: str = ""
        self.selected_base: str = ""
        self.last_report_md: str = ""
        self.last_comment_md: str = ""
        self.last_handoff_md: str = ""
        self._suppress_branch_events: bool = False
        self._repo_loaded_path: str = ""
        self._syncing_paths: bool = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._reviewing: bool = False  # a review or a dry run in its worker, from its start to its end

    def has_multiple_roots(self) -> bool:
        """Whether more than one root workspace directory is configured."""
        return len(self.tui_config.root_dirs) > 1

    def _step(self, multi_index: int) -> int:
        """Visible step number, shifted down by 1 when the root workspace picker is hidden."""
        return multi_index if self.has_multiple_roots() else multi_index - 1

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        # Reported by the owner: the screen listed another folder than the one they set, and named neither the file it
        # read nor the folder. Its first line names both.
        folders = ", ".join(root.path for root in self.tui_config.root_dirs)
        yield Static(
            f"Config: {self.tui_config.config_path}\nFolders of repositories: {folders}",  # each on a line of its own
            id="config-line",
            markup=False,
        )
        yield from self._compose_chain_row()
        initial_msg = (
            "Select a root folder and repository to begin."
            if self.has_multiple_roots()
            else f"Workspace: {self.selected_root.path} | Select a repository to begin."
        )
        # No markup in what a repository, a branch, a path or a model wrote: Textual reads `[name]` in a string as a
        # tag. Read by review, and reproduced: a title lost the choice it names, "Branch Under Review [main]" drawn
        # with no branch, and a finding titled "The path [/tmp] is shared" raised `MarkupError` and ended the screen.
        yield Static(initial_msg, id="status-banner", markup=False)
        yield from self._compose_mid_row()
        yield Static("Select a repository to preview review scope.", id="selection-summary", markup=False)
        yield from self._compose_command_bar()
        yield from self._compose_output_tabs()
        yield Footer()

    def _compose_chain_row(self) -> ComposeResult:
        root_classes = "chain-box" if self.has_multiple_roots() else "chain-box hidden"
        with Horizontal(id="chain-row"):
            with Collapsible(title="1. Root Workspace Folder", collapsed=False, id="root-box", classes=root_classes):
                yield Input(placeholder="Filter roots...", id="root-filter", classes="filter-input")
                yield OptionList(id="root-list", markup=False)
            with Collapsible(
                title=f"{self._step(2)}. Local Repository", collapsed=False, id="repo-box", classes="chain-box"
            ):
                yield Input(placeholder="Filter repos...", id="repo-filter", classes="filter-input")
                yield OptionList(id="repo-list", markup=False)
            with Collapsible(
                title=f"{self._step(3)}. Branch Under Review", collapsed=False, id="head-box", classes="chain-box"
            ):
                yield Input(placeholder="Filter branches...", id="head-filter", classes="filter-input")
                yield OptionList(id="head-list", markup=False)
            with Collapsible(
                title=f"{self._step(4)}. Target Branch", collapsed=False, id="base-box", classes="chain-box"
            ):
                yield Input(placeholder="Filter target branches...", id="base-filter", classes="filter-input")
                yield OptionList(id="base-list", markup=False)

    def _compose_mid_row(self) -> ComposeResult:
        with Horizontal(id="mid-row"):
            with Collapsible(title=f"{self._step(5)}. Path Scoping", collapsed=False, id="paths-pane"):
                with Horizontal(classes="path-lists-row"):
                    with Vertical(classes="path-col"):
                        yield Label("Include only folders:")
                        yield SelectionList[str](id="path-list")
                    with Vertical(classes="path-col"):
                        yield Label("Exclude folders:")
                        yield SelectionList[str](id="exclude-list")
                yield Input(
                    placeholder="Extra included paths (comma-separated, e.g. src/api,src/core)", id="extra-path"
                )
                yield Input(
                    placeholder="Extra excluded paths (comma-separated, e.g. tests,docs/reports)",
                    id="extra-exclude",
                )
            with (
                Collapsible(title=f"{self._step(6)}. Review Profile", collapsed=False, id="profile-pane"),
                VerticalScroll(),
            ):
                yield from self._compose_profile_controls()
            with (
                Collapsible(title=f"{self._step(7)}. Output Options", collapsed=False, id="output-options-pane"),
                VerticalScroll(),
            ):
                yield from self._compose_output_controls()

    def _compose_profile_controls(self) -> ComposeResult:
        d = self.tui_config.defaults
        mode_opts: list[tuple[str, str]] = [
            ("Branch diff (vs Target Branch)", "branch"),
            ("Staged index only (--staged)", "staged"),
            ("Whole repository at head (--all)", "all"),
        ]
        prof_opts = [(p.label(), p.name) for p in self.profiles]
        default_prof = d.profile if any(p.name == d.profile for p in self.profiles) else self.profiles[0].name
        yield Select(mode_opts, value="branch", allow_blank=False, id="mode-select")
        yield Select(prof_opts, value=default_prof, allow_blank=False, id="profile-select")
        yield Static(self._profile_detail(default_prof), id="profile-desc", classes="opt-desc", markup=False)
        yield Select(fail_on_choices(), value=d.fail_on, allow_blank=False, id="fail-on-select")
        with Horizontal(classes="budget-row"):
            yield Input(placeholder="--check IDs (optional, overrides profile)", id="check-input")
            yield Input(placeholder="--diff-cap (chars)", id="diff-cap-input")

    def _compose_output_controls(self) -> ComposeResult:
        d = self.tui_config.defaults
        yield from self._switch_row("sw-comment", d.comment, f"--comment: {OPTION_DESCRIPTIONS['comment']}")
        yield from self._switch_row("sw-handoff", d.handoff, f"--handoff: {OPTION_DESCRIPTIONS['handoff']}")
        yield from self._switch_row("sw-verify", d.verify, f"--verify: {OPTION_DESCRIPTIONS['verify']}")
        yield from self._switch_row("sw-cache", d.use_cache, f"--cache: {OPTION_DESCRIPTIONS['use_cache']}")
        yield from self._switch_row(
            "sw-previous", d.auto_previous, f"--previous: {OPTION_DESCRIPTIONS['use_previous']}"
        )

    @staticmethod
    def _switch_row(widget_id: str, value: bool, description: str) -> ComposeResult:
        with Horizontal(classes="opt-row"):
            yield Switch(value=value, id=widget_id, classes="opt-switch")
            yield Static(description, classes="opt-desc")

    def _compose_command_bar(self) -> ComposeResult:
        with Horizontal(id="cmd-bar"):
            yield Static("adk-review ...", id="cli-preview", markup=False)
            yield Button("Dry Run (Ctrl+D)", id="btn-dry-run", variant="default", classes="action-btn")
            yield Button("Run Review (Ctrl+R)", id="btn-run", variant="success", classes="action-btn")
            yield Button("Stop (Ctrl+X)", id="btn-stop", variant="error", classes="action-btn", disabled=True)

    def _compose_output_tabs(self) -> ComposeResult:
        sev_opts = [("All severities", "all"), ("blocker", "blocker"), ("major", "major"), ("minor", "minor")]
        tier_opts = [
            ("All tiers", "all"),
            ("NEW", "NEW"),
            ("Active", "Active"),
            ("Old", "Old"),
            ("Resolved", "Resolved"),
        ]
        with TabbedContent(id="output-tabs"):
            with TabPane("1. Review Report", id="tab-report"):
                yield RichLog(id="report-log", wrap=True, highlight=True, markup=False)
            with TabPane("2. Findings Explorer", id="tab-findings"):
                with Horizontal(id="findings-toolbar"):
                    yield Select(sev_opts, value="all", allow_blank=False, id="finding-sev-filter")
                    yield Select(tier_opts, value="all", allow_blank=False, id="finding-tier-filter")
                    yield Input(placeholder="Search findings (file, check, title)...", id="finding-search")
                    yield Button("Explain Check", id="btn-explain-check", variant="default", disabled=True)
                yield DataTable(id="findings-table", cursor_type="row")
                with VerticalScroll(id="finding-detail-scroll"):
                    yield Static("Run a review to see its findings.", id="finding-detail", markup=False)
            with TabPane("3. Author Comment (--comment)", id="tab-comment"), VerticalScroll():
                yield Markdown("*No `--comment` generated yet.*", id="comment-md")
            with TabPane("4. Agent Handoff (--handoff)", id="tab-handoff"), VerticalScroll():
                yield Markdown("*No `--handoff` generated yet.*", id="handoff-md")
            with TabPane("5. Live Progress & Git Log", id="tab-log"):
                yield RichLog(id="progress-log", wrap=True, highlight=True, markup=False)

    def on_mount(self) -> None:
        """Configure Findings Explorer columns, populate roots, and scan initial workspace."""
        table = self.query_one("#findings-table", DataTable)
        table.add_columns("Severity", "Tier", "Check", "Location", "Title")
        self._populate_roots(initial=True)
        root_list = self.query_one("#root-list", OptionList)
        if self.has_multiple_roots():
            root_list.focus()
        else:
            self.query_one("#repo-list", OptionList).focus()

    def _populate_roots(self, filter_text: str = "", *, initial: bool = False) -> None:
        root_list = self.query_one("#root-list", OptionList)
        root_list.clear_options()
        needle = filter_text.strip().lower()
        shown = [r for r in self.tui_config.root_dirs if not needle or needle in f"{r.label} {r.path}".lower()]
        for root in shown:
            root_list.add_option(Option(f"{root.label} ({root.path})", id=root.path))
        if not shown:
            return
        if not initial and self.selected_root in shown:
            root_list.highlighted = shown.index(self.selected_root)
            return
        root_list.highlighted = 0
        if initial:
            self._select_root(shown[0])

    def _select_root(self, root: RootDir) -> None:
        self.selected_root = root
        self.workers.cancel_group(self, "git_load")
        self.workers.cancel_group(self, "branch_sync")
        self.workers.cancel_group(self, "folder_refresh")
        self._load_root_projects(root)

    @work(exclusive=True, group="root_scan")
    async def _load_root_projects(self, root: RootDir) -> None:
        repo_list = self.query_one("#repo-list", OptionList)
        repo_list.loading = True
        self._set_banner(f"Scanning repositories under {root.label} ({root.path})...")
        try:
            found = await asyncio.to_thread(discover_projects, root.path, self.tui_config.scan_depth)
        finally:
            self._set_loading(False, repo_list)
        if self.selected_root != root:
            return
        self.projects = found
        self._populate_repos(self.query_one("#repo-filter", Input).value, initial=True)

    def _populate_repos(self, filter_text: str = "", *, initial: bool = False) -> None:
        repo_list = self.query_one("#repo-list", OptionList)
        repo_list.clear_options()
        needle = filter_text.strip().lower()
        shown = [p for p in self.projects if not needle or needle in p.label().lower()]
        for proj in shown:
            repo_list.add_option(Option(proj.label(), id=proj.path))
        if not shown:
            self._set_banner(f"No Git repositories found under {self.selected_root.path}.")
            return
        if not initial and self.selected_project in shown:
            repo_list.highlighted = shown.index(self.selected_project)
            return
        repo_list.highlighted = 0
        if initial or self.selected_project is None:
            self._on_project_chosen(shown[0])

    def _on_project_chosen(self, project: ProjectInfo) -> None:
        prev_path = self.selected_project.path if self.selected_project else ""
        self.workers.cancel_group(self, "branch_sync")
        self.workers.cancel_group(self, "folder_refresh")
        self.selected_project = project
        self._repo_loaded_path = ""
        self._sync_repo_list_highlight(project.path)
        if prev_path and os.path.normcase(prev_path) != os.path.normcase(project.path):
            self._reset_outputs()
        self._apply_repo_preset(project.name)
        self._load_repo_branches(project)

    def _sync_repo_list_highlight(self, repo_path: str) -> None:
        """Move `#repo-list` highlight onto `repo_path` when selected outside the list (e.g. Command Palette)."""
        repo_list = self.query_one("#repo-list", OptionList)
        target_key = os.path.normcase(repo_path)
        for idx in range(repo_list.option_count):
            if os.path.normcase(repo_list.get_option_at_index(idx).id or "") == target_key:
                repo_list.highlighted = idx
                break

    def _reset_outputs(self) -> None:
        """Clear prior repository outputs and tab readiness badges when switching repositories."""
        self.last_report_md = ""
        self.last_comment_md = ""
        self.last_handoff_md = ""
        self.findings_rows = []
        self._filtered_findings = []
        self._selected_finding = None
        self.query_one("#report-log", RichLog).clear()
        self.query_one("#findings-table", DataTable).clear()
        self.query_one("#finding-detail", Static).update("Run a review to see its findings.")
        self.query_one("#btn-explain-check", Button).disabled = True
        self.query_one("#comment-md", Markdown).update("*No `--comment` generated yet.*")
        self.query_one("#handoff-md", Markdown).update("*No `--handoff` generated yet.*")
        tabs = self.query_one("#output-tabs", TabbedContent)
        tabs.get_tab("tab-findings").label = "2. Findings Explorer"
        tabs.get_tab("tab-comment").label = "3. Author Comment (--comment)"
        tabs.get_tab("tab-handoff").label = "4. Agent Handoff (--handoff)"

    def _apply_repo_preset(self, repo_name: str) -> None:
        preset = self.tui_config.projects.get(repo_name)
        d = self.tui_config.defaults
        prof = preset.profile if preset and preset.profile else d.profile
        if any(p.name == prof for p in self.profiles):
            self.query_one("#profile-select", Select).value = prof
        fail_on = preset.fail_on if preset and preset.fail_on else d.fail_on
        self.query_one("#fail-on-select", Select).value = fail_on
        self.query_one("#sw-comment", Switch).value = (
            preset.comment if preset and preset.comment is not None else d.comment
        )
        self.query_one("#sw-handoff", Switch).value = (
            preset.handoff if preset and preset.handoff is not None else d.handoff
        )
        self.query_one("#sw-verify", Switch).value = preset.verify if preset and preset.verify is not None else d.verify
        self.query_one("#extra-path", Input).value = ""  # the saved paths come back when the folders are listed
        self.query_one("#extra-exclude", Input).value = ""
        self.query_one("#check-input", Input).value = ", ".join(preset.checks) if preset else ""
        self.query_one("#diff-cap-input", Input).value = str(preset.diff_cap) if preset and preset.diff_cap > 0 else ""

    @work(exclusive=True, group="git_load")
    async def _load_repo_branches(self, project: ProjectInfo, *, fetch: bool = False) -> None:
        """List `project`'s branches as the repository holds them; with `fetch`, the person's own F5, after a
        `git fetch --prune`. Nothing is fetched unasked, as the reviewer writes nothing into a repository under review.
        Read by review, and reproduced: opening the screen fetched the first repository of the root, and pruned a
        remote-tracking ref there."""
        head_list = self.query_one("#head-list", OptionList)
        base_list = self.query_one("#base-list", OptionList)
        head_list.loading = True
        base_list.loading = True
        said = "branches as the repository holds them"
        try:
            if fetch:
                self._set_banner(f"Running git fetch --prune on {project.name}...")
                self._log_progress(f"[{project.name}] git fetch --prune ...")
                fetch_res = await asyncio.to_thread(fetch_remote, project.path, self.tui_config.fetch_timeout_s)
                if self.selected_project != project:
                    return
                said = fetch_res.message
                self._log_progress(f"[{project.name}] {said}")
                if not fetch_res.ok:
                    self.notify(said, severity="warning", timeout=4, markup=False)
            branches = await asyncio.to_thread(list_branches, project.path)
        finally:
            self._set_loading(False, head_list, base_list)
        if self.selected_project != project:
            return
        self.branches = branches
        names = {b.name for b in branches}
        if fetch and self.selected_head in names and self.selected_base in {*names, ""}:
            # F5 keeps the branches chosen where they are still there. Read by review: it set them back to the branch
            # checked out and the default target, and a review after it read another pair.
            head_default, base_default = self.selected_head, self.selected_base
        else:
            preset = self.tui_config.projects.get(project.name)
            head_default = (
                project.branch
                if any(b.name == project.branch for b in branches)
                else (branches[0].name if branches else "HEAD")
            )
            base_default = preferred_base_branch(project.path, branches, head_default, preset.base if preset else "")
        self.selected_head = head_default
        self.selected_base = base_default
        self._render_branch_lists()
        self._set_banner(f"{project.name}: {said} | Head: {self.selected_head} | Base: {self.selected_base}")
        await self._refresh_folders()

    def _is_head_branch(self, branch: BranchInfo) -> bool:
        """Whether `branch` (or its local/remote tracking counterpart) is Branch Under Review."""
        if not self.selected_head:
            return False
        head_local = next((b.local_name for b in self.branches if b.name == self.selected_head), self.selected_head)
        return (
            branch.name == self.selected_head
            or branch.local_name == head_local
            or branch.remote_name == self.selected_head
        )

    def _reconcile_base_against_head(self, repo: str) -> None:
        """Ensure Target Branch (`--base`) differs from Branch Under Review (`--head`)."""
        same = not self.selected_base or any(
            b.name == self.selected_base and self._is_head_branch(b) for b in self.branches
        )
        if same or self.selected_base == self.selected_head:
            candidates = [b for b in self.branches if not self._is_head_branch(b)]
            preset = self.tui_config.projects.get(self.selected_project.name) if self.selected_project else None
            self.selected_base = preferred_base_branch(
                repo, candidates, self.selected_head, preset.base if preset else ""
            )

    def _render_branch_lists(self) -> None:
        if self.selected_project is not None:
            self._reconcile_base_against_head(self.selected_project.path)
        self._suppress_branch_events = True
        try:
            head_filter = self.query_one("#head-filter", Input).value.strip().lower()
            base_filter = self.query_one("#base-filter", Input).value.strip().lower()
            self._fill_branch_option_list("#head-list", head_filter, self.selected_head, exclude_head=False)
            self._fill_branch_option_list("#base-list", base_filter, self.selected_base, exclude_head=True)
        finally:
            self._suppress_branch_events = False
        self._update_cli_preview()

    def _fill_branch_option_list(self, selector: str, needle: str, active_name: str, *, exclude_head: bool) -> None:
        widget = self.query_one(selector, OptionList)
        widget.clear_options()
        shown = [
            b
            for b in self.branches
            if (not exclude_head or not self._is_head_branch(b)) and (not needle or needle in b.label().lower())
        ]
        active_idx = 0
        for idx, branch in enumerate(shown):
            widget.add_option(Option(branch.label(), id=branch.name))
            if branch.name == active_name:
                active_idx = idx
        if shown:
            widget.highlighted = active_idx

    @work(exclusive=True, group="branch_sync")
    async def _sync_chosen_branch(self, role: str, branch_name: str) -> None:
        project = self.selected_project
        if project is None:
            return
        repo = project.path
        info = next((b for b in self.branches if b.name == branch_name), None)
        if info is None:
            return
        if role == "base" and self._is_head_branch(info):
            self._set_banner("Target Branch cannot be the same as Branch Under Review.")
            return
        if info.is_remote:
            # As the remote holds it, never through a local copy of it. Read by review: with a local branch ahead of it,
            # `origin/x` reviewed the local one, its unpushed work and all.
            resolved = info.name
        else:
            status = await asyncio.to_thread(inspect_branch_sync, repo, info)
            if self.selected_project != project:
                return
            resolved = await self._resolve_sync_status(repo, role, info, status)
            if self.selected_project != project:
                return
        before = self._target()
        if role == "head":
            self.selected_head = resolved
        else:
            self.selected_base = resolved
        if self._target() != before:
            self._reset_outputs()
        refreshed = await asyncio.to_thread(list_branches, repo)
        if self.selected_project != project:
            return
        self.branches = refreshed
        self._render_branch_lists()
        await self._refresh_folders()

    async def _resolve_sync_status(self, repo: str, role: str, info: BranchInfo, status: SyncStatus) -> str:
        """The ref to review for the local branch `info`: where it is behind its remote or apart from it, asked."""
        if status.kind == "behind":
            should_update = await self.push_screen_wait(SyncBranchModal(role, status))
            if should_update:
                outcome = await asyncio.to_thread(
                    fast_forward_local_branch, repo, status.local_branch, status.upstream_branch
                )
                self._log_progress(outcome.message)
                self._set_banner(outcome.message)
                severity = "information" if outcome.ok else "warning"
                self.notify(outcome.message, severity=severity, timeout=4, markup=False)
                return outcome.resolved_ref
            return status.local_branch
        if status.kind == "diverged":
            chosen_ref = await self.push_screen_wait(DivergedBranchModal(role, status))
            self._log_progress(f"Selected {chosen_ref} for diverged {role} branch")
            return chosen_ref
        return info.name

    def _target(self) -> tuple[str, str, str, str]:
        """What a review reads: the repository, the mode, and its branches."""
        repo = self.selected_project.path if self.selected_project else ""
        return repo, self.selected_mode, self.selected_head, self.selected_base

    def _path_selection_sets(
        self, project: ProjectInfo, path_list: SelectionList[str], excl_list: SelectionList[str], listed: set[str]
    ) -> tuple[set[str], set[str]]:
        """The folders ticked: the saved ones when a repository loads, else those ticked now. A saved path comes back
        once, ticked where it is a listed folder and typed where it is not. Read by review: it came back as both, so
        unticking it changed nothing, the typed copy still passed."""
        if self._repo_loaded_path != project.path:
            self._repo_loaded_path = project.path
            preset = self.tui_config.projects.get(project.name)
            paths, excludes = (preset.path, preset.path_exclude) if preset else ((), ())
            self.query_one("#extra-path", Input).value = ", ".join(p for p in paths if p not in listed)
            self.query_one("#extra-exclude", Input).value = ", ".join(p for p in excludes if p not in listed)
            return set(paths) & listed, set(excludes) & listed
        return set(path_list.selected), set(excl_list.selected)

    @work(exclusive=True, group="folder_refresh")
    async def _refresh_folders_worker(self) -> None:
        await self._refresh_folders()

    async def _refresh_folders(self) -> None:
        project = self.selected_project
        if project is None:
            return
        repo = project.path
        base = self.selected_base if self.selected_mode == "branch" else None
        head = self.selected_head or "HEAD"
        path_list = self.query_one("#path-list", SelectionList)
        excl_list = self.query_one("#exclude-list", SelectionList)
        path_list.loading = True
        excl_list.loading = True
        try:
            folders, changed_count = await asyncio.to_thread(changed_folders, repo, base, head, self.selected_mode)
        finally:
            self._set_loading(False, path_list, excl_list)
        if self.selected_project != project:
            return
        listed = {folder for folder, _label in folders}
        chosen_paths, chosen_excludes = self._path_selection_sets(project, path_list, excl_list, listed)
        self._syncing_paths = True
        try:
            path_list.clear_options()
            excl_list.clear_options()
            for folder, label in folders:
                is_excl = folder in chosen_excludes
                path_list.add_option(Selection(Content(label), folder, folder in chosen_paths and not is_excl))
                excl_list.add_option(Selection(Content(label), folder, is_excl))
        finally:
            self._syncing_paths = False
        prev_file = baseline_path(repo, self.selected_base, self.selected_head, self.selected_mode)
        has_prev = await asyncio.to_thread(prev_file.is_file)
        if self.selected_project != project:
            return
        prev_badge = "prior baseline available" if has_prev else "no prior baseline yet"
        self._set_banner(
            f"{project.name} ({self.selected_base or self.selected_mode} -> {self.selected_head}): "
            f"{changed_count} changed file(s) across {len(folders)} folder(s) | {prev_badge} | F5 fetches"
        )
        await self._sync_prior_baseline_findings(project, prev_file, has_prev=has_prev)
        self._update_cli_preview()

    async def _sync_prior_baseline_findings(self, project: ProjectInfo, prev_file: Path, *, has_prev: bool) -> None:
        """Populate or clear `2. Findings Explorer` from any saved `--previous` baseline for `(project, target)`."""
        tabs = self.query_one("#output-tabs", TabbedContent)
        if has_prev:
            rows = await asyncio.to_thread(load_findings_from_json, prev_file)
            if (
                self.selected_project != project
                or baseline_path(project.path, self.selected_base, self.selected_head, self.selected_mode) != prev_file
            ):
                return
            self.findings_rows = rows
            tabs.get_tab("tab-findings").label = (
                f"2. Findings Explorer ({len(rows)} prior)" if rows else "2. Findings Explorer"
            )
        else:
            self.findings_rows = []
            tabs.get_tab("tab-findings").label = "2. Findings Explorer"
        self._render_findings_table()

    def current_selection(self, *, dry_run: bool = False) -> ReviewSelection:
        """Collect all current widget states into a `ReviewSelection`."""
        repo = self.selected_project.path if self.selected_project else ""
        checked_paths = list(self.query_one("#path-list", SelectionList).selected)
        extra_paths = parse_csv_paths(self.query_one("#extra-path", Input).value)
        checked_excludes = list(self.query_one("#exclude-list", SelectionList).selected)
        extra_excludes = parse_csv_paths(self.query_one("#extra-exclude", Input).value)
        checks = tuple(parse_csv_checks(self.query_one("#check-input", Input).value))
        diff_cap = parse_diff_cap(self.query_one("#diff-cap-input", Input).value)
        return ReviewSelection(
            repo=repo,
            mode=self.selected_mode,
            head=self.selected_head,
            base=self.selected_base,
            profile=str(self.query_one("#profile-select", Select).value),
            fail_on=str(self.query_one("#fail-on-select", Select).value),
            comment=self.query_one("#sw-comment", Switch).value,
            handoff=self.query_one("#sw-handoff", Switch).value,
            verify=self.query_one("#sw-verify", Switch).value,
            use_cache=self.query_one("#sw-cache", Switch).value,
            use_previous=self.query_one("#sw-previous", Switch).value,
            dry_run=dry_run,
            paths=tuple(dict.fromkeys([*checked_paths, *extra_paths])),
            path_excludes=tuple(dict.fromkeys([*checked_excludes, *extra_excludes])),
            checks=checks,
            diff_cap=diff_cap,
            output_dir=self.tui_config.output_dir,
        )

    def _has_dom(self, *selectors: str) -> bool:
        """Whether the app is mounted and every widget in `selectors` still exists in the active screen."""
        return self.is_running and all(bool(self.query(sel)) for sel in selectors)

    def _update_cli_preview(self) -> None:
        if self.selected_project is None or not self._has_dom("#path-list", "#cli-preview"):
            return
        sel = self.current_selection(dry_run=False)
        self._update_section_titles(sel)
        self._update_selection_summary(sel)
        try:
            argv, _outputs = build_review_argv(sel)
        except ValueError as exc:
            self.query_one("#cli-preview", Static).update(str(exc))
            return
        self.query_one("#cli-preview", Static).update(format_cli_command(argv))

    def _update_selection_summary(self, sel: ReviewSelection) -> None:
        """Keep a 1-line summary of active choices visible even when sections are minimized."""
        proj = self.selected_project.name if self.selected_project else "none"
        target = f"{sel.head or 'HEAD'} -> {sel.base}" if sel.mode == "branch" else f"--{sel.mode}"
        checks_or_prof = f"--check ({len(sel.checks)})" if sel.checks else sel.profile
        path_tag = (
            f"{len(sel.paths)} in / {len(sel.path_excludes)} ex" if (sel.paths or sel.path_excludes) else "all files"
        )
        summary = (
            f"Repo: {proj}  |  Target: {target}  |  Paths: {path_tag}  "
            f"|  Checks: {checks_or_prof}  |  Fail on: {sel.fail_on}"
        )
        self.query_one("#selection-summary", Static).update(summary)

    def _update_section_titles(self, sel: ReviewSelection | None = None) -> None:
        """Keep each collapsible header synced between full summary (expanded) and compact initials (collapsed)."""
        active = sel or self.current_selection(dry_run=False)
        proj_name = self.selected_project.name if self.selected_project else "none"
        paths_tag = (
            f"{len(active.paths)} in, {len(active.path_excludes)} ex"
            if (active.paths or active.path_excludes)
            else "all"
        )
        head_tag = self.selected_head or self.selected_mode
        base_tag = self.selected_base if self.selected_mode == "branch" else "n/a"
        profile_tag = f"--check x{len(active.checks)}" if active.checks else active.profile
        specs = (
            ("#root-box", f"1. Root Workspace Folder [{self.selected_root.label}]", "1.RWF"),
            ("#repo-box", f"{self._step(2)}. Local Repository [{proj_name}]", f"{self._step(2)}.LR"),
            ("#head-box", f"{self._step(3)}. Branch Under Review [{head_tag}]", f"{self._step(3)}.BUR"),
            ("#base-box", f"{self._step(4)}. Target Branch [{base_tag}]", f"{self._step(4)}.TB"),
            ("#paths-pane", f"{self._step(5)}. Path Scoping [{paths_tag}]", f"{self._step(5)}.PS"),
            ("#profile-pane", f"{self._step(6)}. Review Profile [{profile_tag}]", f"{self._step(6)}.RP"),
            ("#output-options-pane", f"{self._step(7)}. Output Options", f"{self._step(7)}.OO"),
        )
        for selector, full_title, short_title in specs:
            box = self.query_one(selector, Collapsible)
            box.title = literal(short_title if box.collapsed else full_title)  # a title takes a string alone
            box.tooltip = Content(full_title)

    def select_profile_by_name(self, name: str) -> None:
        """Set `#profile-select` to `name` (used by Command Palette shortcuts)."""
        if any(p.name == name for p in self.profiles):
            self.query_one("#profile-select", Select).value = name

    def _profile_detail(self, profile_name: str) -> str:
        found = next((p for p in self.profiles if p.name == profile_name), None)
        return found.detail() if found else OPTION_DESCRIPTIONS["profile"]

    @on(Input.Changed, "#root-filter")
    def _on_root_filter(self, event: Input.Changed) -> None:
        self._populate_roots(event.value, initial=False)

    @on(Input.Changed, "#repo-filter")
    def _on_repo_filter(self, event: Input.Changed) -> None:
        self._populate_repos(event.value, initial=False)

    @on(Input.Changed, "#head-filter")
    def _on_head_filter(self) -> None:
        self._render_branch_lists()

    @on(Input.Changed, "#base-filter")
    def _on_base_filter(self) -> None:
        self._render_branch_lists()

    @on(Input.Changed, "#extra-path")
    @on(Input.Changed, "#extra-exclude")
    @on(Input.Changed, "#check-input")
    @on(Input.Changed, "#diff-cap-input")
    def _on_extra_inputs_changed(self) -> None:
        self._update_cli_preview()

    @on(OptionList.OptionSelected, "#root-list")
    def _on_root_selected(self, event: OptionList.OptionSelected) -> None:
        root_key = os.path.normcase(event.option.id or "")
        root = next((r for r in self.tui_config.root_dirs if os.path.normcase(r.path) == root_key), None)
        if root is not None:
            self._select_root(root)

    @on(OptionList.OptionSelected, "#repo-list")
    def _on_repo_selected(self, event: OptionList.OptionSelected) -> None:
        repo_key = os.path.normcase(event.option.id or "")
        proj = next((p for p in self.projects if os.path.normcase(p.path) == repo_key), None)
        if proj is not None:
            self._on_project_chosen(proj)

    @on(OptionList.OptionSelected, "#head-list")
    def _on_head_selected(self, event: OptionList.OptionSelected) -> None:
        if self._suppress_branch_events:
            return
        branch_name = event.option.id or ""
        if branch_name:
            self._sync_chosen_branch("head", branch_name)

    @on(OptionList.OptionSelected, "#base-list")
    def _on_base_selected(self, event: OptionList.OptionSelected) -> None:
        if self._suppress_branch_events:
            return
        branch_name = event.option.id or ""
        if branch_name:
            self._sync_chosen_branch("base", branch_name)

    @on(Select.Changed, "#mode-select")
    def _on_mode_changed(self, event: Select.Changed) -> None:
        val = str(event.value)
        if val in ("branch", "staged", "all") and self._has_dom("#base-list", "#head-list"):
            if val != self.selected_mode:
                self._reset_outputs()  # another target's results are never shown as this one's
            self.selected_mode = val  # type: ignore[assignment]
            self.query_one("#base-list", OptionList).disabled = val != "branch"
            self.query_one("#head-list", OptionList).disabled = val == "staged"
            self._update_cli_preview()
            self._refresh_folders_worker()

    @on(Select.Changed, "#profile-select")
    def _on_profile_changed(self, event: Select.Changed) -> None:
        if not self._has_dom("#profile-desc"):
            return
        prof = str(event.value)
        self.query_one("#profile-desc", Static).update(self._profile_detail(prof))
        self._update_cli_preview()

    @on(SelectionList.SelectedChanged, "#path-list")
    def _on_path_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if self._syncing_paths:
            return
        self._reconcile_path_lists(event.selection_list, "#exclude-list")
        self._update_cli_preview()

    @on(SelectionList.SelectedChanged, "#exclude-list")
    def _on_exclude_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if self._syncing_paths:
            return
        self._reconcile_path_lists(event.selection_list, "#path-list")
        self._update_cli_preview()

    def _reconcile_path_lists(self, source: SelectionList[str], other_id: str) -> None:
        """Deselect any folder in `other_id` that was just selected in `source`."""
        self._syncing_paths = True
        try:
            other = self.query_one(other_id, SelectionList)
            for item in set(source.selected) & set(other.selected):
                other.deselect(item)
        finally:
            self._syncing_paths = False

    @on(Select.Changed, "#fail-on-select")
    @on(Switch.Changed)
    def _on_any_option_changed(self) -> None:
        self._update_cli_preview()

    @on(Select.Changed, "#finding-sev-filter")
    @on(Select.Changed, "#finding-tier-filter")
    @on(Input.Changed, "#finding-search")
    def _on_finding_filter_changed(self) -> None:
        self._render_findings_table()

    @on(DataTable.RowHighlighted, "#findings-table")
    @on(DataTable.RowSelected, "#findings-table")
    def _on_finding_row_active(self, event: DataTable.RowHighlighted | DataTable.RowSelected) -> None:
        self._select_finding_at(event.cursor_row)

    @on(Button.Pressed, "#btn-explain-check")
    def action_explain_selected_check(self) -> None:
        """Open the check reference modal for the selected finding in Findings Explorer."""
        if self._selected_finding and self._selected_finding.check:
            self.push_screen(ExplainCheckModal(self._selected_finding.check))

    @on(Collapsible.Collapsed)
    @on(Collapsible.Expanded)
    @on(Collapsible.Toggled)
    def _on_collapsible_toggled(self) -> None:
        self._update_section_titles()

    @on(Button.Pressed, "#btn-dry-run")
    def action_run_dry(self) -> None:
        """Validate review target and model readiness via `--dry-run`."""
        self._execute_review(dry_run=True)

    @on(Button.Pressed, "#btn-run")
    def action_run_review(self) -> None:
        """Execute `adk-review` with the configured options."""
        self._execute_review(dry_run=False)

    @on(Button.Pressed, "#btn-stop")
    def action_stop_review(self) -> None:
        """Cancel the active review worker and terminate its subprocess."""
        self.workers.cancel_group(self, "review_exec")
        self._log_progress("Review stopped by user.")

    def action_refresh_repo(self) -> None:
        """Fetch latest remote refs and reload branches for the active repository: the person's own `git fetch`."""
        if self.selected_project is not None:
            self._load_repo_branches(self.selected_project, fetch=True)

    def action_toggle_step(self, step_num: int) -> None:
        """Toggle the visible section numbered `step_num` when focus is outside a text `Input`."""
        if isinstance(self.focused, Input):
            return
        sections = [c for c in self.query(Collapsible) if not c.has_class("hidden")]
        if 1 <= step_num <= len(sections):
            section = sections[step_num - 1]
            section.collapsed = not section.collapsed
            self._update_section_titles()

    def action_minimize_all(self) -> None:
        """Minimize every visible panel, however each stands. Asked by the owner: one key that folded or unfolded
        them all, by how it found them, gave no sure way to either."""
        self._fold_all(collapsed=True)

    def action_restore_all(self) -> None:
        """Restore every visible panel, however each stands."""
        self._fold_all(collapsed=False)

    def _fold_all(self, *, collapsed: bool) -> None:
        for section in self.query(Collapsible):
            if not section.has_class("hidden"):
                section.collapsed = collapsed
        self._update_section_titles()

    def action_copy_active_tab(self) -> None:
        """Copy the active output tab's text or selected finding detail to the clipboard."""
        tabs = self.query_one("#output-tabs", TabbedContent)
        active = tabs.active
        text = self.last_report_md
        if active == "tab-findings" and self._selected_finding is not None:
            text = format_finding_detail(self._selected_finding)
        elif active == "tab-comment":
            text = self.last_comment_md
        elif active == "tab-handoff":
            text = self.last_handoff_md
        if text:
            name = active.removeprefix("tab-")
            if copy_text(self, text):
                self._set_banner(f"Copied {name} content to the clipboard ({len(text):,} chars).")
                self.notify("Copied active tab to the clipboard.", severity="information", timeout=3)
            else:  # through the terminal alone, which no program can check
                self._set_banner(f"Sent {name} content to the clipboard through the terminal ({len(text):,} chars).")
                self.notify(
                    "Sent to the clipboard through the terminal. "
                    "If nothing pastes, use Windows Terminal, iTerm2 or kitty.",
                    severity="information",
                    timeout=6,
                )

    def _save_current_preset(self, sel: ReviewSelection) -> None:
        if self.selected_project is None:
            return
        preset = RepoPreset(
            base=sel.base,
            profile=sel.profile,
            fail_on=sel.fail_on,
            comment=sel.comment,
            handoff=sel.handoff,
            verify=sel.verify,
            path=sel.paths,
            path_exclude=sel.path_excludes,
            checks=sel.checks,
            diff_cap=sel.diff_cap,
        )
        self.tui_config = save_repo_preset(self.tui_config, self.selected_project.name, preset)

    @staticmethod
    def _set_loading(on: bool, *widgets: Widget) -> None:
        """Show or end each list's loading mark, where the list is still on a screen. Seen on Windows in CI, where git
        is slower: a scan the closing screen cut short set it on a list whose screen was gone, `NoScreen`."""
        for widget in widgets:
            if widget.is_attached:
                widget.loading = on

    def _set_running_ui(self, running: bool) -> None:
        if not self._has_dom("#btn-stop", "#btn-run", "#btn-dry-run"):  # a review the closing screen cut short
            return
        self.query_one("#btn-stop", Button).disabled = not running
        self.query_one("#btn-run", Button).disabled = running
        self.query_one("#btn-dry-run", Button).disabled = running

    @work(exclusive=True, group="review_exec")
    async def _execute_review(self, *, dry_run: bool) -> None:
        if self.selected_project is None:
            self._set_banner("Select a repository first.")
            return
        if self.selected_mode == "branch" and (not self.selected_base or self.selected_head == self.selected_base):
            self._set_banner("Branch Under Review and Target Branch cannot be the same.")
            return
        label = "dry-run" if dry_run else "review"
        self._reviewing = True
        try:
            await self._run_review(dry_run=dry_run, label=label)
        except (OSError, ValueError) as exc:
            # Read by review: an error here, a config the screen could not write, ended the whole screen with a
            # traceback, Textual's own way with a worker's error.
            self._set_banner(f"The {label} could not run: {exc}")
            self.notify(f"The {label} could not run: {exc}", severity="error", timeout=8, markup=False)
        finally:
            self._reviewing = False

    async def _run_review(self, *, dry_run: bool, label: str) -> None:
        project, target = self.selected_project, self._target()
        if project is None:
            return
        sel = self.current_selection(dry_run=dry_run)
        if not dry_run:
            await asyncio.to_thread(self._save_current_preset, sel)
        argv, outputs = build_review_argv(sel)
        report_log = self.query_one("#report-log", RichLog)
        report_log.clear()
        self._set_banner(f"Running {label} on {project.name}...")
        self._log_progress(f"$ {format_cli_command(argv)}")
        self._set_running_ui(True)
        try:
            code, stdout_text = await self._stream_subprocess(review_command(argv))
        except asyncio.CancelledError:
            self._set_banner(f"{label.capitalize()} stopped by user.")
            raise
        finally:
            self._set_running_ui(False)
        if not dry_run:
            await asyncio.to_thread(promote_baseline, outputs, code)
        if self._target() != target:
            # Read by review: a review that ended after the person chose another target filled that target's tabs.
            where = f" Its files are in {self.tui_config.output_dir}." if outputs else ""
            self._set_banner(
                f"Finished the {label} of {target[2] or target[1]} (exit code {code}). It is not shown: you chose "
                f"another target since.{where}"
            )
            return
        self.last_report_md = stdout_text
        if stdout_text:
            report_log.write(stdout_text.rstrip("\n"))
        await self._load_generated_files(outputs)
        self.query_one("#output-tabs", TabbedContent).active = "tab-report"
        self._set_banner(f"Finished {label} on {project.name} (exit code {code}).")
        self.notify(
            f"Finished {label} (exit code {code}).", severity="information" if code == 0 else "warning", timeout=4
        )

    async def _stream_subprocess(self, cmd: list[str]) -> tuple[int, str]:
        # UTF-8 streams: on Windows Python writes a pipe in the system's code page, cp1252, where this reads UTF-8.
        # Reproduced with that code page set: a progress line's `ü` and `—` read as replacement marks, and the
        # report's `→` stopped the review with an error.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        self._active_proc = proc
        stdout_chunks: list[str] = []

        def told(raw: bytes) -> None:
            if line := raw.decode("utf-8", errors="replace").rstrip():
                self._log_progress(line)
                self._set_banner(line[:300])

        async def _read_stderr() -> None:
            # In pieces, split here: `readline` raises past 64 KiB, and read by review, one line of a provider's error
            # body could be longer.
            if proc.stderr is None:
                return
            pending = b""
            while piece := await proc.stderr.read(65_536):
                *lines, pending = (pending + piece).split(b"\n")
                for raw in lines:
                    told(raw)
            told(pending)

        async def _read_stdout() -> None:
            if proc.stdout is None:
                return
            data = await proc.stdout.read()
            stdout_chunks.append(data.decode("utf-8", errors="replace"))

        try:
            await asyncio.gather(_read_stderr(), _read_stdout())
            code = await proc.wait()
        finally:
            self._active_proc = None
            await self._terminate_proc(proc)
        return code, "".join(stdout_chunks).replace("\r\n", "\n")  # Windows ends a line in CR LF

    @staticmethod
    async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            if os.name == "nt" and proc.pid is not None:
                taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                await asyncio.to_thread(
                    subprocess.run,
                    [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

    async def _load_generated_files(self, outputs: dict[str, Path]) -> None:
        tabs = self.query_one("#output-tabs", TabbedContent)
        json_path = outputs.get("json")
        if json_path and json_path.is_file():
            self.findings_rows = await asyncio.to_thread(load_findings_from_json, json_path)
            count = len(self.findings_rows)
            tabs.get_tab("tab-findings").label = f"2. Findings Explorer ({count})" if count else "2. Findings Explorer"
            self._render_findings_table()
        comment_path = outputs.get("comment")
        if comment_path and comment_path.is_file():
            self.last_comment_md = await asyncio.to_thread(comment_path.read_text, encoding="utf-8")
            await self.query_one("#comment-md", Markdown).update(self.last_comment_md)
            tabs.get_tab("tab-comment").label = "3. Author Comment (ready)"
        else:
            tabs.get_tab("tab-comment").label = "3. Author Comment (--comment)"
        handoff_path = outputs.get("handoff")
        if handoff_path and handoff_path.is_file():
            self.last_handoff_md = await asyncio.to_thread(handoff_path.read_text, encoding="utf-8")
            await self.query_one("#handoff-md", Markdown).update(self.last_handoff_md)
            tabs.get_tab("tab-handoff").label = "4. Agent Handoff (ready)"
        else:
            tabs.get_tab("tab-handoff").label = "4. Agent Handoff (--handoff)"

    def _render_findings_table(self) -> None:
        if not self._has_dom("#finding-sev-filter", "#finding-tier-filter", "#finding-search", "#findings-table"):
            return
        sev = str(self.query_one("#finding-sev-filter", Select).value)
        tier = str(self.query_one("#finding-tier-filter", Select).value)
        query = self.query_one("#finding-search", Input).value
        filtered = filter_findings(self.findings_rows, severity=sev, tier=tier, query=query)
        self._filtered_findings = filtered
        table = self.query_one("#findings-table", DataTable)
        table.clear()
        for row in filtered:
            cells = (row.severity, row.tier, row.check, row.location(), row.title)
            table.add_row(*(Text(cell) for cell in cells))  # a cell given as a string is read as markup
        if filtered:
            self._select_finding_at(0)
        else:
            self._selected_finding = None
            self.query_one("#btn-explain-check", Button).disabled = True
            msg = "No finding matches the filters." if self.findings_rows else "This review has no findings."
            self.query_one("#finding-detail", Static).update(msg)

    def _select_finding_at(self, row_index: int) -> None:
        if 0 <= row_index < len(self._filtered_findings):
            selected = self._filtered_findings[row_index]
            self._selected_finding = selected
            self.query_one("#finding-detail", Static).update(format_finding_detail(selected))
            self.query_one("#btn-explain-check", Button).disabled = not bool(selected.check)

    def _set_banner(self, message: str) -> None:
        if self._has_dom("#status-banner"):
            self.query_one("#status-banner", Static).update(message)

    def _log_progress(self, line: str) -> None:
        if self._has_dom("#progress-log"):
            self.query_one("#progress-log", RichLog).write(line)
