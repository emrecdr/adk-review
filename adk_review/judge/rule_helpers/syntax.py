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
    """What `call` passes for one parameter: by its `name`, or as the argument at `position`, counted from 0. Where
    the call spreads `*args` or `**options`, those, since they may hold it; None where the call does not pass it."""
    for keyword in call.keywords:
        if keyword.arg in (name, None):  # None: `**options`
            return keyword.value
    if position is not None:
        if len(call.args) > position:
            return call.args[position]
        return next((given for given in call.args if isinstance(given, ast.Starred)), None)
    return None


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
