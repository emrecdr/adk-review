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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

#: A file that depends on a changed one is named; past this many, the rest are counted.
NAMES_SHOWN = 5
#: Measured on a namespace package, a folder with no `__init__.py`: its `typing.py` was read as imported by 81 files,
#: where 7 import it. `import typing` is the standard library's, wherever a root is inferred.
_STDLIB = frozenset(sys.stdlib_module_names)


@dataclass(frozen=True)
class Radius:
    """What depends on one changed file at the head: modules and tests, directly or through others."""

    path: str
    modules: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    deleted: bool = False
    #: Of those, the ones that import the file themselves, modules first: the call sites a change meets.
    direct: tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        """Python is parsed; any other file is named, and not measured."""
        return self.path.endswith(".py")

    @property
    def reach(self) -> int:
        return len(self.modules) + len(self.tests)


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
        for path in paths:
            parts = _module(path)
            if parts:
                self.exact[parts] = path
                for start in range(len(parts)):  # each root above the first package, where a short name is looked up
                    if PurePosixPath(*parts[:start]) in self.packages:
                        break
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
    modules: Mapping[str, ast.Module | None], changed: list[dict[str, str]], skipped: int = 0, *, listed: bool = True
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
    radii = []
    for entry in changed:
        path = entry["path"]
        if not path.endswith(".py"):
            radii.append(Radius(path))
            continue
        seen: set[str] = set()
        frontier = [path]
        while frontier:  # everything that imports the file, or imports something that does
            for dependent in dependents[frontier.pop()] - seen - {path}:
                seen.add(dependent)
                frontier.append(dependent)
        importers = dependents[path]  # every one also reached, so `direct` is a part of `modules` and `tests`
        modules, tests = tuple(sorted(seen - test_files)), tuple(sorted(seen & test_files))
        direct = (*sorted(importers - test_files), *sorted(importers & test_files))
        radii.append(Radius(path, modules, tests, deleted=path in deleted, direct=direct))
    # Widest first: the most modules importing it themselves, then the most reached. By reach alone, measured on ADK,
    # its `__init__.py`, which 613 modules import, came after 88 files reaching one module more, one imported once.
    radii.sort(key=lambda r: (not r.measured, -len(set(r.direct) - test_files), -len(r.modules), -len(r.tests), r.path))
    return Blast(tuple(radii), tuple(sorted(unparsed)), skipped, listed)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def describe(radius: Radius, floor: bool) -> str:
    """One line a guideline or a person reads: what depends on the file, by name, or why nothing was counted.

    `floor` is the run's `Blast.floor`: over a tree not read whole, a zero is never a leaf, deleted or not.
    """
    if not radius.measured:
        return "not measured: not a Python file"
    if not radius.reach:
        if floor:
            said = "nothing that was read imports it; the tree was not read whole"
        else:
            said = "nothing at the head imports it"
        if radius.deleted:
            return f"deleted by this change; {said}"
        return said if floor else f"a leaf: {said}"
    parts = [_count(len(radius.modules), "module")] if radius.modules else []
    parts += [_count(len(radius.tests), "test")] if radius.tests else []
    who = " and ".join(parts)
    # The counts are all that depends on it; the names are those that import it themselves, the call sites to read.
    shown = ", ".join(radius.direct[:NAMES_SHOWN])
    if len(radius.direct) > NAMES_SHOWN:
        shown += f", and {len(radius.direct) - NAMES_SHOWN} more"
    directly = f", {len(radius.direct)} of them directly" if len(radius.direct) < radius.reach else ""
    verb = "depends" if radius.reach == 1 else "depend"
    at_least = " (at least: the tree was not read whole)" if floor else ""
    if radius.deleted:
        return f"deleted by this change, yet {who} at the head still {verb} on it{directly}{at_least}: {shown}"
    return f"{who} {verb} on it at the head{directly}{at_least}: {shown}"
