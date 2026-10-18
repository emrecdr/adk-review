"""Text a reader cannot see, in any file a change adds to: the tag characters and the bidirectional controls.

A line of tag characters (U+E0000 to U+E007F) spells a sentence no editor shows and every model reads, an order to the
reviewer among them. A bidirectional override or isolate makes a line read in another order than a program reads it,
the "Trojan Source" shape: a comment that seems to close, a string that seems to end. Ruff names the controls in a
Python file's code (PLE2502); this rule reads every file, the docs and the configuration a model also reads. The
marks a right-to-left script needs, U+200E and U+200F, and the joiners of emoji and Indic text are left alone: they
are how those scripts are written.
"""

from __future__ import annotations

from ..rule_helpers import Rule

#: The characters, by code point, so this file holds none of them: the tag characters, and the bidirectional
#: embeddings, overrides and isolates.
HIDDEN = frozenset(
    chr(point)
    for first, last in ((0xE0000, 0xE007F), (0x202A, 0x202E), (0x2066, 0x2069))
    for point in range(first, last + 1)
)


class HiddenText(Rule):
    """What a reader cannot see cannot be reviewed, and what a model reads unseen can give it orders."""

    id = "hidden-text"
    severity = "major"
    reads_tests = True  # a test's text reaches its readers, and a model, as any file's does
    title = "text a reader cannot see: tag characters or bidirectional controls"
    fix = "Remove the hidden characters, so a reader sees what a program or a model reads."
    bad = (
        'access = "user"  # \u202e admin only',
        "x = 1  # \U000e0069\U000e0067\U000e006e\U000e006f\U000e0072\U000e0065",
    )
    good = ("x = 1  # a plain comment", 'arrow = "\u2192"', ("notes.md", "caf\u00e9"))

    def check(self, path: str, line: str) -> bool:  # noqa: ARG002 -- every file: hidden text hides anywhere
        return not line.isascii() and not HIDDEN.isdisjoint(line)  # most lines are ASCII, which holds none of them
