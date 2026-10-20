"""Each outside tool a rule may name, and the one adapter that runs it: on copies of the files it reads, in a folder
of its own, with the rule's settings and never the repository's own configuration: ruff, and pyrefly. Another tool is
one adapter more here and one name in a rule, and the rule contract stays as it is.
"""

from __future__ import annotations

from collections.abc import Callable

from ..core.rules import Diagnostic
from . import pyrefly, ruff

#: Each tool a rule may name, and its adapter: the evidence and the rule's settings in, each changed file's
#: diagnostics by path out, or, as a string, why the tool could not look.
ADAPTERS: dict[str, Callable[..., dict[str, list[Diagnostic]] | str]] = {
    "ruff": ruff.diagnose,
    "pyrefly": pyrefly.diagnose,
}
#: Each tool by the name a rule gives it, and which one would run, its version among it: what a rule's findings
#: depend on beside the change, so a review kept under one tool is never served under another.
IDENTITIES: dict[str, Callable[[], str]] = {"ruff": ruff.identity, "pyrefly": pyrefly.identity}
