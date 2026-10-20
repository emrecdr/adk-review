"""Blast radius: what at the head commit depends on each changed file, read from the syntax tree.

Review depth should follow blast radius. A change to a module half the tree
imports reaches all of it; a change nothing imports reaches nothing. So the
guidelines are told, per file, what depends on it, and the report opens with the
same list, widest first, for the person deciding where to read closely. The
verdict never reads it: severity decides, as before.

Counted by code, from Python's own parser. `ast.parse` reads a module; it
never runs one, and running the reviewed code is the one thing this reviewer
never does. An import is resolved the way the tree spells it:

- a relative import (`from .charge import Charge`) by the importing file's own
  folder, exactly;
- an absolute one (`from payments.charge import charge`) by the last parts of
  a file's path, so a `src/` layout resolves without knowing the path Python
  would search, and only where the folder above those parts is a root:
  Python imports a short name from a root, and neither a folder holding
  `__init__.py` nor any folder inside one is a root, so `from types import`
  lands on no `rules/types.py`, and `import openai` on no `llms/openai.py`
  in a package's folder that lacks an `__init__.py` of its own. The one
  exception is the importing file's own folder, a script's or pytest's root
  for its own files, when that is no package. A name the standard library
  holds (`typing`, `email.utils`) lands only on a file at the top: through a
  root or that folder, it was nearly always the standard library's import,
  so a script's rare shadow of one goes uncounted. Any other name two files
  could answer counts for both: the radius errs wide there, never narrow;
- and either way an import depends on the module it names and on every
  package on the way to it, because Python runs each package's `__init__.py`
  first: `from pkg.x import y` reaches `pkg/__init__.py` as well as `pkg/x.py`,
  and `from pkg import name` reaches `pkg/name.py` when there is one.

Checked against an exact resolver built on `importlib`, over a 164-file tool
with one import root: no import missed, and every edge the exact one lacked
was real, a test importing `conftest.py` from pytest's own root. Hostile
input is refused, never run: a megabyte of nested brackets, a null byte and
200 levels of indentation each came back unparsed within a millisecond.

What depends on a file counts whether it imports the file itself or imports
something that does; the files named are those that import it themselves,
the call sites a change meets, and they decide what is widest. Measured over
30 installed packages: in 23, most files reach over half the tree through
other files, one of two counts shared by nearly all, so a count of all
alone told the files apart by name; the median file has 1 to 4 importers of
its own. Tests are counted apart from modules: a test that
imports a file is coverage, not exposure. What was not read is never read as
nothing: a file that is not Python is not measured, and when the parser
refused a file, the collector skipped one or the tree could not be listed at
all, a count of zero says the tree was not read whole rather than calling the
file a leaf.
"""

from __future__ import annotations

import ast
import functools
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from .evidence import Entry

#: A file that depends on a changed one is named; past this many, the rest are counted.
NAMES_SHOWN = 5
#: A dotted name is read to its first parts, and a path looked up by its last, this many: measured by review, 100
#: files 1,000 folders deep took 20 s and 432 MB to index, every tail of every path a key.
_PARTS_READ = 32
#: The steps the radii take in all, one for each component of import cycles a walk from a changed file reaches, its
#: own included: past them a radius is not counted, and says so. Measured by review, a walk from each changed file
#: read a cycle of 4,000 modules again from each, 9.3 s, and litellm's whole project, 2,685 files, 5.0 s.
RADIUS_STEPS = 2_000_000
#: Measured on a namespace package, a folder with no `__init__.py`: its `typing.py` was read as imported by 81 files,
#: where 7 import it. `import typing` is the standard library's, wherever a root is inferred.
_STDLIB = frozenset(sys.stdlib_module_names)


@dataclass(frozen=True)
class Radius:
    """What depends on one changed file at the head: how many modules and tests, directly or through others, and those
    that import it themselves, by name. Counted, never listed: a cycle of thousands of modules listed every one of
    them for each, measured by review at 2.15 GB, where only the counts and the names of its importers are read."""

    path: str
    modules: int = 0
    tests: int = 0
    deleted: bool = False
    #: Of those, the ones that import the file themselves, modules first: the call sites a change meets.
    direct: tuple[str, ...] = ()
    #: False where the radii's steps ran out before it: its importers are named, and what imports them not counted.
    counted: bool = True

    @property
    def measured(self) -> bool:
        """Python is parsed; any other file is named, and not measured."""
        return self.path.endswith(".py")

    @property
    def reach(self) -> int:
        return self.modules + self.tests


@dataclass(frozen=True)
class Blast:
    """Every changed file's radius, widest first — the most modules importing it, then the most reached — and what of
    the tree was not read."""

    radii: tuple[Radius, ...]
    unparsed: tuple[str, ...]  # files the parser refused: what they import is not counted
    skipped: int  # files the collector did not read: over a size cap, past a timeout, or not in a partial clone
    listed: bool = True  # False when the head's tree could not be listed at all, so nothing was read
    #: False when no check that ran reads the head whole — no tree rule, no guideline — so it was not read: measured,
    #: reading and parsing it took 88% of evidence collection on a 625-file tree. Never a floor and never a leaf.
    asked: bool = True

    @property
    def floor(self) -> bool:
        """The tree was not read whole: every count is a floor, and a zero is never a leaf. One fact per run."""
        return bool(self.unparsed or self.skipped or not self.listed)


def _module(path: str) -> tuple[str, ...]:
    """A file's dotted parts: `src/payments/charge.py` is `(src, payments, charge)`; a package is its folder."""
    parts = PurePosixPath(path).with_suffix("").parts
    return parts[:-1] if parts and parts[-1] == "__init__" else parts


@functools.lru_cache(maxsize=4096)
def is_test(path: str) -> bool:
    """A test by the conventions pytest and unittest share: under `tests/` or `test/`, or named `test_*`, `*_test`,
    `conftest`. Cached: four line rules ask it for every added line, and measured over 180,000 lines, the answer for a
    path already seen was 345 ms of each rule's 360."""
    p = PurePosixPath(path)
    in_a_tests_folder = bool({"tests", "test"} & set(p.parts[:-1]))
    return in_a_tests_folder or p.name.startswith("test_") or p.stem.endswith("_test") or p.name == "conftest.py"


def parse(source: str) -> ast.Module | None:
    """The module's syntax tree, or None for one the parser refuses: a syntax error, a null byte, nesting too deep."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def parsed(sources: Mapping[str, str]) -> dict[str, ast.Module | None]:
    """Every source as Python reads it, by path, None for one the parser refuses: parsed once, at the head's read,
    for the blast radius and the tree rules alike."""
    return {path: parse(source) for path, source in sources.items()}


class _Tree:
    """The files an import may name, indexed both ways an import names one."""

    def __init__(self, paths: set[str]) -> None:
        self.exact: dict[tuple[str, ...], str] = {}
        self.by_tail: dict[tuple[str, ...], set[str]] = defaultdict(set)
        self.packages = {PurePosixPath(p).parent for p in paths if PurePosixPath(p).name == "__init__.py"}
        trie: dict = {}  # the packages' folders, a part a level, `None` marking one that is a package
        for folder in self.packages:
            node = trie
            for part in folder.parts:
                node = node.setdefault(part, {})
            node[None] = True
        for path in paths:
            parts = _module(path)
            if parts:
                self.exact[parts] = path
                # Each root above the first package, where a short name is looked up, and a tail no longer than an
                # import is read: the first package found in one walk down the path's folders.
                first, node = len(parts), trie
                for start in range(len(parts)):
                    if None in node:
                        first = start
                        break
                    node = node.get(parts[start], {})
                for start in range(max(0, len(parts) - _PARTS_READ), first):
                    self.by_tail[parts[start:]].add(path)

    def absolute(self, importer: str, dotted: str | None, names: list[str]) -> set[str]:
        """What `import a.b` or `from a.b import c` names, by the last parts of a path, and from the importing file's
        own folder when that is no package: a script's folder, or pytest's root for its own files."""
        base = tuple(dotted.split(".")) if dotted else ()
        if base and base[0] in _STDLIB:  # the standard library's, unless a file at the top stands in for it
            return _resolve(self.exactly, base, names)
        folder = PurePosixPath(importer).parent
        own = None if folder in self.packages else folder.parts

        def lookup(key: tuple[str, ...]) -> set[str]:
            found = self.by_tail.get(key, set())
            return found if own is None else found | self.exactly((*own, *key))

        return _resolve(lookup, base, names)

    def relative(self, importer: str, level: int, dotted: str | None, names: list[str]) -> set[str]:
        """What `from .x import y` names, by the importing file's own folder: exact, never by a path's tail."""
        folder = PurePosixPath(importer).parent.parts
        if level - 1 > len(folder):
            return set()
        base = folder[: len(folder) - (level - 1)] + (tuple(dotted.split(".")) if dotted else ())
        return _resolve(self.exactly, base, names)

    def exactly(self, key: tuple[str, ...]) -> set[str]:
        """The one file `key` names from the tree's top, or none."""
        return {self.exact[key]} if key in self.exact else set()


def _resolve(lookup: Callable[[tuple[str, ...]], set[str]], base: tuple[str, ...], names: list[str]) -> set[str]:
    """The files an import depends on: every package on the way to `base`, `base` itself, and each name that is a
    submodule of it. One rule for both lookups; measured before it, a changed `pkg/__init__.py` that three files
    reached through `pkg.x` was called a leaf."""
    found: set[str] = set()
    base = base[:_PARTS_READ]
    for depth in range(1, len(base) + 1):  # each package on the way runs its `__init__.py`; `base` is the last
        found |= lookup(base[:depth])
    for name in names:
        if name and name != "*":
            found |= lookup((*base, name))
    return found


#: The fields of a node that hold statements; no expression holds one.
_BODIES = ("body", "orelse", "finalbody", "handlers", "cases")


def _statements(module: ast.Module) -> Iterator[ast.AST]:
    """Every statement in `module` at any depth, and the handlers and cases that hold more: an import is always a
    statement, and statements are a tenth of a module's nodes. Measured on a 1,737-module head: `ast.walk` took 732 ms
    to find the imports, this 129 ms, the same ones."""
    stack: list[ast.AST] = list(module.body)
    while stack:
        node = stack.pop()
        yield node
        for field in _BODIES:
            stack.extend(getattr(node, field, ()))


def _imports(path: str, module: ast.Module, tree: _Tree) -> set[str]:
    """Every file in the tree `path` imports, itself excluded."""
    found: set[str] = set()
    for node in _statements(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= tree.absolute(path, alias.name, [])
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            if node.level:
                found |= tree.relative(path, node.level, node.module, names)
            else:
                found |= tree.absolute(path, node.module, names)
    return found - {path}


def blast_radius(
    modules: Mapping[str, ast.Module | None], changed: Sequence[Entry], skipped: int = 0, *, listed: bool = True
) -> Blast:
    """Each changed file's radius over the Python files at the head, `modules` as parsed by path, None for one the
    parser refused; `skipped` were not read, and `listed` is False when the tree could not be listed, so `modules`
    is empty for want of looking.

    A file the change deleted is still looked for: what at the head imports it
    now imports nothing, which is the widest radius of all.
    """
    deleted = {c["path"] for c in changed if c["status"] == "D" and c["path"].endswith(".py")}
    tree = _Tree(set(modules) | deleted)
    dependents: dict[str, set[str]] = defaultdict(set)
    unparsed: list[str] = []
    for path, module in modules.items():
        if module is None:
            unparsed.append(path)
            continue
        for target in _imports(path, module, tree):
            dependents[target].add(path)

    # Asked once per file, never per file reached: measured as --all reads 4,340 files, the radii took 20.6 s, now 7.4.
    test_files = {path for path in modules if is_test(path)}
    measured = [entry["path"] for entry in changed if entry["path"].endswith(".py")]
    component = _components(dependents, measured)
    reached = _Reached(dependents, component, test_files)
    radii = []
    for entry in changed:
        path = entry["path"]
        if not path.endswith(".py"):
            radii.append(Radius(path))
            continue
        importers = dependents.get(path, set())  # each one also reached, so `direct` is a part of the counts
        direct = (*sorted(importers - test_files), *sorted(importers & test_files))
        counts = reached.counts(component[path])  # what imports the file, or imports something that does
        if counts is None:
            radii.append(Radius(path, deleted=path in deleted, direct=direct, counted=False))
            continue
        modules_reached, tests_reached = counts
        own = (0, 1) if path in test_files else (1, 0)  # the file itself, in its own component, is no dependent
        radii.append(Radius(path, modules_reached - own[0], tests_reached - own[1], path in deleted, direct))
    # Widest first: the most modules importing it themselves, then the most reached. By reach alone, measured on ADK,
    # its `__init__.py`, which 613 modules import, came after 88 files reaching one module more, one imported once.
    radii.sort(
        key=lambda r: (not r.measured, not r.counted, -len(set(r.direct) - test_files), -r.modules, -r.tests, r.path)
    )
    return Blast(tuple(radii), tuple(sorted(unparsed)), skipped, listed)


def _components(edges: Mapping[str, set[str]], roots: Iterable[str]) -> dict[str, int]:
    """Each file a walk from `roots` along `edges` reaches, by the number of its component: files that reach each other
    share one, a cycle of imports read as one. Only what the roots reach is read."""
    found = _Tarjan(edges)
    for root in roots:
        found.walk(root)
    return found.component


class _Tarjan:
    """Tarjan's components, an edge a turn on a list of its own, so a cycle of any length costs no recursion."""

    def __init__(self, edges: Mapping[str, set[str]]) -> None:
        self.edges = edges
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.held: set[str] = set()
        self.component: dict[str, int] = {}

    def walk(self, root: str) -> None:
        work = [] if root in self.index else [self._enter(root)]
        while work:
            node, onward = work[-1]
            nxt = next(onward, None)
            if nxt is None:  # every edge of it read
                work.pop()
                if work:
                    self.low[work[-1][0]] = min(self.low[work[-1][0]], self.low[node])
                self._leave(node)
            elif nxt not in self.index:
                work.append(self._enter(nxt))
            elif nxt in self.held:
                self.low[node] = min(self.low[node], self.index[nxt])

    def _enter(self, node: str) -> tuple[str, Iterator[str]]:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        self.held.add(node)
        return node, iter(self.edges.get(node, ()))

    def _leave(self, node: str) -> None:
        """A component's root takes itself and all above it on the stack, numbered by it."""
        if self.low[node] == self.index[node]:
            member = ""
            while member != node:
                member = self.stack.pop()
                self.held.discard(member)
                self.component[member] = self.index[node]


class _Reached:
    """How many modules and tests each component reaches, its own members included: walked once a component, shared by
    every changed file in it, every walk spending the radii's `RADIUS_STEPS`. None past them."""

    def __init__(self, edges: Mapping[str, set[str]], component: Mapping[str, int], tests: set[str]) -> None:
        self.onward: dict[int, set[int]] = defaultdict(set)
        self.members: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # a component's modules and tests
        for node, number in component.items():
            self.members[number][node in tests] += 1
            self.onward[number].update(component[nxt] for nxt in edges.get(node, ()) if component[nxt] != number)
        self.known: dict[int, tuple[int, int]] = {}
        self.steps = RADIUS_STEPS

    def counts(self, start: int) -> tuple[int, int] | None:
        if start in self.known:
            return self.known[start]
        seen, frontier, totals = {start}, [start], [0, 0]
        while frontier:
            if self.steps <= 0:
                return None
            self.steps -= 1
            number = frontier.pop()
            totals[0] += self.members[number][0]
            totals[1] += self.members[number][1]
            for nxt in self.onward[number] - seen:
                seen.add(nxt)
                frontier.append(nxt)
        self.known[start] = (totals[0], totals[1])
        return self.known[start]


def _names(radius: Radius) -> str:
    """The files that import it themselves, `NAMES_SHOWN` of them named and the rest counted."""
    shown = ", ".join(radius.direct[:NAMES_SHOWN])
    return shown + (f", and {len(radius.direct) - NAMES_SHOWN} more" if len(radius.direct) > NAMES_SHOWN else "")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def describe(radius: Radius, floor: bool) -> str:
    """One line a guideline or a person reads: what depends on the file, by name, or why nothing was counted.

    `floor` is the run's `Blast.floor`: over a tree not read whole, a zero is never a leaf, deleted or not.
    """
    if not radius.measured:
        return "not measured: not a Python file"
    if not radius.counted:
        named = f". Direct importers: {_names(radius)}" if radius.direct else ""
        return f"not counted: the radii spent the {RADIUS_STEPS:,} steps they take in all before it{named}"
    if not radius.reach:
        if floor:
            said = "nothing read imports it. The review did not read the tree whole"
        else:
            said = "nothing at the head imports it"
        if radius.deleted:
            return f"deleted by this change. {said[:1].upper()}{said[1:]}"
        return said if floor else f"a leaf: {said}"
    parts = [_count(radius.modules, "module")] if radius.modules else []
    parts += [_count(radius.tests, "test")] if radius.tests else []
    who = " and ".join(parts)
    # The counts are all that depends on it; the names are those that import it themselves, the call sites to read.
    shown = _names(radius)
    directly = f", {len(radius.direct)} of them directly" if len(radius.direct) < radius.reach else ""
    verb = "depends" if radius.reach == 1 else "depend"
    at_least = " (at least: the tree was not read whole)" if floor else ""
    if radius.deleted:
        return f"deleted by this change, yet {who} at the head still {verb} on it{directly}{at_least}: {shown}"
    return f"{who} {verb} on it at the head{directly}{at_least}: {shown}"
