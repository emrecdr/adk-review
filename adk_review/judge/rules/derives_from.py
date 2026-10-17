"""The demo project's own conventions, as a project writes its own: what a class named like this derives from."""

from __future__ import annotations

from collections.abc import Iterator

from ..rule_helpers import Tree, TreeRule, classes

#: A class whose name ends with the key derives from one of the parents named, or from a class whose own name ends
#: the same way: `LookupError` is an `Exception`, `MyModel` a `BaseModel`, an `AdminUser` a `BaseUser` through a
#: `StaffUser`, as far as a name can say. A parent named here is its kind's base, and promises nothing of itself.
PARENTS = {
    "Model": ("BaseModel",),
    "Error": ("Exception",),
    "User": ("BaseUser",),
    "Repository": ("BaseRepository",),
}
#: The keys a name may also start with, each a key of `PARENTS`: a `ModelView` is a model too. An ending decides
#: first, so a `ModelError` is an error, never a model.
PREFIXES = ("Model",)


class DerivesFrom(TreeRule):
    """A name promises a kind: a `UserModel` is read and validated as a model, a `PaymentError` is caught as an
    exception, an `AdminUser` is handed what every user is, an `OrderRepository` is used as every repository is, so
    each must be one. Read as written, in every Python file at the head, with no import followed."""

    id = "derives-from"
    severity = "major"
    title = "a class does not derive from the parent its name promises"
    fix = "Derive the class from the parent named, or rename it so its name promises nothing."
    bad = (
        {"k.py": "class PaymentError:\n    pass\n"},
        {"k.py": "class AdminUser:\n    pass\n"},
        {"k.py": "class OrderRepository(Base):\n    pass\n"},
    )
    good = (
        {"k.py": "class PaymentError(Exception):\n    pass\n\n\nclass UserModel(pydantic.BaseModel):\n    pass\n"},
        {"k.py": "class BaseUser:\n    pass\n\n\nclass AdminUser(BaseUser):\n    pass\n"},
        {"k.py": "class BaseRepository(ABC):\n    pass\n\n\nclass OrderRepository(BaseRepository):\n    pass\n"},
    )

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        return [(path, problem) for path, name, bases in _classes_at(tree) if (problem := _broken(name, bases))]


def _classes_at(tree: Tree) -> Iterator[tuple[str, str, list[str]]]:
    """Every class at the head: its path, its name and its parents as written."""
    for path, module in tree.modules():
        for name, bases in classes(module).items():
            yield path, name, bases


def _promised(name: str) -> str | None:
    """The kind a class name promises: the key it ends with, else the key among `PREFIXES` it starts with."""
    ending = next((key for key in PARENTS if name.endswith(key)), None)
    return ending or next((key for key in PARENTS if key in PREFIXES and name.startswith(key)), None)


def _broken(name: str, bases: list[str]) -> str | None:
    """What is wrong with one class, or None: its name promises a kind, it is not that kind's base, and no written
    parent is that kind. Measured: with `User` among the kinds, `class BaseUser:` was flagged for not deriving from
    itself."""
    key = _promised(name)
    if key is None or name in PARENTS[key] or any(_is(base, key) for base in bases):
        return None
    return f"class {name} derives from {', '.join(bases) or 'nothing'}, not {' or '.join(PARENTS[key])}"


def _is(base: str, key: str) -> bool:
    """A written parent of the kind: the parent named, or one whose own name promises the kind, read as a class's is."""
    leaf = base.rsplit(".", 1)[-1]
    return leaf in PARENTS[key] or _promised(leaf) == key
