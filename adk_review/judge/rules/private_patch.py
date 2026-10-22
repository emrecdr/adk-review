"""A project's own rule: a test that patches a private name is tied to an implementation, not to a behaviour."""

from __future__ import annotations

import ast
import re

from ..rule_helpers import ChangedFile, FileRule, calls_in, is_test

#: The patch calls, as the code writes them: patch(...), patch.object(...), mocker.patch(...), monkeypatch.setattr(...)
#: and setattr(...).
CALLS = ("patch", "*.patch", "patch.object", "*.patch.object", "setattr", "*.setattr")
#: A string argument naming what is patched: a dotted path, or an attribute name alone.
_NAME = re.compile(r"[A-Za-z_][\w.]*")


class PrivatePatch(FileRule):
    """A name with one leading underscore is the module's own business: a test that patches it fails when the
    implementation is refactored with its behaviour unchanged. A dunder name is public by convention. Read from the
    module, as Python reads a call: one a comment or a string names is none, and one a formatter wrapped over lines,
    its name on a line of its own, is found where it starts."""

    id = "private-patch"
    severity = "minor"
    reads_tests = True  # tests alone, which `check` chooses
    title = "a test patches a private name"
    fix = "Patch the public boundary the unit depends on, or pass the collaborator in."
    bad = (
        ("tests/test_a.py", 'with patch("payments.charge._send"):\n    pass\n'),
        ("tests/test_a.py", 'with patch.object(\n    service,\n    "_send_payment",\n):\n    pass\n'),
        ("tests/test_a.py", 'monkeypatch.setattr(mod, "_cache", {})\n'),
    )
    good = (
        ("tests/test_a.py", 'with patch("payments.charge.send"):\n    pass\n'),
        ("tests/test_a.py", 'patch("os.environ", {})\n'),
        ("tests/test_a.py", '# patch("pkg._x") would tie the test to it\n'),
        ("src/a.py", 'patch("pkg._x")\n'),
    )

    def check(self, file: ChangedFile) -> list[int]:
        """Each line where a patch call the change added to a test starts and names a private name: the report quotes
        that line."""
        if not file.path.endswith(".py") or not is_test(file.path):
            return []
        return [call.lineno for call in calls_in(file.module, *CALLS) if call.lineno in file.added and _private(call)]


def _private(call: ast.Call) -> bool:
    """Whether a string `call` passes names a private name, by its last part: one leading underscore, a dunder being
    public."""
    given = (*call.args, *(keyword.value for keyword in call.keywords))
    named = (node.value for node in given if isinstance(node, ast.Constant) and isinstance(node.value, str))
    leaves = (name.rsplit(".", 1)[-1] for name in named if _NAME.fullmatch(name))
    return any(leaf.startswith("_") and not leaf.startswith("__") for leaf in leaves)
