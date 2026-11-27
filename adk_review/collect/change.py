"""The change as the evidence holds it, decided from the diffs alone: what the guidelines are shown of each changed
file and the block they read, what no one could read, each file's blast radius, and why there is nothing to review.
No git runs here: `git.py` reads, this module arranges, so each piece is tried on strings.
"""

from __future__ import annotations

import ast
import functools
import re
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import PurePosixPath

from ..core.blast import PASSED_ON, Blast, blast_radius, is_test, parse
from ..core.evidence import Entry, Failure, entry
from ..core.paths import in_scope, matches, no_such_path
from ..core.rules import Tree, UnreadError, is_python_text
from ..core.secrets import holds_private_key
from ..core.text import lines_of, squash
from .diff import BINARY, LINK_MODE, SUBMODULE, added_lines, capped, framed, headers, hunk_lines, kept_lines, quotable

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
#: Why a file that holds a private key is shown to no model, and what to do: the rules read it still.
KEY_UNREAD = (
    "holds a private key, so no model is shown a line of it. "
    "Move the key out of the file (recommended), or add the file to --skip"
)
#: Why a file whose change removes a private key is shown to no model, and what to do: the head holds the key no more.
KEY_REMOVED = (
    "removes a private key, so no model is shown a line of its change. "
    "Rotate the key if it was a real one: the git history still holds it. Add the file to --skip to end this note"
)
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
    for at, (number, line) in enumerate(kept_lines(diff), 1):
        if number != at or read > POINTER_BYTES:
            break
        opening.append(line)
        read += len(line) + 1
    return pointer_text("\n".join(opening))


def unread(entry: Entry, diff: str, cut: bool, fits: bool, cap: int, folder: str | None = None) -> str:
    """Why the guidelines could not read all of a changed file, or "" when they could: named, never read as "found
    nothing". A deleted file adds nothing: what it removes was reviewed when it came in. `fits` is False for a
    file past the block's cap, and `folder` the `--path` of the planned run that reads it, where one does."""
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
        then = f"Review it in a smaller run with --path {folder}" if folder else "--path narrows a review"
        return f"past the {BLOCK_CAP_CHARS:,} characters the guidelines read in all. {then}"
    if cut:
        return f"cut at {cap:,} characters: the guidelines read only its start"
    return ""


#: What a printed command holds as it stands, in a POSIX shell, cmd and PowerShell alike: no space, quote or sign one
#: would read. A branch's name is its author's, and `;`, `$` and a backtick are legal in one.
_PASTABLE = re.compile(r"[A-Za-z0-9._/-]+")


def pastable(*words: str) -> bool:
    """Whether a printed command can hold each of `words` as it is: no character a shell reads, and none a flag."""
    return all(_PASTABLE.fullmatch(word) and not word.startswith("-") for word in words)


def _planned(path: str, units: Collection[str]) -> str | None:
    """The `--path` value of the planned run that reads `path`, the file or the nearest folder above it among `units`,
    which fits the block on its own; None where no plan fits, or where a shell would need it quoted. Read by review: a
    folder guessed from the paths alone, `--path src`, was three times the block, where the plan beside it named runs
    that fit; and each file scanned every unit, 1.28 s over 3,000 files, where a walk up its folders takes one look a
    level."""
    unit = path
    while unit and unit not in units:
        unit = unit.rpartition("/")[0]
    return unit if unit and pastable(unit) else None


def in_parts(sizes: Mapping[str, int], scope: Sequence[str], cap: int) -> list[list[str]]:
    """The runs that read a change the guidelines cannot read in one, each the `--path` values one run names, every
    file in one run; [] where the change fits, or where one file alone is past `cap`. Each file costs its size in the
    block and the line joining it to the next, as the block counts it. A folder goes whole where it fits, else its
    folders and files one level down; each, in path order, goes to the first run with room for it."""
    if _cost(sizes, list(sizes)) <= cap:
        return []
    roots: dict[str, list[str]] = defaultdict(list)
    for path in sorted(sizes):
        # The outermost folder given that holds it, as a folder sorts before every one inside it: read by review, the
        # first given put `a/b` beside `a`, and the run `--path a` read `a/b` again.
        roots[next((folder for folder in sorted(scope) if in_scope(path, [folder])), "")].append(path)
    units = [unit for root in sorted(roots) for unit in _units(sizes, root, roots[root], cap)]
    return _packed(units, cap)


def _cost(sizes: Mapping[str, int], paths: Sequence[str]) -> int:
    """What `paths` take of the block: each file's size, and the line joining each to the next."""
    return sum(sizes[path] for path in paths) + 2 * (len(paths) - 1)


def _units(sizes: Mapping[str, int], prefix: str, paths: list[str], cap: int) -> list[tuple[str, int]]:
    """`paths` under `prefix` as `--path` values and what each costs: the folder whole where it fits or holds one
    file, else each folder and file one level down, in path order."""
    if len(paths) == 1 or _cost(sizes, paths) <= cap:
        return [(prefix, _cost(sizes, paths))]
    below: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        part = path.removeprefix(prefix).lstrip("/").partition("/")[0]
        below[f"{prefix}/{part}" if prefix else part].append(path)
    return [unit for key in sorted(below) for unit in _units(sizes, key, below[key], cap)]


def _packed(units: list[tuple[str, int]], cap: int) -> list[list[str]]:
    """`units` in order, each in the first run with room for it, each run's cost at most `cap`; [] where one unit alone
    is past it. Measured over a project's history: each filling only the last run planned 83 runs where 69 held the
    same 1,431 files, and every run more is a review's calls."""
    runs: list[list[str]] = []
    rooms: list[int] = []
    for value, size in units:
        if size > cap:
            return []
        at = next((at for at, room in enumerate(rooms) if size + 2 <= room), None)  # and the line joining it
        if at is None:
            runs.append([value])
            rooms.append(cap - size)
        else:
            runs[at].append(value)
            rooms[at] -= size + 2
    return runs


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


def _key_note(status: str, diff: str, *, held: bool) -> str:
    """Why a file whose text or diff holds a private key is shown to no model: nothing where the change deletes it,
    `KEY_REMOVED` where only the lines the change removed hold the key, else `KEY_UNREAD`. Read by an audit: a change
    that removed a key was told to move it out of a file it is gone from."""
    if status == "D":
        return ""
    if held or holds_private_key("\n".join(line for _at, line in kept_lines(diff))):
        return KEY_UNREAD
    return KEY_REMOVED


def shown_to_guidelines(
    entries: list[dict[str, str]],
    diffs: list[str],
    diff_cap: int,
    skip: Sequence[str],
    *,
    shown: bool = True,
    scope: Sequence[str] = (),
    held: Collection[str] = (),
) -> tuple[list[Entry], str, dict[str, str], int, list[list[str]]]:
    """Each changed file with its whole diff and its added lines, for the rules, and what the guidelines are shown of
    it; the block they read, each file capped at `diff_cap` and the block at `BLOCK_CAP_CHARS`, what it leaves out
    counted at its end; the files the operator's skip list left out, by path with the glob; how many files the block's
    cap left out that the change keeps, each a hole, which the collector reads to know whether the function around each
    hunk cost one its reading; and, where it left any out, the runs that read them all, as `in_parts` plans them.
    Decided from the diffs alone: no git runs here. With no guideline to show it to, `shown` False, no file is capped,
    framed or shown: measured, the view cost 2.3 s of a 10.5 s `default-rules` review of 2,683 files, the pre-push
    hook's profile, and nothing read it. A file `held` names, its text at the head holding a private key, or one whose
    diff shows a key's header or footer, is shown to no model, a hole where the change keeps it."""
    files: list[Entry] = []
    blocks: list[str] = []
    room, excluded, keyed, removed = BLOCK_CAP_CHARS, {}, 0, 0
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
        # Read by a finding from adk-spec, and reproduced: a diff shows three lines around each change, so a change
        # beside a key, or inside one, showed lines of it with neither its header nor its footer, which the redaction
        # reads it between, and every guideline was sent them. The rules read it as any other file.
        if file["path"] in held or holds_private_key(diff):
            note = _key_note(file["status"], diff, held=file["path"] in held)
            file.update(unread=note, cut=True)
            # Read by the third code review: one the change removed a key from, or deleted, was named to the
            # guidelines as a file that holds one, which a security guideline could report as a key committed.
            keyed += note == KEY_UNREAD  # no branch: this function is at the complexity bar
            removed += note != KEY_UNREAD
            continue
        view, cut = capped(diff, diff_cap)
        shown_any.append((file, diff, cut, len(framed(file["path"], view)[0])))
    # The files a run of their own would read: none deleted, and none no run can read, a binary file, a submodule or a
    # Git LFS pointer. Read by review, a run of deleted files alone, or of a binary file, cost a review's calls for no
    # hole it could mend, and alone asked whether to go on.
    readable = {
        at
        for at, (file, diff, _cut, _size) in enumerate(shown_any)
        if file["status"] != "D" and not unread(file, diff, cut=False, fits=True, cap=diff_cap)
    }
    # Those take the room first, the files read whole before the cut ones, each in path order: measured by review, a
    # long file cut at the cap took the room of small files after it, read whole before the cap rose, and left them out
    # whole; and a deleted or binary file first in path order took the room of a file the change added.
    fitted: set[int] = set()
    first = sorted(range(len(shown_any)), key=lambda at: (at not in readable, shown_any[at][2]))
    for at in first:  # stable: path order within each
        if (size := shown_any[at][3]) <= room:
            fitted.add(at)
            room -= size + 2  # and the blank line joining it to the next
    not_shown = len(shown_any) - len(fitted)
    left_out = len(readable - fitted)
    sizes = {shown_any[at][0]["path"]: shown_any[at][3] for at in sorted(readable)}
    parts = in_parts(sizes, scope, BLOCK_CAP_CHARS) if left_out else []
    units = {unit for run in parts for unit in run}
    for at, (file, diff, cut, _size) in enumerate(shown_any):
        # The guidelines' limits alone: the rules read every added line.
        # No run's `--path` for a file its own run would cut still: read by review, the hint promised what it could not.
        folder = None if at in fitted or cut else _planned(file["path"], units)
        file.update(unread=unread(file, diff, cut, at in fitted, diff_cap, folder), cut=cut or at not in fitted)
        if at in fitted:  # a file no guideline read, none may quote; its view made again, none kept for the rest
            view, _cut = capped(diff, diff_cap)
            block, framing = framed(file["path"], view)
            file.update(quotable=quotable(view), framing=framing)
            blocks.append(block)
    # Each note beside a file shown alone: read by review, a change of only files the skip list names made a block of
    # its note, and each guideline ran over it, billed.
    if blocks:
        blocks += _left_out(not_shown, len(excluded), keyed, removed)
    return files, "\n\n".join(blocks), excluded, left_out, parts


def _left_out(past_cap: int, skipped: int, keyed: int, removed: int) -> list[str]:
    """What the block leaves out of the change, named to the guidelines: a test left out is no test missing."""
    whys = (
        (past_cap, f"past {BLOCK_CAP_CHARS:,} characters in all"),
        (skipped, "the operator's skip list leaves them out"),
        (keyed, "each holds a private key"),
        (removed, "the change removes a private key from each"),
    )
    return [f"[… {count} more file(s) not shown: {why}]" for count, why in whys if count]


#: At most how many calls into one changed function the guidelines are shown, a call from each file before a second.
CALLS_SHOWN = 3
#: How many lines around a call the guidelines are shown, each way.
CALL_CONTEXT = 3


def callers_of(tree: Tree | None, files: list[Entry]) -> str:
    """Each call at the head into a module-level function whose lines the change added or removed, on no line the
    change added, with the lines around it: what a guideline weighs a change of behaviour against, where no diff
    shows it, in a file the change left alone or elsewhere in its own. Asked by the owner, and measured live on both
    arms over five branches, each a behaviour change an unchanged caller relied on: the correctness guideline raised
    each with or without the calls, without them as a guess that callers may break, with them naming the caller and
    what it relied on. A call is followed through an import as `Tree.imports` binds it, and on where a file only passes
    the name on, as a package's `__init__.py` does; one through a method or an object is not."""
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
    """Each call into a touched function, by the function, none of whose lines the change added: through an import, or
    by its own name in its own module. Measured over ADK: a private helper's every call was in its own module, outside
    the lines the diff shows. Read by review, and reproduced: read by its first line alone, a call over several lines
    whose argument the change edited was shown as left alone, and took a place among the calls shown from a caller no
    diff shows."""
    sites: defaultdict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    bound = _bound(tree)
    for path, module in tree.modules():
        spelled = {function: (owner, function) for owner, function in touched if owner == path}
        for name, (file, attribute) in bound(path).items():
            if attribute is None:
                spelled |= {f"{name}.{function}": (owner, function) for owner, function in touched if owner == file}
            elif target := _defined((file, attribute), touched, bound):
                spelled[name] = target
        new = added.get(path, frozenset())
        for node in ast.walk(module) if spelled else ():
            if (
                isinstance(node, ast.Call)
                and (target := spelled.get(_dotted(node.func)))
                and new.isdisjoint(range(node.lineno, (node.end_lineno or node.lineno) + 1))
            ):
                sites[target].append((path, node.lineno))
    return sites


def _bound(tree: Tree) -> Callable[[str], Mapping[str, tuple[str, str | None]]]:
    """What each file's imports bind, read once a file; nothing for one the head's read left out, a floor."""

    @functools.cache
    def bound(path: str) -> Mapping[str, tuple[str, str | None]]:
        try:
            return tree.imports(path)
        except UnreadError:
            return {}

    return bound


def _defined(
    target: tuple[str, str],
    touched: set[tuple[str, str]],
    bound: Callable[[str], Mapping[str, tuple[str, str | None]]],
) -> tuple[str, str] | None:
    """The touched function `target` stands for, followed `PASSED_ON` files deep where a file only passes it on, as
    `callers` follows it: read by review, a call through a package's `__init__.py` was not shown."""
    for _ in range(PASSED_ON + 1):
        if target in touched:
            return target
        passed = bound(target[0]).get(target[1])
        if passed is None or passed[1] is None:
            return None
        target = (passed[0], passed[1])
    return None


def _calls_shown(tree: Tree, file: str, function: str, sites: list[tuple[str, int]]) -> str:
    """The calls into `function` of `file` the guidelines are shown, a call from each file before a second, each with
    the lines around it, and how many more there are. A call in a file that holds a private key is counted, never
    shown: reproduced, the lines above a call under a test's key were the key's, from a file the change left alone."""
    by_file: defaultdict[str, list[int]] = defaultdict(list)
    for path, line in sites:
        by_file[path].append(line)
    shows = {path: lines for path, lines in by_file.items() if not holds_private_key(tree.sources[path])}
    ranked = sorted((rank, path, line) for path, lines in shows.items() for rank, line in enumerate(sorted(lines)))
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


#: At most how many definitions the change calls or overrides the guidelines are shown, in the order the change first
#: calls or overrides each, the source's calls before the tests'.
CALLEES_SHOWN = 8
#: At most how many lines of each of those definitions the guidelines are shown, from its first.
CALLEE_LINES = 20

type _Defined = tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]


def callees_of(tree: Tree | None, files: list[Entry]) -> str:
    """Each project function, class or parent method a line the change added calls or overrides, as the head defines
    it outside the lines the diff shows: the contract a guideline weighs a call against, the other way from
    `callers_of`. Read by an outside audit and reproduced: a branch indexed `find_user(uid)["name"]`, where `find_user`
    returns None for an id it does not hold, and no model was shown `find_user`, whose file the change left alone.
    Measured live on Copilot over five planted misuses and a branch using the same helpers correctly, three runs each:
    the correctness guideline named 12 of 15 with them, 3 without, and the correct branch drew no finding with them,
    three false majors without; neither side named a parent's return contract an override broke. On Gemini
    (`gemini-3.5-flash-lite`), one run each, it named 5 of 5 with them, the broken contract among them, 1 without,
    and the correct branch drew no finding either way. A call is
    followed through an import as `Tree.imports` binds it, on where a file only passes the name on, by its own
    name in its own module, and through `self` to the class around it and its parents; one through any other object is
    not, nor a definition a test file holds, nor one in a file holding a private key, named there and never shown."""
    if tree is None:
        return ""
    bound, diffed = _bound(tree), _diffed(files)
    relied: list[tuple[str, int, str, str, _Defined]] = []
    for item in files:
        try:
            module = tree.module(item["path"]) if item["path"].endswith(".py") and item["added_at"] else None
        except UnreadError:
            continue
        if isinstance(module, ast.Module):
            relied += _relied_on(tree, item["path"], module, item["added_at"], bound)
    shown: dict[tuple[str, int], str] = {}
    # The source's calls first, which correctness and security judge, then the tests'.
    for path, _line, verb, label, (file, node) in sorted(relied, key=lambda site: (is_test(site[0]), *site[:2])):
        if (file, node.lineno) in shown or _span(node) & diffed.get(file, set()) or is_test(file):
            continue
        shown[file, node.lineno] = _definition_shown(tree, path, verb, label, file, node)
    listed = list(shown.values())
    if (more := len(listed) - CALLEES_SHOWN) > 0:
        noun = "definition" if more == 1 else "definitions"
        listed = [*listed[:CALLEES_SHOWN], f"- {more:,} more {noun} the change calls or overrides, not shown"]
    return "\n".join(listed)


def _diffed(files: list[Entry]) -> dict[str, set[int]]:
    """Each changed file's lines the diff shows as changed, by the head's numbers: what a guideline reads already."""
    return {
        item["path"]: set(item["added_at"]) | {line for line, raw in hunk_lines(item["diff"] or "") if raw[:1] == "-"}
        for item in files
    }


def _relied_on(
    tree: Tree, path: str, module: ast.Module, added: Collection[int], bound: Callable[[str], Mapping]
) -> list[tuple[str, int, str, str, _Defined]]:
    """What the lines `added` of `path` call or override, each as `(path, line, verb, label, definition)`."""
    parents = {child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and set(range(node.lineno, (node.end_lineno or node.lineno) + 1)) & set(added):
            name = _dotted(node.func)
            if name.startswith("self.") and name.count(".") == 1 and (cls := _class_around(node, parents)):
                if method := _method(tree, [(path, cls)], name[5:], bound):
                    found.append((path, node.lineno, "calls", f"{method[2]}.{method[1].name}", method[:2]))
            elif defined := _resolved(tree, path, name, bound):
                found.append((path, node.lineno, "calls", defined[1].name, defined))
        elif isinstance(node, ast.ClassDef):
            parents_of = [found_ for base in node.bases if (found_ := _resolved(tree, path, _dotted(base), bound))]
            classes = [(file, cls) for file, cls in parents_of if isinstance(cls, ast.ClassDef)]
            methods = [item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)]
            found += [
                (path, item.lineno, "overrides", f"{method[2]}.{item.name}", method[:2])
                for item in methods
                if classes and _span(item) & set(added) and (method := _method(tree, classes, item.name, bound))
            ]
    return found


def _class_around(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> ast.ClassDef | None:
    """The class whose method holds `node`, directly: `self` in a function nested in the method is another name."""
    up = parents.get(node)
    while up is not None and not isinstance(up, ast.FunctionDef | ast.AsyncFunctionDef):
        up = parents.get(up)
    around = parents.get(up) if up is not None else None
    return around if isinstance(around, ast.ClassDef) else None


def _method(
    tree: Tree, classes: list[tuple[str, ast.ClassDef]], name: str, bound: Callable[[str], Mapping]
) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str] | None:
    """Method `name` as the first of `classes`, each by its file, that defines it does, then their parents',
    `PASSED_ON` levels up, with the class's name."""
    level = classes
    for _ in range(PASSED_ON + 1):
        upper = []
        for file, cls in level:
            for node in cls.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
                    return file, node, cls.name
            upper += [found for base in cls.bases if (found := _resolved(tree, file, _dotted(base), bound))]
        level = [(file, cls) for file, cls in upper if isinstance(cls, ast.ClassDef)]
    return None


def _resolved(tree: Tree, path: str, name: str, bound: Callable[[str], Mapping]) -> _Defined | None:
    """The module-level function or class `name`, as `path` spells it, defines: through an import, a module's name
    and an attribute of it, or its own module."""
    if not name:
        return None
    imports = bound(path)
    if "." in name:
        owner, _, attribute = name.rpartition(".")
        target = imports.get(owner)
        return _top_level(tree, (target[0], attribute), bound) if target and target[1] is None else None
    if (target := imports.get(name)) is not None:
        return _top_level(tree, target, bound) if target[1] is not None else None
    return _top_level(tree, (path, name), bound)


def _top_level(tree: Tree, target: tuple[str, str], bound: Callable[[str], Mapping]) -> _Defined | None:
    """What `target`'s file defines at its top under its name, followed `PASSED_ON` files deep where it only passes
    the name on, as `callers` follows it."""
    for _ in range(PASSED_ON + 1):
        try:
            module = tree.module(target[0])
        except UnreadError:
            return None
        if not isinstance(module, ast.Module):
            return None
        for node in module.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name == target[1]:
                return target[0], node
        passed = bound(target[0]).get(target[1])
        if passed is None or passed[1] is None:
            return None
        target = (passed[0], passed[1])
    return None


def _definition_shown(tree: Tree, path: str, verb: str, label: str, file: str, node: ast.AST) -> str:
    """One definition as the guidelines read it: its first `CALLEE_LINES` lines, numbered as the head numbers them."""
    named = f"- `{squash(path)}` {verb} `{label}()`, defined in `{squash(file)}`"
    if holds_private_key(tree.sources[file]):
        return f"{named}, a file that holds a private key: not shown"
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    lines = lines_of(tree.sources[file])
    span = range(min(_span(node)), min(len(lines), node.end_lineno or node.lineno) + 1)
    numbered = [f"{number:>4} | {lines[number - 1]}".rstrip() for number in span[:CALLEE_LINES]]
    if (more := len(span) - CALLEE_LINES) > 0:
        numbered.append(f"{'':>4} | … {more:,} more {'line' if more == 1 else 'lines'}, not shown")
    return "\n".join([f"{named}:", *numbered])


def touched_names(files: list[Entry]) -> set[str]:
    """The names a change touched in its Python files: each function and class whose lines it added or between whose
    lines it removed one, each it removed, and each module it moved or removed, a package by its folder's name. What
    the head is read for first where it cannot be read whole: a file that names one may call into what changed. A
    special name, `__init__`, is left out: measured on LiteLLM, nearly every file defining a class names it, so a
    constructor's change read those first, and 27 of 41 calls it broke went unnamed. The class's own name stays."""
    names: set[str] = set()
    for item in files:
        old = item.get("old", item["path"])
        if not (item["path"].endswith(".py") or old.endswith(".py")):
            continue
        if item["status"] in ("D", "R"):
            gone = PurePosixPath(old)
            names.add(gone.parent.name if gone.name == "__init__.py" else gone.stem)
        names |= {found[1] for line in (item["diff"] or "").splitlines() if (found := _DEFINED.match(line))}
        if item["status"] == "D" or (module := parse(item["text"] or "")) is None:
            continue
        lines = item["added_at"] | {line for line, raw in hunk_lines(item["diff"] or "") if raw[:1] == "-"}
        names |= {
            node.name
            for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and lines & _span(node)
        }
    return {name for name in names if name and not (name.startswith("__") and name.endswith("__"))}


#: A definition on a line the change removed: its name, after `def`, `async def` or `class`.
_DEFINED = re.compile(r"^-\s*(?:async\s+def|def|class)\s+(\w+)")


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> set[int]:
    """Every line a function or a class is written on, its decorators' among them."""
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


def no_change(
    head: str, base: str | None, scope: Sequence[str], exclude: Sequence[str] = (), unmatched: Collection[str] = ()
) -> Failure:
    """Why there is nothing to review: the head holds no file, adds nothing to the base, or nothing under the scope
    and outside what `--path-exclude` names. A head that adds nothing is marked `rescope`: most often it is the base
    itself, a branch checked out on `main`. A scope that leaves nothing is marked `narrowed`, for the command to name
    its flags: chat takes none. A name of `unmatched`, which the head holds no path under, says so as the report does:
    read by review, one misspelt `--path` read as a branch that changed nothing there."""
    if scope or exclude:

        def named(paths: Sequence[str]) -> str:
            return ", ".join(path + (no_such_path(path) if path in unmatched else "") for path in paths)

        what = f"no file at {head!r}" if base is None else f"nothing {head!r} changes"
        where = " ".join(
            [*([f"under {named(scope)}"] if scope else []), *([f"outside {named(exclude)}"] if exclude else [])]
        )
        return {"status": "error", "error_message": f"{what} lies {where}: no change to review", "narrowed": True}
    if base is None:
        return {"status": "error", "error_message": f"{head!r} holds no file: nothing to review"}
    why = f"{head!r} adds nothing to {base!r}: no change to review"
    return {"status": "error", "error_message": why, "rescope": True}
