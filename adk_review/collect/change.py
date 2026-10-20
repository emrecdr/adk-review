"""The change as the evidence holds it, decided from the diffs alone: what the guidelines are shown of each changed
file and the block they read, what no one could read, each file's blast radius, and why there is nothing to review.
No git runs here: `git.py` reads, this module arranges, so each piece is tried on strings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..core.blast import Blast, blast_radius
from ..core.evidence import Entry, Failure, entry
from ..core.paths import matches
from ..core.rules import Tree, is_python_text
from .diff import BINARY, SUBMODULE, added_lines, capped, framed, headers, hunk_lines, quotable

#: And the block of diffs the guidelines read, in all: a file past it is left out whole, and named. Measured with none,
#: `--all` over 4,826 files gave each guideline and each verifier 14.9 MB; this repository's whole project, 275,688
#: characters, was 80,000 tokens a guideline, answered on Copilot; the planted branch is 2,205.
BLOCK_CAP_CHARS = 250_000


def radius_of(tree: Tree | None, files: list[Entry]) -> Blast:
    """What at the head depends on each changed file, from the tree `tree_at_head` gave, so the head is parsed once
    for the radius and the tree rules alike; None, a tree not listed, is a floor."""
    if tree is None:
        return blast_radius({}, files, listed=False)
    return blast_radius(tree.parsed, files, len(tree.unread))


#: A file Git LFS stores is committed as the pointer to its content, opening with a version line and then the
#: object's id: the spec's version, and the two Git LFS took before it. Measured by review, a branch tracked `*.py` with
#: LFS, committed a pointer, and the review approved what it never read; and measured with git-lfs 3.8.0, each version
#: line is one it smudges, with CRLF line ends, blank lines or spaces before it, or extension lines after it, where the
#: spec's alone was known here and a pointer spelled another way was linted as Python.
LFS_VERSIONS = frozenset(
    f"version {url}"
    for url in ("https://git-lfs.github.com/spec/v1", "https://hawser.github.com/spec/v1", "http://git-media.io/v/2")
)
#: How much of a file Git LFS reads to know a pointer: past it, nothing.
POINTER_BYTES = 1_024


def pointer_text(text: str) -> bool:
    """Whether a file opening with `text` is a Git LFS pointer, as Git LFS reads one: its first `POINTER_BYTES`, the
    space around them trimmed and blank lines passed over, a version line it reads first and the object's id after.
    A page quoting the format holds them further down."""
    said = [line.strip() for line in text[:POINTER_BYTES].strip().split("\n") if line.strip()]
    return bool(said) and said[0] in LFS_VERSIONS and any(line.startswith("oid sha256:") for line in said[1:])


def lfs_pointer(diff: str) -> bool:
    """Whether the file the diff leaves is a Git LFS pointer: `pointer_text` of its opening lines as the diff shows
    them, whatever lines it removed before them. Read by review: four lines of the hunk, removed ones counted, missed a
    file the change turned into a pointer, its old lines removed first."""
    opening: list[str] = []
    read = 0
    kept = ((number, line[1:]) for number, line in hunk_lines(diff) if line[:1] != "-")
    for at, (number, line) in enumerate(kept, 1):
        if number != at or read > POINTER_BYTES:
            break
        opening.append(line)
        read += len(line) + 1
    return pointer_text("\n".join(opening))


def unread(entry: Entry, diff: str, cut: bool, fits: bool, cap: int) -> str:
    """Why the guidelines could not read all of a changed file, or "" when they could: named, never read as "found
    nothing". A deleted file adds nothing: what it removes was reviewed when it came in. `fits` is False for a
    file past the block's cap."""
    if entry["status"] == "D":
        return ""
    if not diff.strip():
        return "git showed none of it"
    if SUBMODULE.search(top := headers(diff)):  # each header: a type change prints the new file's second
        return "a submodule: its commits are another repository's, not read here"
    if BINARY.search(top):
        return "binary: no line of it can be read or quoted"
    if lfs_pointer(diff):
        return "a Git LFS pointer: the file's content is stored elsewhere, and none of it was read"
    if not fits:
        return f"past the {BLOCK_CAP_CHARS:,} characters the guidelines read in all; --path narrows a review"
    if cut:
        return f"cut at {cap:,} characters: the guidelines read only its start"
    return ""


def widens(entry: Mapping[str, str], skip: Sequence[str]) -> bool:
    """Whether a guideline may read `entry` within the function around each hunk: a Python file the change modifies,
    renames or copies, which the skip list leaves to the guidelines. An added or a deleted file's diff holds the whole
    file already: measured by review, widened, each came back byte for byte the same."""
    return (
        entry["status"] not in ("A", "D")
        and is_python_text(entry["path"], None)
        and _skipped(entry["path"], skip) is None
    )


def _skipped(path: str, skip: Sequence[str]) -> str | None:
    """The glob of the operator's skip list that names `path`, or None."""
    return next((glob for glob in skip if matches(path, glob)), None)


def shown_to_guidelines(
    entries: list[dict[str, str]],
    diffs: list[str],
    diff_cap: int,
    skip: Sequence[str],
    *,
    shown: bool = True,
) -> tuple[list[Entry], str, dict[str, str], int]:
    """Each changed file with its whole diff and its added lines, for the rules, and what the guidelines are shown of
    it; the block they read, each file capped at `diff_cap` and the block at `BLOCK_CAP_CHARS`, what it leaves out
    counted at its end; the files the operator's skip list left out, by path with the glob; and how many files the
    block's cap left out, which the collector reads to know whether the function around each hunk cost one its
    reading. Decided from the
    diffs alone: no git runs here. With no guideline to show it to, `shown` False, no file is capped, framed or
    shown: measured, the view cost 2.3 s of a 10.5 s `default-rules` review of 2,683 files, the pre-push hook's
    profile, and nothing read it."""
    files: list[Entry] = []
    blocks: list[str] = []
    room, left_out, excluded = BLOCK_CAP_CHARS, 0, {}
    for listed, diff in zip(entries, diffs, strict=True):
        file = entry(listed["path"], listed["status"], diff, added_lines(diff), old=listed.get("old"))
        files.append(file)
        # A file the operator's skip list names: the rules read it as any other, the guidelines are not shown it,
        # and it is named and counted, never a hole. The operator chose it, in the tool's own config: a branch
        # cannot, and code it hides behind such a name is still every rule's to read. Never a submodule, which the
        # branch names as it likes and no rule reads: measured by review, one named `logo.png` was approved.
        if (glob := _skipped(file["path"], skip)) and not SUBMODULE.search(headers(diff)):
            excluded[file["path"]] = glob
            continue
        if not shown:  # the rules read every added line: what no one can read is all `unread` still names
            file["unread"] = unread(file, diff, cut=False, fits=True, cap=diff_cap)
            continue
        view, cut = capped(diff, diff_cap)
        block, framing = framed(file["path"], view)
        fits = len(block) <= room
        # The guidelines' limits alone: the rules read every added line.
        file.update(unread=unread(file, diff, cut, fits, diff_cap), cut=cut or not fits)
        if fits:  # a file no guideline read, none may quote
            file.update(quotable=quotable(view), framing=framing)
            blocks.append(block)
            room -= len(block) + 2  # and the blank line joining it to the next
        else:
            left_out += 1
    if left_out:  # named to the guidelines too: a test left out is no test missing
        blocks.append(f"[… {left_out} more file(s) not shown: past {BLOCK_CAP_CHARS:,} characters in all]")
    if excluded:
        blocks.append(f"[… {len(excluded)} more file(s) not shown: the operator's skip list leaves them out]")
    return files, "\n\n".join(blocks), excluded, left_out


def radius(tree: Tree | None, files: list[Entry], *, read: bool, asked: bool) -> Blast:
    """What at the head depends on each changed file, where the head was read; else, asked of files it does not
    measure, each one named with no tree to read; else not asked."""
    if read:
        return radius_of(tree, files)
    if asked:
        return blast_radius({}, files)
    return Blast((), (), 0, asked=False)


def no_change(head: str, base: str | None, scope: Sequence[str]) -> Failure:
    """Why there is nothing to review: the head holds no file, adds nothing to the base, or nothing under the scope.
    Only a head that adds nothing is marked: most often it is the base itself, a branch checked out on `main`."""
    if scope:
        what = f"no file at {head!r}" if base is None else f"nothing {head!r} changes"
        return {"status": "error", "error_message": f"{what} lies under {', '.join(scope)}: no change to review"}
    if base is None:
        return {"status": "error", "error_message": f"{head!r} holds no file: nothing to review"}
    why = f"{head!r} adds nothing to {base!r}: no change to review"
    return {"status": "error", "error_message": why, "adds_nothing": True}
