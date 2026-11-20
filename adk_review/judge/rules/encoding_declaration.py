"""A source-encoding declaration that turns plain text into other code: what every rule reads is not what Python runs.

PEP 263 lets a Python file name its encoding on its first or second line, and Python decodes the file with it. A codec
such as `utf_7` or `unicode_escape` reads plain ASCII as other characters: `+AGk-` is an `i`, `\\x69` is too. The
reviewer reads every file as UTF-8, as git stores it, so lint, the syntax-tree rules and the secrets rule all read the
encoded text. Measured: a `shell=True` call written in `utf_7` ran under Python, and no rule named it; the same call
written plainly is a lint major. A file with such a declaration is a hole, never a finding below the bar: no one read
the code it runs. So is one whose text goes past ASCII in a codec that reads a byte past ASCII and a plain one after
it as one character, as Shift JIS does: the quote or backslash UTF-8 shows is then part of another character.
"""

from __future__ import annotations

import codecs
import functools
import re

from ..rule_helpers import ChangedFile, FileRule, UnreadError, is_python

#: PEP 263's declaration: a comment on the first or second line naming a codec.
DECLARATION = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
#: A line break as Python reads source, a lone CR among them: git, and the file's lines, break at "\n" alone.
#: Measured by review: a lone CR ending a blank first line put the declaration on Python's second line and on no
#: line of the file's own, and code Python ran as UTF-7 was approved.
PYTHON_BREAK = re.compile(r"\r\n?|\n")
#: Plain text no codec Python accepts for code may change, and what the codecs that rewrite code read as other
#: characters: a UTF-7 shift, three backslash escapes, ISO-2022's shifts into a Japanese and a Korean character set,
#: and a label between two dots, which `idna` reads as Unicode. A backslash on its own is left out: an escape it does
#: not start is deprecated. Read by an audit, and reproduced: a file declared `iso2022_jp` ran a call that, read as
#: UTF-8, sat in a comment.
PROBE = bytes(range(32, 127)).replace(b"\\", b"") + (
    b'\t\n +AGk- \\x69 \\u0069 \\151 \x1b$B"#\x1b(B \x1b$)C\x0e"#\x0f .xn--ls8h. '
)


def rewrites(codec: str) -> bool:
    """Whether Python, decoding with `codec`, would read other code than the plain text: a codec that changes the
    probe, fails on it, or is unknown here."""
    try:
        return PROBE.decode(codecs.lookup(codec).name) != PROBE.decode("ascii")
    except LookupError, UnicodeDecodeError, ValueError:
        return True


class EncodingDeclaration(FileRule):
    """Code Python runs and the reviewer cannot read: a hole, so the review never approves what no one read."""

    id = "encoding-declaration"
    severity = "blocker"
    reads_tests = True  # a test is code Python runs too, in CI with the job's keys
    title = "an encoding that rewrites the code every rule reads"
    fix = "Save the file as UTF-8 and remove the encoding declaration."
    why = "Python reads the file in that encoding, so the code that runs can differ from the code a reviewer sees."
    bad = (
        "# -*- coding: utf_7 -*-\nx = 1\n",
        "#!/usr/bin/env python\n# coding=raw_unicode_escape\n",
        "\r# -*- coding: utf_7 -*-\nx = 1\n",
    )
    good = ("# -*- coding: utf-8 -*-\nx = 1\n", "# coding: latin-1\nx = 1\n", ("notes.txt", "# coding: utf_7\n"))

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        if not is_python(file.path, lambda: file.line(1)):
            return []
        for line in PYTHON_BREAK.split("\n".join(file.lines[:2]))[:2]:  # Python's first two lines
            if (found := DECLARATION.match(line)) and _misread(found.group(1), file):
                raise UnreadError(f"{file.path} declares {found.group(1)}: Python runs code no rule read")
        return []


def _misread(codec: str, file: ChangedFile) -> bool:
    """Whether Python, decoding the file with `codec`, reads other code than its plain text: a codec that rewrites
    plain text, or one that joins a byte past ASCII with the plain one after it, where the file's text holds such a
    byte."""
    return rewrites(codec) or (joins(codecs.lookup(codec).name) and not all(line.isascii() for line in file.lines))


@functools.cache  # by the codec's own name, one of those Python ships, however a file spells it
def joins(codec: str) -> bool:
    """Whether `codec` reads a byte past ASCII and a plain one after it as one character. Read by the simplify pass,
    and reproduced: a file declared `shift_jis` ran a call that, read as UTF-8, sat in a string, since the backslash
    UTF-8 shows ended a character there. Measured over the 80 codecs `rewrites` passes: nine join, each Chinese,
    Japanese or Korean, and asking all 80 took 0.8 s, one at most a few milliseconds."""
    return any(_one_character(bytes((lead, plain)), codec) for lead in range(0x80, 0x100) for plain in range(0x80))


def _one_character(pair: bytes, codec: str) -> bool:
    """Whether `codec` decodes the two bytes as one character, as Python decodes source: a pair it refuses is none,
    since Python runs no file it cannot decode."""
    try:
        return len(pair.decode(codec)) == 1
    except UnicodeDecodeError:
        return False
