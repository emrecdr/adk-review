"""A function a change made hard to follow: one it touched that now stands above the bar, and that it raised.

The measure is SonarSource's cognitive complexity, which counts each break in a straight reading and how deep it
stands, where McCabe's counts branches alone: a function of flat early returns and one of the same branches nested six
deep read alike to `lint`'s C901, and nothing alike to a person. Measured before this rule was written: of this
repository's 43 functions at 15 or more, ruff named none at its defaults.

Weighed against what the file held before, so a function found hard and left as hard is not the change's to answer
for. Measured over four projects' last 150 commits each: at a bar of 15, 6 of 15 alerts read were marginal, a flat
ladder of checks or a new function at 15 or 16; above 20, 2 of 8. This rule over the same 599 commits, read as a
review reads a commit: 87 functions in 62 of them, 3 in this repository's 150.
"""

from __future__ import annotations

import ast
from collections import Counter

from ..rule_helpers import ChangedFile, FileRule, cognitive, is_test, parse

#: Above this a function is named: the bar this repository's own SonarQube setup holds a function to.
BAR = 20
#: And only where the change raised it by this much: a line added to a function found hard is not what made it so.
RAISED = 5


class CognitiveComplexity(FileRule):
    """A function is read far more often than it is written, and each level of nesting is one more thing its reader
    holds in mind."""

    id = "cognitive-complexity"
    severity = "minor"
    cleanup = True  # how the code reads, never what it does
    title = "a function this change made hard to follow"
    fix = "Split it. Return early where a check fails, and move a nested block into a function of its own."
    bad = (
        (
            "src/work.py",
            "def work(rows):\n"
            "    for row in rows:\n"
            "        if row:\n"
            "            for cell in row:\n"
            "                if cell:\n"
            "                    while cell:\n"
            "                        if cell.done and cell.kept:\n"
            "                            cell = cell.next\n",
        ),
    )
    good = (
        ("src/work.py", "def work(rows):\n    for row in rows:\n        if not row:\n            continue\n"),
        ("src/work.py", "def work(row):\n    return [cell for cell in row if cell]\n"),
    )

    def check(self, file: ChangedFile) -> list[tuple[int, str]]:
        """Each function the change touched that stands above `BAR` and that it raised by `RAISED` or more, at its
        `def`, with what it reads at now and read at before."""
        if not file.path.endswith(".py") or is_test(file.path) or file.before is None or isinstance(file.module, str):
            return []
        base = parse(file.before) if file.before else None
        if file.before and base is None:
            return []  # a base Python cannot read: nothing to weigh the change against
        before = dict(_readers(base))
        touched = (
            (name, function)
            for name, function in _readers(file.module)
            if not file.added.isdisjoint(range(function.lineno, (function.end_lineno or function.lineno) + 1))
        )
        return [said for name, function in touched if (said := _raised(name, function, before)) is not None]


def _raised(
    name: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    before: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> tuple[int, str] | None:
    """What to say of `function`, at its `def`, where the change raised it past the bar, and None where it did not."""
    now, was = _score(function), _score(before.get(name))
    if now is None or now <= BAR or (name in before and was is None):
        return None
    # A function renamed or moved, its body as it was, is not new: read by review, each was named as new.
    if name not in before and any(_body(other) == _body(function) for other in before.values()):
        return None
    if now - (was or 0) < RAISED:
        return None
    weighed = "" if was is None else f", {was} before this change"
    return function.lineno, f"`{name.partition('#')[0]}` reads at cognitive complexity {now}{weighed}"


def _score(function: ast.FunctionDef | ast.AsyncFunctionDef | None) -> int | None:
    """What `function` reads at, None for none and for one whose expressions nest past what Python walks: read by
    review, a string joined from 400 parts ended the rule, and the review a hole."""
    if function is None:
        return None
    try:
        return cognitive(function)
    except RecursionError:
        return None


def _body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A function's body as written, whatever it is named and wherever it stands."""
    return ast.dump(ast.Module(body=function.body, type_ignores=[]))


def _readers(module: ast.Module | None) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Each function a reader meets on its own, by its dotted name, in the order written: a module's, and a class's
    methods, under an `if` or a `try` too. One inside a function is that function's part, and counts there. Two of one
    name, a property and its setter, are told apart by their order, `x` and `x#2`: read by review, the setter's
    score stood for the getter's. None of a module the parser refused."""
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    pending: list[tuple[ast.AST, str]] = [] if module is None else [(module, "")]
    while pending:
        node, prefix = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((prefix + child.name, child))
            elif isinstance(child, ast.ClassDef):
                pending.append((child, f"{prefix}{child.name}."))
            elif isinstance(child, ast.stmt):
                pending.append((child, prefix))
    seen: Counter[str] = Counter()
    keyed = []
    for name, function in sorted(found, key=lambda item: item[1].lineno):
        seen[name] += 1
        keyed.append((name if seen[name] == 1 else f"{name}#{seen[name]}", function))
    return keyed
