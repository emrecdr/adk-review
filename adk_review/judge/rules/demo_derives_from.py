"""The payments project's own conventions: what a class named like this derives from."""

from __future__ import annotations

from collections.abc import Iterator

from ..rule_helpers import Tree, TreeRule, classes

#: A class whose name ends with the key derives from one of the parents named, or from a class whose own name
#: promises the same kind: `LookupError` is an `Exception`, `MyModel` a `BaseModel`, an `AdminUser` a `BaseUser`
#: through a `StaffUser`, as far as a name can say. The kind's own name alone promises nothing: a Pydantic `User` is
#: no `BaseUser`, while a `User` as a parent is a user, as Django's `models.Model` is a model.
PARENTS = {
    "Model": ("BaseModel",),
    "Error": ("Exception",),
    "User": ("BaseUser",),
    "Repository": ("BaseRepository",),
}


class DerivesFrom(TreeRule):
    """A name promises a kind: a `UserModel` is read and validated as a model, a `PaymentError` is caught as an
    exception, an `OrderRepository` is used as any repository is, so each must be one. Read as written, in every
    Python file at the head but the tests, whose fakes are named for what they stand in for, with no import
    followed."""

    id = "demo-derives-from"
    severity = "major"
    title = "a class does not derive from the parent its name promises"
    fix = "Derive the class from the parent named, or rename it so its name promises nothing."
    why = "Code that checks the parent class, or calls its methods, treats the class wrongly."
    bad = (
        {"k.py": "class PaymentError:\n    pass\n"},
        {"k.py": "class AdminUser:\n    pass\n"},
        {"k.py": "class OrderRepository(Base):\n    pass\n"},
    )
    good = (
        {"k.py": "class PaymentError(Exception):\n    pass\n\n\nclass UserModel(pydantic.BaseModel):\n    pass\n"},
        {
            "k.py": "class BaseUser:\n    pass\n\n\nclass StaffUser(BaseUser):\n    pass\n\n\n"
            "class AdminUser(StaffUser):\n    pass\n\n\nclass OrderRepository(BaseRepository):\n    pass\n"
        },
        {"k.py": "class User(pydantic.BaseModel):\n    pass\n\n\nclass AdminUser(User):\n    pass\n"},
        {"tests/test_k.py": "class TestInspectRepository:\n    pass\n\n\nclass FakeModel:\n    pass\n"},
        {"k.py": "class ModelForm(forms.Form):\n    pass\n\n\nclass ModelConfig:\n    pass\n"},
    )

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        return [(path, problem) for path, name, bases in _classes_at(tree) if (problem := _broken(name, bases))]


def _classes_at(tree: Tree) -> Iterator[tuple[str, str, list[str]]]:
    """Every class at the head: its path, its name and its parents as written."""
    for path, module in tree.modules():
        for name, bases in classes(module).items():
            yield path, name, bases


def _promised(name: str) -> str | None:
    """The kind a class name promises: the key it ends with; none for the kind's own name or its base, which promise
    nothing of themselves, nor for a name that only starts with a key. Measured: with `User` among the kinds,
    `class BaseUser:` was flagged for not deriving from itself, and a Pydantic `class User(BaseModel)` for not
    deriving from `BaseUser`; and while a name starting with `Model` promised a model, Django's `ModelAdmin` and
    `ModelForm`, DRF's `ModelSerializer` and a `ModelConfig` were each flagged for not deriving from `BaseModel`."""
    key = next((key for key in PARENTS if name.endswith(key)), None)
    return None if key is None or name == key or name in PARENTS[key] else key


def _broken(name: str, bases: list[str]) -> str | None:
    """What is wrong with one class, or None: its name promises a kind and no written parent is that kind."""
    key = _promised(name)
    if key is None or any(_is(base, key) for base in bases):
        return None
    return f"class {name} derives from {', '.join(bases) or 'nothing'}, not {' or '.join(PARENTS[key])}"


def _is(base: str, key: str) -> bool:
    """A written parent of the kind: the kind's own name, the parent named, or one whose own name promises the kind,
    read as a class's is."""
    leaf = base.rsplit(".", 1)[-1]
    return leaf == key or leaf in PARENTS[key] or _promised(leaf) == key
