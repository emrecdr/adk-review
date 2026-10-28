"""Text a reader cannot see, in any file a change adds to: the tag characters, the bidirectional controls, and what
smuggles text past a reader as they do.

A line of tag characters (U+E0000 to U+E007F) spells a sentence no editor shows and every model reads, an order to the
reviewer among them. A bidirectional override or isolate makes a line read in another order than a program reads it,
the "Trojan Source" shape: a comment that seems to close, a string that seems to end. Ruff names the controls in a
Python file's code (PLE2502); this rule reads every file, the docs and the configuration a model also reads. The
marks a right-to-left script needs, U+200E and U+200F, and the joiners of emoji and Indic text are left alone: they
are how those scripts are written. Read by review: text can be smuggled in the variation selectors behind an emoji,
the supplementary ones carrying its bytes, where an emoji takes one basic selector, so the supplementary ones are
named, and two or more basic ones in a row; and the invisible operators and the Hangul fillers show nothing, a filler
having served as an invisible name in code.
"""

from __future__ import annotations

import re

from ..rule_helpers import Rule

#: The characters, by code point, so this file holds none of them: the tag characters; the bidirectional embeddings,
#: overrides and isolates; the supplementary variation selectors; the invisible operators; and the Hangul fillers.
HIDDEN = frozenset(
    chr(point)
    for first, last in ((0xE0000, 0xE007F), (0x202A, 0x202E), (0x2066, 0x2069), (0xE0100, 0xE01EF), (0x2061, 0x2064))
    for point in range(first, last + 1)
) | frozenset(map(chr, (0x115F, 0x1160, 0x3164, 0xFFA0)))
#: Two or more basic variation selectors in a row: an emoji takes one, smuggled text strings them.
SELECTOR_RUN = re.compile("[\ufe00-\ufe0f]{2,}")


class HiddenText(Rule):
    """What a reader cannot see cannot be reviewed, and what a model reads unseen can give it orders."""

    id = "hidden-text"
    severity = "major"
    reads_tests = True  # a test's text reaches its readers, and a model, as any file's does
    title = "text a reader cannot see: tag characters, bidirectional controls or smuggled selectors"
    fix = "Remove the hidden characters, so a reader sees what a program or a model reads."
    why = "A person reviews what they see, but a program or a model reads the hidden text too."
    bad = (
        'access = "user"  # \u202e admin only',
        "x = 1  # \U000e0069\U000e0067\U000e006e\U000e006f\U000e0072\U000e0065",
    )
    good = ("x = 1  # a plain comment", 'arrow = "\u2192"', ("notes.md", "caf\u00e9"))

    def check(self, path: str, line: str) -> bool:  # noqa: ARG002 -- every file: hidden text hides anywhere
        if line.isascii():  # most lines are, and an ASCII line holds none of them
            return False
        return not HIDDEN.isdisjoint(line) or SELECTOR_RUN.search(line) is not None
