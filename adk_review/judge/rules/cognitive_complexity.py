"""A function a change made hard to follow: one it touched that now stands above the bar, and that it raised.

The measure is SonarSource's cognitive complexity, which counts each break in a straight reading and how deep it
stands, where McCabe's counts branches alone: a function of flat early returns and one of the same branches nested six
deep read alike to `lint`'s C901, and nothing alike to a person. Measured before this rule was written: of this
repository's 43 functions at 15 or more, ruff named none at its defaults.

Weighed against what the file held before, so a function found hard and left as hard is not the change's to answer
for: against the function of its name, and a function renamed or moved inside its file, its body as it was, is not
new. Limits, named and not chased: one moved in from another file is read as new, since a file is weighed alone; so
is one renamed or moved and edited in one change; and where a change adds or removes a function of a name the file
holds more than one of, a property's setter or an overload, each is weighed against the hardest of that name, so a
hard one added beside a hard one is not named.

Measured over four projects' last 150 commits each: at a bar of 15, 6 of 15 alerts read were marginal, a flat
ladder of checks or a new function at 15 or 16; above 20, 2 of 8. This rule over the same 599 commits, read as a
review reads a commit: 87 functions in 62 of them, 3 in this repository's 150.
"""

from __future__ import annotations

import ast

from ..rule_helpers import ChangedFile, FileRule, cognitive, is_test, parse

type _Function = ast.FunctionDef | ast.AsyncFunctionDef
type _Named = dict[str, list[_Function]]

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
        now, was = _by_name(file.module), _by_name(base)
        return sorted(
            said
            for name, functions in now.items()
            for at, function in enumerate(functions)
            if not file.added.isdisjoint(range(function.lineno, (function.end_lineno or function.lineno) + 1))
            and (said := _raised(name, at, now, was)) is not None
        )


def _raised(name: str, at: int, now: _Named, was: _Named) -> tuple[int, str] | None:
    """What to say of `now[name][at]`, at its `def`, where the change raised it past the bar; None where it did not."""
    function = now[name][at]
    score = _score(function)
    if score is None or score <= BAR or (name not in was and _moved(function, now, was)):
        return None
    before = [_score(old) for old in _held(name, at, now, was)]
    if None in before:
        return None  # one of them too deep to walk: nothing to weigh the change against
    most = max((count for count in before if count is not None), default=None)
    if score - (most or 0) < RAISED:
        return None
    return function.lineno, f"`{name}` reads at cognitive complexity {score}" + (
        "" if most is None else f", {most} before this change"
    )


def _held(name: str, at: int, now: _Named, was: _Named) -> list[_Function]:
    """The base's functions `now[name][at]` is weighed against, the hardest deciding: the one in its place where the
    base holds as many of its name, a property and its setter each against its own; else every one of its name; and
    none for a name the base does not hold. Kept this plain on purpose: four reviews each taught it one more case and
    broke another, and no case of them came up in 599 real commits. The limits are the module's to name."""
    held = was.get(name, [])
    return [held[at]] if len(held) == len(now[name]) else held


def _moved(function: _Function, now: _Named, was: _Named) -> bool:
    """Whether `function`, under a name the base does not hold, is a base function renamed or moved, its body as it
    was: one whose own name no longer holds that body. A copy of a function the head still holds is new."""
    body = _body(function)
    return body is not None and any(
        _body(old) == body and all(_body(kept) != body for kept in now.get(name, ()))
        for name, olds in was.items()
        for old in olds
    )


def _score(function: _Function) -> int | None:
    """What `function` reads at, None for one whose expressions nest past what Python walks: read by review, a string
    joined from 400 parts ended the rule, and the review a hole."""
    try:
        return cognitive(function)
    except RecursionError:
        return None


def _body(function: _Function) -> str | None:
    """A function's body as written, whatever it is named and wherever it stands; None for one too deep to walk,
    which is the same as no other."""
    try:
        return ast.dump(ast.Module(body=function.body, type_ignores=[]))
    except RecursionError:
        return None


def _by_name(module: ast.Module | None) -> _Named:
    """Each function a reader meets on its own, under its dotted name, in the order written: a module's, and a class's
    methods, under an `if`, a `try`, an `except` or a `case` too. One inside a function is that function's part, and
    counts there. Two of one name stand together, a property and its setter. None of a module the parser refused."""
    found: list[tuple[str, _Function]] = []
    pending: list[tuple[ast.AST, str]] = [] if module is None else [(module, "")]
    while pending:
        node, prefix = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((prefix + child.name, child))
            elif isinstance(child, ast.ClassDef):
                pending.append((child, f"{prefix}{child.name}."))
            elif isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case)):
                pending.append((child, prefix))
    named: _Named = {}
    for name, function in sorted(found, key=lambda item: item[1].lineno):
        named.setdefault(name, []).append(function)
    return named
