"""A path, whether a glob names it: the operator's skip list's question of each changed file, and a rule's of a path,
through the door; and the path a symbolic link names. Whether a path is a test is `blast.py`'s `is_test`, which the
blast radius asks too."""

from __future__ import annotations

import functools
import posixpath
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


def matches(path: str, *globs: str) -> bool:
    """Whether one of `globs` names `path`, each read as `.gitignore` reads one. A name alone, `*.py` or
    `Dockerfile*`, is the file's own name, wherever the file lies. With a `/`, `deploy/*.yaml` or `src/**/*.py`, it
    is the whole path from the top, `**` standing for any folders. Ending in `/`, `src/payments/`, it is everything
    under that folder. Letters match as written: `*.PY` is not `*.py`."""
    return any(_matches(path, glob) for glob in globs)


@functools.lru_cache(maxsize=4096)
def _matches(path: str, glob: str) -> bool:
    """One path against one glob. Cached, as `is_test` is: a line rule asks for every added line of a file."""
    if glob.endswith("/"):
        return path.startswith(glob)
    if "/" in glob:
        return PurePosixPath(path).full_match(glob)
    # The name off the path as git spells it, no `PurePosixPath` built: measured by review, the shipped skip list's
    # globs asked of every changed file took 45 µs a file that way and 6 µs this one, a path being new each time.
    return fnmatchcase(path.rpartition("/")[2], glob)


def linked(path: str, named: str) -> str | None:
    """The path a symbolic link at `path` names, `named` as git stores it, relative to the link's folder; None for
    one that names no path inside the repository: absolute, a drive's, or climbing out of it."""
    if not named or named.startswith(("/", "\\")) or named[1:2] == ":":
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), named))
    return None if joined == "." or joined.split("/")[0] == ".." else joined


def matching(paths: Iterable[str], *globs: str) -> list[str]:
    """The paths one of `globs` names, sorted: `matching(tree.paths, "src/**/*.py")` is every Python file under
    `src/` at the head, `matching(file.changed, "tests/")` every changed file under `tests/`."""
    return sorted(path for path in paths if matches(path, *globs))
