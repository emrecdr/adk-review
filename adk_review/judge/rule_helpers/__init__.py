"""What a rule's author reaches for, through one door: the kind a rule is one of, and the helpers its `check` reads
with — a path, a line, a module's syntax tree — and tries itself with.

    from ..rule_helpers import Rule, calls, is_test

A rule file needs this one import. The kinds and what they are handed (`Rule`, `FileRule`, `TreeRule`, `ChangedFile`,
`Diagnostic`, `Tree`, `Hit`), the hole a rule raises where it could not look (`UnreadError`) and the scale its
severity is on (`Severity`, `RANK`) are the contract, defined in `core/` and only passed on here; everything else is a
helper, defined in the module here named after what it reads, or passed on from `core/` where a review reads it too:
a credential's shape, which redaction shares, and a test's path. Beside `rules/`, never inside it: that folder holds
rules and nothing else.

May import `core` alone, and runs nothing: no process, no network, no model. Standard library only.
"""

from ...core.blast import PASSED_ON, is_test, parse
from ...core.evidence import Entry
from ...core.findings import RANK, Severity
from ...core.paths import matches, matching
from ...core.rules import ChangedFile, Diagnostic, FileRule, Hit, Rule, Tree, TreeRule, UnreadError, is_python
from ...core.secrets import (
    SECRET_WORDS,
    is_placeholder,
    named_at,
    named_values,
    shape_of,
    vendor_shape,
    without_examples,
)
from .diagnostics import PER_FILE, family, fix_of, listed, tier
from .lines import calls, comment_of, tokens, words
from .syntax import argument, calls_in, classes, defined, functions_in, imports_in, name_of, nodes_in, parameters
from .testing import lines_hit, self_check, self_test, try_rule

__all__ = [
    "PASSED_ON",
    "PER_FILE",
    "RANK",
    "SECRET_WORDS",
    "ChangedFile",
    "Diagnostic",
    "Entry",
    "FileRule",
    "Hit",
    "Rule",
    "Severity",
    "Tree",
    "TreeRule",
    "UnreadError",
    "argument",
    "calls",
    "calls_in",
    "classes",
    "comment_of",
    "defined",
    "family",
    "fix_of",
    "functions_in",
    "imports_in",
    "is_placeholder",
    "is_python",
    "is_test",
    "lines_hit",
    "listed",
    "matches",
    "matching",
    "name_of",
    "named_at",
    "named_values",
    "nodes_in",
    "parameters",
    "parse",
    "self_check",
    "self_test",
    "shape_of",
    "tier",
    "tokens",
    "try_rule",
    "vendor_shape",
    "without_examples",
    "words",
]
