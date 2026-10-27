"""A call that no longer fits the function it calls: a change to a function's parameters breaks each call it leaves
behind, in any file, one the change never touched among them, where no diff shows it."""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence

from ..rule_helpers import Entry, Tree, TreeRule, UnreadError, name_of

#: How many modules deep a name is followed where a module only imports it to pass it on, as a package's
#: `__init__.py` does with `from .repo import get_user`, so `from pkg import get_user` reaches the function.
PASSED_ON = 4
#: Bases whose call a metaclass answers, never the class's own `__init__`: an enum's call looks up a member, a named
#: tuple's and a typed dict's are made from their fields, and a protocol is never called.
CALLED_BY_BASE = frozenset(
    {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "NamedTuple", "TypedDict", "Protocol", "Generic"}
)
#: What opens a scope of its own, whose names hide a module's from the code inside it.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
#: A function or class a diff's removed line defined: one whose every line the change rewrote was there before, as a
#: two-line function whose parameter was renamed is, all of it added again.
_REMOVED_DEF = re.compile(r"^-\s*(?:async\s+def|def|class)\s+(\w+)", re.MULTILINE)

type Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
type Function = tuple[str, str]  # the file a function is defined in, and its name
type Changed = dict[Function, tuple[ast.arguments, bool]]  # its parameters, and whether a call passes one fewer


class Callers(TreeRule):
    """Each module-level function, and each class's own `__init__`, whose parameters the change touched is held to
    every call the head makes to it: through `from … import`, `import … as`, a package that passes the name on, and in
    its own module. A call is bound as Python binds it — the positional arguments it takes, each keyword, what it
    needs, what goes by position only — so a call named is one that raises `TypeError` when it runs. Pyrefly reads
    each changed file alone and the guidelines read the diff, so a caller the change left alone is read here or
    nowhere. Left alone, since each may fit at run time: a decorated function or class, a call that unpacks `*args` or
    `**kwargs`, a name a scope binds to something else, a method on an object, a class a metaclass or `__new__` makes,
    and an import two files could answer. Read as a floor where the head was not read whole. The head is read only
    where the change touched the parameters of a function that was there before: one it adds whole leaves no caller
    behind, and the calls to it in the change are read from the changed files."""

    id = "callers"
    severity = "major"
    reads_tests = True  # a test that calls the function breaks with it
    title = "a call does not fit the function it calls"
    fix = "Change the call to fit the function, or keep the parameters its callers pass."
    bad = (
        {
            "pkg/repo.py": "def get_user(uid):\n    return uid\n",
            "pkg/service.py": "from pkg.repo import get_user\n\nget_user(user_id=3)\n",
        },
    )
    good = (
        {
            "pkg/repo.py": "def get_user(uid):\n    return uid\n",
            "pkg/service.py": "from pkg.repo import get_user\n\nget_user(uid=3)\n",
        },
    )

    def needs_head(self, files: list[Entry]) -> bool:
        for item in files:
            if not item["path"].endswith(".py") or not item["added_at"]:
                continue
            if item["text"] is None:  # not read: what it touched cannot be told
                return True
            try:
                module = ast.parse(item["text"])
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                continue
            gone = set(_REMOVED_DEF.findall(item["diff"] or ""))
            touched = _touched(module, item["added_at"])
            if any(name in gone or not _span(node) <= item["added_at"] for name, node in touched.items()):
                return True
        return False

    def check(self, tree: Tree) -> list[tuple[str, int, str]]:
        changed = _changed(tree)
        if not changed:
            return []
        imports, found = _Imports(tree, changed), []
        for path, module in tree.modules():
            if names := _names(path, imports.bound(path), changed, imports.defined):
                found += _misfit_calls(path, module, names, changed)
        return found


class _Imports:
    """What each file's imports bind, read once a file, and the changed function a name in a file stands for."""

    def __init__(self, tree: Tree, changed: Changed) -> None:
        self.tree, self.changed = tree, changed
        self.read: dict[str, dict[str, tuple[str, str | None]]] = {}

    def bound(self, path: str) -> dict[str, tuple[str, str | None]]:
        """What `path`'s imports bind; nothing for a file the head's read left out, so a name passed on through one is
        followed no further, a floor as the rest of the tree's reading is."""
        if path not in self.read:
            try:
                self.read[path] = self.tree.imports(path)
            except UnreadError:
                self.read[path] = {}
        return self.read[path]

    def defined(self, target: Function) -> Function | None:
        """The changed function `target`'s name stands for in its file, followed where the file only passes it on."""
        for _ in range(PASSED_ON + 1):
            if target in self.changed:
                return target
            passed = self.bound(target[0]).get(target[1])
            if passed is None or passed[1] is None:
                return None
            target = (passed[0], passed[1])
        return None


def _misfit_calls(
    path: str, module: ast.Module, names: dict[str, Function], changed: Changed
) -> list[tuple[str, int, str]]:
    """Each call in the module at `path` to a changed function that does not fit it, with why: a call through a name a
    scope around it binds to something else, or the module binds to more than the function, is left alone."""
    other, _imported, _declared = _bound(module.body)
    found = []
    for call, hidden in _calls(module):
        target = names.get(spelled := name_of(call.func))
        root = spelled.split(".")[0]
        if target is None or _unpacks(call) or root in hidden or other[root] > (target == (path, spelled)):
            continue
        params, method = changed[target]
        if said := _misfits(params, call, method, f"`{target[1]}()` in `{target[0]}`"):
            found.append((path, call.lineno, said))
    return found


def _changed(tree: Tree) -> Changed:
    """Each function whose parameters the change touched: a module-level function, or a class whose `__init__` is
    its own, each undecorated and the one thing its name stands for in its module."""
    changed: Changed = {}
    for path, added in tree.added.items():
        if not path.endswith(".py") or not added or path not in tree.paths:
            continue
        module = tree.module(path)
        if not isinstance(module, str):
            changed |= {
                (path, name): (node.args, node.name == "__init__") for name, node in _touched(module, added).items()
            }
    return changed


def _touched(module: ast.Module, added: frozenset[int]) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """By name, each module-level function, and each class's own `__init__`, whose parameters `added` touches, each
    undecorated and the one thing its name stands for in the module."""
    other, imported, _declared = _bound(module.body)
    touched: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in module.body:
        if other[getattr(node, "name", "")] != 1 or imported[getattr(node, "name", "")]:
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.decorator_list and _header(node) & added:
                touched[node.name] = node
        elif isinstance(node, ast.ClassDef) and (init := _own_init(node)) and _header(init) & added:
            touched[node.name] = init
    return touched


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Every line a function is written on, its decorators' among them: all added, the change made it whole."""
    first = min([decorator.lineno for decorator in node.decorator_list] + [node.lineno])
    return set(range(first, (node.end_lineno or node.lineno) + 1))


def _own_init(node: ast.ClassDef) -> ast.FunctionDef | None:
    """The class's own undecorated `__init__`, where its call is that `__init__`'s: no decorator, no metaclass, no
    `__new__`, and no base whose call a metaclass answers."""
    bases = {name_of(base).rsplit(".", 1)[-1] for base in node.bases}
    if node.decorator_list or node.keywords or bases & CALLED_BY_BASE:
        return None
    methods = {item.name: item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)}
    init = methods.get("__init__")
    if "__new__" in methods or not isinstance(init, ast.FunctionDef) or init.decorator_list:
        return None
    return init


def _header(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """The lines a function's parameters are written on: its `def` line to the line before its body."""
    return set(range(node.lineno, max(node.lineno + 1, node.body[0].lineno)))


def _names(
    path: str,
    imported: dict[str, tuple[str, str | None]],
    changed: Changed,
    defined: Callable[[Function], Function | None],
) -> dict[str, Function]:
    """Each name a call in the module at `path` may spell that stands for a changed function: the module's own, an
    imported one as bound, and one of an imported module, through the module's name."""
    names = {function: (file, function) for file, function in changed if file == path}
    for name, (file, attribute) in imported.items():
        if attribute is None:
            names |= {f"{name}.{function}": (owner, function) for owner, function in changed if owner == file}
        elif target := defined((file, attribute)):
            names[name] = target
    return names


def _bound(body: Sequence[ast.AST]) -> tuple[Counter[str], Counter[str], set[str]]:
    """How often each name is bound in one scope, its own statements and never a nested scope's: by anything but an
    import, and by an import; and the names it declares `global` or `nonlocal`, which bind elsewhere."""
    other, imported, declared = Counter[str](), Counter[str](), set[str]()
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            other[node.name] += 1
            continue
        if isinstance(node, (ast.Lambda, *_COMPREHENSIONS)):
            continue
        if isinstance(node, ast.Import):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            declared.update(node.names)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            other[node.id] += 1
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            other[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            other[node.rest] += 1
        stack.extend(ast.iter_child_nodes(node))
    return other, imported, declared


def _calls(module: ast.Module) -> Iterator[tuple[ast.Call, frozenset[str]]]:
    """Each call in `module`, with the names the scopes around it bind to something other than an import, which hide
    the module's own: a parameter, a local, a comprehension's variable. Read without recursion, so no nesting depth
    the parser accepts can stop it."""
    stack: list[tuple[ast.AST, frozenset[str]]] = [(module, frozenset())]
    while stack:
        node, hidden = stack.pop()
        if isinstance(node, ast.Call):
            yield node, hidden
        if isinstance(node, _SCOPES):
            stack.extend((part, hidden) for part in _around(node))
            other, _imported, declared = _bound(node.body if isinstance(node.body, list) else [node.body])
            inner = hidden | (set(other) - declared) | _parameters(node)
            body = node.body if isinstance(node.body, list) else [node.body]
            stack.extend((part, frozenset(inner)) for part in body)
            continue
        if isinstance(node, _COMPREHENSIONS):
            targets = {n.id for gen in node.generators for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
            stack.extend((child, hidden | targets) for child in ast.iter_child_nodes(node))
            continue
        stack.extend((child, hidden) for child in ast.iter_child_nodes(node))


def _around(node: Scope) -> list[ast.AST]:
    """What a scope's own statement evaluates in the scope around it: its decorators, defaults and bases."""
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    decorators = [] if isinstance(node, ast.Lambda) else node.decorator_list
    return [*decorators, *node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]


def _parameters(node: Scope) -> set[str]:
    """The names a function's or a lambda's parameters bind; none for a class."""
    if isinstance(node, ast.ClassDef):
        return set()
    args = node.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    return {param.arg for param in params if param is not None}


def _unpacks(call: ast.Call) -> bool:
    """Whether a call passes `*args` or `**kwargs`, which no reading of the call can count."""
    return any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords)


def _misfits(params: ast.arguments, call: ast.Call, method: bool, function: str) -> str:
    """What makes `call` raise where it binds to `params`, as Python binds a call, one sentence; empty where it fits.
    `method`: the first parameter is passed for the call, as `self` is to `__init__`."""
    positional = [*params.posonlyargs, *params.args]
    names = [param.arg for param in positional]
    required = names[: len(names) - len(params.defaults)]
    if method and names:
        names, required = names[1:], [name for name in required if name != positional[0].arg]
    needed = [
        param.arg for param, default in zip(params.kwonlyargs, params.kw_defaults, strict=True) if default is None
    ]
    said: list[tuple[str, str]] = []
    if params.vararg is None and len(call.args) > len(names):
        given, taken = len(call.args), len(names)
        said.append(
            (
                f"{function} takes at most {taken} positional arguments, and the call passes {given}",
                f"the call passes {given} positional arguments, where {taken} are taken",
            )
        )
    filled = set(names[: len(call.args)])
    said += _by_keyword(params, call, names, filled, function)
    said.extend(
        (f"the call passes no `{name}`, which {function} needs", f"the call passes no `{name}`")
        for name in [*required, *needed]
        if name not in filled
    )
    return said[0][0] + "".join(f", and {short}" for _full, short in said[1:]) if said else ""


def _by_keyword(
    params: ast.arguments, call: ast.Call, names: list[str], filled: set[str], function: str
) -> list[tuple[str, str]]:
    """What each keyword of `call` breaks, as a sentence naming `function` and a short one, filling `filled` with the
    parameters each keyword gives."""
    by_position = {param.arg for param in params.posonlyargs}
    keywords = {param.arg for param in params.kwonlyargs}
    said = []
    for keyword in call.keywords:
        name = keyword.arg or ""
        if name in by_position and params.kwarg is None:
            said.append((f"{function} takes `{name}` by position only", f"`{name}` goes by position only"))
        elif name in names or name in keywords:
            if name in filled:
                said.append((f"the call passes `{name}` twice to {function}", f"the call passes `{name}` twice"))
            filled.add(name)
        elif params.kwarg is None:
            said.append((f"{function} takes no keyword `{name}`", f"there is no keyword `{name}`"))
    return said
