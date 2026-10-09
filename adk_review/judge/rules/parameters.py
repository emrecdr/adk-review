"""The payments project's own contract: the parameters its functions must take."""

from __future__ import annotations

from ..rule_helpers import Tree, TreeRule, parameters

#: Each module, its functions, and the parameters each must take, by name.
PARAMETERS = {"src/payments/charge.py": {"charge": ("amount_cents", "currency")}}


class Parameters(TreeRule):
    """A caller passes these by name; a parameter renamed or dropped breaks the caller, which the function's own
    diff never shows."""

    id = "parameters"
    severity = "major"
    title = "a function no longer takes a parameter its callers pass"
    fix = "Take the parameter again, or move every caller with it and change the list in this rule."
    bad = ({"src/payments/charge.py": "def charge(amount_cents):\n    pass\n"},)
    good = ({"src/payments/charge.py": "def charge(amount_cents, currency):\n    pass\n"},)

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        problems = []
        for path, functions in PARAMETERS.items():
            module = tree.module(path)
            if isinstance(module, str):  # why there is no module
                problems.append((path, module))
                continue
            for function, names in functions.items():
                taken = parameters(module, function)
                if taken is None:
                    problems.append((path, f"defines no function {function}"))
                elif missing := [name for name in names if name not in taken]:
                    problems.append((path, f"{function}() takes no {', '.join(missing)}"))
        return problems
