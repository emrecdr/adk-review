"""A module's syntax tree, read for a rule: the calls it makes, the functions it holds, what it imports and defines.

For a file rule over `file.module` and for a tree rule over `tree.modules()` alike. A file with no module — not
Python, or one that does not parse — has a sentence in its place, why there is none; every `…_in` reader here takes
that sentence too and finds nothing in it, so a rule never has to ask which it was given. Python's own `ast` reads
the code and never runs it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from fnmatch import fnmatchcase


def nodes_in[Node: ast.AST](code: ast.AST | str, *kinds: type[Node]) -> list[Node]:
    """Every node of one of `kinds` under `code`, in the order written: `nodes_in(file.module, ast.ExceptHandler)`.
    None where `code` is why a file has no module."""
    return _in_order(node for node in _walk(code) if isinstance(node, kinds))


def _walk(code: ast.AST | str) -> Iterator[ast.AST]:
    """Every node under `code`; none where `code` is why a file has no module."""
    return iter(()) if isinstance(code, str) else ast.walk(code)


def _in_order[Node: ast.AST](nodes: Iterable[Node]) -> list[Node]:
    """`nodes` as the file writes them, top to bottom: `ast.walk` goes level by level instead."""
    return sorted(nodes, key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))


def name_of(node: ast.AST) -> str:
    """A name as written, dotted: `open`, `requests.get`, `self.session.get`; a method of something with no name,
    as `"…".format` and `client().get` are, is `.format` and `.get`. Empty for what is no name at all."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{name_of(node.value)}.{node.attr}"
    return ""


def calls_in(code: ast.AST | str, *names: str) -> list[ast.Call]:
    """Every call under `code` to a function one of `names` names, each as the call is written and `*` for anything:
    `open`, `requests.get`, `*.execute` for a method of that name on anything. In the order written; a call's line
    is `call.lineno`, where it starts."""
    return _in_order(
        node
        for node in _walk(code)
        if isinstance(node, ast.Call) and any(fnmatchcase(name_of(node.func), name) for name in names)
    )


def argument(call: ast.Call, name: str, position: int | None = None) -> ast.expr | None:
    """What `call` passes for one parameter: by its `name`, or as the argument at `position`, counted from 0, before
    a spread one; where the call passes it neither way but spreads `*args` or `**options`, those, since they may hold
    it; None where the call does not pass it. Read by review, and reproduced: a `**options` was read first, so
    `patch("pkg._x", **options)` was read as patching whatever the options held."""
    named = next((keyword.value for keyword in call.keywords if keyword.arg == name), None)
    if named is not None:
        return named
    if position is not None:
        placed = call.args[: position + 1]
        if len(placed) > position and not any(isinstance(given, ast.Starred) for given in placed):
            return placed[position]
    spread = next((keyword.value for keyword in call.keywords if keyword.arg is None), None)
    return spread or next((given for given in call.args if isinstance(given, ast.Starred)), None)


def functions_in(code: ast.AST | str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function under `code`, methods and nested ones among them, in the order written."""
    return nodes_in(code, ast.FunctionDef, ast.AsyncFunctionDef)


def imports_in(code: ast.AST | str) -> list[tuple[int, str]]:
    """Every name `code` imports, as `(line, name)`, the name dotted from the top as written: `import os.path` is
    `os.path`, `from requests import Session` is `requests.Session`, `from os import *` is `os.*`, and a relative
    `from .charge import Charge` keeps its dots, `.charge.Charge`."""
    found: list[tuple[int, str]] = []
    for node in nodes_in(code, ast.Import, ast.ImportFrom):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        else:
            source = "." * node.level + (node.module or "")
            joined = source if source.endswith(".") or not source else f"{source}."
            found += [(node.lineno, f"{joined}{alias.name}") for alias in node.names]
    return found


def defined(module: ast.Module) -> set[str]:
    """The names a module defines at its top level: functions, classes, the names it assigns, one or several at a
    time, its type aliases, and the names it imports, which it exports as its own."""
    names: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(name.id for name in getattr(target, "elts", [target]) if isinstance(name, ast.Name))
        elif isinstance(node, ast.TypeAlias):  # `type Money = int`
            names.add(node.name.id)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return names


def classes(module: ast.Module) -> dict[str, list[str]]:
    """Each class a module defines at its top level, with its parents as written: `Exception`, `pydantic.BaseModel`."""
    return {
        node.name: [ast.unparse(base) for base in node.bases] for node in module.body if isinstance(node, ast.ClassDef)
    }


def parameters(module: ast.Module, function: str) -> list[str] | None:
    """The parameters of a top-level function by name: the positional ones, then the keyword-only ones, then `*args`
    and `**kwargs` by their names; None where the module defines no such function."""
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            args = node.args
            names = [param.arg for param in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            names += [extra.arg for extra in (args.vararg, args.kwarg) if extra is not None]
            return names
    return None


def cognitive(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """How hard `function` is to follow, as SonarSource's cognitive complexity counts it: one for each break in a
    straight reading, an `if`, `elif`, `else`, loop, `except`, `match`, conditional expression, run of one boolean
    operator and call of itself, and one more for each level it stands at, a function or a lambda inside it a level
    too. A `try`, a `with` and a comprehension read straight, and a method that calls a built-in of its own name
    reads as calling itself. Checked against this repository at one commit before it
    was written in: 43 functions at 15 or more and 12 above 20, as two other counts of the measure gave."""
    return _block(function.body, 0, function.name)


def _block(nodes: Iterable[ast.AST], depth: int, name: str) -> int:
    return sum(_cost(node, depth, name) for node in nodes)


def _cost(node: ast.AST, depth: int, name: str) -> int:
    """What `node` adds, standing `depth` levels deep in the function `name`."""
    if isinstance(node, ast.If):
        return _if(node, depth, name, chained=False)
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        head = [node.test] if isinstance(node, ast.While) else [node.target, node.iter]
        total = 1 + depth + _block(head, depth, name) + _block(node.body, depth + 1, name)
        return total + (1 + _block(node.orelse, depth + 1, name) if node.orelse else 0)
    if isinstance(node, (ast.Try, ast.TryStar)):
        straight = _block([*node.body, *node.orelse, *node.finalbody], depth, name)
        return straight + sum(1 + depth + _block(handler.body, depth + 1, name) for handler in node.handlers)
    if isinstance(node, ast.Match):
        cases = sum(_block([*([case.guard] if case.guard else []), *case.body], depth + 1, name) for case in node.cases)
        return 1 + depth + _cost(node.subject, depth, name) + cases
    if isinstance(node, ast.IfExp):
        return 1 + depth + _cost(node.test, depth, name) + _block([node.body, node.orelse], depth + 1, name)
    if isinstance(node, ast.BoolOp):
        return 1 + _block(node.values, depth, name)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return _block(node.body if isinstance(node.body, list) else [node.body], depth + 1, name)
    itself = isinstance(node, ast.Call) and name_of(node.func) in (name, f"self.{name}", f"cls.{name}")
    return itself + _block(ast.iter_child_nodes(node), depth, name)


def _if(node: ast.If, depth: int, name: str, *, chained: bool) -> int:
    """An `if` with what hangs on it: an `elif`, which stands where its `if` does, adds one and no depth."""
    total = (1 if chained else 1 + depth) + _cost(node.test, depth, name) + _block(node.body, depth + 1, name)
    rest = node.orelse
    if len(rest) == 1 and isinstance(rest[0], ast.If) and rest[0].col_offset == node.col_offset:
        return total + _if(rest[0], depth, name, chained=True)
    return total + (1 + _block(rest, depth + 1, name) if rest else 0)
