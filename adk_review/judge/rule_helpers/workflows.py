"""A GitHub workflow's line, read as YAML reads a key: with the line under it, where the key's value stands there.

A workflow rule reads a file's lines, or as many as its diff shows, one at a time. YAML reads a key and a value
written on the line under it, plain or as a folded or literal block, as one key, so a rule that reads a line alone
misses that key. Each rule that reads `uses:` reads it here, so a spelling one is taught the other reads too: read by
the simplify pass, the two drifted twice.
"""

from __future__ import annotations

import re

#: A `uses:` key as a step (`- uses:`) or a job (`uses:`) writes it, up to its value. The key in quotes, or with a
#: space before its colon, is the same key: read by an audit, `"uses": actions/checkout@v4` and
#: `uses : actions/setup-python@v5` named none.
USES_KEY = r"""^\s*(?:-\s+)?(?:uses|"uses"|'uses')\s*:\s*"""
#: A `uses:` key whose value YAML reads from the line under it: nothing after the key, or a folded or literal block's
#: `>` or `|`, its chomping sign and its indent's digit in either order, a comment after it. Read by an audit, and
#: reproduced: `uses:` on one line and `actions/checkout@v4` on the next was read as no key at all, and `uses: >-2`.
USES_OPEN = re.compile(USES_KEY + r"(?:[>|](?:[1-9][-+]?|[-+][1-9]?)?\s*)?(?:#.*)?$")


def uses_joined(line: str, below: str | None) -> str:
    """`line` as a rule reads a `uses:` key: the key with its value from `below`, the line under it, where YAML reads
    the value from there, and `line` as it is otherwise. Not read: a value a comment or a blank line parts from its
    key."""
    return "uses: " + (below or "").strip() if USES_OPEN.match(line) else line
