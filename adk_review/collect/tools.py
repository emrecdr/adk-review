"""Each outside tool a rule may name, and the one adapter that runs it: on copies of the files it reads, in a folder
of its own, with the rule's settings and never the repository's own configuration. Ruff is the first; another tool is
one adapter more here and one name in a rule, and the rule contract stays as it is.
"""

from __future__ import annotations

from collections.abc import Callable

from ..core.rules import Diagnostic
from .ruff import diagnose

#: Each tool a rule may name, and its adapter: the evidence and the rule's settings in, each changed file's
#: diagnostics by path out, or, as a string, why the tool could not look.
ADAPTERS: dict[str, Callable[..., dict[str, list[Diagnostic]] | str]] = {"ruff": diagnose}
