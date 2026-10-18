"""The payments project's own shape: the files and folders every branch must keep."""

from __future__ import annotations

from ..rule_helpers import Tree, TreeRule

#: What must exist at the head: a file by its path, a folder by its path ending in `/`.
REQUIRED = ("README.md", "src/payments/__init__.py", "tests/")


class RequiredPaths(TreeRule):
    """A branch may add what it likes; what it must not do is lose the project's shape: its README, its package and
    its tests. The list is the rule's own: a project edits it, as it would the words of `demo-money-in-cents`."""

    id = "demo-required-paths"
    severity = "major"
    title = "a file or folder the project needs is missing"
    fix = "Restore it, or change the list in this rule if the shape has changed on purpose."
    bad = ({"README.md": ""},)
    good = ({"README.md": "", "src/payments/__init__.py": "", "tests/test_charge.py": ""},)

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        return [(path, "not at the head") for path in REQUIRED if not tree.has(path)]
