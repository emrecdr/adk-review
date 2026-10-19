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
from collections.abc import Sequence
from pathlib import Path, PurePath, PurePosixPath

from ..core.evidence import Change
from ..core.rules import Diagnostic, is_python_text
from ..core.text import squash
from .git import on_path

RUFF_TIMEOUT_S = 60


def diagnose(
    evidence: Change, select: Sequence[str], ignore: Sequence[str], line_length: int | None = None
) -> dict[str, list[Diagnostic]] | str:
    """Ruff's diagnostics on the Python files the change touched, as the collector read them at the head, by path —
    or, as a string, why ruff could not look. `select` and `ignore` are the reading rule's own codes: ruff reads no
    other configuration; `line_length` the width it holds `E501` to, None for ruff's own."""
    ruff = beside() or on_path("ruff")  # by its full path: on Windows the current directory is searched first
    if ruff == "ruff":  # the name alone: found nowhere on PATH
        return "ruff is not on PATH; `uv run` puts the project's own there"
    texts = {
        f["path"]: f["text"]
        for f in evidence["files"]
        if f["text"] is not None and is_python_text(f["path"], f["text"])
    }
    if not texts:
        return {}
    try:
        return _run(ruff, texts, select, ignore, line_length)
    except OSError as exc:  # a disk that refuses the write: full, or read-only
        return f"a changed file could not be written to lint ({exc.strerror})"
    except (ValueError, KeyError, IndexError, TypeError) as exc:  # an answer of another shape than ruff's JSON
        return f"ruff's answer could not be read ({type(exc).__name__})"
    except subprocess.CalledProcessError as exc:  # ruff's cause sits on no fixed line, so all it said goes, flat
        return f"ruff exited {exc.returncode}: {squash(exc.stderr) or 'no reason given'}"
    except subprocess.TimeoutExpired:
        return f"ruff did not answer within {RUFF_TIMEOUT_S} s"


def _run(
    ruff: str, texts: dict[str, str], select: Sequence[str], ignore: Sequence[str], line_length: int | None
) -> dict[str, list[Diagnostic]]:
    """Ruff over each file's text as it stands at the head, written into a temporary directory: its diagnostics by
    path, or it raises — ruff failing, or past its timeout — for `diagnose` to name. Under `--exit-zero` a diagnostic
    never fails ruff, so any other exit is a ruff that could not run."""
    with tempfile.TemporaryDirectory() as tmp:
        targets, sources = _laid_out(tmp, texts)
        # Every file by name, never a folder: ruff's default excludes skipped a `.venv/` or `node_modules/` a branch
        # had changed, never a file it is named. `--ignore-noqa`: an inline noqa comment in the branch silenced it.
        # `--no-cache`: nothing is written beside the files, which Windows could hold open when the folder goes.
        argv = [ruff, "check", "--no-fix", "--no-cache", "--isolated", "--exit-zero", "--ignore-noqa"]
        argv += ["--select", ",".join(select), *(["--ignore", ",".join(ignore)] if ignore else [])]
        argv += ["--line-length", str(line_length)] if line_length else []
        argv += ["--output-format", "json", *targets]
        return _parsed(_ruff(argv, tmp), sources)


def _laid_out(folder: str, texts: dict[str, str]) -> tuple[list[str], dict[str, list[str]]]:
    """Each file written into `folder`, and the names ruff is given for them, with each file's lines as ruff counts
    them, by path."""
    sources: dict[str, list[str]] = {}
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
        # Split where ruff counts a row: `str.splitlines` breaks at a form feed or U+2028 too, and measured, the
        # quote after one was the line above ruff's.
        sources[path] = re.split(r"\r\n?|\n", text)
    return targets, sources


def _ruff(argv: list[str], cwd: str) -> str:
    """The one place ruff runs, as `git._run` is git's: its JSON on stdout, or `CalledProcessError` or
    `TimeoutExpired`. The caller's environment with every `RUFF_*` variable left out, as git's runs without `GIT_*`:
    measured, `RUFF_OUTPUT_FILE`, set for another step of a pipeline, sent the report to that file, and stdout read
    empty."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("RUFF_")}
    done = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUFF_TIMEOUT_S,
        check=True,
    )
    return done.stdout


def _parsed(stdout: str, sources: dict[str, list[str]]) -> dict[str, list[Diagnostic]]:
    """Ruff's JSON read back: each hit under the path whose index names its folder, `sources` in the order `_laid_out`
    numbered them, with the line it names as the file holds it; `ValueError`, `KeyError`, `IndexError` or `TypeError`
    for an answer of another shape."""
    paths = list(sources)
    found: dict[str, list[Diagnostic]] = {}
    for hit in json.loads(stdout):
        path = paths[int(PurePath(hit["filename"]).parts[-2])]  # `<index>/<name>`, however ruff spells the root
        row = hit["location"]["row"]
        code = hit["code"] or "invalid-syntax"  # ruff 0.12.0 to 0.12.7 name a file that does not parse with null
        lines = sources[path]
        line = lines[row - 1] if 0 < row <= len(lines) else ""
        offered = hit.get("fix") or {}
        fix, label = offered.get("message"), offered.get("applicability")
        found.setdefault(path, []).append(Diagnostic(row, line, code, hit["message"], fix, label))
    return found


def identity() -> str:
    """The ruff a review would run, as what its result depends on: its path and its own `--version`, or why it said
    none. Measured by review: a review `--cache` kept with one ruff was served after another that found a major in
    the change, its key the same."""
    ruff = beside() or on_path("ruff")
    try:
        return f"{ruff} {_ruff([ruff, '--version'], tempfile.gettempdir()).strip()}"
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
