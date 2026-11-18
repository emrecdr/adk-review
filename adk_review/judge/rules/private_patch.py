"""A project's own rule: a test that patches a private name is tied to an implementation, not to a behaviour."""

from __future__ import annotations

import ast
import re

from ..rule_helpers import ChangedFile, FileRule, argument, calls_in, is_test, name_of

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
    """Whether what `call` patches is a private name, by its last part: one leading underscore, a dunder being public.
    What it patches is `patch`'s target, `patch.object`'s attribute, `setattr`'s name, and `monkeypatch.setattr`'s
    dotted path where its first argument is one: reproduced by review, read in every string the call passed, a public
    name patched to `"_staging"` was named."""
    target = _patched(call)
    if not (isinstance(target, ast.Constant) and isinstance(target.value, str) and _NAME.fullmatch(target.value)):
        return False
    leaf = target.value.rsplit(".", 1)[-1]
    return leaf.startswith("_") and not leaf.startswith("__")


def _patched(call: ast.Call) -> ast.expr | None:
    """The argument naming what `call` patches, as each call takes it."""
    called = name_of(call.func)
    if called.endswith("patch.object"):
        return argument(call, "attribute", 1)
    if called == "patch" or called.endswith(".patch"):
        return argument(call, "target", 0)
    target = argument(call, "target", 0)  # `monkeypatch.setattr`: a dotted path in place of an object
    dotted = isinstance(target, ast.Constant) and isinstance(target.value, str) and called != "setattr"
    return target if dotted else argument(call, "name", 1)
