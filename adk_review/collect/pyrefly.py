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

import json
import os
import re
import subprocess
import sysconfig
import tempfile
import threading
from collections.abc import Collection
from pathlib import Path, PurePath

from ..core.evidence import Change, unreleased
from ..core.rules import Diagnostic, is_python_text
from ..core.text import squash
from .git import on_path
from .ruff import Rows, laid_out

PYREFLY_TIMEOUT_S = 60
#: Pyrefly's answer is read up to this many bytes, and past them none of it is: the rule did not look. Measured by
#: review: files of `1 + ""` lines wrote 62 MB of JSON each, and twelve of them held 3.1 GB.
PYREFLY_ANSWER_MAX_BYTES = 64_000_000
#: The reviewer's own configuration, written beside the copies: nothing of the repository's is read. With none,
#: pyrefly falls back to its `basic` preset; and asked, an interpreter on PATH types a branch against the reviewer's own
#: installed packages, where a reviewed project's are not.
CONFIG = """\
search-path = ["."]
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
    except OSError as exc:  # a disk that refuses the write, full or read-only, or a pyrefly that cannot start
        return f"pyrefly could not run on the changed files ({exc.strerror or type(exc).__name__})"
    except _UnreadError as why:
        return str(why)
    except subprocess.CalledProcessError as exc:
        return f"pyrefly exited {exc.returncode}: {squash(exc.stderr) or 'no reason given'}"
    except subprocess.TimeoutExpired:
        return f"pyrefly did not answer within {PYREFLY_TIMEOUT_S} s"


def _run(
    pyrefly: str, texts: dict[str, str], added: dict[str, Collection[int]], kinds: frozenset[str]
) -> dict[str, list[Diagnostic]]:
    """Pyrefly over each file's text, written into a temporary directory: its diagnostics of `kinds` by path, or it
    raises for `diagnose` to name. A folder that will not go is left to the system's own cleaning, as ruff's is."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        targets, sources = laid_out(tmp, texts)
        Path(tmp, "pyrefly.toml").write_text(CONFIG, encoding="utf-8")
        argv = [pyrefly, "check", "-c", "pyrefly.toml", "--output-format", "json", "--summary=none"]
        answer, said = _answer([*argv, "--progress-bar", "no", *targets], tmp)
    if _UNTAKEN in said:  # the configuration is the rule's guarantee: one not taken whole is no look
        untaken = said.split(_UNTAKEN, 1)[1].splitlines()[0].strip()
        raise _UnreadError(f"pyrefly did not take the reviewer's configuration whole: no key {untaken}")
    try:
        errors = json.loads(answer)["errors"]
    except (ValueError, KeyError, TypeError) as exc:  # pyrefly's own reason is on stderr: no file matched, say
        raise _UnreadError(
            f"pyrefly's answer could not be read ({type(exc).__name__}): {squash(said) or 'no reason'}"
        ) from exc
    return _parsed(errors, sources, added, kinds)


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
        if error["name"] == "parse-error" and _GAVE_UP in error["description"]:
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
