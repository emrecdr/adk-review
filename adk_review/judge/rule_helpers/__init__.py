"""What a rule's author reaches for, through one door: the kind a rule is one of, and the helpers its `check` reads
with — a path, a line, a module's syntax tree — and tries itself with.

    from ..rule_helpers import Rule, calls, is_test

A rule file needs this one import. The kinds and what they are handed (`Rule`, `FileRule`, `TreeRule`, `ChangedFile`,
`Tree`, `Hit`), the hole a rule raises where it could not look (`UnreadError`) and the scale its severity is on
(`RANK`) are the contract, defined in `core/` and only passed on here; everything else is a helper, defined in the
module here named after what it reads, or passed on from `core/` where a review reads it too: a credential's shape,
which redaction shares, and a test's path. Beside `rules/`, never inside it: that folder holds rules and nothing else.

May import `core` alone, and runs nothing: no process, no network, no model. Standard library only.
"""

from ...core.blast import is_test
from ...core.findings import RANK
from ...core.rules import ChangedFile, FileRule, Hit, Rule, Tree, TreeRule, UnreadError, is_python
from ...core.secrets import (
    SECRET_WORDS,
    is_placeholder,
    named_at,
    named_values,
    shape_of,
    vendor_shape,
    without_examples,
)
from .lines import calls, comment_of, tokens, words
from .paths import matches, matching
from .syntax import argument, calls_in, classes, defined, functions_in, imports_in, name_of, nodes_in, parameters
from .testing import lines_hit, self_check, self_test, try_rule

__all__ = [
    "RANK",
    "SECRET_WORDS",
    "ChangedFile",
    "FileRule",
    "Hit",
    "Rule",
    "Tree",
    "TreeRule",
    "UnreadError",
    "argument",
    "calls",
    "calls_in",
    "classes",
    "comment_of",
    "defined",
    "functions_in",
    "imports_in",
    "is_placeholder",
    "is_python",
    "is_test",
    "lines_hit",
    "matches",
    "matching",
    "name_of",
    "named_at",
    "named_values",
    "nodes_in",
    "parameters",
    "self_check",
    "self_test",
    "shape_of",
    "tokens",
    "try_rule",
    "vendor_shape",
    "without_examples",
    "words",
]
