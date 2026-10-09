"""A whole project's health, measured by code alone: what the review read, how large the source and the tests are, how
the source's function lines spread over McCabe complexity, and what each rule found, in source and in tests apart.

Never part of the verdict. Measured on this repository's own whole scan, raw counts misled: long lines against a
width the project does not use were most of lint's findings, the tests' planted keys read as blockers, and a
project's own rules counted against a generic view. So the measures are kept apart rather than summed, test files
apart from source, and a rule's findings by severity, never blended into one number.

One grade, maintainability's: the Software Improvement Group's published rating of a complexity risk profile, given
only where most of the source parsed. Measured on twelve real Python projects, it told them apart, from A to D, where
a grade from the worst finding did not: every one read C or E on security, and eleven read C on correctness; nor
did narrower ones, confirmed exposures being documentation examples and defect codes a framework's own idioms.

Complexity is McCabe's as ruff's C901 counts it, read from Python's own syntax tree, so the report and the lint rule
name one number: measured on eleven real projects, counting each `and`, `or`, conditional expression and
comprehension clause too gave a different number for 14 to 45% of the functions, up to 193 a project past 10 where
lint said nothing, and moved six of the eleven grades. Each function is a unit of its own lines, one nested in it
apart, so no line is counted twice; ruff adds a nested function to the one around it as well. A stub, `.pyi`, holds
no code that runs, and is left out.
Shown for a review of every file at the head, where the files are the project rather than a change.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from .blast import is_test, parse
from .findings import RANK
from .profiles import rule_id
from .rules import Tree, is_python_text
from .text import lines_of
from .verdict import Sourced

#: McCabe complexity bands, as the Software Improvement Group's maintainability model reads them (Heitlager, Kuipers
#: and Visser, 2007): up to 10 a function is simple; past 10 it carries moderate risk, past 20 high, past 50 very high.
BANDS: tuple[tuple[int | None, str], ...] = ((10, "1-10"), (20, "11-20"), (50, "21-50"), (None, "over 50"))
#: How many of the most complex source functions the report names.
WORST_SHOWN = 5
#: SIG's rating of a complexity risk profile (Heitlager, Kuipers and Visser, 2007): each grade, and the most it allows
#: of the source's function lines in the moderate (11-20), high (21-50) and very high (over 50) bands, in percent;
#: past D, E.
GRADES: tuple[tuple[str, float, float, float], ...] = (
    ("A", 25, 0, 0),
    ("B", 30, 5, 0),
    ("C", 40, 10, 0),
    ("D", 50, 15, 5),
)
#: A grade needs most of what it grades: a partial scan reads as a healthier one. Measured: the reviewer's size budget
#: read 47% of one large project's files, and its profile then graded what it had read.
PARSED_AT_LEAST = 0.9
#: And enough of it: a share of a handful of functions says little of a project.
FILES_AT_LEAST = 10
#: What adds a path through a function's code, as ruff's C901 counts it: each `if` and `elif`, loop and `except`.
_BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Unit:
    """One function: its file, the line its `def` is on, its name, its complexity and the lines that are its own."""

    path: str
    line: int
    name: str
    complexity: int
    lines: int


@dataclass(frozen=True)
class Health:
    """A project's measures at its head: every file and those read whole; the Python source and the tests, each as
    files and lines read; the Python source files at the head and those parsed; the source's function lines in each
    complexity band; the source files that do not parse; and the most complex source functions."""

    files: int
    read: int
    source: tuple[int, int]
    tests: tuple[int, int]
    python: tuple[int, int]
    bands: tuple[tuple[str, int], ...]
    not_parsed: tuple[str, ...]
    worst: tuple[Unit, ...]


def _own(scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Every node of a module's or a function's body, and nothing of a function defined inside it, which is a unit of
    its own; a class defined inside it is its code, its methods units of their own."""
    stack: list[ast.AST] = list(scope.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _FUNCTIONS):
            stack.extend(ast.iter_child_nodes(node))


def _paths(node: ast.AST) -> int:
    """What one node of a function's own code adds to McCabe's complexity, as ruff's C901 counts it: one for each `if`
    and `elif`, loop and `except`, for a `try`'s `else`, and for each `case` but one that matches anything, `case _`
    or a bare name with no guard. An `and`, an `or`, a conditional expression, a comprehension, a lambda, a `with` and
    a loop's `else` add none; a function nested in it counts on its own."""
    if isinstance(node, _BRANCHES) or (isinstance(node, ast.Try | ast.TryStar) and node.orelse):
        return 1
    if isinstance(node, ast.Match):
        return sum(not _matches_anything(case) for case in node.cases)
    return 0


def _matches_anything(case: ast.match_case) -> bool:
    """`case _:` or `case name:` with no guard: the last way through a `match`, which ruff counts no more than an
    `else`."""
    return case.guard is None and isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None


def _span(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """The lines from a function's `def` to its last, its decorators left out."""
    return (function.end_lineno or function.lineno) - function.lineno + 1


def units(path: str, module: ast.Module) -> list[Unit]:
    """Every function in `module`, a method or a nested one included, each with its complexity, one and what its own
    code adds, and the lines that are its own: its span less the spans of the functions directly inside it. Each node
    is visited once, as the code of the function it belongs to: measured on ADK's source, the measure took 0.86 s with
    each function's nodes walked three times, and 0.45 s once."""
    found: list[Unit] = []
    stack = [node for node in _own(module) if isinstance(node, _FUNCTIONS)]  # the outermost; the nested join below
    while stack:
        node = stack.pop()
        count, inner = 1, 0
        for child in _own(node):
            if isinstance(child, _FUNCTIONS):  # a unit of its own, in turn
                inner += _span(child)
                stack.append(child)
            else:
                count += _paths(child)
        found.append(Unit(path, node.lineno, node.name, count, _span(node) - inner))
    return found


def band(value: int) -> str:
    """The complexity band `value` falls in."""
    return next(label for limit, label in BANDS if limit is None or value <= limit)


def measured(files: Sequence[Mapping], tree: Tree | None) -> Health:
    """A project's measures from the files a review read, each as the collector lists it, its text or None for one
    not read whole: each Python file's syntax tree is the head's own parse where the tree read it, else its text is
    parsed here. A test's lines are counted, its complexity is not: the measure is of the code a project ships."""
    lines = dict.fromkeys((label for _, label in BANDS), 0)
    sizes = {"source": [0, 0], "tests": [0, 0]}
    eligible = parsed = 0
    not_parsed: list[str] = []
    found: list[Unit] = []
    for f in files:
        path, text = f["path"], f["text"]
        # A file not read is Python by its suffix alone: its first line is unknown. A stub runs nothing.
        if path.endswith(".pyi") or not is_python_text(path, text):
            continue
        side = "tests" if is_test(path) else "source"
        eligible += side == "source"
        if text is None:
            continue
        sizes[side][0] += 1
        sizes[side][1] += len(lines_of(text))
        if side == "tests":
            continue
        module = tree.parsed[path] if tree is not None and path in tree.parsed else parse(text)
        if module is None:
            not_parsed.append(path)
            continue
        parsed += 1
        for unit in units(path, module):
            lines[band(unit.complexity)] += unit.lines
            found.append(unit)
    found.sort(key=lambda unit: (-unit.complexity, unit.path, unit.line))
    return Health(
        files=len(files),
        read=sum(f["text"] is not None for f in files),
        source=(sizes["source"][0], sizes["source"][1]),
        tests=(sizes["tests"][0], sizes["tests"][1]),
        python=(eligible, parsed),
        bands=tuple(lines.items()),
        not_parsed=tuple(sorted(not_parsed)),
        worst=tuple(found[:WORST_SHOWN]),
    )


def grade(health: Health) -> tuple[str | None, str]:
    """Maintainability's grade, SIG's rating of the source's complexity risk profile, and the profile it rated; None,
    and why, where too little of the source parsed, or too little of it was there, to grade."""
    eligible, parsed = health.python
    if parsed < FILES_AT_LEAST:
        return None, f"{parsed} Python source file(s) parsed; a grade needs {FILES_AT_LEAST}"
    if parsed < PARSED_AT_LEAST * eligible:
        return None, f"{parsed} of {eligible} Python source files parsed; a grade needs {PARSED_AT_LEAST:.0%}"
    total = sum(lines for _label, lines in health.bands)
    if not total:
        return None, "no function measured"
    shares = [100 * lines / total for _label, lines in health.bands[1:]]
    letter = next((g for g, *most in GRADES if all(s <= m for s, m in zip(shares, most, strict=True))), "E")
    moderate, high, very_high = shares
    return letter, f"{moderate:.1f}% moderate, {high:.1f}% high, {very_high:.1f}% very high"


def found_by_rule(kept: Iterable[Sourced]) -> dict[str, dict[str, dict[str, int]]]:
    """What each rule found, in source and in tests apart, by severity, rules in order of their ids. A guideline's
    findings are a model's reading of the code, not a measure of it, so they are left out."""
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for item in kept:
        if not item.source.startswith(rule_id("")):
            continue
        side = "tests" if is_test(item.finding.file) else "source"
        row = counts.setdefault(item.source, {"source": dict.fromkeys(RANK, 0), "tests": dict.fromkeys(RANK, 0)})
        row[side][item.finding.severity] += 1
    return dict(sorted(counts.items()))
