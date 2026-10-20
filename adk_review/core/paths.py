"""A path, whether a glob names it: the operator's skip list's question of each changed file, and a rule's of a path,
through the door; and the file a symbolic link runs as. Whether a path is a test is `blast.py`'s `is_test`, which the
blast radius asks too."""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
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


#: How many links one path is followed through, as Linux follows them: past forty, a loop, and it names no file.
LINK_HOPS = 40
#: The parts of a path a review follows its links through, in all: past them, where a link leads is not known, a hole
#: for a rule that reads it whole. Measured: an ordinary link took 7; a branch of 2,000 links, each heading a chain of
#: links whose paths ran 2,000 parts through `a/..`, took 78,041 a link and 17 s in all, which stops here in 24 ms.
LINK_STEPS = 200_000


class UnfollowedError(Exception):
    """Where a link leads is not known: a link on the way was not read, or the review's `LINK_STEPS` ran out."""


class Links:
    """The head as a symbolic link reads it: its plain files, each link with the path it names, None for one not read,
    and every folder their paths hold. `others`, a submodule's path say, is no file and no link here, and still holds
    up the folders it lies in. One a review, its steps counted across every link it follows."""

    def __init__(self, files: Iterable[str], links: Mapping[str, str | None], others: Iterable[str] = ()) -> None:
        self.files = set(files)
        self.links = links
        self.folders: set[str] = set()
        for path in (*self.files, *links, *others):
            end = path.rfind("/")
            while end > 0 and (folder := path[:end]) not in self.folders:
                self.folders.add(folder)
                end = path.rfind("/", 0, end)
        self.steps = LINK_STEPS

    def follow(self, path: str) -> str | None:
        """The plain file `path`, a link, runs as: each part followed in turn as a system follows it, a link on the
        way read from the folder it lies in and a `..` climbing from where the link before it led, up to `LINK_HOPS`
        links. None where it names no file here: a folder, a submodule, nothing, a file read as a folder, a path out
        of the repository, or a loop. `UnfollowedError` where a link on the way was not read, or the steps ran out.
        Read by review: a link was looked up whole and made plain as text first, so one through a link to a folder
        named no file, and `lib/../notes.txt`, `lib` a link to `assets/pkg`, read as `notes.txt` where a system runs
        `assets/notes.txt`."""
        self._spend(path.count("/") + 1)  # each part counted as it is stacked, before any list of them is made
        parts = path.split("/")[::-1]  # a stack, the next part last
        folder, ends = "", []  # where the walk stands, and where each folder on the way to it ends in it
        hops = 0
        while parts:
            part = parts.pop()
            if part in ("", "."):
                continue
            if part == "..":
                if not ends:
                    return None  # out of the repository
                ends.pop()
                folder = folder[: ends[-1]] if ends else ""
                continue
            here = f"{folder}/{part}" if folder else part
            if here in self.links:
                if hops == LINK_HOPS:
                    return None  # a loop, as a system reads one past its forty
                hops += 1
                named = self.links[here]
                if named is None:
                    raise UnfollowedError(f"the link {here} was not read")
                if not named or named.startswith(("/", "\\")) or named[1:2] == ":":
                    return None  # out of the repository: absolute, or a drive's
                self._spend(named.count("/") + 1)
                parts.extend(reversed(named.split("/")))
            elif here in self.folders:
                folder = here
                ends.append(len(here))
            else:
                return here if here in self.files and not parts else None
        return None  # a folder, or the top

    def _spend(self, steps: int) -> None:
        self.steps -= steps
        if self.steps < 0:
            raise UnfollowedError(f"past the {LINK_STEPS:,} parts of a path a review follows its links through")


def matching(paths: Iterable[str], *globs: str) -> list[str]:
    """The paths one of `globs` names, sorted: `matching(tree.paths, "src/**/*.py")` is every Python file under
    `src/` at the head, `matching(file.changed, "tests/")` every changed file under `tests/`."""
    return sorted(path for path in paths if matches(path, *globs))
