"""Modal dialogs for branch synchronization, `--explain` check lookup and a pull request's comment in
`adk-review-tui`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, Static, TextArea

from .clipboard import copy_text
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
            "(Runs `git branch -f`: no checkout, no file touched. A checked-out branch stays as it is,\n"
            "and the remote's branch is reviewed.)"
        )
        with Vertical(id="modal-dialog"):
            yield Label("Branch Behind Remote", classes="chain-title")
            yield Static(msg, markup=False)
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
            yield Static(msg, markup=False)
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
            yield Label(f"Check Reference: {self.check_id}", classes="chain-title", markup=False)
            with VerticalScroll(id="explain-scroll"):
                yield Markdown(explain_check_markdown(self.check_id), id="explain-md")
            with Horizontal(id="modal-buttons"):
                yield Button("Close (Esc)", id="btn-explain-close", variant="primary")

    @on(Button.Pressed, "#btn-explain-close")
    def action_close_modal(self) -> None:
        """Close the check reference dialog."""
        self.dismiss(None)


def _seen(line: str) -> str:
    """A file's line as the screen may draw it: a tab as the spaces it stands for, and every character a reader
    cannot see as a space. Reproduced by review: a branch's own line that held a terminal's commands, the screen
    cleared or the clipboard written, reached the terminal as written."""
    return "".join(letter if letter.isprintable() else " " for letter in line.expandtabs(4))


class CommentModal(ModalScreen[tuple[bool, str]]):
    """A finding's comment, for the person to edit before it is posted: whether to post it, and the text as it
    stands, which is kept where they cancel, so no words are lost. Nothing leaves the machine from here. A model
    drafted the words, and the person is their author: every string a model, a repository or a person wrote is
    drawn as text, never as markup."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    #: The most lines shown of those a suggestion replaces: the rest are counted.
    REPLACED_SHOWN = 12

    def __init__(self, text: str, target: str, lines: Sequence[str]) -> None:
        super().__init__()
        self.text = text
        self.target = target
        self.lines = tuple(lines)  # what the comment stands on, as the commit reviewed holds them

    def compose(self) -> ComposeResult:
        with Vertical(id="comment-dialog"):
            yield Label("Comment on the Pull Request", classes="chain-title")
            yield Static(self.target, id="comment-target", markup=False)
            # Always in view, whatever the text holds now: read by review, shown only where the draft opened with a
            # suggestion, one typed in the editor was posted with nothing to check it against.
            more = len(self.lines) - self.REPLACED_SHOWN
            lines = [_seen(line) for line in self.lines[: self.REPLACED_SHOWN]]
            shown = [*lines, *([f"({more} more lines)"] if more > 0 else [])]
            said = "Your comment stands on these lines. A suggestion block replaces them when its author applies it."
            yield Static(said, id="comment-replaces", markup=False)
            yield Static("\n".join(shown), id="comment-replaced", markup=False)
            yield TextArea(self.text, id="comment-text")
            with Horizontal(id="modal-buttons"):
                yield Button("Post", id="btn-comment-post", variant="primary")
                yield Button("Copy", id="btn-comment-copy", variant="default")
                yield Button("Cancel", id="btn-comment-cancel", variant="default")

    def on_mount(self) -> None:
        self.query_one("#comment-text", TextArea).focus()

    def _edited(self) -> str:
        return self.query_one("#comment-text", TextArea).text

    @on(Button.Pressed, "#btn-comment-post")
    def _post(self) -> None:
        if self._edited().strip():
            self.dismiss((True, self._edited()))
        else:
            self.app.notify("The comment is empty. Write it, or cancel.", severity="warning", timeout=4, markup=False)

    @on(Button.Pressed, "#btn-comment-copy")
    def _copy(self) -> None:
        """The text as it stands, for a comment the person pastes by hand: where a post is refused, say."""
        known = copy_text(self.app, self._edited())
        said = "Copied the comment." if known else "Sent the comment to the clipboard through the terminal."
        self.app.notify(said, severity="information", timeout=4, markup=False)

    @on(Button.Pressed, "#btn-comment-cancel")
    def action_cancel(self) -> None:
        """Close the editor and post nothing: the text goes back as it stands."""
        self.dismiss((False, self._edited()))


class ConfirmPostModal(ModalScreen[bool]):
    """The last question before a comment leaves the machine: it is posted as the person, where `target` says."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "go_back", "Go Back")]

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("Post the Comment?", classes="chain-title")
            yield Static(f"Post this comment as you, on {self.target}?", id="confirm-post", markup=False)
            with Horizontal(id="modal-buttons"):
                yield Button("Go Back", id="btn-post-no", variant="primary")
                yield Button("Post Comment", id="btn-post-yes", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#btn-post-no", Button).focus()  # Enter by habit posts nothing

    @on(Button.Pressed, "#btn-post-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-post-no")
    def action_go_back(self) -> None:
        """Return to the editor, the text as it was."""
        self.dismiss(False)
