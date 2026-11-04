"""Ruff over the changed Python files at the head commit, for a rule that names the codes it reads: its diagnostics.

The files, as the collector read them at the head commit, are written into a temporary directory; nothing runs from, or
writes into, the repository under review. Ruff runs `--isolated`, with the reading rule's own codes and nothing of the
reviewed repository's configuration, and `--ignore-noqa`, so a branch can neither configure nor silence the rule that
judges it. Ruff lints the texts the collector read and no more: a file not read whole is the reading rule's hole, as it
is every file rule's. What the diagnostics mean — a severity, a fix — is each rule's that names ruff: `lint`, `pitfalls`
and `simplify`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sysconfig
import tempfile
import threading
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path, PurePath, PurePosixPath
from typing import NamedTuple

from ..core.evidence import Change
from ..core.rules import Diagnostic, is_python_text
from ..core.text import lines_of, squash
from .git import on_path

RUFF_TIMEOUT_S = 60
#: Ruff's answer is read a finding at a time, and past this many none is: what it named past them is a hole. Measured
#: by review, three files of unary minus chains made ruff write 589 MB of JSON each, read whole: 90 s and 4.5 GB.
RUFF_FINDINGS_MAX = 500_000


class _FloodError(Exception):
    """Ruff named more findings than `RUFF_FINDINGS_MAX`."""


#: What breaks a row where ruff and pyrefly count one: a lone `\r` too, which git reads as within a line.
_ROW_BREAK = re.compile(r"\r\n?|\n")
#: How ruff ends the words of a count over its limit, the count first: "Too many arguments (6 > 5)", and C901's,
#: PLR0911's, PLR0912's and PLR0915's alike.
_COUNT = re.compile(r"\((\d+) > (\d+)\)$")


class Rows(NamedTuple):
    """A file's lines as git numbers them, and the git line each row a tool counts falls on: a tool counts a lone
    `\r` as a line's end, and git does not. Read by review: one put every finding below it a line past git's, and one
    on an added line was dropped."""

    lines: list[str]
    at: list[int]

    def place(self, row: int) -> tuple[int, str]:
        """A tool's `row` as `(git's line number, that line)`: past the file's end, the row itself and no line."""
        number = self.at[row - 1] if 0 < row <= len(self.at) else row
        return number, self.lines[number - 1] if 0 < number <= len(self.lines) else ""


def rows_of(text: str) -> Rows:
    """`text`'s lines as git numbers them, and the git line of each row a tool counts."""
    if "\r" not in text:  # each row a line: measured on litellm, the pattern's walk took 0.17 s, this 0.06
        return Rows(lines_of(text), list(range(1, text.count("\n") + 2)))
    at, line = [1], 1
    for found in _ROW_BREAK.finditer(text):
        line += "\n" in found.group()
        at.append(line)
    return Rows(lines_of(text), at)


def diagnose(
    evidence: Change, select: Sequence[str], ignore: Sequence[str], line_length: int | None = None
) -> dict[str, list[Diagnostic]] | str:
    """Ruff's diagnostics on the Python files the change touched, as the collector read them at the head, by path —
    or, as a string, why ruff could not look. `select` and `ignore` are the reading rule's own codes: ruff reads no
    other configuration; `line_length` the width it holds `E501` to, None for ruff's own."""
    ruff = beside() or on_path("ruff")  # by its full path: on Windows the current directory is searched first
    if ruff == "ruff":  # the name alone: found nowhere on PATH
        return "ruff is not on PATH. `uv run` puts the project's own there"
    texts = {
        f["path"]: f["text"]
        for f in evidence["files"]
        if f["text"] is not None and is_python_text(f["path"], f["text"])
    }
    if not texts:
        return {}
    try:
        return _known(
            evidence,
            _run(ruff, texts, select, ignore, line_length),
            lambda base: _run(ruff, base, select, ignore, line_length),
        )
    except OSError as exc:  # a disk that refuses the write: full, or read-only
        return f"a changed file could not be written to lint ({exc.strerror})"
    except _FloodError:
        return f"ruff named more than {RUFF_FINDINGS_MAX:,} findings, and those past them were not read"
    except (ValueError, KeyError, IndexError, TypeError) as exc:  # an answer of another shape than ruff's JSON
        return f"ruff's answer could not be read ({type(exc).__name__})"
    except subprocess.CalledProcessError as exc:  # ruff's cause sits on no fixed line, so all it said goes, flat
        return f"ruff exited {exc.returncode}: {squash(exc.stderr) or 'no reason given'}"
    except subprocess.TimeoutExpired:
        return f"ruff did not answer within {RUFF_TIMEOUT_S} s"


def _known(
    evidence: Change,
    found: dict[str, list[Diagnostic]],
    linted: Callable[[dict[str, str]], dict[str, list[Diagnostic]]],
) -> dict[str, list[Diagnostic]]:
    """`found` with each diagnostic on a line the change did not add set against the base: `known` where the base held
    it too, the same code and words on a line of the same text, a count in them no higher, each base one matched once,
    and not where the change made it. A line the change added is its own, whatever the base held, and only a file with
    a diagnostic elsewhere is linted at the base. Where the base was not read or could not be linted, `known` stays
    None, and `listed` counts those diagnostics with the change, at the bar, as before: "did not look" never reads as
    "not caused by this change". Read by review: ruff's words tell a shell call that "seems safe" from one an edit below
    its first line made unsafe. Asked by the owner: what a change causes is kept apart from what was already in the
    files it touched."""
    held = evidence.get("base_texts") or {}  # read by the collector, as the head's texts are: no git here
    added = {f["path"]: f.get("added_at", frozenset()) for f in evidence["files"]}
    wanted = {
        f["path"]: base
        for f in evidence["files"]
        if f.get("status") != "A"
        and (base := f.get("old", f["path"])) in held
        and any(item.line not in added[f["path"]] for item in found.get(f["path"], ()))
    }
    if not wanted:
        return found
    try:
        before = linted({path: held[base] for path, base in wanted.items()})
    except (OSError, ValueError, KeyError, IndexError, TypeError, _FloodError, subprocess.SubprocessError):
        return found  # not compared: each `known` stays None

    # A count is read apart from the words around it: read by review, a parameter removed from a function of seven
    # turned "(7 > 5)" into "(6 > 5)" on the `def` line the change left alone, and the finding read as the change's.
    def key(item: Diagnostic) -> tuple[str, str, str]:
        return item.code, squash(item.source), _COUNT.sub(r"(> \2)", item.message)

    def count(item: Diagnostic) -> int:
        return int(said[1]) if (said := _COUNT.search(item.message)) else 0

    marked = dict(found)
    for path in wanted:
        left: dict[tuple[str, str, str], list[int]] = {}
        for item in before.get(path, ()):
            left.setdefault(key(item), []).append(count(item))
        marked[path] = []
        for item in found[path]:
            if item.line not in added[path]:
                # Old where the base held it at this count or above, each base one matched once: a count the change
                # raised is its own.
                fits = [held for held in left.get(key(item), []) if held >= count(item)]
                if fits:
                    left[key(item)].remove(min(fits))
                item = replace(item, known=bool(fits))
            marked[path].append(item)
    return marked


def _run(
    ruff: str, texts: dict[str, str], select: Sequence[str], ignore: Sequence[str], line_length: int | None
) -> dict[str, list[Diagnostic]]:
    """Ruff over each file's text as it stands at the head, written into a temporary directory: its diagnostics by
    path, or it raises — ruff failing, or past its timeout — for `diagnose` to name. Under `--exit-zero` a diagnostic
    never fails ruff, so any other exit is a ruff that could not run."""
    # A folder that will not go is left to the system's own cleaning: measured on CI's Windows runner, another process
    # held a copy open after ruff had answered, and its removal failing was named as a file that could not be written.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        targets, sources = laid_out(tmp, texts)
        # Every file by name, never a folder: ruff's default excludes skipped a `.venv/` or `node_modules/` a branch
        # had changed, never a file it is named. `--ignore-noqa`: an inline noqa comment in the branch silenced it.
        # `--no-cache`: nothing is written beside the files, which Windows could hold open when the folder goes.
        argv = [ruff, "check", "--no-fix", "--no-cache", "--isolated", "--exit-zero", "--ignore-noqa"]
        argv += ["--select", ",".join(select), *(["--ignore", ",".join(ignore)] if ignore else [])]
        argv += ["--line-length", str(line_length)] if line_length else []
        argv += ["--output-format", "json-lines", *targets]
        # Closed before the folder goes, a reader that stopped early stopping ruff: measured on Windows, which deletes
        # no file a process holds open, the folder's removal failed under a ruff still running, and its error was named.
        with closing(_ruff(argv, tmp)) as answer:
            return _parsed(answer, sources)


def laid_out(folder: str, texts: dict[str, str]) -> tuple[list[str], dict[str, Rows]]:
    """Each file written into `folder`, and the names a tool is given for them, with each file's lines as ruff and
    pyrefly count them, by path: ruff's, and `collect/pyrefly.py`'s."""
    sources: dict[str, Rows] = {}
    targets = []
    for index, (path, text) in enumerate(texts.items()):
        # Each in a folder of its own, under a name of the rule's own, never the branch's: `x.py` beside
        # `X.py/y.py` collided on a case-insensitive disk, `../../x.py` was written outside the folder, and
        # Windows holds no `nul.py`, `a:b.py` or control character. Ruff, `--isolated`, reads only the name
        # `__init__.py` and a stub's suffix from a file's path, so those are kept.
        name = "__init__.py" if PurePosixPath(path).name == "__init__.py" else "module" + _suffix(path)
        target = f"{index}/{name}"
        written = Path(folder, target)
        written.parent.mkdir()
        written.write_text(text, encoding="utf-8", newline="")
        targets.append(target)
        # Rows where a tool counts them, never `str.splitlines`, which breaks at a form feed or U+2028 too: measured,
        # the quote after one was the line above ruff's. Each is placed on git's line, which a lone `\r` breaks not.
        sources[path] = rows_of(text)
    return targets, sources


def _ruff(argv: list[str], cwd: str) -> Generator[str]:
    """The one place ruff runs, as `git._run` is git's: each line it writes on stdout as it comes, then
    `CalledProcessError` for an exit that is not 0, or `TimeoutExpired`, a watch stopping it at `RUFF_TIMEOUT_S`. What
    it says on stderr goes to a file, so no pipe it fills can stop it. The caller's environment with every `RUFF_*`
    variable left out, as git's runs without `GIT_*`: measured, `RUFF_OUTPUT_FILE`, set for another step of a
    pipeline, sent the report to that file, and stdout read empty."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("RUFF_")}
    with (
        tempfile.TemporaryFile() as said,
        subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=said, encoding="utf-8", errors="replace"
        ) as running,
    ):
        late = threading.Event()
        watch = threading.Timer(RUFF_TIMEOUT_S, lambda: (late.set(), running.kill()))
        watch.start()
        try:
            yield from running.stdout or ()
        finally:  # a reader that stops early stops ruff too
            watch.cancel()
            if running.poll() is None:
                running.kill()
        code = running.wait()
        if late.is_set():
            raise subprocess.TimeoutExpired(argv, RUFF_TIMEOUT_S)
        if code:
            said.seek(0)
            raise subprocess.CalledProcessError(code, argv, stderr=said.read().decode("utf-8", "replace"))


def _parsed(answer: Iterable[str], sources: dict[str, Rows]) -> dict[str, list[Diagnostic]]:
    """Ruff's answer read back, a line of JSON a finding: each under the path whose index names its folder, `sources`
    in the order `laid_out` numbered them, with the line it names as the file holds it; `ValueError`, `KeyError`,
    `IndexError` or `TypeError` for an answer of another shape, `_FloodError` past `RUFF_FINDINGS_MAX`. A finding keeps
    no more than a `Diagnostic` holds, never its fix's edits, and each text said again is kept once."""
    paths = list(sources)
    found: dict[str, list[Diagnostic]] = {}
    texts: dict[str, str] = {}

    def once(text: str) -> str:
        return texts.setdefault(text, text)

    count = 0
    for text in answer:
        if not text.strip():
            continue
        if (count := count + 1) > RUFF_FINDINGS_MAX:
            raise _FloodError
        hit = json.loads(text)
        path = paths[int(PurePath(hit["filename"]).parts[-2])]  # `<index>/<name>`, however ruff spells the root
        row, line = sources[path].place(hit["location"]["row"])
        code = hit["code"] or "invalid-syntax"  # ruff 0.12.0 to 0.12.7 name a file that does not parse with null
        offered = hit.get("fix") or {}
        fix, label = offered.get("message"), offered.get("applicability")
        url = hit.get("url")
        said = Diagnostic(
            row, line, once(code), once(hit["message"]), fix and once(fix), label and once(label), url and once(url)
        )
        found.setdefault(path, []).append(said)
    return found


def identity() -> str:
    """The ruff a review would run, as what its result depends on: its path and its own `--version`, or why it said
    none. Measured by review: a review `--cache` kept with one ruff was served after another that found a major in
    the change, its key the same."""
    ruff = beside() or on_path("ruff")
    if ruff == "ruff":  # found nowhere: the name alone is never run, which Windows seeks in the current folder first
        return "ruff: not on PATH"
    try:
        return f"{ruff} {''.join(_ruff([ruff, '--version'], tempfile.gettempdir())).strip()}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{ruff}: {type(exc).__name__}"


def beside() -> str | None:
    """The ruff installed with the reviewer, in its own interpreter's scripts folder: what an installed `adk-review`
    runs, its environment never activated, so never on PATH. None where there is none, and PATH is asked."""
    found = Path(sysconfig.get_path("scripts")) / ("ruff.exe" if os.name == "nt" else "ruff")
    return str(found) if found.is_file() else None


def _suffix(path: str) -> str:
    """The suffix ruff reads a copy of `path` under: a stub's own, since ruff holds a stub to a stub's rules, else
    `.py`, a `.pyw` file or a script with no suffix among them."""
    return ".pyi" if path.endswith(".pyi") else ".py"
