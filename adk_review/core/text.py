"""The string rules every group shares: whitespace flattened, lines numbered as git numbers them, and a cut that names
itself and never halves a credential.

Pure, and here rather than beside their first callers because both sides of
the model boundary use them: `collect` cuts a diff before a model reads it,
and `deliver` cuts a quote before a person does.
"""

from __future__ import annotations

import re

from .secrets import spans

#: A reason is read by a person: a verifier's prose, a raw exception that escaped the engine; 400 characters say it.
REASON_MAX_CHARS = 400
#: A quote is read by a person, who has its file and line for the rest, or by the chat's verdict agent. A rule quotes
#: the whole added line, and a minified one ran to 200,000 characters. Cut after the scrub, so no cut halves a
#: credential past its pattern.
EVIDENCE_MAX_CHARS = 400
#: A finding's title and its change are a model's own words, read by a person and by the coding agent a hand-off is
#: for. Nothing capped them, and an injection that survives into one needs room: a title is one line, a change a few
#: sentences or a short snippet. Cut where the finding is made, so every reader reads the same.
TITLE_MAX_CHARS = 200
SUGGESTION_MAX_CHARS = 1_200


def squash(text: str) -> str:
    """`text` with every run of whitespace, newlines included, as one space."""
    return " ".join(text.split())


def lines_of(text: str) -> list[str]:
    """`text` as git numbers its lines, the numbers a diff's hunk headers and the report carry: broken at "\\n" alone,
    a CRLF one break. `str.splitlines` breaks at a form feed, U+2028 and six more besides, and measured, one form feed
    put every line after it one off, so a file rule read the wrong line and a directive hid from the rule that guards
    the guidelines."""
    lines = text.replace("\r\n", "\n").split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def backticks(text: str) -> int:
    """The longest run of backticks in `text`, 0 for none: a fence or a code span one longer is one the text's own
    backticks cannot close, for a diff the guidelines read and a quote the report prints alike."""
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


def cut_at(text: str, limit: int, *, sep: str = " ") -> str:
    """`text` cut at `limit` characters with the cut named, as every cap in the reviewer names its cut, and never inside
    a credential: the cut moves to the start of one it would split, and names where it fell. Half a credential matches
    no shape, so no scrub after the cut would hide it: measured with the cut where the cap fell, 30 of an AWS secret's
    40 characters reached every guideline, and a cap a scrub did not follow sent 20 characters of a token.

    `sep` sits between the kept text and the marker: a space inline, a newline after a block.
    """
    if len(text) <= limit:
        return text
    found = spans(text)
    while straddled := [start for start, end in found if start < limit < end]:
        limit = min(straddled)
    return f"{text[:limit]}{sep}[… cut at {limit:,} characters]"
