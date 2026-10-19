"""What a review reads, as the collector hands it over: the evidence, and each changed file in it.

One dict, not a class, since ADK's session state and a kept review hold it as one, and the checks node lets go of
what no one reads after the rules by blanking keys, never by changing shape. Spelled here once, so each key is read
as it was written: measured before it was spelled, a key the checks node blanked after the rules was read by the
JSON report, and `added_at` came in to outlive it. `collect/git.py` gathers it, `collect/change.py` arranges it,
`judge/checks.py` runs the rules over it and lets go, `deliver/report.py` reads it last. Where there is nothing to
review, the collector answers a `Failure` in its place; the rules read the `Change` alone, which `--try` hands them
too.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:  # named in quotes below: `rules.py` reads `Entry` back, and a cycle at import would follow
    from .blast import Blast
    from .rules import Diagnostic, Tree


class Entry(TypedDict):
    """One changed file: as git lists it, as the rules read it, as the guidelines are shown it, and as the report
    names it."""

    path: str  # repository-relative, as git spells it
    status: str  # A, M, D, R, C or T, as `diff-tree --name-status` letters it
    old: NotRequired[str]  # a renamed or copied file's former path, under R and C alone
    diff: str | None  # its whole diff as git printed it, for the rules; None once the checks node let it go
    added: list[tuple[int, str]] | None  # each line the change added, numbered in the new file; None once let go
    added_at: frozenset[int]  # the added lines' numbers, kept for the report after `added` is let go
    quotable: list[list[tuple[int, str]]]  # the lines a guideline may quote, hunk by hunk; [] where none read it
    framing: list[str]  # the lines the fence puts around what the guidelines read, which a quote may run into
    unread: str  # why the guidelines could not read it whole, "" where they could
    cut: bool  # whether the guidelines' view of it is cut: at the diff cap, or past the block's
    text: str | None  # its text at the head, for the file rules; None where not read, or once let go
    unread_text: str | None  # why its text was not read whole, a hole for a rule that reads it whole; None otherwise
    shown: NotRequired[
        dict[int, str] | None
    ]  # the lines its diff shows, by number, where its text was not read; None once let go
    diagnostics: NotRequired["Sequence[Diagnostic]"]  # an outside tool's, on the copy a rule naming the tool reads


def entry(
    path: str,
    status: str,
    diff: str | None,
    added: list[tuple[int, str]],
    *,
    old: str | None = None,
    text: str | None = None,
) -> Entry:
    """One changed file as it first stands, before the guidelines' view and the head's text are added: every key
    spelled once, `added_at` derived once. Measured before it was spelled: the collector and `--try` each built the
    dict by hand, and a key added to one went missing from the other."""
    made: Entry = {
        "path": path,
        "status": status,
        "diff": diff,
        "added": added,
        "added_at": frozenset(number for number, _text in added),
        "quotable": [],
        "framing": [],
        "unread": "",
        "cut": False,
        "text": text,
        "unread_text": None,
    }
    if old is not None:
        made["old"] = old
    return made


def unreleased[T](value: T | None) -> T:
    """A file's diff or added lines, read while the evidence still holds them: the checks node lets go of both once
    the rules have read them, and a read after is a mistake named, never an empty answer read as nothing found."""
    if value is None:
        raise LookupError("read after the checks node let it go")
    return value


class Change(TypedDict):
    """What the rules read of the evidence: each changed file and the head's tree, as `--try` hands them its own."""

    files: list[Entry]  # each changed file, in path order
    tree: "Tree | None"  # the head as the tree rules read it; None where it could not be listed, or once let go


class Failure(TypedDict):
    """Why there is nothing to review, in the evidence's place."""

    status: Literal["error"]
    error_message: str  # one sentence, the command's exit 2
    adds_nothing: NotRequired[bool]  # the head adds nothing to its base: the command names the flags that widen it


class Evidence(Change):
    """Everything the rules and the guidelines read, gathered in plain Python before any model runs."""

    status: Literal["success"]
    preflight: (
        dict  # the scope as `preflight` resolved it: the repository, the base and head, their shas, the merge base
    )
    diff: (
        str  # the block the guidelines read: a `### <path>` heading and a fenced diff per file, capped, each cut named
    )
    blast: "Blast"  # what at the head depends on each changed file, read beside the block, never inside it
    whole: bool  # under --all: every file at the head is the change, diffed against the empty tree
    scope: list[str]  # the folders --path named, as git spells them; [] for the whole change
    skipped: dict[str, str]  # the files the operator's skip list left to the rules, by path with the glob
    untracked: list[str] | None  # under --staged, what git neither tracks nor ignores; None otherwise
    outside: int  # how many changed files lay outside the scope, for the report to count
