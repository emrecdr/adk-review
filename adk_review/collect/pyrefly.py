"""Pyrefly over the changed Python files at the head commit, for a rule that names the kinds it reads: its diagnostics.

As ruff is (`collect/ruff.py`), each file, as the collector read it at the head, is written alone into a temporary
directory under a name of the rule's own, and nothing runs from, or writes into, the repository under review. Pyrefly
reads it there with the reviewer's configuration and never the repository's: the `default` preset, Python 3.13, no
installed package and no interpreter asked, so a name a file imports from beyond itself reads as `Any`; and no ignore
comment honoured, so a branch cannot silence the rule that judges it, as ruff runs `--ignore-noqa`. Measured over four
libraries' source and 60 planted edits: each file alone found 27 of 30 type defects on their own lines, as the whole
package did, at 0.1 s a file where the package took up to 5 s, with a third of the false alarms. What the diagnostics
mean, a kind kept and its severity, is the rule's, `types`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sysconfig
import tempfile
from pathlib import Path, PurePath

from ..core.evidence import Change
from ..core.rules import Diagnostic, is_python_text
from ..core.text import squash
from .git import on_path
from .ruff import laid_out

PYREFLY_TIMEOUT_S = 60
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
"""


def diagnose(evidence: Change, kinds: tuple[str, ...]) -> dict[str, list[Diagnostic]] | str:
    """Pyrefly's diagnostics of `kinds` on the Python files the change touched, as the collector read them at the head,
    by path, or, as a string, why pyrefly could not look."""
    pyrefly = beside() or on_path("pyrefly")  # by its full path: on Windows the current directory is searched first
    if pyrefly == "pyrefly":  # the name alone: found nowhere on PATH
        return "pyrefly is not on PATH. `uv run` puts the project's own there"
    texts = {
        f["path"]: f["text"]
        for f in evidence["files"]
        if f["text"] is not None and is_python_text(f["path"], f["text"])
    }
    if not texts:
        return {}
    try:
        return _run(pyrefly, texts, frozenset(kinds))
    except OSError as exc:  # a disk that refuses the write: full, or read-only
        return f"a changed file could not be written to type-check ({exc.strerror})"
    except (ValueError, KeyError, IndexError, TypeError) as exc:  # an answer of another shape than pyrefly's JSON
        return f"pyrefly's answer could not be read ({type(exc).__name__})"
    except subprocess.CalledProcessError as exc:
        return f"pyrefly exited {exc.returncode}: {squash(exc.stderr) or 'no reason given'}"
    except subprocess.TimeoutExpired:
        return f"pyrefly did not answer within {PYREFLY_TIMEOUT_S} s"


def _run(pyrefly: str, texts: dict[str, str], kinds: frozenset[str]) -> dict[str, list[Diagnostic]]:
    """Pyrefly over each file's text, written into a temporary directory: its diagnostics of `kinds` by path, or it
    raises for `diagnose` to name. Exit 1 is an answer, the errors it found; any other but 0 is a pyrefly that could
    not run. The caller's environment with every `PYREFLY_*` variable left out, as ruff's runs without `RUFF_*`."""
    with tempfile.TemporaryDirectory() as tmp:
        targets, sources = laid_out(tmp, texts)
        Path(tmp, "pyrefly.toml").write_text(CONFIG, encoding="utf-8")
        argv = [pyrefly, "check", "-c", "pyrefly.toml", "--output-format", "json", "--summary=none"]
        argv += ["--progress-bar", "no", *targets]
        env = {name: value for name, value in os.environ.items() if not name.startswith("PYREFLY")}
        done = subprocess.run(
            argv, cwd=tmp, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=PYREFLY_TIMEOUT_S
        )
        if done.returncode not in (0, 1):
            raise subprocess.CalledProcessError(done.returncode, argv, stderr=done.stderr)
        return _parsed(json.loads(done.stdout)["errors"], sources, kinds)


def _parsed(errors: list[dict], sources: dict[str, list[str]], kinds: frozenset[str]) -> dict[str, list[Diagnostic]]:
    """Pyrefly's errors of `kinds`, each under the path whose index names its folder, `sources` in the order
    `laid_out` numbered them, with the line it names as the file holds it."""
    paths = list(sources)
    found: dict[str, list[Diagnostic]] = {}
    for error in errors:
        if error["name"] not in kinds:
            continue
        path = paths[int(PurePath(error["path"]).parts[-2])]  # `<index>/<name>`, however pyrefly spells the root
        row, lines = error["line"], sources[path]
        line = lines[row - 1] if 0 < row <= len(lines) else ""
        found.setdefault(path, []).append(Diagnostic(row, line, error["name"], error["concise_description"]))
    return found


def identity() -> str:
    """The pyrefly a review would run, as what its result depends on: its path and its own `--version`, or why it said
    none, so a review kept under one pyrefly is never served under another."""
    pyrefly = beside() or on_path("pyrefly")
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
