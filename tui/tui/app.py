"""Textual front-end shell (`adk-review-tui`) implementing the cascading Chain of Selects."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import ClassVar, NamedTuple

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.command import Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.notifications import SeverityLevel
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

from adk_review.core.text import kinds_of, squash
from adk_review.deliver.report import one_line, unhidden

from .azure import Place, PullRequest, draft, files_link, line_link, parse_pr_url, thread_link
from .azure_client import AzLogin, AzureError, Client, Credential, post_comment
from .azure_state import forget_posted, keep_posted, keep_pull_request_url, posted_threads, pull_request_url
from .clipboard import copy_text
from .commands import ReviewCommandProvider
from .config import RepoPreset, RootDir, TuiConfig, load_tui_config, save_repo_preset
from .findings import (
    FindingRow,
    filter_findings,
    format_finding_detail,
    load_report,
)
from .git_sync import (
    BranchInfo,
    ProjectInfo,
    SyncStatus,
    discover_projects,
    fast_forward_local_branch,
    fetch_remote,
    file_lines,
    inspect_branch_sync,
    list_branches,
    preferred_base_branch,
    read_change,
)
from .modals import CommentModal, ConfirmPostModal, DivergedBranchModal, ExplainCheckModal, SyncBranchModal
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
    make_output_dir,
    parse_csv_checks,
    parse_csv_paths,
    parse_diff_cap,
    promote_baseline,
    review_command,
)
from .styles import APP_CSS, literal

#: Said where the findings shown are of no commit, as a review of what is staged: a line is linked to, and a comment
#: placed, in a commit the pull request holds.
NO_COMMIT = "This review read no commit. Review the pull request's branch, then try again."
NO_LINE = "This finding names no line."
NOT_POSTED = "The comment was not posted. Press c to open your text again."
#: The panels, in their steps' order: each docks as a chip while minimized.
PANELS = ("#root-box", "#repo-box", "#head-box", "#base-box", "#paths-pane", "#profile-pane", "#output-options-pane")
#: What leads a docked panel's chip: the mark Textual's Collapsible draws for a folded section.
DOCKED = "\u25b6"
#: The columns a chip takes beside its label: Textual measures a button as its label and a column either side, and
#: the dock's style puts two between chips. Read by review: counted without them, the sixth chip of a 100-column
#: screen started at its last column.
DOCK_GAP = 2 + 2


#: The fewest characters of a choice a chip keeps with its step's name: below it, the names give way to the choices.
DOCK_NAMED_MIN = 10


class Panel(NamedTuple):
    """One panel as the screen names it: its selector, its step's number, its title, the dock's name for it, and the
    choice made in it."""

    selector: str
    number: int
    title: str
    name: str
    choice: str

    @property
    def heading(self) -> str:
        """Its title as the panel and its tooltip read it: `2. Branch Under Review [main]`."""
        return f"{self.number}. {self.title} [{self.choice}]"


def _dock_labels(docked: Sequence[Panel], width: int) -> list[str]:
    """Each docked panel's chip, `▶ 2 Review: main`, on one line of `width` terminal columns, in the first form that
    fits: the step's number, name and choice, each choice keeping `DOCK_NAMED_MIN` columns; the number and the choice;
    the number alone. The choices are cut the longest first, and each chip's tooltip names its panel. Measured: at 100
    columns the names left 13 columns for six choices. Read by review: a character a terminal draws two columns wide
    counted as one, and seven chips at 80 columns ran past the screen in the shortest form there was."""
    choices = [panel.choice for panel in docked]
    for named in (True, False):
        leads = [f"{DOCKED} {p.number} {p.name}: " if named else f"{DOCKED} {p.number} " for p in docked]
        room = width - 2 - sum(cell_len(lead) + DOCK_GAP for lead in leads)  # the dock's padding, and each gap
        cut = _fitted(choices, room)
        kept = all(cell_len(s) >= min(cell_len(f), DOCK_NAMED_MIN) for s, f in zip(cut, choices, strict=True))
        if sum(map(cell_len, cut)) <= room and (kept or not named):
            return [lead + short for lead, short in zip(leads, cut, strict=True)]
    return [f"{DOCKED} {panel.number}" for panel in docked]


def _fitted(choices: list[str], room: int) -> list[str]:
    """`choices` cut, the longest first, so together they take at most `room` terminal columns where each can keep 4:
    an ellipsis ends one that was cut. A step's number and name are never cut."""
    cap = max(map(cell_len, choices), default=0)
    while cap > 4 and sum(min(cell_len(choice), cap) for choice in choices) > room:
        cap -= 1
    return [choice if cell_len(choice) <= cap else set_cell_size(choice, cap - 1) + "\u2026" for choice in choices]


#: What Ctrl+Y says on a tab that holds no text of its own, where running a review is not the whole answer: each true
#: whether the tab's switch is on or off, since the last review may have run with it off.
NOTHING_TO_COPY = {
    "tab-findings": "No finding to copy. Run a review, or change the filters above the table.",
    "tab-comment": "No comment to copy yet. Run a review with the comment switched on.",
    "tab-handoff": "No hand-off to copy yet. Run a review with the hand-off switched on.",
}


#: The last lines a run that wrote no report said, shown in the report's place: read by review, a run cut short
#: repeated its every progress line there, and its last is what it said as it ended.
NO_REPORT_LINES = 20
#: The most of a line the banner shows, a run's error line or git's among them.
BANNER_CHARS = 300
#: The tabs a review's own files fill, by the flag that writes each, and what each says where none was written.
WRITTEN = {"comment": "3. Author Comment", "handoff": "4. Agent Handoff"}
_NONE_WRITTEN = "*No `--{flag}` generated yet.*"
#: What the Findings tab says where it holds no review's verdict: no review run yet, or a report it could not read.
RUN_FIRST = "Run a review to see its findings."


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
        # A finding's place in Azure DevOps: plain letters, which a text field takes as text, and which act on the
        # Findings tab alone, where the person sees the finding they open.
        Binding("o", "open_lines", "Open Lines"),
        Binding("p", "open_pull_request", "Open in PR"),
        Binding("c", "comment", "Comment"),
        Binding("1", "toggle_step(1)", "Step 1", show=False),
        Binding("2", "toggle_step(2)", "Step 2", show=False),
        Binding("3", "toggle_step(3)", "Step 3", show=False),
        Binding("4", "toggle_step(4)", "Step 4", show=False),
        Binding("5", "toggle_step(5)", "Step 5", show=False),
        Binding("6", "toggle_step(6)", "Step 6", show=False),
        Binding("7", "toggle_step(7)", "Step 7", show=False),
        # Read by an audit: a plain `q` closed the screen and the review it ran; it now quits only where none runs.
        # Textual's own Ctrl+Q quits whatever runs: read by a code review, a terminal that keeps Ctrl+Q, as VS Code's
        # does, then had no key that quits.
        Binding("q", "quit_when_idle", "Quit"),
    ]

    #: What a key starts that never runs behind a dialog: a review, a dry run, the sections folded, a tab copied. Not
    #: Stop, which must always reach a running review.
    #: What acts on the finding chosen, so only where the Findings tab shows it.
    ON_A_FINDING = frozenset({"open_lines", "open_pull_request", "comment"})
    BEHIND_NO_DIALOG = ON_A_FINDING | {
        "run_review",
        "run_dry",
        "minimize_all",
        "restore_all",
        "copy_active_tab",
        "quit",
        "quit_when_idle",
    }

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002 -- Textual's
        """No action key but Stop runs while a dialog is open. Measured: once the keys went first, Ctrl+R and Ctrl+D
        started a review behind the question a branch behind its remote raises, which they had not before."""
        if action in {"run_review", "run_dry"} and self._reviewing:  # the action says why where the branches load
            return False
        if action in self.ON_A_FINDING and not self._on_findings_tab():
            return False
        if action == "stop_review":
            # Stop goes first while a review runs, and only then: with none running it stopped nothing and took
            # Ctrl+X, a text field's own cut, from every field. No comment opens while a review runs, so the key is
            # the comment editor's own too.
            return self._reviewing
        return not (action in self.BEHIND_NO_DIALOG and isinstance(self.screen, ModalScreen))

    def _run_waits(self) -> bool:
        """Whether a review or a dry run waits: while one runs, whose progress shows, and while a repository's branches
        and folders load, which a notice says. Read by review: the review's worker is exclusive, so Ctrl+R or Ctrl+D
        during a run stopped it and started another, its model calls spent again; a review started while a pick's
        fetch ran read the branches chosen in the last repository; and waiting said nothing."""
        if self._listing_for is not None and not self._reviewing:
            self.notify("The branches are loading. Run the review once they are listed.", timeout=4, markup=False)
        return self._reviewing or self._listing_for is not None

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.tui_config: TuiConfig = load_tui_config(config_path)
        self.profiles: list[ProfileOption] = load_profile_options()
        self.projects: list[ProjectInfo] = []
        self.branches: list[BranchInfo] = []
        self.findings_rows: list[FindingRow] = []
        self._filtered_findings: list[FindingRow] = []
        self._selected_finding: FindingRow | None = None
        self._reviewed: str | None = None  # the commit the findings' report reviewed: their lines are counted in it
        self._verdict: str | None = None  # the verdict that report reached: no finding is "nothing found" only so
        # The last lines the running review said on its error stream, its reason where it ends with no report.
        self._said: deque[str] = deque(maxlen=NO_REPORT_LINES)
        self._pull_request: PullRequest | None = None  # the address field's, where it reads as one
        self._pull_request_of: tuple[str, str] | None = None  # the repository and branch the field was restored for
        self._kept_address: str = ""  # what the state holds for that pair, so it is written when it changes alone
        self._posted_threads: dict[str, int] = {}  # the pull request's posted findings, read when it changes
        self._credential: Credential = AzLogin()  # the person's own `az` login, asked only when a comment is posted
        self._clients: dict[str, Client] = {}  # one a pull request, by its address: what it read once is kept
        self._drafts: dict[tuple[str, str, str], str] = {}  # an edit left for later: where it belongs, and its text
        self._commenting: bool = False  # a comment from its key to its answer: one at a time
        self.selected_root: RootDir = self.tui_config.root_dirs[0]
        self.selected_project: ProjectInfo | None = None
        self.selected_mode: ReviewMode = "branch"
        self.selected_head: str = ""
        self.selected_base: str = ""
        self.last_report_md: str = ""
        self.last_comment_md: str = ""
        self.last_handoff_md: str = ""
        self._progress: list[str] = []  # the progress tab's lines as written, for a copy: the log holds them wrapped
        self._suppress_branch_events: bool = False
        self._repo_loaded_path: str = ""
        self._syncing_paths: bool = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._reviewing: bool = False  # a review or a dry run in its worker, from its start to its end
        self._listing_for: ProjectInfo | None = None  # the repository whose branches are loading, a fetch among it
        self._rows_target: tuple[str, str, str, str] | None = None  # the target whose own review the findings are

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
        yield from self._compose_dock()
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

    def _compose_dock(self) -> ComposeResult:
        """The line a minimized panel docks into, as one compact chip that names its step and its choice: hidden while
        every panel is open."""
        with Horizontal(id="panel-dock", classes="hidden"):
            for selector in PANELS:
                yield Button("", id=f"dock-{selector[1:]}", compact=True, classes="dock-chip hidden")

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
                with Horizontal(id="pr-bar"):
                    yield Input(
                        placeholder="Pull request address (Azure DevOps): paste it once for a branch", id="pr-url"
                    )
                    yield Static("", id="pr-status", markup=False)
                yield DataTable(id="findings-table", cursor_type="row")
                with VerticalScroll(id="finding-detail-scroll"):
                    yield Static(RUN_FIRST, id="finding-detail", markup=False)
            # No link opens on a click: Markdown makes one of an address in a finding's words, which are a model's.
            for flag, title in WRITTEN.items():
                with TabPane(f"{title} (--{flag})", id=f"tab-{flag}"), VerticalScroll():
                    yield Markdown(_NONE_WRITTEN.format(flag=flag), id=f"{flag}-md", open_links=False)
            with TabPane("5. Live Progress & Git Log", id="tab-log"):
                yield RichLog(id="progress-log", wrap=True, highlight=True, markup=False)

    def on_mount(self) -> None:
        """Configure Findings Explorer columns, populate roots, and scan initial workspace."""
        table = self.query_one("#findings-table", DataTable)
        table.add_columns("Severity", "Tier", "Check", "Location", "Title", "Posted")
        # Read by an audit, and reproduced: `focus()` waits a turn, and by then the scan had marked the list as loading,
        # which takes no focus, so the screen's own focus stayed on a chip of the hidden dock and the arrows moved
        # nothing. Set now, before the scan, it is held: a loading list keeps a focus it already has.
        self.set_focus(self._start_list())
        self._populate_roots(initial=True)

    def _start_list(self) -> OptionList:
        """The list the keys start on: the roots' where several show, else the repositories'."""
        return self.query_one("#root-list" if self.has_multiple_roots() else "#repo-list", OptionList)

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
            self._on_project_chosen(shown[0], picked=False)  # the screen's own pick, never fetched

    def _on_project_chosen(self, project: ProjectInfo, *, picked: bool = True) -> None:
        """`project` chosen: `picked` by the person, in the list or the palette, which fetches its new branches where
        `fetch_on_select` says so, or by the screen itself as it lists the repositories, which fetches nothing."""
        prev_path = self.selected_project.path if self.selected_project else ""
        again = bool(prev_path) and os.path.normcase(prev_path) == os.path.normcase(project.path)
        self.workers.cancel_group(self, "branch_sync")
        self.workers.cancel_group(self, "folder_refresh")
        self.selected_project = project
        self._sync_repo_list_highlight(project.path)
        if not again:
            # Another repository: its own preset and folders, and none of the last one's branches, which the palette
            # would offer while its fetch runs. Read by review, and reproduced: picked again, as a fetch of its new
            # branches, a repository set every option back to its preset, a switch turned on among them.
            self._repo_loaded_path = ""
            self.branches = []
            self._forget_pull_request()
            if prev_path:
                self._reset_outputs()
            self._apply_repo_preset(project.name)
        self._load_repo_branches(project, fetch=picked and self.tui_config.fetch_on_select, keep=again)

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
        self._clear_written()
        self._hold([], None, None, None)
        self.query_one("#report-log", RichLog).clear()

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
    async def _load_repo_branches(
        self, project: ProjectInfo, *, fetch: bool = False, prune: bool = False, keep: bool = False
    ) -> None:
        """List `project`'s branches as the repository holds them, after a fetch where `fetch` says: with `--prune` at
        F5, the person's own; without it, removing nothing, as the person picks the repository. Measured on the
        owner's repositories, 5 of 13 lists were out of step with their remote. The fetch comes first and the branches
        are listed once, as F5 lists them: read by review, a listing before the fetch and one after cleared a review's
        findings that ended between the two, moved a highlight being moved, and let a branch chosen meanwhile be
        checked against its remote as it stood before. The branches chosen stay chosen after a fetch, or where `keep`
        says the person picked the same repository again, wherever they are still there. Nothing is fetched as the
        screen opens: read by review, and reproduced, opening it fetched the first repository of the root, and pruned
        a remote-tracking ref there."""
        self._listing_for = project
        try:
            await self._list_branches(project, fetch=fetch, prune=prune, keep=keep)
        finally:
            if self._listing_for is project:  # a later pick's worker may hold it already
                self._listing_for = None

    async def _list_branches(self, project: ProjectInfo, *, fetch: bool, prune: bool, keep: bool) -> None:
        """One pick's or F5's listing of `project`'s branches, its fetch first, and then its folders: a review waits
        for all of it. Read by review: the wait ended as the branches were drawn, while the folders still held the
        last repository's ticks, so a review started then ran with its `--path`."""
        head_list = self.query_one("#head-list", OptionList)
        base_list = self.query_one("#base-list", OptionList)
        said = "branches as the repository holds them"
        self._set_loading(True, head_list, base_list)
        try:
            if fetch:
                command = "git fetch --prune" if prune else "git fetch"
                self._set_banner(f"Running {command} on {project.name}...")
                self._log_progress(f"[{project.name}] {command} ...")
                fetch_res = await asyncio.to_thread(
                    fetch_remote, project.path, self.tui_config.fetch_timeout_s, prune=prune
                )
                if self.selected_project != project:
                    return
                said = fetch_res.message
                self._log_progress(f"[{project.name}] {said}")
                if not fetch_res.ok:
                    off = "" if prune else " fetch_on_select = false turns off the fetch at a pick."
                    self.notify(
                        f"{said}. The lists show the branches as the repository holds them. F5 fetches again.{off}",
                        severity="warning",
                        timeout=6,
                        markup=False,
                    )
            branches = await asyncio.to_thread(list_branches, project.path)
        finally:
            self._set_loading(False, head_list, base_list)
        if self._left(project, "#head-list", "#base-list"):
            return
        self.branches = branches
        names = {b.name for b in branches}
        if (prune or keep) and self.selected_head in names and self.selected_base in {*names, ""}:
            # F5 and a pick of the same repository keep the branches chosen where they are still there; a pick of
            # another takes its own. Read by review: F5 set them back to the branch checked out and the default
            # target; and, kept at every fetch, another repository took the last one's head and base.
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
        self._restore_pull_request()  # here, where the branch becomes the repository's own, and nowhere before
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
            self._restore_pull_request()  # another branch: its own pull request's address
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
                self.notify(outcome.message, severity="information" if outcome.ok else "warning", timeout=4)
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
            change = await asyncio.to_thread(read_change, repo, base, head, self.selected_mode)
        finally:
            self._set_loading(False, path_list, excl_list)
        if self._left(project, "#path-list", "#exclude-list"):
            return
        listed = {folder for folder, _label in change.folders}
        chosen_paths, chosen_excludes = self._path_selection_sets(project, path_list, excl_list, listed)
        self._syncing_paths = True
        try:
            path_list.clear_options()
            excl_list.clear_options()
            for folder, label in change.folders:
                is_excl = folder in chosen_excludes
                path_list.add_option(Selection(Content(label), folder, folder in chosen_paths and not is_excl))
                excl_list.add_option(Selection(Content(label), folder, is_excl))
        finally:
            self._syncing_paths = False
        prev_file = baseline_path(repo, self.selected_base, self.selected_head, self.selected_mode)
        has_prev = await asyncio.to_thread(prev_file.is_file)
        if self._left(project, "#output-tabs"):
            return
        prev_badge = "prior baseline available" if has_prev else "no prior baseline yet"
        # Asked by the owner: what a review would read, told before it runs, as the review's own collector reads it.
        commits = "" if change.commits is None else f"{change.commits} commit(s), "
        by_kind = f" ({kinds})" if (kinds := kinds_of(change.statuses)) else ""
        files = f"{len(change.statuses)} changed file(s){by_kind} across {len(change.folders)} folder(s)"
        self._set_banner(
            f"{project.name} ({self.selected_base or self.selected_mode} -> {self.selected_head}): "
            f"{change.refused or commits + files} | {prev_badge} | F5 fetches"
        )
        await self._sync_prior_baseline_findings(project, prev_file, has_prev=has_prev)
        self._update_cli_preview()

    async def _sync_prior_baseline_findings(self, project: ProjectInfo, prev_file: Path, *, has_prev: bool) -> None:
        """Populate or clear `2. Findings Explorer` from any saved `--previous` baseline for `(project, target)`, unless
        it shows this session's own review of that target. Read by an audit, and reproduced: F5 put an older saved
        review over a degraded one, which is never saved, and the tab said the review found nothing."""
        if self._rows_target == self._target():
            return
        found: tuple[list[FindingRow], str | None, str | None] = ([], None, None)
        if has_prev:
            found = await asyncio.to_thread(load_report, prev_file)
            if (
                self._rows_target == self._target()  # read by a code review: a review that ended during the read
                or self._left(project, "#output-tabs")
                or baseline_path(project.path, self.selected_base, self.selected_head, self.selected_mode) != prev_file
            ):
                return
        self._hold(*found, None, prior=True)

    def _hold(
        self,
        rows: list[FindingRow],
        reviewed: str | None,
        verdict: str | None,
        target: tuple[str, str, str, str] | None,
        *,
        prior: bool = False,
    ) -> None:
        """What the Findings tab holds, the review it came from, and its name, drawn: one place sets them together. Read
        by a code review: a saved report read as the screen closed found no tab to name."""
        if not self._has_dom("#output-tabs"):
            return
        self.findings_rows, self._reviewed, self._verdict, self._rows_target = rows, reviewed, verdict, target
        self.query_one("#output-tabs", TabbedContent).get_tab("tab-findings").label = self._findings_label(prior=prior)
        self._render_findings_table()

    def _findings_label(self, *, prior: bool = False) -> str:
        """The Findings tab's name: how many findings it holds, `prior` where a saved review's, and, where the review
        could not check everything, DEGRADED in words. Read by an audit: the banner alone said so, and the next copy or
        link replaced it."""
        count = len(self.findings_rows)
        said = [f"{count} prior" if prior else str(count)] if count else []
        said += ["DEGRADED"] if self._verdict == "DEGRADED" else []
        return f"2. Findings Explorer ({', '.join(said)})" if said else "2. Findings Explorer"

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

    def _left(self, project: ProjectInfo, *selectors: str) -> bool:
        """Whether what a listing read for `project` has no place to be drawn now: another repository was picked
        meanwhile, or the screen closed and took `selectors` away. Textual's test harness closes the widgets before
        it cancels a worker, where a person's quit cancels first: on a slow runner a test that left mid-listing
        failed there, `NoMatches`."""
        return self.selected_project != project or not self._has_dom(*selectors)

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
        """The one-line summary of the choices, shown while every panel is open: the dock carries them once one is
        minimized."""
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

    def _panel_specs(self, sel: ReviewSelection | None = None) -> list[Panel]:
        """Each panel as the selection stands."""
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
        outputs = (("comment", active.comment), ("hand-off", active.handoff), ("verify", active.verify))
        named = (  # each panel's title, its chip's name, and its choice
            ("Root Workspace Folder", "Root", self.selected_root.label),
            ("Local Repository", "Repo", proj_name),
            ("Branch Under Review", "Review", head_tag),
            ("Target Branch", "Target", base_tag),
            ("Path Scoping", "Folders", paths_tag),
            ("Review Profile", "Profile", f"{profile_tag}, fail on {active.fail_on}"),
            ("Output Options", "Outputs", ", ".join(name for name, on in outputs if on) or "none"),
        )
        return [
            Panel(selector, 1 if step == 1 else self._step(step), title, name, choice)
            for step, (selector, (title, name, choice)) in enumerate(zip(PANELS, named, strict=True), 1)
        ]

    def _update_section_titles(self, sel: ReviewSelection | None = None) -> None:
        """Each panel's title, its step and its choice, and the same whole on hover, which a narrow box cuts: read by
        review, the dock had set it on its chips alone. Then the dock, its chips naming the same choices."""
        if not self._has_dom("#panel-dock"):
            return
        panels = self._panel_specs(sel)
        for panel in panels:
            box = self.query_one(panel.selector, Collapsible)
            box.title = literal(panel.heading)  # a title takes a string alone
            box.tooltip = Content(panel.heading)
        self._update_dock(panels)

    def _update_dock(self, panels: list[Panel] | None = None, *, width: int | None = None) -> None:
        """The dock: a minimized panel leaves its row for a chip in one line, a row with no open panel goes, and so
        does the summary, while the dock carries the choices. Asked by the owner: six minimized panels took two rows
        of boxes labelled with initials, `2.BUR`, and 8 of 45 rows."""
        if not self._has_dom("#panel-dock"):
            return
        chips: list[Button] = []
        docked: list[Panel] = []
        for panel in self._panel_specs() if panels is None else panels:
            box = self.query_one(panel.selector, Collapsible)
            on_dock = box.collapsed and not box.has_class("hidden")
            box.set_class(on_dock, "docked")
            chip = self.query_one(f"#dock-{panel.selector[1:]}", Button)
            chip.set_class(not on_dock, "hidden")
            if on_dock:
                chip.tooltip = Content(panel.heading)
                chips.append(chip)
                docked.append(panel)
        labels = _dock_labels(docked, self.size.width if width is None else width)
        for chip, label in zip(chips, labels, strict=True):
            chip.label = Content(label)  # as written: a repository's name may hold `[`
        for row in ("#chain-row", "#mid-row"):
            boxes = self.query_one(row).query(Collapsible)
            self.query_one(row).set_class(all(b.has_class("hidden") or b.has_class("docked") for b in boxes), "hidden")
        self.query_one("#panel-dock").set_class(not docked, "hidden")
        self.query_one("#selection-summary").set_class(bool(docked), "hidden")

    @on(Collapsible.Toggled)
    def _on_panel_folded(self, event: Collapsible.Toggled) -> None:
        """A panel minimized or restored, by its title, a number key, Ctrl+T or Ctrl+O: the dock follows, alone, as a
        fold leaves every choice as it was. Read by review, and measured: an older handler took the same messages, and
        the actions laid the dock out too, 13 times for one Ctrl+T. The focus inside a panel that docks goes to its
        chip, never to a field no one sees."""
        self._update_dock()
        focused, box = self.focused, event.collapsible
        if isinstance(event, Collapsible.Collapsed) and focused is not None and box in focused.ancestors:
            self.call_after_refresh(self.query_one(f"#dock-{box.id}", Button).focus)

    @on(Button.Pressed, ".dock-chip")
    def _on_dock_chip(self, event: Button.Pressed) -> None:
        """A chip restores its own panel, in its own place."""
        self.query_one(f"#{(event.button.id or '').removeprefix('dock-')}", Collapsible).collapsed = False

    def on_resize(self, event: events.Resize) -> None:
        """The dock's choices cut again for the width the event names: read by review, and reproduced, this runs
        before Textual stores the new size, and the chips were cut for the old one."""
        self._update_dock(width=event.size.width)

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

    @on(Input.Changed, "#pr-url")
    def _on_pull_request_typed(self, event: Input.Changed) -> None:
        self._read_pull_request(event.value, keep=True)

    @on(TabbedContent.TabActivated, "#output-tabs")
    def _on_output_tab(self) -> None:
        self.refresh_bindings()  # the keys that act on a finding show on its tab alone

    def _on_findings_tab(self) -> bool:
        return self._has_dom("#output-tabs") and self.query_one("#output-tabs", TabbedContent).active == "tab-findings"

    def _read_pull_request(self, text: str, *, keep: bool) -> None:
        """The address field read: the pull request it names, or the sentence that says what to paste. A whole
        address is kept for the repository and branch chosen, as Azure DevOps shows it and never as pasted, which can
        hold a credential; an emptied field forgets it, and a wrong one keeps what was kept."""
        before = self._pull_request
        pasted, said, self._pull_request = text.strip(), "", None
        if pasted:
            try:
                self._pull_request = parse_pr_url(pasted)
                said = f"Pull request {self._pull_request.number} of {self._pull_request.repository}."
            except ValueError as refused:
                said = str(refused)
        self.query_one("#pr-status", Static).update(said)
        # Kept for the pair the field was put back for, and for no other. Reproduced by review: after a pick the
        # repository is the new one while the branch is still the last one's, for the whole fetch, and a paste then
        # was kept as the new repository's under the last one's branch.
        pair = self._pair()
        wanted = self._pull_request.web if self._pull_request is not None else ""
        whole = self._pull_request is not None or not pasted  # an address being edited keeps what was kept
        change = keep and pair == self._pull_request_of and whole and wanted != self._kept_address
        if change and self._kept("address", lambda: keep_pull_request_url(*pair, wanted)):
            self._kept_address = wanted
        if self._pull_request != before:  # the threads posted are a pull request's own: read and drawn for another
            self._refresh_posted()
            self._render_findings_table(keep=self._selected_finding)

    def _forget_pull_request(self) -> None:
        """The address field emptied, as another repository is picked: its own comes back with its branches. Read
        as any emptied field is, so nothing of the last pull request stays: reproduced by review, its posted
        threads did, and a finding of the same identity in a second clone showed as posted."""
        self._pull_request_of = None
        if self._has_dom("#pr-url", "#pr-status"):
            field = self.query_one("#pr-url", Input)
            with self.prevent(Input.Changed):
                field.value = ""
            field.disabled = True  # until the repository's own branch is chosen: an address is a branch's
            self._read_pull_request("", keep=False)

    def _restore_pull_request(self) -> None:
        """The address kept for the repository and branch now chosen, put back in its field. Called where the
        branch under review is set, and nowhere else: reproduced by review, called from the folders' refresh, a
        change of the mode while another repository loaded put an address back, and kept one, for the new
        repository under the last one's branch."""
        pair = self._pair()
        if pair == self._pull_request_of or not self._has_dom("#pr-url"):
            return
        self._pull_request_of = pair
        kept = self._kept_address = pull_request_url(*pair)
        field = self.query_one("#pr-url", Input)
        with self.prevent(Input.Changed):
            field.value = kept
        field.disabled = False
        self._read_pull_request(kept, keep=False)

    def _pair(self) -> tuple[str, str]:
        """The repository and the branch under review: what a pull request's address is kept for."""
        return (self.selected_project.path if self.selected_project else "", self.selected_head)

    def _kept(self, what: str, write: Callable[[], None]) -> bool:
        """Whether `write` put something into the state file: where it could not, a notice says so, and the screen
        goes on with what it holds."""
        try:
            write()
        except OSError as failed:
            said = f"The {what} could not be kept: {failed.strerror or type(failed).__name__}."
            self.notify(said, severity="warning", markup=False)
            return False
        return True

    def _refresh_posted(self) -> None:
        """What the state holds of the pull request's posted findings, read once for it: measured by review, read at
        each drawing of the table, the file was read and parsed at each key of the findings' search field."""
        self._posted_threads = posted_threads(self._pull_request) if self._pull_request is not None else {}

    def _open(self, url: str) -> None:
        """`url` opened in the person's browser, and shown too: over SSH or in a container none opens, and the key
        would then do nothing a person could see."""
        self.open_url(url)
        self._set_banner(f"Opening in your browser: {url}")

    def _finding_to_open(self) -> tuple[FindingRow, PullRequest] | None:
        """The finding chosen and the pull request to open it in, or a notice of which is missing."""
        row, pr = self._selected_finding, self._pull_request
        if row is None or pr is None:
            missing = (
                "Run a review first." if row is None else "Paste the pull request's address above the table first."
            )
            self.notify(missing, timeout=4, markup=False)
            return None
        return row, pr

    def action_open_lines(self) -> None:
        """Open the chosen finding's lines in Azure DevOps, in the file as the commit reviewed holds it."""
        if (ready := self._finding_to_open()) is None:
            return
        row, pr = ready
        span = row.span()
        if (said := self._unplaced(row)) or span is None or self._reviewed is None:
            self.notify(said, timeout=6, markup=False)
            return
        self._open(line_link(pr, self._reviewed, row.file, *span))

    def action_open_pull_request(self) -> None:
        """Open the pull request at the chosen finding: its thread once posted, else its file in the Files tab."""
        if (ready := self._finding_to_open()) is None:
            return
        row, pr = ready
        thread = self._posted_threads.get(row.fingerprint)
        self._open(files_link(pr, row.file) if thread is None else thread_link(pr, thread))

    def _unplaced(self, row: FindingRow) -> str:
        """Why `row` has no lines to open or to comment on in the commit reviewed, as a sentence, or nothing where
        it has. A resolved finding's lines were counted in an earlier review's commit: this one's line of that
        number is other code."""
        if row.tier == "Resolved":
            return "This finding is resolved. Its lines are of an earlier commit."
        if self._reviewed is None or self.selected_project is None:
            return NO_COMMIT
        return NO_LINE if row.span() is None else ""

    def _no_comment(self, row: FindingRow) -> str:
        """Why `row` takes no comment, as a sentence, or nothing where it takes one."""
        if self._commenting:
            return "A comment is on its way. Wait for its answer."
        if self._reviewing:  # Ctrl+X stops a review from anywhere, and cuts in the editor: never both at once
            return "A review is running. Comment when it ends."
        return self._unplaced(row)

    def action_comment(self) -> None:
        """Draft a comment on the chosen finding, for the person to edit, confirm and post to the pull request."""
        if (ready := self._finding_to_open()) is None:
            return
        row, pr = ready
        if said := self._no_comment(row):
            self.notify(said, timeout=6, markup=False)
            return
        self._commenting = True
        self._comment(row, pr)

    @work(group="azure")
    async def _comment(self, row: FindingRow, pr: PullRequest) -> None:
        """One comment, from its draft to its answer. Never the only worker of its group by Textual's own rule,
        which would stop a post under way to start another: `_commenting` holds the next one off instead."""
        try:
            await self._comment_on(row, pr)
        finally:
            self._commenting = False

    async def _comment_on(self, row: FindingRow, pr: PullRequest) -> None:
        """The three steps of one comment: the file's lines read, the person's edit and their yes, the post."""
        span, reviewed, project = row.span(), self._reviewed, self.selected_project
        if span is None or reviewed is None or project is None:
            return
        first, last = span
        if row.fingerprint in self._posted_threads and not await self._gone(row, pr):
            return
        lines = await asyncio.to_thread(file_lines, project.path, reviewed, row.file)
        if lines is None or last > len(lines):
            said = "This clone does not hold the reviewed commit's file. Fetch, then press c again."
            self.notify(said, severity="warning", timeout=8, markup=False)
            return
        where = f"line {first}" if first == last else f"lines {first} to {last}"
        kept = self._draft_key(row, pr, reviewed)
        target = f"{row.file}, {where}, pull request {pr.number}"
        text = await self._edited(row, kept, target, lines[first - 1 : last])
        if text is not None:
            place = Place(row.file, first, last, lines[last - 1])
            await self._post(row, pr, text, place, reviewed)

    def _client(self, pr: PullRequest) -> Client:
        """The one client of `pr` for this run of the screen: what it read once, an iteration's files, is kept."""
        if pr.web not in self._clients:
            self._clients[pr.web] = Client(pr, self._credential)
        return self._clients[pr.web]

    async def _gone(self, row: FindingRow, pr: PullRequest) -> bool:
        """Whether a finding the state holds as posted has no thread in the pull request any more, asked of Azure
        DevOps itself: what the state holds is a convenience, and a thread the person deleted there is posted again
        from here. Where the thread stands, or cannot be asked for, the sentence says so."""
        kept = self._posted_threads[row.fingerprint]
        try:
            live = await asyncio.to_thread(self._client(pr).threads)
        except AzureError as refused:
            self.notify(str(refused), severity="error", timeout=12, markup=False)
            return False
        # By the thread's own number, which the screen holds: read by review, asked for by its mark, a thread the
        # person had posted with the mark taken out was said to be gone, and forgotten.
        if any(number == kept for number, _said in live):
            self.notify(f"This finding is posted as thread {kept}. Press p to open it.", timeout=6, markup=False)
            return False
        with contextlib.suppress(OSError):  # not forgotten: asked again at the next c, and answered the same
            forget_posted(pr, row.fingerprint)
        self._refresh_posted()
        self._render_findings_table(keep=row)
        said = f"Thread {kept} is gone from the pull request. You can post the finding again."
        self.notify(said, timeout=6, markup=False)
        return True

    @staticmethod
    def _draft_key(row: FindingRow, pr: PullRequest, commit: str) -> tuple[str, str, str]:
        """What an edit left for later is kept under: its pull request, the commit reviewed, and the finding's
        identity, or its place where it has none. Read by review: kept by the finding alone, an edit came back after
        the next review, the same finding on other lines, and in another pull request."""
        return pr.web, commit, row.fingerprint or row.location()

    async def _edited(
        self, row: FindingRow, kept: tuple[str, str, str], target: str, lines: Sequence[str]
    ) -> str | None:
        """The comment as the person edited it and said yes to, or None where they closed the editor. An edit they
        closed is kept for the next `c`. A draft left as it was, or emptied, is drafted again."""
        drafted = draft(row)
        text = self._drafts.get(kept) or drafted
        while True:
            post, text = await self.push_screen_wait(CommentModal(text, target, lines))
            if not post:
                self._drafts.pop(kept, None)
                if text.strip() and text != drafted:
                    self._drafts[kept] = text
                return None
            if await self.push_screen_wait(ConfirmPostModal(target)):
                return text

    async def _post(self, row: FindingRow, pr: PullRequest, text: str, place: Place, commit: str) -> None:
        """One post, off the screen's own loop, and what it answered: the thread kept and shown, or the refusal as
        its sentence, the person's text kept for the next try."""
        kept = self._draft_key(row, pr, commit)
        self._set_banner(f"Posting the comment to pull request {pr.number}.")
        try:
            posted = await asyncio.to_thread(
                post_comment, self._client(pr), text=text, place=place, commit=commit, fingerprint=row.fingerprint
            )
        except AzureError as refused:
            self._drafts[kept] = text
            # Never "not posted" where the post may have arrived: read by review, the person then pasted it by hand.
            # The client says what happened, and the screen names its own key for what to do.
            unsure = "The comment may be posted. Press c again: one that arrived is found, and no second one is sent."
            self._set_banner(unsure if refused.unsure else NOT_POSTED)
            said = f"{refused} {unsure}" if refused.unsure else str(refused)
            self.notify(said, severity="error", timeout=12, markup=False)
            return
        self._drafts.pop(kept, None)
        said = f"Posted as thread {posted.thread}. Press p to open it."
        if posted.already:
            said = f"This finding was posted before, as thread {posted.thread}. Nothing was posted."
        if row.fingerprint:
            self._kept("thread", lambda: keep_posted(pr, row.fingerprint, posted.thread))
        self._refresh_posted()
        self._render_findings_table(keep=row)
        self._set_banner(said)
        self.notify(said, severity="information", timeout=8, markup=False)

    @on(DataTable.RowHighlighted, "#findings-table")
    @on(DataTable.RowSelected, "#findings-table")
    def _on_finding_row_active(self, event: DataTable.RowHighlighted | DataTable.RowSelected) -> None:
        self._select_finding_at(event.cursor_row)

    @on(Button.Pressed, "#btn-explain-check")
    def action_explain_selected_check(self) -> None:
        """Open the check reference modal for the selected finding in Findings Explorer."""
        if self._selected_finding and self._selected_finding.check:
            self.push_screen(ExplainCheckModal(self._selected_finding.check))

    @on(Button.Pressed, "#btn-dry-run")
    def action_run_dry(self) -> None:
        """Validate review target and model readiness via `--dry-run`: its button as its key, never while one runs or
        the branches load."""
        if not self._run_waits():
            self._execute_review(dry_run=True)

    @on(Button.Pressed, "#btn-run")
    def action_run_review(self) -> None:
        """Execute `adk-review` with the configured options: its button as its key, never while one runs or the
        branches load."""
        if not self._run_waits():
            self._execute_review(dry_run=False)

    def action_quit_when_idle(self) -> None:
        """Quit the screen, where no review runs: one that runs is stopped with Ctrl+X first, or quit with Ctrl+Q."""
        if self._reviewing:
            self.notify("A review is running. Press Ctrl+X to stop it, then q to quit.", timeout=4, markup=False)
            return
        self.exit()

    @on(Button.Pressed, "#btn-stop")
    def action_stop_review(self) -> None:
        """Cancel the active review worker and terminate its subprocess."""
        self.workers.cancel_group(self, "review_exec")
        self._log_progress("Review stopped by user.")

    def action_refresh_repo(self) -> None:
        """Fetch latest remote refs and reload branches for the active repository: the person's own `git fetch`."""
        if self.selected_project is not None:
            self._load_repo_branches(self.selected_project, fetch=True, prune=True)

    def action_toggle_step(self, step_num: int) -> None:
        """Toggle the visible section numbered `step_num` when focus is outside a text `Input`."""
        if isinstance(self.focused, Input):
            return
        sections = [c for c in self.query(Collapsible) if not c.has_class("hidden")]
        if 1 <= step_num <= len(sections):
            section = sections[step_num - 1]
            section.collapsed = not section.collapsed  # its message lays the dock out

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
                section.collapsed = collapsed  # each panel's message lays the dock out

    def action_copy_active_tab(self) -> None:
        """Copy the open tab's own text to the clipboard: the report, the chosen finding, the comment, the hand-off or
        the progress lines. Read by an audit, and confirmed: it started from the report's text, so the progress tab and
        the findings with none chosen copied the report and named themselves; a tab with nothing to copy says so."""
        active = self.query_one("#output-tabs", TabbedContent).active
        chosen = format_finding_detail(self._selected_finding) if self._selected_finding is not None else ""
        text = {
            "tab-report": self.last_report_md,
            "tab-findings": chosen,
            "tab-comment": self.last_comment_md,
            "tab-handoff": self.last_handoff_md,
            "tab-log": "\n".join(self._progress),
        }.get(active, "")
        if not text:
            self.notify(
                NOTHING_TO_COPY.get(active, "Nothing to copy yet. Run a review first."), timeout=4, markup=False
            )
            return
        name = active.removeprefix("tab-")
        if copy_text(self, text):
            self._set_banner(f"Copied {name} content to the clipboard ({len(text):,} chars).")
            self.notify("Copied active tab to the clipboard.", severity="information", timeout=3)
        else:  # through the terminal alone, which no program can check
            self._set_banner(f"Sent {name} content to the clipboard through the terminal ({len(text):,} chars).")
            self.notify(
                "Sent to the clipboard through the terminal. If nothing pastes, use Windows Terminal, iTerm2 or kitty.",
                severity="information",
                timeout=6,
            )

    @staticmethod
    def _preset(sel: ReviewSelection) -> RepoPreset:
        """The choices a review saves for its repository."""
        return RepoPreset(
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
        self._set_reviewing(True)
        try:
            await self._run_review(dry_run=dry_run, label=label)
        except (OSError, ValueError) as exc:
            # Read by review: an error here, a config the screen could not write, ended the whole screen with a
            # traceback, Textual's own way with a worker's error.
            self._set_banner(f"The {label} could not run: {exc}")
            self.notify(f"The {label} could not run: {exc}", severity="error", timeout=8, markup=False)
        finally:
            self._set_reviewing(False)

    def _set_reviewing(self, on: bool) -> None:
        """Whether a review runs, and the line under the screen redrawn for it: it shows the keys that act now, Stop
        while one runs and the two runs while none does. Nothing is redrawn once the screen has closed: measured,
        Textual's redraw raises `ScreenStackError` there, where a closing screen cut a review short."""
        self._reviewing = on
        if self.is_running:
            self.refresh_bindings()

    async def _run_review(self, *, dry_run: bool, label: str) -> None:
        project, target = self.selected_project, self._target()
        if project is None:
            return
        sel = self.current_selection(dry_run=dry_run)
        if not dry_run:
            # Under the repository the review started for, named here; the thread writes the file alone. Read by an
            # audit: the thread read the repository picked when it ran, so a pick made meanwhile took these choices.
            self.tui_config = await asyncio.to_thread(
                save_repo_preset, self.tui_config, project.name, self._preset(sel)
            )
            await asyncio.to_thread(make_output_dir, sel, self.tui_config.config_path)
        argv, outputs = build_review_argv(sel)
        report_log = self.query_one("#report-log", RichLog)
        report_log.clear()
        self.last_report_md = ""  # read by an audit: a stopped run left the last report for Ctrl+Y as its own
        self._set_banner(f"Running {label} on {project.name}...")
        self._log_progress(f"$ {format_cli_command(argv)}")
        self._set_running_ui(True)
        self._said.clear()  # this run's own: read by review, a run whose stream gave nothing would show the last one's
        try:
            code, stdout_text = await self._stream_subprocess(review_command(argv))
        except asyncio.CancelledError:
            self._set_banner(f"{label.capitalize()} stopped by user.")
            if self._has_dom("#report-log"):  # not where the closing screen stopped it
                again = "Ctrl+D" if dry_run else "Ctrl+R"
                self.last_report_md = f"The {label} was stopped, so it wrote no report. Press {again} to run it again."
                report_log.write(self.last_report_md)
            raise
        finally:
            self._set_running_ui(False)
        if not dry_run:
            await asyncio.to_thread(promote_baseline, outputs, code)
        decided = await self._load_generated_files(outputs, target)
        if self._target() != target:
            # Read by review: a review that ended after the person chose another target filled that target's tabs. Read
            # by an audit, and reproduced: so did one whose report was read while the person chose.
            where = f" Its files are in {self.tui_config.output_dir}." if outputs else ""
            self._set_banner(
                f"Finished the {label} of {target[2] or target[1]} (exit code {code}). It is not shown: you chose "
                f"another target since.{where}"
            )
            return
        self.last_report_md = stdout_text
        if stdout_text:
            report_log.write(stdout_text.rstrip("\n"))
        self.query_one("#output-tabs", TabbedContent).active = "tab-report"
        # Read by an audit: at 80 by 24 the results got one row while the panels stayed open. Ctrl+O brings them back.
        # Read by a code review: a review refused, or a dry run, minimized the step to mend: only a report read does.
        if decided is not None:
            self._fold_all(collapsed=True)
        if decided is None and not stdout_text.strip():
            # Read by an audit, and reproduced: a review refused before it ran, as a first one with no login is, left
            # the Report tab empty and the banner at "exit code 2", its reason on the progress tab alone. Known by the
            # report it did not write, never by its code: read by review, a broken install exits 1 with a traceback.
            said = list(self._said) or [f"It gave no reason (exit code {code})."]
            self.last_report_md = "\n".join([f"The {label} ended with no report. It said:", "", *said])
            report_log.write(self.last_report_md)
            why = f": {self._said[-1]}" if self._said else "."
            self._set_banner(f"The {label} on {project.name} ended with no report (exit code {code}){why}")
            told = f"The {label} ended with no report (exit code {code}). The Report tab says why."
        else:
            reached = f": {decided}" if decided else ""
            self._set_banner(f"Finished {label} on {project.name}{reached} (exit code {code}).")
            told = f"Finished {label}{reached} (exit code {code})."
        self.notify(told, severity="information" if code == 0 else "warning", timeout=4, markup=False)

    async def _stream_subprocess(self, cmd: list[str]) -> tuple[int, str]:  # its error lines kept in `_said`
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
            # Read by an audit: another program's line, a warning or a traceback's, kept a terminal's escapes and any
            # credential it held, for the Report tab and the clipboard. Made safe as the report makes a model's line.
            if line := unhidden(raw.decode("utf-8", errors="replace")).rstrip():
                self._said.append(line)
                # Read by a code review: made safe three times, 250 ms a 650 KB line; once here.
                self._log_progress(line, safe=True)
                self._set_banner(line, safe=True)

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

    async def _load_generated_files(
        self, outputs: dict[str, Path], target: tuple[str, str, str, str] | None = None
    ) -> str | None:
        """What a decided review wrote, on its three tabs: its findings, and its comment and hand-off or that it wrote
        none, and the verdict its report names. A run that decided nothing wrote no report, a dry run or a review the
        reviewer refused, and leaves the tabs as the last review left them, and it returns None. Read by an audit, and
        confirmed: a review with the comment switched off set the tab's name back and left the earlier review's comment
        under it, for Ctrl+Y to copy as its own. Where `target` is given, all is read first and shown only while it is
        still the target chosen: read by an audit, a branch picked while the report was read was shown it."""
        json_path = outputs.get("json")
        if json_path is None or not await asyncio.to_thread(json_path.is_file):
            return None
        rows, reviewed, verdict = await asyncio.to_thread(load_report, json_path)
        comment, handoff = await self._written(outputs.get("comment")), await self._written(outputs.get("handoff"))
        if target is not None and self._target() != target:
            return None
        self._hold(rows, reviewed, verdict, target)
        self.last_comment_md, self.last_handoff_md = self._shown(comment, "comment"), self._shown(handoff, "handoff")
        return verdict or ""  # read by a code review: a verdict this screen does not know is still a report read

    def _clear_written(self) -> None:
        """The comment and hand-off tabs as before any review: nothing to copy, each saying the review wrote none."""
        if not self._has_dom("#output-tabs"):
            return
        self.last_comment_md, self.last_handoff_md = self._shown(None, "comment"), self._shown(None, "handoff")

    @staticmethod
    async def _written(path: Path | None) -> str | None:
        """The text a review wrote at `path`, or None where it wrote none."""
        if path is None or not await asyncio.to_thread(path.is_file):
            return None
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    def _shown(self, text: str | None, flag: str) -> str:
        """`text` drawn on the tab `flag` names, or the tab saying the review wrote none; the text, or "". Never
        awaited: read by a code review, a target picked while one drew was given it; a later draw wins over one still
        running, measured on Textual 8.2.8, so nothing is drawn once the target is checked."""
        tab = self.query_one("#output-tabs", TabbedContent).get_tab(f"tab-{flag}")
        state = f"--{flag}" if text is None else "ready"
        tab.label = f"{WRITTEN[flag]} ({state})"
        self.query_one(f"#{flag}-md", Markdown).update(text or _NONE_WRITTEN.format(flag=flag))
        return text or ""

    def _render_findings_table(self, *, keep: FindingRow | None = None) -> None:
        if not self._has_dom("#finding-sev-filter", "#finding-tier-filter", "#finding-search", "#findings-table"):
            return
        sev = str(self.query_one("#finding-sev-filter", Select).value)
        tier = str(self.query_one("#finding-tier-filter", Select).value)
        query = self.query_one("#finding-search", Input).value
        filtered = filter_findings(self.findings_rows, severity=sev, tier=tier, query=query)
        self._filtered_findings = filtered
        table = self.query_one("#findings-table", DataTable)
        table.clear()
        posted = self._posted_threads
        for row in filtered:
            thread = posted.get(row.fingerprint)
            cells = (
                row.severity,
                row.tier,
                row.check,
                row.location(),
                row.title,
                "" if thread is None else str(thread),
            )
            table.add_row(*(Text(cell) for cell in cells))  # a cell given as a string is read as markup
        if filtered:
            chosen = filtered.index(keep) if keep in filtered else 0  # the finding just posted stays the one chosen
            table.move_cursor(row=chosen)
            self._select_finding_at(chosen)
        else:
            self._selected_finding = None
            self.query_one("#btn-explain-check", Button).disabled = True
            msg = "No finding matches the filters." if self.findings_rows else self._none_found()
            self.query_one("#finding-detail", Static).update(msg)

    def _none_found(self) -> str:
        """What the Findings tab says of a review that kept no finding: that it found none only where it approved. Read
        by an audit, and reproduced, a review that could not check everything said "This review has no findings.", the
        words of one that looked and found none; read by review, so did a target no review had read yet, and a report
        the screen could not read."""
        if self._verdict == "APPROVED":
            return "This review has no findings."
        if self._verdict == "DEGRADED":
            return (
                "This review kept no finding, but it could not check everything. "
                "Its report names what it did not check."
            )
        return RUN_FIRST

    def _select_finding_at(self, row_index: int) -> None:
        if 0 <= row_index < len(self._filtered_findings):
            selected = self._filtered_findings[row_index]
            self._selected_finding = selected
            self.query_one("#finding-detail", Static).update(format_finding_detail(selected))
            self.query_one("#btn-explain-check", Button).disabled = not bool(selected.check)

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = False,  # noqa: ARG002 -- Textual's signature: no notice reads markup
    ) -> None:
        """Every notice made safe as the banner is, and drawn with no markup. Read by a code review: a fetch's message,
        Azure's and an error's reached it as written, a token or a terminal's escape among them."""
        super().notify(one_line(message), title=title, severity=severity, timeout=timeout, markup=False)

    def _set_banner(self, message: str, *, safe: bool = False) -> None:
        """`message` on the banner as one line, cut to its room, made safe unless `safe` says it is already. Read by
        the simplify pass: git's own messages, a fetch's among them, which can name a remote with its token, reached
        it as written."""
        if self._has_dom("#status-banner"):
            shown = squash(message) if safe else one_line(message)
            self.query_one("#status-banner", Static).update(shown[:BANNER_CHARS])

    def _log_progress(self, line: str, *, safe: bool = False) -> None:
        """`line` on the progress tab and in what Ctrl+Y copies from it, made safe unless `safe` says it is already,
        its spaces kept: read by a code review, a command copied from it named `My  Docs` as `My Docs`."""
        if self._has_dom("#progress-log"):
            line = line if safe else unhidden(line)
            self._progress.append(line)
            self.query_one("#progress-log", RichLog).write(line)
