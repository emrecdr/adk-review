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
from collections.abc import Iterator

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
        before = {name: cognitive(function) for name, function in _readers(parse(file.before))}
        found = []
        for name, function in _readers(file.module):
            if file.added.isdisjoint(range(function.lineno, (function.end_lineno or function.lineno) + 1)):
                continue
            now, was = cognitive(function), before.get(name)
            if now > BAR and now - (was or 0) >= RAISED:
                weighed = "new in this change" if was is None else f"{was} before this change"
                found.append((function.lineno, f"`{name}` reads at cognitive complexity {now}, {weighed}"))
        return found


def _readers(module: ast.Module | None) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Each function a reader meets on its own, by its dotted name: a module's, and a class's methods. One inside a
    function is that function's part, and counts there. None of a module the parser refused."""
    pending: list[tuple[ast.AST, str]] = [] if module is None else [(module, "")]
    while pending:
        node, prefix = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield prefix + child.name, child
            elif isinstance(child, ast.ClassDef):
                pending.append((child, f"{prefix}{child.name}."))
