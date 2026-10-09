"""A project's own rule, as an example of the shape: one file, one self-contained `Rule`."""

from __future__ import annotations

from ..rule_helpers import Rule, calls, is_test


class NoPrint(Rule):
    """print() in library code writes to whoever runs it, where a logger can be turned down or sent elsewhere."""

    id = "no-print"
    severity = "minor"
    title = "print() left in library code"
    fix = "Use the logger."
    bad = ("    print(total)", "print (x)", "out.print(report)")
    good = (
        "digest = fingerprint(data)",
        "# print(x) for debugging",
        'msg = "call print(x)"',
        "def print(self, text):",
        ("tests/test_a.py", "print(x)"),
    )

    def check(self, path: str, line: str) -> bool:
        # print() called, on its own or as a method: never named in a comment or a string, never fingerprint()
        return path.endswith(".py") and not is_test(path) and calls(line, "print", "*.print")
