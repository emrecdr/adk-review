"""A credential written to a log: a value whose own name says secret, handed whole to a logging call.

Only what is handed whole: a variable or an attribute as an argument, in an f-string, a t-string or a string's `format`,
joined to the message, as either side of `or`, the last value of `and` or either answer of a conditional, in a tuple, a
list or a set, unpacked with `*` or bound with `:=`, or as a value of `extra` or of a keyword. The name decides, by its
last word. Measured over seven projects, 4,314 logging calls, before this rule was written: every logged value whose
name held a secret word gave 106 hits, 16 of them a credential logged, the rest a masked prefix, `token[:8]`, a test for
one, `bool(token)`, a count, or a name about one, `token_dir`. This rule over the same projects, 4,818 files in 5 s: 14
hits, 13 of them a push token logged whole, where lines beside them logged eight characters of it, and one a count. Read
by an audit, `format`, `or`, `and`, a conditional, `private_key` and a passphrase were then read too, and arithmetic but
`+` and `%` no longer: the same 14 hits, and the same 22 over the installed packages. Read by a code review, a t-string,
`:=`, `*`, a set, `fatal`, a `.pyw` file and a script Python runs by its first line were then read too: the same 14 hits
over the projects' 3,800 Python files, and the same 22.

Known limits: a count named as a credential is, `total_no_token`, or a sum of two such counts, `max_token +
used_token`, which `+` reads as a joined string, is named. Not read: `print`; a logger no name holds,
`get_logger().info(...)`; a value passed through a call on its way, `str(token)`, other than a string's own `format`;
and a value on a line the change left alone, under a call it made a logger's: read by review, naming it there named
each leak a reworded message stood over, which the change did not add.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from typing import Any, TypeGuard

from ..rule_helpers import ChangedFile, FileRule, calls_in, is_python, is_test, name_of, names_secret

#: A logger's methods that write a record.
WRITES = ("debug", "info", "warning", "warn", "error", "critical", "fatal", "exception", "log")
#: What a logger's name ends in: `logger`, `log`, `logging`, `self._log`, `LOG`, a project's own `obslog`.
LOGGER = ("log", "logger", "logging")
#: And the words that only end so, however they are written: `change_log`, `changeLog` and `changelog` are one list of
#: changes, and no logger. Read by review: `catalog.info(token)` and `dialog.warning(password)` were named.
NO_LOGGER = frozenset(
    {"catalog", "dialog", "changelog", "backlog", "analog", "prolog", "epilog", "travelog"}
    | {"blog", "blogger", "blogging"}
)
#: What each kind of expression hands a log whole of its parts: a part of an f-string, or of a t-string, whose repr a
#: log writes with its values; a side of a `+` or a `%`, but no other arithmetic; a side of an `or`, and the last value
#: of an `and`, which hands on an earlier one only where it is falsy; either answer of a conditional, never its test; a
#: member of a tuple, a list or a set; a value of a dict; and what `:=` binds or `*` unpacks. Read by an audit, and
#: reproduced: `max_token - used_token` was named, which logs neither; read by a code review, `:=`, `*`, a set and a
#: t-string were not.
HANDS_ON: dict[type[ast.expr], Callable[[Any], list[ast.expr]]] = {
    ast.JoinedStr: lambda node: [part.value for part in node.values if isinstance(part, ast.FormattedValue)],
    ast.TemplateStr: lambda node: [part.value for part in node.values if isinstance(part, ast.Interpolation)],
    ast.BinOp: lambda node: [node.left, node.right] if isinstance(node.op, (ast.Add, ast.Mod)) else [],
    ast.BoolOp: lambda node: node.values if isinstance(node.op, ast.Or) else node.values[-1:],
    ast.IfExp: lambda node: [node.body, node.orelse],
    ast.Tuple: lambda node: node.elts,
    ast.List: lambda node: node.elts,
    ast.Set: lambda node: node.elts,
    ast.Dict: lambda node: node.values,
    ast.NamedExpr: lambda node: [node.value],
    ast.Starred: lambda node: [node.value],
}
#: A name's words: at each `_` and at each hump of camelCase.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


class SensitiveLogging(FileRule):
    """A log is kept longer than the code runs, copied to other systems, and read by people the credential was never
    meant for: a token written there is a token leaked."""

    id = "sensitive-logging"
    severity = "major"
    security = True  # a verifier may dispute its finding, never drop it
    title = "a credential written to a log"
    fix = "Log that the value is set, or a short prefix of it. Never log the value itself."
    why = "A log is kept, copied to other systems, and read by people the credential was never meant for."
    bad = (
        'logger.info("token %s", access_token)',
        'log.debug(f"auth={api_key}")',
        'self.logger.warning("login failed", extra={"password": user.password})',
        'logger.debug("key {}".format(private_key))',
    )
    good = (
        'logger.info("token %s", token[:8])',
        'logger.info("left %d", max_token - used_token)',
        'logger.info("used %s tokens", tokens)',
        'logger.info("page %s", page_token)',
        ("tests/test_auth.py", 'logger.info("token %s", access_token)'),
    )

    def check(self, file: ChangedFile) -> list[tuple[int, str]]:
        """Each value the change added to a logging call whole, under a name that says secret, at the value's line."""
        # Read by a code review: a script Python runs by its first line, and a `.pyw` file, were left to lint alone.
        if not is_python(file.path, lambda: file.line(1)) or is_test(file.path) or isinstance(file.module, str):
            return []
        return sorted(  # a set: read by a code review, a value logged twice was named twice, and folded into itself
            {
                (value.lineno, f"`{name_of(value)}` is written to the log whole")
                for call in calls_in(file.module, *(f"*.{write}" for write in WRITES))
                if _logs(name_of(call.func).rpartition(".")[0])
                for value in _handed(call)
                if value.lineno in file.added and names_secret(name_of(value).rpartition(".")[2])
            }
        )


def _logs(receiver: str) -> bool:
    """Whether what a call is made on is named as a logger is, by the words of its last part: the last one ends as a
    logger's does, and no run of its closing words spells one of `NO_LOGGER`. So `weblogger` and `catalogLogger` are
    loggers, and `myBlogger` and `productCatalog` are none. A `web_log` is a logger: read by review, as a weblog it
    was none, and a leak missed costs more than a blog named once."""
    words = [word.lower() for word in _WORD.findall(receiver.rpartition(".")[2])]
    if not words or not words[-1].endswith(LOGGER):
        return False
    return not any("".join(words[at:]) in NO_LOGGER for at in range(len(words)))


def _handed(call: ast.Call) -> Iterator[ast.Name | ast.Attribute]:
    """Each value `call` hands the log whole: an argument, a value of a keyword, and what `HANDS_ON` and a string's own
    `format` pass on of it. One sliced, tested or passed through another call is not."""
    pending: list[ast.expr] = [*call.args, *(keyword.value for keyword in call.keywords)]
    while pending:
        node = pending.pop()
        if _formats(node):
            pending += [*node.args, *(keyword.value for keyword in node.keywords)]
        elif (parts := HANDS_ON.get(type(node))) is not None:
            pending += parts(node)
        elif isinstance(node, (ast.Name, ast.Attribute)) and name_of(node):
            yield node


def _formats(node: ast.expr) -> TypeGuard[ast.Call]:
    """Whether `node` is a string's own `format`, as `"token {}".format(token)`: the values it is given are written
    whole, as an f-string's are."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    )
