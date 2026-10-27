"""The change as the evidence holds it, decided from the diffs alone: what the guidelines are shown of each changed
file and the block they read, what no one could read, each file's blast radius, and why there is nothing to review.
No git runs here: `git.py` reads, this module arranges, so each piece is tried on strings.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

from ..core.blast import Blast, blast_radius
from ..core.evidence import Entry, Failure, entry
from ..core.paths import matches
from ..core.rules import Tree, UnreadError, is_python_text
from ..core.text import lines_of, squash
from .diff import BINARY, LINK_MODE, SUBMODULE, added_lines, capped, framed, headers, hunk_lines, quotable

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


#: A file Git LFS stores is committed as the pointer to its content: a version line, the object's id and its size,
#: in that order. The version lines are the spec's and the two Git LFS took before it. Measured by review, a branch
#: tracked `*.py` with LFS, committed a pointer, and the review approved what it never read.
LFS_VERSIONS = frozenset(
    f"version {url}"
    for url in ("https://git-lfs.github.com/spec/v1", "https://hawser.github.com/spec/v1", "http://git-media.io/v/2")
)
#: How much of a file Git LFS reads to know a pointer: past it, nothing.
POINTER_BYTES = 1_024
#: Why no one read a pointer: what the report says of one, under not read.
POINTER_UNREAD = "a Git LFS pointer: the file's content is stored elsewhere, and none of it was read"
#: Why a binary file, or a link to one, is not read: the same sentence for both, as both are the same hole.
BINARY_UNREAD = "binary: no line of it can be read or quoted"
_POINTER_KEYS = ("version", "oid", "size")
_POINTER_EXTENSION = re.compile(r"ext-[0-9]-\w", re.ASCII)
_POINTER_OID = re.compile(r"sha256:[0-9a-f]{64}")
_POINTER_SIZE = re.compile(r"[+-]?[0-9]+")
#: What Go reads as space and trims from around a pointer, `unicode.IsSpace`: Python's `str.strip` trims more, the
#: separators `\x1c` to `\x1f` among them, around which Git LFS reads no pointer.
_GO_SPACE = "\t\n\v\f\r \x85\xa0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000"


def pointer_text(text: str) -> bool:
    """Whether a file opening with `text` is a Git LFS pointer, as git-lfs 3.8.0's `pointer --check` reads one: its
    first `POINTER_BYTES` bytes, the space Go reads around them trimmed, each line's carriage return dropped and blank
    lines passed over, then `version`, `oid` and `size` in that order, nothing after, and before any of them extension
    lines, the last of each name kept, each naming an object and a priority no other does. A page quoting the format
    holds other lines first. Read by review: asked only for the spec's version line first, a pointer with an extension
    line before it was linted as Python and approved, and one with a shell line after its size, which Git LFS checks out
    as written, was named stored elsewhere; and read over 1,024 characters, a pointer padded with three-byte spaces to
    Git LFS's 1,024 bytes was read on into the code after them, and approved."""
    # Cut at Git LFS's bytes, a character the cut splits kept as one no space trims, as Go's trim keeps its bytes:
    # measured, a pointer whose 1,024th byte fell inside a three-byte space was none to Git LFS.
    window = text[:POINTER_BYTES].encode("utf-8", "surrogatepass")[:POINTER_BYTES].decode("utf-8", "replace")
    said: dict[str, str] = {}
    extensions: dict[str, str] = {}
    for line in window.strip(_GO_SPACE).split("\n"):
        if not (line := line.removesuffix("\r")):
            continue
        key, space, value = line.partition(" ")
        if not space or len(said) == len(_POINTER_KEYS):
            return False
        if key == _POINTER_KEYS[len(said)]:
            said[key] = value
        elif _POINTER_EXTENSION.match(key):
            extensions[key] = value
        else:
            return False
    size, priorities = said.get("size", ""), [key.split("-", 2)[1] for key in extensions]
    return (
        f"version {said.get('version')}" in LFS_VERSIONS
        and all(_POINTER_OID.fullmatch(oid) for oid in (said.get("oid", ""), *extensions.values()))
        and _POINTER_SIZE.fullmatch(size) is not None
        and 0 <= int(size) < 2**63
        and len(set(priorities)) == len(priorities)
    )


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
        return BINARY_UNREAD
    if lfs_pointer(diff):
        return POINTER_UNREAD
    if not fits:
        return f"past the {BLOCK_CAP_CHARS:,} characters the guidelines read in all. --path narrows a review"
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
    shown_any: list[tuple[Entry, str, bool, int]] = []  # a file a guideline may read: its diff, cut, block size
    for listed, diff in zip(entries, diffs, strict=True):
        # A symbolic link is known by the change's own mode, with no tree listed.
        link = listed.get("mode") == LINK_MODE
        file = entry(listed["path"], listed["status"], diff, added_lines(diff), old=listed.get("old"), link=link)
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
        shown_any.append((file, diff, cut, len(framed(file["path"], view)[0])))
    # The files read whole take the room first, then the cut ones, each in path order: measured by review, a long file
    # cut at the cap took the room of small files after it, read whole before the cap rose, and left them out whole.
    fitted: set[int] = set()
    for at in sorted(range(len(shown_any)), key=lambda at: shown_any[at][2]):  # stable: path order within each
        if (size := shown_any[at][3]) <= room:
            fitted.add(at)
            room -= size + 2  # and the blank line joining it to the next
    for at, (file, diff, cut, _size) in enumerate(shown_any):
        # The guidelines' limits alone: the rules read every added line.
        file.update(unread=unread(file, diff, cut, at in fitted, diff_cap), cut=cut or at not in fitted)
        if at in fitted:  # a file no guideline read, none may quote; its view made again, none kept for the rest
            view, _cut = capped(diff, diff_cap)
            block, framing = framed(file["path"], view)
            file.update(quotable=quotable(view), framing=framing)
            blocks.append(block)
        else:
            left_out += 1
    if left_out:  # named to the guidelines too: a test left out is no test missing
        blocks.append(f"[… {left_out} more file(s) not shown: past {BLOCK_CAP_CHARS:,} characters in all]")
    if excluded:
        blocks.append(f"[… {len(excluded)} more file(s) not shown: the operator's skip list leaves them out]")
    return files, "\n\n".join(blocks), excluded, left_out


#: At most how many calls into one changed function the guidelines are shown, a call from each file before a second.
CALLS_SHOWN = 3
#: How many lines around a call the guidelines are shown, each way.
CALL_CONTEXT = 3


def callers_of(tree: Tree | None, files: list[Entry]) -> str:
    """Each call at the head into a module-level function whose lines the change added or removed, from a line the
    change did not add, with the lines around it: what a guideline weighs a change of behaviour against, where no diff
    shows it, in a file the change left alone or elsewhere in its own. Asked by the owner, and measured live on both
    arms over five branches, each a behaviour change an unchanged caller relied on: the correctness guideline raised
    each with or without the calls, without them as a guess that callers may break, with them naming the caller and
    what it relied on. A call is followed through an import as `Tree.imports` binds it; one through a method or an
    object is not."""
    if tree is None or not (touched := _touched(tree, files)):
        return ""
    sites = _sites(tree, touched, {item["path"]: item["added_at"] for item in files})
    return "\n".join(_calls_shown(tree, file, function, sites[file, function]) for file, function in sorted(sites))


def _touched(tree: Tree, files: list[Entry]) -> set[tuple[str, str]]:
    """Each module-level function, by its file and name, whose lines the change added, or between whose lines it
    removed one: a file the change adds has no caller the change left alone."""
    touched = set()
    for item in files:
        if item["status"] == "A" or not item["path"].endswith(".py"):
            continue
        try:
            module = tree.module(item["path"])
        except UnreadError:
            continue
        if isinstance(module, str):
            continue
        lines = item["added_at"] | {line for line, raw in hunk_lines(item["diff"] or "") if raw[:1] == "-"}
        touched |= {
            (item["path"], node.name)
            for node in module.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and lines & _span(node)
        }
    return touched


def _sites(
    tree: Tree, touched: set[tuple[str, str]], added: Mapping[str, frozenset[int]]
) -> defaultdict[tuple[str, str], list[tuple[str, int]]]:
    """Each call into a touched function, by the function, from a line the change did not add: through an import, or
    by its own name in its own module. Measured over ADK: a private helper's every call was in its own module, outside
    the lines the diff shows."""
    sites: defaultdict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for path, module in tree.modules():
        spelled = {function: (owner, function) for owner, function in touched if owner == path}
        for name, (file, attribute) in tree.imports(path).items():
            if attribute is None:
                spelled |= {f"{name}.{function}": (owner, function) for owner, function in touched if owner == file}
            elif (file, attribute) in touched:
                spelled[name] = (file, attribute)
        new = added.get(path, frozenset())
        for node in ast.walk(module) if spelled else ():
            if isinstance(node, ast.Call) and node.lineno not in new and (target := spelled.get(_dotted(node.func))):
                sites[target].append((path, node.lineno))
    return sites


def _calls_shown(tree: Tree, file: str, function: str, sites: list[tuple[str, int]]) -> str:
    """The calls into `function` of `file` the guidelines are shown, a call from each file before a second, each with
    the lines around it, and how many more there are."""
    by_file: defaultdict[str, list[int]] = defaultdict(list)
    for path, line in sites:
        by_file[path].append(line)
    ranked = sorted((rank, path, line) for path, lines in by_file.items() for rank, line in enumerate(sorted(lines)))
    shown = []
    for _rank, path, line in sorted(ranked[:CALLS_SHOWN], key=lambda site: site[1:]):
        lines = lines_of(tree.sources[path])
        around = range(max(1, line - CALL_CONTEXT), min(len(lines), line + CALL_CONTEXT) + 1)
        numbered = "\n".join(f"{number:>4} | {lines[number - 1]}".rstrip() for number in around)
        shown.append(f"- `{squash(path)}` calls `{function}()` from `{squash(file)}`:\n{numbered}")
    if more := len(sites) - len(shown):
        calls = "call" if more == 1 else "calls"
        shown.append(f"- {more:,} more {calls} into `{function}()` from `{squash(file)}`, not shown")
    return "\n".join(shown)


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Every line a function is written on, its decorators' among them."""
    first = min([decorator.lineno for decorator in node.decorator_list] + [node.lineno])
    return set(range(first, (node.end_lineno or node.lineno) + 1))


def _dotted(node: ast.expr) -> str:
    """A called name as written, dotted, `find_user` or `users.find_user`; empty for anything else. Read without
    recursion, so no chain of attributes the parser accepts can stop it."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) else ""


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
    Only a head that adds nothing is marked `rescope`: most often it is the base itself, a branch checked out on
    `main`."""
    if scope:
        what = f"no file at {head!r}" if base is None else f"nothing {head!r} changes"
        return {"status": "error", "error_message": f"{what} lies under {', '.join(scope)}: no change to review"}
    if base is None:
        return {"status": "error", "error_message": f"{head!r} holds no file: nothing to review"}
    why = f"{head!r} adds nothing to {base!r}: no change to review"
    return {"status": "error", "error_message": why, "rescope": True}
