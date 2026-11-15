"""What a change leaves behind in the files that use what it changed: a call that no longer fits the function it
calls, a name the module no longer defines, and an import of a module the change moved or removed, in any file, one
the change never touched among them, where no diff shows it."""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence

from ..rule_helpers import PASSED_ON, Entry, Hit, Tree, TreeRule, UnreadError, name_of, parse

#: Bases whose call a metaclass answers, never the class's own `__init__`: an enum's call looks up a member, a named
#: tuple's and a typed dict's are made from their fields, and a protocol is never called.
CALLED_BY_BASE = frozenset(
    {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "NamedTuple", "TypedDict", "Protocol", "Generic"}
)
#: functools' decorators that pass each argument on to the function as the call gave it, so a call that does not fit
#: the function raises under one too, and whose wrapper a type checker reads as taking anything: read by review, a
#: call the change broke under one was named by no rule. Measured over the installed packages: 144 module-level
#: functions stand under one of the two alone, and none under one beside another decorator.
CACHES = frozenset({"lru_cache", "cache"})
#: What opens a scope of its own, whose names hide a module's from the code inside it.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_WORD = re.compile(r"\w+")
#: A line a diff removes, its file's header aside: a name one names was there before, as a two-line function whose
#: parameter was renamed is, all of it added again, one moved to another file, or one an assignment bound.
_REMOVED = re.compile(r"^-(?!--)(.*)$", re.MULTILINE)
#: A hunk's header: how many old lines it holds, and where its new lines start and how many.
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
#: What a module holds that no statement of it binds: Python sets each as it runs the module.
_IMPLICIT = frozenset(
    {"__name__", "__doc__", "__file__", "__path__", "__spec__", "__loader__", "__package__", "__dict__"}
    | {"__builtins__", "__cached__", "__annotations__"}
)
#: What an `except` names where it keeps an import that fails from raising.
_CATCHES_IMPORT = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
#: A name the module does not define, its own fix beside the rule's, which is a call's: true of a name the change
#: removed and of one a line the change added never found there.
GONE_FIX = "Use a name the module defines, or define this name there."
#: A module the change moved or removed, which a file still imports by its old path: its own fix.
MOVED_FIX = "Import the module from its new path, or keep a module at the old one."
#: Why each of those matters, by what Python raises at its line. Reproduced in the comment a reviewer pastes: the
#: rule's one reason, a call's `TypeError` "in a file the change did not touch", was said of an import the change
#: itself added.
IMPORT_WHY = "The import fails when it runs: Python raises ImportError at that line."
ATTRIBUTE_WHY = "The line fails when it runs: Python raises AttributeError at that line."
MOVED_WHY = "The import fails when it runs: Python raises ModuleNotFoundError at that line."

type Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
type Function_ = ast.FunctionDef | ast.AsyncFunctionDef
type Function = tuple[str, str]  # the file a function is defined in, and its name
type Changed = dict[Function, tuple[ast.arguments, bool]]  # its parameters, and whether a call passes one fewer


class Callers(TreeRule):
    """Each module-level function, and each class's own `__init__`, whose parameters the change touched is held to
    every call the head makes to it: through `from … import`, `import … as`, a package that passes the name on, and in
    its own module. A call is bound as Python binds it — the positional arguments it takes, each keyword, what it
    needs, what goes by position only — so a call named is one that raises `TypeError` when it runs. Pyrefly reads
    each changed file alone and the guidelines read the diff, so a caller the change left alone is read here or
    nowhere. A function under functools' `lru_cache` or `cache` alone is held to its calls too: each passes every
    argument on as given, and a type checker reads its wrapper as taking anything. Left alone, since each may fit at
    run time: a function under any other decorator, a decorated class, a call that unpacks `*args` or `**kwargs`, a
    name a scope binds to something else, a method on an object, a class a metaclass or `__new__` makes, and an import
    two files could answer. A name a changed module no longer defines is held the same way to each
    file that still takes it: a `from … import`, which raises `ImportError`, and a module's attribute, which raises
    `AttributeError`. A line the change added is held so to the name it takes from a module the change left alone
    too, where that module is a file of its own. Left alone there: an import a `try` guards, a name the file asks
    for by its string or sets itself, a submodule, a module that may hold a name no statement binds — a module
    `__getattr__`, a `*` import, a write to its globals — and, on a line the change kept in a file it left where it
    was, a name the module did not define before the change either, as its text rebuilt from the diff shows: a break
    older than the change, or a name no statement shows, as a compiled extension's is; and a name a line the change
    added takes from a package's `__init__.py` the change left alone, beside which a build can lay a module no git
    tree holds, a compiled extension or a generated file. An import of a module the change moved or removed, by its
    old path, raises `ModuleNotFoundError` unless the head still holds a module there, a package of that name among
    them. Read as a floor where the head was not read whole. The head is read only where the change touched the
    parameters of a name that was there before, one a line the change removed names or one not all of whose lines it
    added, removed a name a module defined, or moved or removed a module; with none, nothing is read, since a
    function the change adds whole leaves no caller behind. A line the change added that takes a name its module
    does not define is named wherever the head is read, for one of these or for another check."""

    id = "callers"
    severity = "major"
    reads_tests = True  # a test that calls the function breaks with it
    title = "a call does not fit the function it calls"
    fix = "Change the call to fit the function, or keep the parameters its callers pass."
    why = "The call fails when it runs: Python raises TypeError at that line."
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
        if any(item["status"] in ("D", "R") and item.get("old", item["path"]).endswith(".py") for item in files):
            return True  # a module moved or removed: what imports it is in the head alone
        python = [item for item in files if item["path"].endswith(".py") and item["added_at"]]
        if any(item["text"] is None for item in python):  # not read: what it touched cannot be told
            return True
        gone = {word for item in files for line in _REMOVED.findall(item["diff"] or "") for word in _WORD.findall(line)}
        for item in python:
            # A file the change adds whole defines nothing that was there, unless a name it defines was removed
            # elsewhere: measured, parsing each new file took 433 ms of ADK's whole review for nothing.
            text = item["text"] or ""  # read, as the check above holds
            if (item["status"] == "A" and not gone) or (module := parse(text)) is None:
                continue
            for name, (_function, statement) in _touched(module, item["added_at"]).items():
                if name in gone or not _span(statement) <= item["added_at"]:
                    return True
        return any(_removes_a_name(item) for item in files if item["status"] == "M" and item["path"].endswith(".py"))

    def check(self, tree: Tree) -> list[tuple[str, int, str] | Hit]:
        changed = _changed(tree)
        imports, gone = _Imports(tree, changed), _Gone(tree)
        found: list[tuple[str, int, str] | Hit] = []
        for path, module in tree.modules():
            bound = imports.bound(path)
            if changed and (names := _names(path, bound, changed, imports.defined)):
                found += _misfit_calls(path, module, names, changed)
            found += gone.uses(path, module, bound)
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


class _Gone:
    """What each changed module defines, and what it defined before the change, each read once a module, and each
    line of a file that takes from one a name it no longer defines."""

    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.read: dict[str, set[str] | None] = {}
        self.before: dict[str, set[str] | None] = {}

    def uses(self, path: str, module: ast.Module, bound: dict[str, tuple[str, str | None]]) -> list[Hit]:
        """Each line of the module at `path` that imports a module the change moved or removed, or takes a name its
        module does not define, as `gone` reads one: an import no `try` around it guards, and an attribute read
        through the name an import bound to the module."""
        lost = self.lost(path, bound)
        # The lines of this file that are the change's own: each it added, and every line of a file it added or moved
        # here, which reads each relative import from its new folder.
        added = self.tree.added.get(path, frozenset())
        whole = path in self.tree.added and path not in self.tree.modified
        found = [
            _moved_hit(self.tree, path, alias.lineno, lost[local], self.tree.removed[lost[local]])
            for node, guarded in ([] if not lost else _imports(module))
            for alias in node.names
            if (local := alias.asname or alias.name) in lost and not guarded
        ]
        # A file the change holds a line of is held to each module of the project it takes from, since a line the
        # change added answers for the name it takes wherever that module lies. Any other file is held to the modules
        # the change touched alone, so the head's other files cost one look at their imports, as before.
        mine = whole or bool(added)
        taken = {
            name: target
            for name, target in bound.items()
            if (mine or target[0] in self.tree.added) and name not in lost
        }
        if not taken:
            return found
        for node, guarded in _imports(module):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                file, attribute = taken.get(alias.asname or alias.name, ("", None))
                new = whole or alias.lineno in added
                if attribute == alias.name and not guarded and self.gone(file, alias.name, new):
                    found.append(_gone_hit(self.tree, path, alias.lineno, file, alias.name, IMPORT_WHY))
        modules = {name: file for name, (file, attribute) in taken.items() if attribute is None}
        if not modules:
            return found
        other, _imported, _declared = _bound(module.body)
        spelled = _spelled(module)
        for node, hidden in _scoped(module, ast.Attribute):
            owner = name_of(node.value)
            file, root = modules.get(owner), owner.split(".")[0]
            if file is None or not isinstance(node.ctx, ast.Load) or root in hidden or other[root]:
                continue
            new = whole or node.lineno in added
            if not {node.attr, f"{owner}.{node.attr}"} & spelled and self.gone(file, node.attr, new):
                found.append(_gone_hit(self.tree, path, node.lineno, file, node.attr, ATTRIBUTE_WHY))
        return found

    def lost(self, path: str, bound: dict[str, tuple[str, str | None]]) -> dict[str, str]:
        """Each name an import in `path` bound to a module the change moved or removed, by the module's old path, where
        the head finds no module for it: no file by its name, nor a name a module there defines but by this very
        import, as a package's `from . import contents` binds `contents` in itself."""
        lost = {}
        for name, (file, _attribute) in self.tree.imports_before(path).items():
            if file not in self.tree.removed:
                continue
            now, taken = bound.get(name, ("", None))
            if not now or now == path or (taken is not None and self.lacks(now, taken)):
                lost[name] = file
        return lost

    def lacks(self, path: str, name: str) -> bool:
        """Whether the module at `path` defines no `name`, beside its `__init__.py` holds no file or folder Python
        would import by that name, and binds every name it holds where a statement can be read."""
        if path not in self.read:
            try:
                module = self.tree.module(path)
            except UnreadError:  # not read whole: a floor, as the rest of the tree's reading is
                module = "not read"
            self.read[path] = None if isinstance(module, str) else _defines(module)
        names = self.read[path]
        return names is not None and name not in names and not _beside(path, name, self.tree.paths)

    def gone(self, path: str, name: str, new: bool) -> bool:
        """Whether a line that takes `name` from the module at `path` takes what the change left it without: the
        module lacks the name now, and the break is the change's. Of a module the change touched, it is where the line
        is the change's own, `new`, or the module may have held the name before the change. A line the change kept, in
        a file it left where it was, that takes a name the module lacked before too was broken before the change, or
        takes a name no statement shows, as a compiled extension's is: neither is the change's. Reproduced on a branch
        that added one line to a package's `__init__.py`, where a major named such a line, and measured over a
        project's history: each release that set its version there. Of a module the change left alone, it is where
        the line is `new` and the module a file of its own. Probed on a branch: an import of a misspelt name, added
        to a file, was named by no rule unless the change touched the module too. A package's `__init__.py` is left
        out there, since a build can lay a module beside it that no git tree holds: measured over seven projects'
        heads read as written anew, the one false name of nine was a compiled extension taken from its package, and
        the five that modules of their own lacked were each a true break."""
        if path in self.tree.added:
            return self.lacks(path, name) and (new or self.held(path, name))
        # What costs nothing is asked first: measured over eight histories, 90,072 of the 92,643 names taken from a
        # module the change left alone stood on a kept line, and none of those needs the module read.
        return new and not _is_package(path) and self.lacks(path, name)

    def held(self, path: str, name: str) -> bool:
        """Whether the module at `path` may have held `name` before the change. No module was at the path of one the
        change added or moved here, so a line takes from it only since the change: yes. For one the change modified
        in place, yes where its text before, rebuilt from its diff, cannot be read, may hold a name no statement
        binds, or binds `name`, and where the change moved or removed, beside a package's `__init__.py`, a file or a
        folder Python imported by that name."""
        if path not in self.before:
            diff, text = self.tree.modified.get(path), self.tree.sources.get(path)
            was = None if diff is None or text is None else parse(_before(text, diff))
            self.before[path] = None if was is None else _defines(was)
        names = self.before[path]
        return names is None or name in names or _beside(path, name, self.tree.removed)


def _beside(path: str, name: str, paths: Iterable[str]) -> bool:
    """Whether `paths` hold, beside the package's `__init__.py` at `path`, a file or a folder Python would import by
    `name`; never for a module that is no package's `__init__.py`."""
    if not _is_package(path):
        return False
    sub = path.removesuffix("__init__.py") + name
    return any(item == sub or item.startswith((f"{sub}.", f"{sub}/")) for item in paths)


def _is_package(path: str) -> bool:
    """Whether `path` is a package's `__init__.py`, which a module may lie beside, and not a module of its own."""
    return path == "__init__.py" or path.endswith("/__init__.py")


def _gone_hit(tree: Tree, path: str, line: int, file: str, name: str, why: str) -> Hit:
    """A line that takes `name` from the module at `file`, which does not define it: quoted, and named by what is
    wrong, as every tree rule's hit is, with `why`, what Python raises there."""
    said = f"`{file}` defines no `{name}`, which this line takes from it"
    return Hit(path, line, tree.quoted(path, line) or said, said, fix=GONE_FIX, why=why)


def _moved_hit(tree: Tree, path: str, line: int, old: str, new: str | None) -> Hit:
    """A line that imports the module at `old`, which the change moved to `new`, or removed where `new` is None."""
    said = (
        f"`{old}` moved to `{new}`, and this line still imports it"
        if new
        else f"the change removed `{old}`, which this line imports"
    )
    return Hit(path, line, tree.quoted(path, line) or said, said, fix=MOVED_FIX, why=MOVED_WHY)


def _removes_a_name(item: Entry) -> bool:
    """Whether the change removed a name the module defined, which a file it left alone may still take: the module
    before the change, rebuilt from its diff, against the module at the head."""
    diff = item["diff"] or ""
    if not _REMOVED.search(diff):
        return False
    if item["text"] is None:  # not read: what it removed cannot be told
        return True
    after = parse(item["text"])
    if after is None or (now := _defines(after)) is None:  # nothing to name from, or a module that may hold any name
        return False
    before = parse(_before(item["text"], diff))
    return before is None or bool((_defines(before) or set()) - now)


def _before(text: str, diff: str) -> str:
    """The file's text before the change, rebuilt from its text at the head and its diff: each hunk's old lines in
    place of its new ones, split on a newline alone, as git numbers lines. Measured over this repository's last 195
    commits: 1,134 of 1,137 modified files rebuilt exactly, the rest each a file's last newline, which no name needs."""
    new, old, at = text.split("\n"), list[str](), 0
    lines = iter(diff.split("\n"))
    for line in lines:
        if not (hunk := _HUNK.match(line)):
            continue
        removed, added = int(hunk[1] or 1), int(hunk[3] or 1)
        start = int(hunk[2]) - (1 if added else 0)  # where no line is added, git names the line before them
        old += new[at:start]
        at = start
        while (removed > 0 or added > 0) and (line := next(lines, None)) is not None:
            mark, body = line[:1], line[1:]
            if mark in (" ", ""):  # a blank line around the change, its space suppressed
                old.append(body)
                removed, added, at = removed - 1, added - 1, at + 1
            elif mark == "-":
                old.append(body)
                removed -= 1
            elif mark == "+":
                added, at = added - 1, at + 1
            elif mark != "\\":  # git's "No newline at end of file" aside, the hunk ended early
                break
    return "\n".join(old + new[at:])


def _defines(module: ast.Module) -> set[str] | None:
    """Each name the module binds at its top, wherever a statement there binds it, a function's `global` among them;
    None where it may hold a name no statement binds: a module `__getattr__`, a `*` import, or a write to its globals
    through `globals()`, `vars()`, `exec` or `sys.modules`."""
    other, imported, _declared = _bound(module.body)
    names = {*other, *imported, *_IMPLICIT}
    if "__getattr__" in names:
        return None
    for node in ast.walk(module):
        if _binds_unseen(node):
            return None
        if isinstance(node, ast.Global):
            names.update(node.names)
    return names


def _binds_unseen(node: ast.AST) -> bool:
    """Whether `node` may bind a module's name no statement spells: a `*` import, or a write to its globals."""
    if isinstance(node, ast.ImportFrom):
        return any(alias.name == "*" for alias in node.names)
    if isinstance(node, ast.Call):
        called = name_of(node.func)
        return called in ("globals", "exec") or (called == "vars" and not node.args)
    return isinstance(node, ast.Attribute) and name_of(node) == "sys.modules"


def _imports(module: ast.Module) -> Iterator[tuple[ast.Import | ast.ImportFrom, bool]]:
    """Each import in the module, and whether a `try` around it catches the `ImportError` it would raise."""
    stack: list[tuple[ast.AST, bool]] = [(module, False)]
    while stack:
        node, guarded = stack.pop()
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node, guarded
        elif isinstance(node, ast.Try | ast.TryStar):
            catches = guarded or any(_catches_import(handler.type) for handler in node.handlers)
            stack.extend((part, catches) for part in node.body)
            stack.extend((part, guarded) for part in [*node.handlers, *node.orelse, *node.finalbody])
        else:
            stack.extend((child, guarded) for child in ast.iter_child_nodes(node))


def _catches_import(kind: ast.expr | None) -> bool:
    """Whether an `except` naming `kind`, none for a bare one, catches an import that fails."""
    parts = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(part is None or name_of(part).rsplit(".", 1)[-1] in _CATCHES_IMPORT for part in parts)


def _spelled(module: ast.Module) -> set[str]:
    """Each string the module spells and each attribute it sets or deletes, dotted: a name `hasattr` or `getattr`
    asks for, or one the module puts on another, may be there when it runs."""
    found = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            found.add(name_of(node))
    return found


def _misfit_calls(
    path: str, module: ast.Module, names: dict[str, Function], changed: Changed
) -> list[tuple[str, int, str]]:
    """Each call in the module at `path` to a changed function that does not fit it, with why: a call through a name a
    scope around it binds to something else, or the module binds to more than the function, is left alone."""
    other, _imported, _declared = _bound(module.body)
    found = []
    for call, hidden in _scoped(module, ast.Call):
        target = names.get(spelled := name_of(call.func))
        root = spelled.split(".")[0]
        own = 1 if target == (path, spelled) else 0  # its own module's def binds the name once
        if target is None or _unpacks(call) or root in hidden or other[root] > own:
            continue
        params, method = changed[target]
        if said := _misfits(params, call, method, f"`{target[1]}()` in `{target[0]}`"):
            found.append((path, call.lineno, said))
    return found


def _changed(tree: Tree) -> Changed:
    """Each function whose parameters the change touched: a module-level function, or a class whose `__init__` is
    its own, each as `_touched` takes it and the one thing its name stands for in its module."""
    changed: Changed = {}
    for path, added in tree.added.items():
        if added and not isinstance(module := tree.module(path), str):
            changed |= {
                (path, name): (function.args, function.name == "__init__")
                for name, (function, _statement) in _touched(module, added).items()
            }
    return changed


def _touched(module: ast.Module, added: frozenset[int]) -> dict[str, tuple[Function_, ast.stmt]]:
    """By name, each module-level function, and each class's own `__init__`, whose parameters `added` touches, each
    under no decorator but functools' caches and the one thing its name stands for in the module, with the statement
    that defines the name: the function, or its class."""
    other, imported, _declared = _bound(module.body)
    caches = _caches(module, other, imported)
    touched: dict[str, tuple[Function_, ast.stmt]] = {}
    for node in module.body:
        if other[getattr(node, "name", "")] != 1 or imported[getattr(node, "name", "")]:
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            called_as_written = all(
                name_of(mark.func if isinstance(mark, ast.Call) else mark) in caches for mark in node.decorator_list
            )
            if called_as_written and _header(node) & added:
                touched[node.name] = (node, node)
        elif isinstance(node, ast.ClassDef) and (init := _own_init(node)) and _header(init) & added:
            touched[node.name] = (init, node)
    return touched


def _caches(module: ast.Module, other: Counter[str], imported: Counter[str]) -> set[str]:
    """Each way the module spells one of functools' `CACHES`: through the name its `import functools` binds, or by
    the name its `from functools import` gives one, each an import at the module's top that binds a name nothing else
    in the module binds. The same spelling from another module is none, since that decorator may take the call."""
    spelled: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.Import):
            through = {alias.asname or alias.name for alias in node.names if alias.name == "functools"}
            spelled |= {f"{name}.{cache}" for name in through for cache in CACHES}
        elif isinstance(node, ast.ImportFrom) and node.module == "functools" and not node.level:
            spelled |= {alias.asname or alias.name for alias in node.names if alias.name in CACHES}
    return {name for name in spelled if imported[root := name.split(".")[0]] == 1 and not other[root]}


def _span(node: ast.stmt) -> set[int]:
    """Every line a statement is written on, its decorators' among them: all added, the change made it whole."""
    first = min([decorator.lineno for decorator in getattr(node, "decorator_list", ())] + [node.lineno])
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


def _scoped[Node: ast.AST](module: ast.Module, kind: type[Node]) -> Iterator[tuple[Node, frozenset[str]]]:
    """Each node of `kind` in `module`, a call or an attribute, with the names the scopes around it bind to something
    other than an import, which hide the module's own: a parameter, a local, a comprehension's variable. Read without
    recursion, so no nesting depth the parser accepts can stop it."""
    stack: list[tuple[ast.AST, frozenset[str]]] = [(module, frozenset())]
    while stack:
        node, hidden = stack.pop()
        if isinstance(node, kind):
            yield node, hidden
        if isinstance(node, _SCOPES):
            stack.extend((part, hidden) for part in _around(node))
            body = node.body if isinstance(node.body, list) else [node.body]
            other, _imported, declared = _bound(body)
            inner = hidden | (set(other) - declared) | _parameters(node)
            stack.extend((part, inner) for part in body)
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
    names = [param.arg for param in (*params.posonlyargs, *params.args)]
    required = names[: len(names) - len(params.defaults)]
    if method and names:  # `self`, first of both: `required` is a prefix of `names`
        names, required = names[1:], required[1:]
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
