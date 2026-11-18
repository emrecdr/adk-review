"""Modal dialogs for branch synchronization and `--explain` check lookup in `adk-review-tui`."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, Static

from .findings import explain_check_markdown
from .git_sync import SyncStatus


class SyncBranchModal(ModalScreen[bool]):
    """Modal asking whether to fast-forward a local branch that is behind its remote tracking branch."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "decline", "Close")]

    def __init__(self, role: str, status: SyncStatus) -> None:
        super().__init__()
        self.role = role
        self.status = status

    def compose(self) -> ComposeResult:
        s = self.status
        msg = (
            f"Local {self.role} branch '{s.local_branch}' is {s.behind} commit(s) behind "
            f"'{s.upstream_branch}'.\n\n"
            "Do you want to fast-forward your local branch from remote before reviewing?\n"
            "(Updates refs without git checkout so uncommitted files stay untouched.)"
        )
        with Vertical(id="modal-dialog"):
            yield Label("Branch Behind Remote", classes="chain-title")
            yield Static(msg)
            with Horizontal(id="modal-buttons"):
                yield Button("Update Local Branch", id="btn-sync-yes", variant="primary")
                yield Button("Keep Local As-Is", id="btn-sync-no", variant="default")

    @on(Button.Pressed, "#btn-sync-yes")
    def _accept(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-sync-no")
    def action_decline(self) -> None:
        """Close the modal without updating the local branch."""
        self.dismiss(False)


class DivergedBranchModal(ModalScreen[str]):
    """Modal asking whether to review the local branch or the remote branch when they have diverged."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "pick_local", "Use Local")]

    def __init__(self, role: str, status: SyncStatus) -> None:
        super().__init__()
        self.role = role
        self.status = status

    def compose(self) -> ComposeResult:
        s = self.status
        msg = (
            f"Local {self.role} branch '{s.local_branch}' has diverged from '{s.upstream_branch}' "
            f"(+{s.ahead} ahead locally, -{s.behind} behind remote).\n\n"
            "Which revision should adk-review use?"
        )
        with Vertical(id="modal-dialog"):
            yield Label("Diverged Branch Detected", classes="chain-title")
            yield Static(msg)
            with Horizontal(id="modal-buttons"):
                yield Button(f"Local ({s.local_branch})", id="btn-div-local", variant="primary")
                yield Button(f"Remote ({s.upstream_branch})", id="btn-div-remote", variant="warning")

    @on(Button.Pressed, "#btn-div-local")
    def action_pick_local(self) -> None:
        """Choose the local diverged branch."""
        self.dismiss(self.status.local_branch)

    @on(Button.Pressed, "#btn-div-remote")
    def _pick_remote(self) -> None:
        self.dismiss(self.status.upstream_branch)


class ExplainCheckModal(ModalScreen[None]):
    """Modal displaying `--explain <ID>` documentation for the currently selected check."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close_modal", "Close")]

    def __init__(self, check_id: str) -> None:
        super().__init__()
        self.check_id = check_id

    def compose(self) -> ComposeResult:
        with Vertical(id="explain-dialog"):
            yield Label(f"Check Reference: {self.check_id}", classes="chain-title")
            with VerticalScroll(id="explain-scroll"):
                yield Markdown(explain_check_markdown(self.check_id), id="explain-md")
            with Horizontal(id="modal-buttons"):
                yield Button("Close (Esc)", id="btn-explain-close", variant="primary")

    @on(Button.Pressed, "#btn-explain-close")
    def action_close_modal(self) -> None:
        """Close the check reference dialog."""
        self.dismiss(None)
