"""A path, whether a glob names it: the operator's skip list's question of each changed file, and a rule's of a path,
through the door; and the file a symbolic link runs as. Whether a path is a test is `blast.py`'s `is_test`, which the
blast radius asks too."""

from __future__ import annotations

import bisect
import functools
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


def matches(path: str, *globs: str) -> bool:
    """Whether one of `globs` names `path`, each read as `.gitignore` reads one. A name alone, `*.py` or
    `Dockerfile*`, is the file's own name, wherever the file lies. With a `/`, `deploy/*.yaml` or `src/**/*.py`, it
    is the whole path from the top, `**` standing for any folders. Ending in `/`, `src/payments/`, it is everything
    under that folder. Letters match as written: `*.PY` is not `*.py`. A backslash reads as a slash, as Windows writes
    a path and `--path` reads one: read by review, `tests\\test_*.py` and `vendor\\` matched nothing."""
    return any(_matches(path, glob.replace("\\", "/")) for glob in globs)


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
#: for a rule that reads it whole and for each line rule that reads the link. Measured: an ordinary relative link took
#: 7, an absolute one 59, and a whole project's 52,000 icon links 260,000 in 171 ms; a branch of 2,000 links, each
#: heading a chain of 39 links whose paths ran 2,000 parts through `a/..`, took 78,041 a link and 42 s in all, which
#: stops here in 0.6 s.
LINK_STEPS = 2_000_000


class UnfollowedError(Exception):
    """Where a link leads is not known: a link on the way was not read, a file inside a submodule, a name a file system
    blind to case or to Unicode's forms finds more than one spelling of, several files a copy's place decides between,
    or the review's `LINK_STEPS` ran out."""


@dataclass
class _At:
    """Where a walk stands: the parts still to walk, stacked, the next last; the links it has followed; the folders
    the repository lies below the top of the file system, None until its path leaves the repository; how many folders
    over the repository's top it stands; and the folder it stands in, with where each folder on the way ends in it."""

    parts: list[str]
    hops: int
    depth: int | None
    above: int = 0
    folder: str = ""
    ends: list[int] = field(default_factory=list)


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
        links. A path that leaves the repository is walked for every depth the repository could lie at below the top
        of the file system, a clone's folder or a deployed copy's, `/app` say, each name over the repository's top
        read as the way back down to it, and the top of the file system its own `..`; and a name git holds no
        spelling of is read as a file system blind to case and to Unicode's forms finds it, as a Mac's does. None
        where it names no file here: a folder, nothing, a file read as a folder, a loop, or a path that stays out.
        `UnfollowedError` where what it runs is not known. Read by review, over four rounds: a link looked up whole
        and made plain as text first; a path out of the repository, one only a Mac finds and one into a submodule,
        each naming no file; the top of the file system read as having folders over it; and a near-miss of case, a
        hole even where it named a folder."""
        found = self._walk(_At(self._stacked(path), hops=0, depth=None))
        if len(found) > 1:
            raise UnfollowedError(f"leads to {len(found)} files of the repository, as where a copy of it lies decides")
        return next(iter(found), None)

    def _walk(self, at: _At) -> set[str]:
        """The plain files the walk from `at` reaches."""
        while at.parts:
            part = at.parts.pop()
            if part in ("", "."):
                continue
            if part == "..":
                ended = self._climb(at)
            elif at.above:  # a name over the top, read as the way back down: a checkout's own folder, named as it says
                at.above -= 1
                continue
            else:
                ended = self._step(at, self._at(at.folder, part))
            if ended is not None:
                return ended
        return set()  # a folder, the top, or a path that stays out

    def _climb(self, at: _At) -> set[str] | None:
        """A `..`: up a folder, or out over the repository's top, which the top of the file system's own stops."""
        if at.ends and not at.above:
            at.ends.pop()
            at.folder = at.folder[: at.ends[-1]] if at.ends else ""
        elif at.depth is None:
            return self._each_depth([*at.parts, ".."], at.hops, at_root=False)
        else:
            at.above = min(at.above + 1, at.depth)
        return None

    def _step(self, at: _At, here: str) -> set[str] | None:
        """A name, `here`: a link followed, a folder entered, a file reached, or nothing."""
        if here in self.links:
            return self._through(at, here)
        if here in self.modules or _holds(self.paths, here):
            at.folder = here
            at.ends.append(len(here))
        elif here in self.files:
            return set() if at.parts else {here}
        elif (spelled := self._spelled(at.folder, here)) is not None:
            at.parts.append(spelled)  # the spelling git holds, which such a system finds as the name given
        else:
            return set()
        return None

    def _through(self, at: _At, link: str) -> set[str] | None:
        """Through `link`: the path it names, walked on from the folder it lies in or from the file system's top."""
        if at.hops == LINK_HOPS:
            return set()  # a loop, as a system reads one past its forty
        at.hops += 1
        if (named := self.links[link]) is None:
            raise UnfollowedError(f"the link {link} was not read")
        stacked = self._stacked(named)
        if named.startswith("/"):
            if at.depth is None:
                return self._each_depth([*at.parts, *stacked], at.hops, at_root=True)
            at.folder, at.ends, at.above = "", [], at.depth
        at.parts.extend(stacked)
        return None

    def _each_depth(self, parts: list[str], hops: int, *, at_root: bool) -> set[str]:
        """What the stacked `parts` lead to with the repository at each depth it could lie at below the top of the
        file system, from that top where `at_root`, else from the repository's own: the branch may know where a copy
        lies, and the review does not. As many depths as names are left, a name over the top for each."""
        depths = range(sum(part not in ("", ".", "..") for part in parts) + 1)
        return set().union(
            *(self._walk(_At(self._copied(parts), hops, depth, above=depth if at_root else 0)) for depth in depths)
        )

    def _at(self, folder: str, part: str) -> str:
        """The path `part` names in `folder`, which a submodule's own files never are here."""
        if folder in self.modules:
            raise UnfollowedError(f"names {folder}/{part}, in a submodule: another repository's, not read here")
        return f"{folder}/{part}" if folder else part

    def _spelled(self, folder: str, here: str) -> str | None:
        """The one name in `folder` that a file system blind to case and to Unicode's forms, a Mac's by default and
        Windows' for case, finds as `here`'s last part, where git holds it spelled another way: None where git holds no
        such name, `UnfollowedError` where it holds more than one. Only what lies in `folder` is asked, so the name
        found is one git holds there, and the walk goes on through it."""
        under, at = f"{folder}/" if folder else "", here.count("/")
        names = {path.split("/")[at] for path in self._near(_fold(here)) if path.startswith(under)}
        if len(names) > 1:
            raise UnfollowedError(f"names {here}, of which a file system blind to case finds {len(names)} spellings")
        return next(iter(names), None)

    def _near(self, key: str) -> Iterator[str]:
        """Each path whose folded form is `key`, or lies under it: the rows of `_folded` it reads, and no others."""
        rows = self._folded
        row = bisect.bisect_left(rows, (key,))
        while row < len(rows) and rows[row][0] == key:
            self._spend(1)
            yield rows[row][1]
            row += 1
        row = bisect.bisect_left(rows, (f"{key}/",))
        while row < len(rows) and rows[row][0].startswith(f"{key}/"):
            self._spend(1)
            yield rows[row][1]
            row += 1

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
    def _folded(self) -> list[tuple[str, str]]:
        """Every path as a file system blind to case and to Unicode's forms reads it, beside its spelling, sorted:
        made the first time a name is not found as spelled."""
        return sorted((_fold(path), path) for path in self.paths)


def _holds(paths: list[str], folder: str) -> bool:
    """Whether a path of the sorted `paths` lies under `folder`: git lists no folder, only what it holds."""
    under = f"{folder}/"
    at = bisect.bisect_left(paths, under)
    return at < len(paths) and paths[at].startswith(under)


def _fold(path: str) -> str:
    """`path` as a file system blind to case and to Unicode's forms reads it: Unicode's canonical caseless match."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def in_scope(path: str, scope: Sequence[str]) -> bool:
    """Whether a changed file is one of `scope`'s, or lies under one of its folders; no scope holds every file."""
    return not scope or any(path == folder or path.startswith(f"{folder}/") for folder in scope)


def in_review(path: str, scan: Sequence[str], third_party: Sequence[str]) -> bool:
    """Whether a review reads `path`: one `scan` names, Python's or a config's, every file where it names none, and
    one with no suffix, which may be a script that runs Python, as only its first line tells; and none under a folder
    `third_party` names, another project's code. Any other is ignored, never read or scanned."""
    named = not scan or matches(path, *scan) or not PurePosixPath(path).suffix
    return named and not matches(path, *third_party)


def matching(paths: Iterable[str], *globs: str) -> list[str]:
    """The paths one of `globs` names, sorted: `matching(tree.paths, "src/**/*.py")` is every Python file under
    `src/` at the head, `matching(file.changed, "tests/")` every changed file under `tests/`."""
    return sorted(path for path in paths if matches(path, *globs))
