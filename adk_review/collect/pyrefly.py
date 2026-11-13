"""Pyrefly over the changed Python files at the head commit, for a rule that names the kinds it reads: its diagnostics.

As ruff is (`collect/ruff.py`), each file, as the collector read it at the head, is written alone into a temporary
directory under a name of the rule's own, and nothing runs from, or writes into, the repository under review. Pyrefly
reads it there with the reviewer's configuration and never the repository's: the `default` preset, Python 3.13, no
installed package and no interpreter asked, so a name a file imports from beyond itself reads as `Any`; and no ignore
comment honoured, as ruff runs `--ignore-noqa`. Measured over four libraries' source and 60 planted edits: each file
alone found 27 of 30 type defects on their own lines, as the whole package did, at 0.1 s a file where the package took
up to 5 s. What the diagnostics mean, a kind kept and its severity, is the rule's, `types`.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sysconfig
import tempfile
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePath, PurePosixPath

from ..core.blast import PASSED_ON, is_test
from ..core.evidence import Change, Entry, Evidence, unreleased
from ..core.rules import Diagnostic, UnreadError, is_python_text
from ..core.text import squash
from .diff import base_line
from .git import GitError, base_files, on_path
from .ruff import Rows, laid_out, rows_of

PYREFLY_TIMEOUT_S = 60
#: Pyrefly's answer is read up to this many bytes, and past them none of it is: the rule did not look. Measured by
#: review: files of `1 + ""` lines wrote 62 MB of JSON each, and twelve of them held 3.1 GB.
PYREFLY_ANSWER_MAX_BYTES = 64_000_000
#: The reviewer's own configuration, written beside the copies: nothing of the repository's is read. With none,
#: pyrefly falls back to its `basic` preset; and asked, an interpreter on PATH types a branch against the reviewer's own
#: installed packages, where a reviewed project's are not.
CONFIG = """\
disable-search-path-heuristics = true
site-packages-path = []
skip-interpreter-query = true
python-version = "3.13"
preset = "default"
enabled-ignores = []
disable-project-excludes-heuristics = true
"""
#: What pyrefly says of a key in the configuration above it does not take, and runs on: read by review, a release
#: that renamed `enabled-ignores` would honour a branch's ignore comments again, unseen.
_UNTAKEN = "Extra keys found in config:"
#: What pyrefly says of a file it checked nothing else in: read by review, a line Python compiles, `-` a thousand times
#: before `1`, and a planted missing argument with it went unread as clean.
_GAVE_UP = "too deeply nested"
#: A string's literal in pyrefly's words, blanked: it names a literal's value, escaped, `Literal['\nAKIA...\n']`, where
#: no shape of the scrub's reads it. Read by review: a key reached the report and the chat's verdict in clear.
_LITERAL = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*\"""")


class _UnreadError(Exception):
    """Why pyrefly's answer was not read: past its cap, of another shape than its JSON, or a file it gave up on."""


def diagnose(evidence: Change, kinds: Collection[str]) -> dict[str, list[Diagnostic]] | str:
    """Pyrefly's diagnostics of `kinds` on the Python files the change touched, as the collector read them at the head,
    by path, or, as a string, why pyrefly could not look."""
    pyrefly = beside() or on_path("pyrefly")  # by its full path: on Windows the current directory is searched first
    if pyrefly == "pyrefly":  # the name alone: found nowhere on PATH
        return "pyrefly is not on PATH. `uv run` puts the project's own there"
    texts = {
        f["path"]: text
        for f in evidence["files"]
        if (text := f["text"]) is not None and is_python_text(f["path"], text)
    }
    if not texts:
        return {}
    added: dict[str, Collection[int]] = {
        f["path"]: f.get("added_at") or {number for number, _line in unreleased(f["added"])}
        for f in evidence["files"]
        if f["path"] in texts
    }
    try:
        return _run(pyrefly, texts, added, frozenset(kinds))
    except (OSError, _UnreadError, subprocess.SubprocessError) as exc:
        return _why(exc, "the changed files")


def _why(exc: Exception, read: str) -> str:
    """Why pyrefly could not look, as its rule says it: a disk that refuses the write, full or read-only, or a pyrefly
    that cannot start, on what it was to `read`; its answer not read; its own exit; or no answer in time."""
    if isinstance(exc, subprocess.CalledProcessError):
        return f"pyrefly exited {exc.returncode}: {squash(exc.stderr) or 'no reason given'}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"pyrefly did not answer within {PYREFLY_TIMEOUT_S} s"
    if isinstance(exc, OSError):
        return f"pyrefly could not run on {read} ({exc.strerror or type(exc).__name__})"
    return str(exc)


def _run(
    pyrefly: str, texts: dict[str, str], added: dict[str, Collection[int]], kinds: frozenset[str]
) -> dict[str, list[Diagnostic]]:
    """Pyrefly over each file's text, written into a temporary directory: its diagnostics of `kinds` by path, or it
    raises for `diagnose` to name. A folder that will not go is left to the system's own cleaning, as ruff's is."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        targets, sources = laid_out(tmp, texts)
        errors = _errors(pyrefly, tmp, (".",), targets)
    return _parsed(errors, sources, added, kinds)


def _errors(pyrefly: str, folder: str, roots: Sequence[str], targets: Sequence[str] = ()) -> list[dict]:
    """Pyrefly's errors over `folder`, the reviewer's configuration written there with `roots`: over `targets` where
    they are named, else over the whole folder. `_UnreadError` where its answer was not read."""
    Path(folder, "pyrefly.toml").write_text(_config(roots), encoding="utf-8")
    argv = [pyrefly, "check", "-c", "pyrefly.toml", "--output-format", "json", "--summary=none", "--progress-bar", "no"]
    answer, said = _answer([*argv, *targets], folder)
    if _UNTAKEN in said:  # the configuration is the rule's guarantee: one not taken whole is no look
        untaken = said.split(_UNTAKEN, 1)[1].splitlines()[0].strip()
        raise _UnreadError(f"pyrefly did not take the reviewer's configuration whole: no key {untaken}")
    try:
        return json.loads(answer)["errors"]
    except (ValueError, KeyError, TypeError) as exc:  # pyrefly's own reason is on stderr: no file matched, say
        raise _UnreadError(
            f"pyrefly's answer could not be read ({type(exc).__name__}): {squash(said) or 'no reason'}"
        ) from exc


def _gave_up(error: dict) -> bool:
    """Whether the error says pyrefly checked nothing else in its file."""
    return error["name"] == "parse-error" and _GAVE_UP in error["description"]


def _answer(argv: list[str], cwd: str) -> tuple[str, str]:
    """What pyrefly writes on stdout and on stderr, read as ruff's answer is, its process watched: `TimeoutExpired` past
    `PYREFLY_TIMEOUT_S`, `_UnreadError` past `PYREFLY_ANSWER_MAX_BYTES`, `CalledProcessError` for an exit but 0 or 1,
    the errors it found. The caller's environment with every `PYREFLY_*` variable left out, as ruff's runs without
    `RUFF_*`."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYREFLY")}
    with (
        tempfile.TemporaryFile() as said,
        subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=said) as running,
    ):
        late = threading.Event()
        watch = threading.Timer(PYREFLY_TIMEOUT_S, lambda: (late.set(), running.kill()))
        watch.start()
        try:
            out = running.stdout.read(PYREFLY_ANSWER_MAX_BYTES + 1) if running.stdout else b""
        finally:
            watch.cancel()
            if running.poll() is None:  # past the cap, or stopped: no more of it is read
                running.kill()
        code = running.wait()
        said.seek(0)
        stderr = said.read().decode("utf-8", "replace")
    if late.is_set():
        raise subprocess.TimeoutExpired(argv, PYREFLY_TIMEOUT_S)
    if len(out) > PYREFLY_ANSWER_MAX_BYTES:
        raise _UnreadError(f"pyrefly wrote more than {PYREFLY_ANSWER_MAX_BYTES:,} bytes, and none of them was read")
    if code not in (0, 1):
        raise subprocess.CalledProcessError(code, argv, stderr=stderr)
    return out.decode("utf-8", "replace"), stderr


def _parsed(
    errors: list[dict], sources: dict[str, Rows], added: dict[str, Collection[int]], kinds: frozenset[str]
) -> dict[str, list[Diagnostic]]:
    """Pyrefly's errors of `kinds`, each under the path whose index names its folder, `sources` in the order `laid_out`
    numbered them, on git's line: the first line of its range the change added, where one is, as a call laid out over
    several lines is placed on its first, else where it starts. `_UnreadError` for a file pyrefly gave up on."""
    paths = list(sources)
    found: dict[str, list[Diagnostic]] = {}
    for error in errors:
        path = paths[int(PurePath(error["path"]).parts[-2])]  # `<index>/<name>`, however pyrefly spells the root
        if _gave_up(error):
            raise _UnreadError(f"pyrefly checked nothing in {path}: {squash(error['concise_description'])}")
        if error["name"] not in kinds:
            continue
        rows = sources[path]
        start = rows.place(error["line"])[0]
        stop = rows.place(error.get("stop_line") or error["line"])[0]
        number = next((at for at in range(start, stop + 1) if at in added[path]), start)
        line = rows.lines[number - 1] if 0 < number <= len(rows.lines) else ""
        message = _LITERAL.sub("'…'", squash(error["concise_description"]))
        found.setdefault(path, []).append(Diagnostic(number, line, error["name"], message))
    return found


#: A name `written` writes: letters, digits, `_` and `-`, and dots only between them, so never `..`, a drive's `:`,
#: Windows' `\`, nor a name Windows trims, as it trims a final dot.
_PLAIN = re.compile(r"[\w-]+(?:\.[\w-]+)*")
#: What Windows holds as a device under any extension: a module so named is written nowhere.
_DEVICES = frozenset({"con", "prn", "aux", "nul", *(f"{port}{n}" for port in ("com", "lpt") for n in range(1, 10))})
#: A diagnostic as pyrefly's run over one tree names it: its file, its kind and its line's text, and the order pyrefly
#: gave it in, which keeps two of one line apart.
type Found = tuple[str, str, str, int]
#: A diagnostic and the lines its range covers, as git numbers them.
type Ranged = tuple[Diagnostic, range]
#: A diagnostic as the radius tells one from another across the base and the head: its file, its kind, its line's text
#: and the base's number for its line, which `_placed` reads off the diff for a line the change kept; None for a line
#: of the head the change added, which its text alone places.
type Placed = tuple[str, str, str, int | None, int]


def radius(
    evidence: Evidence, ignored: Collection[str], added_kinds: Collection[str], tests: bool = True
) -> dict[str, list[Diagnostic]] | str:
    """Pyrefly over the head's tree and the base's, each laid out as it is so an import finds what it names: by path,
    each diagnostic new at the head, of a kind not `ignored`, in a file the change left alone or on a line of a changed
    file it kept, and on a line it added where its kind is one of `added_kinds`, a call's or an attribute's, which the
    `types` rule, reading each changed file alone, cannot see across files. A test's file is in neither run where
    `tests` is False: measured on ADK, half its bytes tests, a run took 0.96 s where it took 1.59. Nothing under `--all`
    or `--try`, which have no base, where the change only adds files, nor where the head was not read. Measured over
    ADK, 31 method edits: 24 named, none wrongly, 0.5 s a run; over this repository's last 195 commits, none wrongly,
    where 37 were before a test's file was left out, 34 of them in tests, and before a new diagnostic was charged first
    to the lines the change added; and one true break, a value that may be None passed where a string is taken, which
    the next commit fixed."""
    tree, resolved = evidence.get("tree"), evidence.get("preflight")
    altered = [
        f
        for f in evidence["files"]
        if f["status"] in ("M", "T", "R", "D")
        and f.get("old", f["path"]).endswith(".py")
        and (tests or not is_test(f["path"]))
    ]
    if tree is None or resolved is None or evidence.get("whole") or not altered:
        return {}
    pyrefly = beside() or on_path("pyrefly")
    if pyrefly == "pyrefly":
        return "pyrefly is not on PATH. `uv run` puts the project's own there"
    try:
        held = base_files(resolved, {f.get("old", f["path"]) for f in altered})
    except GitError as exc:
        return f"git could not read the base's files ({exc})"
    head = {p: text for p, text in tree.sources.items() if p.endswith(".py") and (tests or not is_test(p))}
    base, renamed = _base(head, evidence["files"], held)
    # Names that differ in case, in either tree, left out of both alike: read by review, a branch deleted `A.py` beside
    # a kept `a.py`, neither was written for the base, the head's `a.py` was, and an error it held before read as new.
    folded = Counter(path.casefold() for path in {*head, *base})
    head, base = ({p: text for p, text in texts.items() if folded[p.casefold()] == 1} for texts in (head, base))
    try:
        # The two runs side by side: measured on ADK's 815 files, one after the other added 2.2 s to a push's review.
        # One set of roots for both runs, from both trees: found apart, a branch that added a package's first
        # `__init__.py` gave the head a root the base lacked, and a call broken before it read as the change's.
        roots = _roots({*head, *base})
        with ThreadPoolExecutor(max_workers=2) as pool:
            later = pool.submit(_whole, pyrefly, base, ignored, roots)
            (now, gave_now), (before, gave_before) = _whole(pyrefly, head, ignored, roots), later.result()
    except (OSError, _UnreadError, subprocess.SubprocessError) as exc:
        return _why(exc, "the head's tree")
    # The base's diagnostics under each file's path at the head, so the two runs name a file alike; and a file the
    # head no longer holds no hole: read by review, a branch that deleted one pyrefly gave up on could not be pushed.
    before = {(renamed.get(p, p), kind, line, order): found for (p, kind, line, order), found in before.items()}
    lost = (gave_now | {renamed.get(path, path) for path in gave_before}) & head.keys()
    # Only a file the base's tree holds as git held it is numbered by its diff: one left as the head holds it is not.
    numbered = frozenset(renamed.values())
    return _compared(evidence["files"], tree.imports, now, before, lost, frozenset(added_kinds), numbered)


def _compared(
    files: list[Entry],
    imports: Callable[[str], Mapping[str, tuple[str, str | None]]],
    now: Mapping[Found, Ranged],
    before: Mapping[Found, Ranged],
    lost: Collection[str],
    added_kinds: Collection[str],
    numbered: Collection[str],
) -> dict[str, list[Diagnostic]] | str:
    """The head's run against the base's, by `_new`, each diagnostic placed first by `_placed`, but for each file
    pyrefly checked nothing in, too deeply nested, left out of both alike: where the change touched one or it reaches a
    file the change touched, the rule could not look there, as `types` says of one, and a file the change does not
    reach is no hole of it. `numbered` names the changed files whose base text the base's tree holds."""
    touched = {f["path"] for f in files} | {f["old"] for f in files if "old" in f}

    @functools.cache
    def bound(path: str) -> Mapping[str, tuple[str, str | None]]:
        """What `path`'s imports bind; nothing for a file the head's read left out, a floor as `callers` reads it: read
        by review, a name imported from one past `MAX_FILE_BYTES` raised, and the rule could not look."""
        try:
            return imports(path)
        except UnreadError:
            return {}

    @functools.cache
    def reaches(path: str) -> bool:
        """Whether a name `path` imports comes from another file the change touched, followed where a file only passes
        it on, as a package's `__init__.py` does: read by review, only the import's own file was asked, and a call
        through `from shop import Repo` was left to `types`."""
        for file, name in bound(path).values():
            for _ in range(PASSED_ON + 1):
                if file != path and file in touched:
                    return True
                if name is None or (passed := bound(file).get(name)) is None:
                    break
                file, name = passed
        return False

    if unread := sorted(path for path in lost if path in touched or reaches(path)):
        return f"pyrefly checked nothing in {', '.join(unread)}: it is nested too deeply for pyrefly"
    now = {key: found for key, found in now.items() if key[0] not in lost}
    before = {key: found for key, found in before.items() if key[0] not in lost}
    added = {f["path"]: f["added_at"] for f in files}
    placed = _placed(now, before, [f for f in files if f["path"] in numbered])
    return _new(*placed, added, lambda path, kind: kind in added_kinds and reaches(path))


def _placed(
    now: Mapping[Found, Ranged], before: Mapping[Found, Ranged], numbered: list[Entry]
) -> tuple[dict[Placed, Ranged], dict[Placed, Ranged]]:
    """Each run's diagnostics with the base's number for the line each stands on, so the two runs name a line alike:
    the base's by their own line; the head's by their own too in a file both trees hold alike, one the change left
    alone, and in a `numbered` one, a changed file the base's tree holds as git held it, by the number its diff gives a
    line the change kept, None on a line it added. A diagnostic is placed by its own line alone, whatever lines it
    covers, a `def`'s decorators among them: a kept line of the head is one line of the base and no other, so the two
    sides agree. Read by a check before the merge: placed only where it covered one line, an override the base held
    wrong under a decorator the change removed was placed at the head and not at the base, and named as the change's.
    A file's diff is read once, and only where the head's run names the file."""
    entries = {f["path"]: f for f in numbered}

    @functools.cache
    def to_base(path: str) -> Callable[[int], int]:
        return base_line(unreleased(entries[path]["diff"]))

    def at(path: str, found: Diagnostic) -> int | None:
        if path not in entries:
            return found.line
        return None if found.line in entries[path]["added_at"] else to_base(path)(found.line)

    return (
        {(path, kind, text, at(path, ranged[0]), order): ranged for (path, kind, text, order), ranged in now.items()},
        {(path, kind, text, ranged[0].line, order): ranged for (path, kind, text, order), ranged in before.items()},
    )


def _base(
    head: Mapping[str, str], files: list[Entry], held: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """The base's tree, the head's with each changed Python file as the base held it, `held` its text as git holds it,
    and where each such file is at the head, by its path before the change. One `held` lacks, a link at the base or a
    file git gave no text of, is left as the head holds it, compared with nothing it never was: measured, a link made
    a file was rebuilt as the one line it names, and a call wrong before the change read as new."""
    base, renamed = dict(head), {}
    for f in files:
        path, old, status = f["path"], f.get("old", f["path"]), f["status"]
        if status in ("A", "C", "R"):
            base.pop(path, None)  # not there before, or there under another name
        if status in ("M", "T", "R", "D") and old in held:
            base[old], renamed[old] = held[old], path
    return base, renamed


def _new(
    now: Mapping[Placed, Ranged],
    before: Mapping[Placed, Ranged],
    added: Mapping[str, Collection[int]],
    charged: Callable[[str, str], bool],
) -> dict[str, list[Diagnostic]]:
    """Each diagnostic at the head the base did not hold. One on a line the base holds too, a line the change kept or
    any line of a file it left alone, is the base's where the base held one of its kind on that line. Read by review:
    set against any base line of the same text, counted, the one wrong before was named where the change broke another
    of its text above it; and where the base's went right as the other broke, the count was even and nothing was. What
    that leaves at the head is set, by its text, against what it leaves at the base in a file the change edited, and
    only there: a wrong line may have moved, to a line the change added or, as a diff prints a block moved among its
    like, to one it kept, where in a file the change left alone no line moves, and what the head no longer holds on
    its line is gone. Measured before the merge over 160 random edits of a package of 494 such errors: one moved a
    class among two that read alike, and the wrong line it carried was named on a kept line. What the base has left is
    charged first to the head's whose range meets a line the change added, where a moved line stands, then to its kept
    ones, and each beyond it is new. One on an added line is named where `charged` says so of its file and kind, a
    call's or an attribute's in a file that reaches another the change touched: read by review, a branch that renamed
    a method and edited the call to it in another file was approved by every rule; and named for any file, a defect
    within the one file `types` reads and grades was named at major, and a new file's beside an unrelated change."""
    held = Counter(key[:4] for key in before)
    left: defaultdict[tuple[str, str, str], list[Ranged]] = defaultdict(list)
    for key, ranged in now.items():
        if key[3] is not None and held[key[:4]]:
            held[key[:4]] -= 1  # the base held it on this line: there before
        else:
            left[key[:3]].append(ranged)
    moved: Counter[tuple[str, str, str]] = Counter()
    for (path, kind, text, _line), count in held.items():
        if path in added:  # a file the change edited
            moved[path, kind, text] += count
    found: dict[str, list[Diagnostic]] = {}
    for (path, kind, text), items in sorted(left.items()):
        mine = set(added.get(path, ()))
        ordered = sorted(items, key=lambda ranged: ranged[0].line)
        on_added = [diagnostic for diagnostic, lines in ordered if not mine.isdisjoint(lines)]
        kept = [diagnostic for diagnostic, lines in ordered if mine.isdisjoint(lines)]
        gone = moved[path, kind, text]
        fresh = max(0, len(on_added) - gone)  # the added lines' beyond what the base has left
        named = on_added[:fresh] if fresh and charged(path, kind) else []
        named += kept[max(0, gone - len(on_added)) :]
        if named:
            found.setdefault(path, []).extend(named)
    return {path: sorted(items, key=lambda diagnostic: diagnostic.line) for path, items in found.items()}


def _whole(
    pyrefly: str, texts: Mapping[str, str], ignored: Collection[str], roots: Sequence[str]
) -> tuple[dict[Found, Ranged], set[str]]:
    """Pyrefly over `texts` laid out as one tree with the reviewer's configuration: each diagnostic of a kind not
    `ignored`, by its key and the order pyrefly gave it in, on git's line, with the lines its range covers; and each
    file pyrefly checked nothing in, too deeply nested."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        laid = written(tmp, texts)
        if not laid:  # no file a run could read: nothing to check, where pyrefly would refuse an empty project
            return {}, set()
        errors = _errors(pyrefly, tmp, roots)
    found: dict[Found, Ranged] = {}
    # A file's lines, made where pyrefly names one: measured, every file's took 47% of the write.
    rows: dict[str, Rows] = {}
    gave_up: set[str] = set()
    for index, error in enumerate(errors):
        path = _written_as(error["path"], laid)
        if path is not None and _gave_up(error):
            gave_up.add(path)
            continue
        if error["name"] in ignored or path is None:
            continue
        if path not in rows:
            rows[path] = rows_of(texts[path])
        number, line = rows[path].place(error["line"])
        stop = rows[path].place(error.get("stop_line") or error["line"])[0]
        message = _LITERAL.sub("'…'", squash(error["concise_description"]))
        diagnostic = Diagnostic(number, line, error["name"], message)
        found[(path, error["name"], squash(line), index)] = (
            diagnostic,
            range(_decorated(rows[path], number), stop + 1),
        )
    return found, gave_up


def _roots(paths: Collection[str]) -> list[str]:
    """Where an import starts, as Python's path holds it: the tree's top, and the folder above each top package, one
    whose own folder is no package, `src` in a `src/` layout. Read by review: with the top alone, `from pkg.mod import
    Repo` there named nothing, `Repo` read as `Any`, and the call the change broke through it went unnamed; and a loop
    up the folders never ended at a package at the tree's top, whose folder's folder is itself."""
    packages = {PurePosixPath(path).parent for path in paths if PurePosixPath(path).name == "__init__.py"}
    tops = {folder.parent for folder in packages if folder.parent not in packages}
    # A folder inside a package is never where an import starts, though it holds no `__init__.py`: read by review, 60
    # such on litellm shadowed installed packages. The tree's top aside, which a package there may be.
    return sorted({".", *(top.as_posix() for top in tops if not any(up in packages for up in top.parents[:-1]))})


def _config(roots: Sequence[str]) -> str:
    """The reviewer's configuration with where an import starts: the tree's top for a file alone, each root over a
    tree. Each root written as TOML reads a string, JSON's escapes but each character as itself, DEL aside: read by
    review, JSON wrote one past U+FFFF as two UTF-16 halves, which TOML refuses, and a package under `𠀀/` left the
    radius unable to look; and TOML refuses a raw DEL too."""
    listed = json.dumps(list(roots), ensure_ascii=False).replace("\x7f", "\\u007f")
    return f"search-path = {listed}\n{CONFIG}"


def _decorated(rows: Rows, number: int) -> int:
    """Where a `def` or `class` on line `number` starts, its decorators above it among it: pyrefly puts the error a
    decorator raises, `@override` over no parent's method, on the `def` below it, which the change may have kept."""
    if not rows.lines[number - 1 : number] or not rows.lines[number - 1].lstrip().startswith(
        ("def ", "async ", "class ")
    ):
        return number
    while number > 1 and rows.lines[number - 2].lstrip().startswith("@"):
        number -= 1
    return number


def _written_as(reported: str, laid: Collection[str]) -> str | None:
    """The path a file was written at, read off the end of the path pyrefly reports, however it spells the folder: on
    Windows, a temporary folder has a short name and a long one, and `_parsed` reads the index the same way."""
    parts = PurePath(reported).parts
    return next((path for k in range(len(parts)) if (path := "/".join(parts[k:])) in laid), None)


def written(folder: str, texts: Mapping[str, str]) -> set[str]:
    """Each Python file of `texts` written into `folder` at its path, the tree laid out as the head holds it, so an
    import from any root finds it. Never a path outside the folder, a name one system reads otherwise than another, or
    one that another differs from only in case: a branch names its own paths. Read by review: written only where each
    folder was a name Python imports, a package under `my-app/`, a root `_roots` names, was checked nowhere. A file the
    disk refuses is left out, as a file the head's read left out is. The paths written."""
    folded = Counter(path.casefold() for path in texts)
    laid: set[str] = set()
    for path, text in texts.items():
        parts = PurePosixPath(path).parts
        if not path.endswith(".py") or folded[path.casefold()] > 1:
            continue
        if not all(_PLAIN.fullmatch(name) and name.split(".")[0].casefold() not in _DEVICES for name in parts):
            continue
        target = Path(folder, *parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
        except OSError:
            continue
        laid.add(path)
    return laid


def identity() -> str:
    """The pyrefly a review would run, as what its result depends on: its path and its own `--version`, or why it said
    none, so a review kept under one pyrefly is never served under another."""
    pyrefly = beside() or on_path("pyrefly")
    if (
        pyrefly == "pyrefly"
    ):  # found nowhere: the name alone is never run, which Windows seeks in the current folder first
        return "pyrefly: not on PATH"
    try:
        said = subprocess.run(
            [pyrefly, "--version"], capture_output=True, encoding="utf-8", errors="replace", timeout=PYREFLY_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{pyrefly}: {type(exc).__name__}"
    return f"{pyrefly} {said.stdout.strip()}"


def beside() -> str | None:
    """The pyrefly installed with the reviewer, in its own interpreter's scripts folder, as ruff's is found: never on
    PATH where the reviewer's environment is not activated. None where there is none, and PATH is asked."""
    found = Path(sysconfig.get_path("scripts")) / ("pyrefly.exe" if os.name == "nt" else "pyrefly")
    return str(found) if found.is_file() else None
