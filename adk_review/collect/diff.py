"""Unified diffs as git prints them, read without git: the one place their syntax is read.

Every function here is pure — text in, text or numbers out — so it is tested on strings alone. `git.py` runs git
and hands each file's diff here: to number the lines a change added, to cap what a model reads and name the cut, to
frame a file's diff for the guidelines in a fence its own backticks cannot close, and to find a file's header,
where git says a file is binary or a submodule.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..core.text import backticks, cut_at, squash

#: Every string the model reads is capped in characters, and the cut is named. Measured live on Copilot at 4,000: a
#: 7,529-character file's planted shell call went unread, a hole, and so did 3 of a 17-file change's files; at 16,000
#: each was read, the call named in both runs, for a fifth more tokens.
DIFF_CAP_CHARS = 16_000
_HUNK_START = re.compile(r"^@@ -\S+ \+(\d+)(,0\b)?")
#: The escapes git writes in a path it quotes, beside a byte's three octal digits.
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
#: What a reader, or `str.splitlines`, breaks a line at besides "\n": a lone `\r`, a form feed, U+2028 among them.
_BREAKS = re.compile("[\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
BINARY = re.compile(r"^Binary files .* differ$", re.MULTILINE)
SUBMODULE = re.compile(r"^(?:new file mode|index \S+) 160000$", re.MULTILINE)


def readable(diff: str) -> str:
    """The diff with "\\n" its only line break: a CRLF file's `\\r` dropped, every other break shown as its escape.
    Measured: a lone `\\r` in an added line split it, so the key after it was no added line for the rule; and text
    after a form feed, with no diff marker, read as the prompt's own."""
    return _BREAKS.sub(lambda m: m.group().encode("unicode_escape").decode(), diff.replace("\r\n", "\n"))


def header(diff: str) -> str:
    """A file's diff before its first hunk, where git prints a submodule's mode and "Binary files … differ": measured,
    searching whole diffs for them took 600 ms over a 66 MB `--all`, the headers alone 12."""
    return diff.partition("\n@@")[0]


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks in `text`: measured, a markdown file's own fence of three,
    a context line of the change, closed the fence of three the change was written in."""
    return "`" * max(3, 1 + backticks(text))


def _hunks(diff: str, aside: list[str] | None = None) -> Iterator[list[tuple[int, str]]]:
    """Each hunk of the diff, as `(new line number, line)` for every line inside it — added, removed or context,
    marker kept — and every line no hunk holds into `aside`, where one is given.

    The one place unified-diff syntax is read. Git's file headers come before
    the first `@@`, and inside a hunk anything that is not a change or context
    line — the next hunk's header, git's `\\ No newline at end of file`, the
    cap's cut marker — is not a line of the file. A removed line carries the
    number of the new line before it, 0 when there is none. Git names an empty
    range by the line before it: measured, `+0,0`, a deleted file's hunk, read
    as the line after it numbered every removed line -1.
    """
    hunk: list[tuple[int, str]] | None = None
    new_line = 0
    for raw in diff.split("\n"):  # git's line break, and no other
        if match := _HUNK_START.match(raw):
            if hunk:
                yield hunk
            new_line, hunk = int(match.group(1)) - (match.group(2) is None), []
        elif hunk is not None and raw[:1] in ("+", "-", " "):
            if raw[:1] != "-":  # a removed line is not in the new file
                new_line += 1
            hunk.append((new_line, raw))
            continue
        if aside is not None:
            aside.append(raw)
    if hunk:
        yield hunk


def _around_hunks(diff: str) -> list[str]:
    """The lines of `diff` no hunk holds, as git printed them, and each hunk's header again without git's guess at the
    function around it, which a quote may leave off."""
    aside: list[str] = []
    for _hunk in _hunks(diff, aside):  # read through for what it sets aside
        pass
    return aside + [line[: line.index(" @@", 2) + 3] for line in aside if _HUNK_START.match(line) and " @@ " in line]


def hunk_lines(diff: str) -> Iterator[tuple[int, str]]:
    """`(new line number, line)` for every line inside the diff's hunks, one hunk after another."""
    for hunk in _hunks(diff):
        yield from hunk


def added_lines(diff: str) -> list[tuple[int, str]]:
    """`(new line number, text)` for every line the change added: a rule that needs "what was added, and where"
    takes this list, never the diff."""
    return [(number, raw[1:]) for number, raw in hunk_lines(diff) if raw[:1] == "+"]


def quotable(diff: str) -> list[list[tuple[int, str]]]:
    """Each hunk's `(new line number, line)` pairs, the lines a guideline may quote from one file's diff as it read it,
    markers kept, hunk by hunk, since a quote stands in one hunk and never across two: nothing git or the cap wrote
    around them, which a finding could otherwise quote and pass as grounded."""
    return list(_hunks(diff))


def framed(path: str, shown: str) -> tuple[str, list[str]]:
    """A file's diff as each guideline reads it — under its heading, in a fence its own backticks cannot close — and
    the lines it was framed in there: the heading, the fence, and what git and the cap wrote around and inside its
    hunks. A guideline quotes a file as it was shown it, so grounding looks past these, and only these: measured by
    review with a list of such lines kept by hand beside this rendering, a file with no final newline and a file cut at
    the cap, each quoted whole, were dropped, git's `\\ No newline at end of file` and the cut's marker on no list."""
    fence = _fence(shown)
    heading, opening = f"### {squash(path)}", f"{fence}diff"
    return f"{heading}\n{opening}\n{shown}\n{fence}", [heading, opening, *_around_hunks(shown), fence]


def capped(diff: str, cap: int = DIFF_CAP_CHARS) -> tuple[str, bool]:
    """The diff as a model may read it: cut at the cap, with the cut named on a line of its own, and never inside a
    credential, as `cut_at` never cuts. The cut comes before the scrub, and half a credential no longer matches its
    shape: measured with the cut at the cap whatever it split, 30 of an AWS secret's 40 characters and four lines of a
    key's body reached every guideline."""
    return cut_at(diff, cap, sep="\n"), len(diff) > cap


def file_added(diff: str) -> dict | None:
    """One file's part of a commit's patch as `{"path", "added"}`, or None where it adds no line: a deleted file, a
    binary one, a rename or a mode alone. The path is the new one, as its `+++` line spells it, quoted or not."""
    to = next((line[4:] for line in header(diff).split("\n") if line.startswith("+++ ")), "/dev/null")
    added = added_lines(diff)
    if to == "/dev/null" or not added:
        return None
    return {"path": _unquoted(to.removesuffix("\t")).removeprefix("b/"), "added": added}


def _unquoted(spelled: str) -> str:
    """A path as git spells it in a patch's header: in double quotes, C's escapes inside, where a byte needs one."""
    if not (len(spelled) > 1 and spelled.startswith('"') and spelled.endswith('"')):
        return spelled
    out, chars = bytearray(), iter(spelled[1:-1])
    for char in chars:
        if char != "\\":
            out += char.encode()
        elif (escaped := next(chars, "")) in _ESCAPES:
            out.append(_ESCAPES[escaped])
        else:  # a byte, in three octal digits
            out.append(int(escaped + next(chars, "") + next(chars, ""), 8))
    return out.decode("utf-8", "replace")
