"""A line, read as Python reads it: its tokens, its words, the calls it makes and the comment it ends with.

A line rule is handed one added line at a time, so these read a line alone. That is fast and it has a limit: a line
in the middle of a docstring reads as code, since nothing on it says it is inside a string. A rule that must not be
wrong about that reads the file instead, with `calls_in` and its kin over `file.module`.
"""

from __future__ import annotations

import functools
import io
import keyword
import re
import tokenize
from collections.abc import Iterator
from fnmatch import fnmatchcase

#: The part of a called name that must be in a line's text for the line to be worth reading: its last word, up to the
#: first `*`. Measured on the rule this came from: tokenizing every added line took 487 ms, and only those holding
#: the word 12 ms.
_LAST_WORD = re.compile(r"[^.*?\[]*$|[^.*?\[]*(?=[*?\[][^.]*$)")


def tokens(line: str) -> list[tokenize.TokenInfo]:
    """The line as Python reads it, as far as it goes: a comment and a string are not code, an f-string's fields
    are, and a line is often a fragment, an open bracket or the middle of a docstring, whose tokens stop there."""
    found: list[tokenize.TokenInfo] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(line.strip() + "\n").readline):
            found.append(token)  # noqa: PERF402 -- one at a time: what was read before a TokenError is kept
    except (tokenize.TokenError, SyntaxError):
        pass
    return found


def words(line: str) -> list[str]:
    """The line's names, numbers and operators in order, as written: `except :  # all` is `["except", ":"]`. No
    comment and no string is among them, so a word in either is not code."""
    return [token.string for token in tokens(line) if token.type in (tokenize.NAME, tokenize.NUMBER, tokenize.OP)]


def calls(line: str, *names: str) -> bool:
    """Whether `line` calls a function one of `names` names, each as the call is written and `*` for anything.
    `print` is `print(x)`: never `fingerprint(x)`, a comment, a string, the definition `def print(` or the method
    `out.print(x)`, which is `*.print`. `pdb.set_trace` is that dotted name; `*.execute` is a method of that name on
    anything."""
    if not any(_last_word(name) in line for name in names):  # the cheap question first
        return False
    code = [token.string if token.type in (tokenize.NAME, tokenize.OP) else "" for token in tokens(line)]
    return any(fnmatchcase(called, name) for called in _called(code) for name in names)


def comment_of(line: str) -> str:
    """The comment `line` ends with, its `#` included; empty where it has none. A `#` inside a string is no
    comment."""
    if "#" not in line:
        return ""
    return next((token.string for token in tokens(line) if token.type == tokenize.COMMENT), "")


@functools.cache
def _last_word(name: str) -> str:
    """`pdb.set_trace` -> `set_trace`, `*.assert*` -> `assert`: what a line must hold to call the name at all. Cached:
    a rule asks for the same few names on every added line. The pattern matches any name, if only as nothing, which
    every line holds."""
    return found.group() if (found := _LAST_WORD.search(name)) else ""


def _called(code: list[str]) -> Iterator[str]:
    """The name of each call among a line's tokens, dotted as written: `print`, `pdb.set_trace`, and `.get` for a
    method of something with no name (`client().get(`). `code` holds each name and operator, and an empty word where
    a string or a number stood, so nothing is read across one. A `def` or a `class` defines and calls nothing, and a
    keyword before a bracket (`if (`, `return (`) is no call."""
    for index, word in enumerate(code):
        if word != "(" or index == 0 or not _is_name(code[index - 1]):
            continue
        start = index - 1
        while start >= 2 and code[start - 1] == "." and _is_name(code[start - 2]):
            start -= 2
        before = code[start - 1] if start else ""
        if before in ("def", "class"):
            continue
        yield ("." if before == "." else "") + "".join(code[start:index])


def _is_name(word: str) -> bool:
    return word.isidentifier() and not keyword.iskeyword(word)
