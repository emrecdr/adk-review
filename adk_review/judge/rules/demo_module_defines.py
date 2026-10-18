"""The payments project's own contract: the names its modules must define."""

from __future__ import annotations

from ..rule_helpers import Tree, TreeRule, defined

#: Each module and the names it must define at its top level: a function, a class, an assigned or an imported name.
DEFINES = {"src/payments/charge.py": ("Charge", "charge"), "src/payments/config.py": ("REGION",)}


class ModuleDefines(TreeRule):
    """What the rest of the project imports from a module is its contract; a rename or a removal breaks every
    importer, which a diff of the one file never shows."""

    id = "demo-module-defines"
    severity = "major"
    title = "a module no longer defines a name the project relies on"
    fix = "Define the name again, or move every importer with it and change the list in this rule."
    bad = (
        {"src/payments/charge.py": "def charge(amount_cents):\n    pass\n", "src/payments/config.py": "REGION = 1\n"},
    )
    good = (
        {
            "src/payments/charge.py": "class Charge:\n    pass\n\n\ndef charge():\n    pass\n",
            "src/payments/config.py": "REGION = 1\n",
        },
    )

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        problems = []
        for path, names in DEFINES.items():
            module = tree.module(path)
            if isinstance(module, str):  # why there is no module
                problems.append((path, module))
                continue
            have = defined(module)
            if missing := [name for name in names if name not in have]:
                problems.append((path, f"defines no {', '.join(missing)}"))
        return problems
