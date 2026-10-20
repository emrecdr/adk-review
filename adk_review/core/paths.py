"""A path, whether a glob names it: the operator's skip list's question of each changed file, and a rule's of a path,
through the door; and the file a symbolic link runs as. Whether a path is a test is `blast.py`'s `is_test`, which the
blast radius asks too."""

from __future__ import annotations

import bisect
import functools
import unicodedata
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
#: 39 links whose paths ran 2,000 parts through `a/..`, took 78,041 a link and 33 s in all, which stops here in 49 ms.
LINK_STEPS = 200_000


class UnfollowedError(Exception):
    """Where a link leads is not known: a link on the way was not read, a file inside a submodule, one only a file
    system blind to case or to Unicode's forms finds, several a copy's place decides between, or the review's
    `LINK_STEPS` ran out."""


class Links:
    """The head as a symbolic link reads it: its plain files, each link with the path it names, None for one not read,
    and its submodules, every folder read off the paths it holds. A link's text is read as git stores it, a POSIX
    path: `\\` and `C:` are a name's letters, and only a leading `/` is the top of the file system. One a review, its
    steps counted across every link it follows."""

    def __init__(self, files: Iterable[str], links: Mapping[str, str | None], modules: Iterable[str] = ()) -> None:
        self.files = set(files)
        self.links = links
        self.modules = set(modules)
        #: Every path, sorted: a folder is read off the paths it holds where it is asked, never kept. Measured by
        #: review: each folder of each path kept as a string of its own, 200 paths 2,000 folders deep took a gigabyte.
        self.paths = sorted({*self.files, *links, *self.modules})
        self.steps = LINK_STEPS

    def follow(self, path: str) -> str | None:
        """The plain file `path`, a link, runs as: each part followed in turn as a system follows it, a link on the
        way read from the folder it lies in and a `..` climbing from where the link before it led, up to `LINK_HOPS`
        links. A path out of the repository is read as coming back in: a name over the top as the checkout's own
        folder, and a path from the top of the file system at every depth a copy could lie at, a deployed `/app`
        say. None where it names no file here: a folder, nothing, a file read as a folder, a loop, or a path that
        stays out. `UnfollowedError` where what it runs is not known. Read by review: a link looked up whole and made
        plain as text first missed links to folders and read `lib/../notes.txt` as `notes.txt`; and a path out of
        the repository, one only a file system blind to case finds and one into a submodule each named no file, and
        the pre-push hook's profile approved what a clone in its default folder, a Mac or a submodule's checkout
        runs."""
        found = self._walk(self._stacked(path), hops=0, above=0)
        if len(found) > 1:
            raise UnfollowedError(f"leads back into the repository at {len(found)} files, as where a copy lies decides")
        return next(iter(found), None)

    def _walk(self, parts: list[str], *, hops: int, above: int) -> set[str]:
        """The plain files the stacked `parts` lead to, from the repository's top, or `above` folders over it."""
        folder, ends = "", []  # where the walk stands, and where each folder on the way to it ends in it
        while parts:
            part = parts.pop()
            if part in ("", "."):
                continue
            if part == "..":
                if above or not ends:
                    above += 1  # out over the top
                else:
                    ends.pop()
                    folder = folder[: ends[-1]] if ends else ""
            elif above:  # a name over the top, read as the way back down: a checkout's own folder, named as it says
                above -= 1
            elif (here := self._at(folder, part)) in self.links:
                if hops == LINK_HOPS:
                    return set()  # a loop, as a system reads one past its forty
                hops += 1
                if (named := self._named(here)).startswith("/"):
                    return self._anywhere([*parts, *self._stacked(named)], named.count("/") + 1, hops)
                parts.extend(self._stacked(named))
            elif here in self.modules or _holds(self.paths, here):
                folder = here
                ends.append(len(here))
            else:
                return self._ended(here, parts)
        return set()  # a folder, the top, or a path that stays out

    def _at(self, folder: str, part: str) -> str:
        """The path `part` names in `folder`, which a submodule's own files never are here."""
        if folder in self.modules:
            raise UnfollowedError(f"names {folder}/{part}, in a submodule: another repository's, not read here")
        return f"{folder}/{part}" if folder else part

    def _named(self, link: str) -> str:
        if (named := self.links[link]) is None:
            raise UnfollowedError(f"the link {link} was not read")
        return named

    def _anywhere(self, parts: list[str], depths: int, hops: int) -> set[str]:
        """What a path from the top of the file system, stacked in `parts`, leads to with the repository at each of
        `depths` folders down: a deployed copy's place, which the branch knows and the review does not."""
        return set().union(*(self._walk(self._copied(parts), hops=hops, above=depth) for depth in range(depths)))

    def _ended(self, here: str, parts: list[str]) -> set[str]:
        """Where a walk stops at `here`, no link, folder or submodule: a file, where nothing is left to follow."""
        if here in self.files:
            return set() if parts else {here}
        if self._near(here):
            raise UnfollowedError(f"names {here}, which only a file system blind to case or to Unicode's forms finds")
        return set()

    def _stacked(self, path: str) -> list[str]:
        """`path`'s parts as a stack, the next last, each counted before any list of them is made."""
        self._spend(path.count("/") + 1)
        return path.split("/")[::-1]

    def _copied(self, parts: list[str]) -> list[str]:
        self._spend(len(parts))
        return [*parts]

    def _spend(self, steps: int) -> None:
        self.steps -= steps
        if self.steps < 0:
            raise UnfollowedError(f"past the {LINK_STEPS:,} parts of a path a review follows its links through")

    @functools.cached_property
    def _folded(self) -> tuple[frozenset[str], list[str]]:
        """Every path as a file system blind to case and to Unicode's forms reads it, macOS's by default and Windows'
        for case, made the first time a path is not found as spelled: the set, and the same sorted."""
        folded = sorted(_fold(path) for path in self.paths)
        return frozenset(folded), folded

    def _near(self, here: str) -> bool:
        """Whether `here`, found nowhere as spelled, is a path or a folder where case and Unicode's forms go unread."""
        paths, ordered = self._folded
        return (key := _fold(here)) in paths or _holds(ordered, key)


def _holds(paths: list[str], folder: str) -> bool:
    """Whether a path of the sorted `paths` lies under `folder`: git lists no folder, only what it holds."""
    under = f"{folder}/"
    at = bisect.bisect_left(paths, under)
    return at < len(paths) and paths[at].startswith(under)


def _fold(path: str) -> str:
    """`path` as a file system blind to case and to Unicode's forms reads it: Unicode's canonical caseless match."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def matching(paths: Iterable[str], *globs: str) -> list[str]:
    """The paths one of `globs` names, sorted: `matching(tree.paths, "src/**/*.py")` is every Python file under
    `src/` at the head, `matching(file.changed, "tests/")` every changed file under `tests/`."""
    return sorted(path for path in paths if matches(path, *globs))
