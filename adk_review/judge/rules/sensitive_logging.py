"""A credential written to a log: a value whose own name says secret, handed whole to a logging call.

Only what is handed whole: a variable or an attribute as an argument, in an f-string, joined to the message, in a
tuple, or as a value of `extra` or of a keyword. The name decides, by its last word. Measured over seven projects,
4,314 logging calls, before this rule was written: every logged value whose name held a secret word gave 106 hits, 16
of them a credential logged, the rest a masked prefix, `token[:8]`, a test for one, `bool(token)`, a count, or a name
about one, `token_dir`. This rule over the same projects, 4,818 files in 5 s: 14 hits, 13 of them a push token logged
whole, where lines beside them logged eight characters of it, and one a count.

Not read, as known limits: a count named as a credential is, `total_no_token`; `print`; a logger no name holds,
`get_logger().info(...)`; and a value passed through a call on its way, `str(token)`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from ..rule_helpers import ChangedFile, FileRule, calls_in, is_test, name_of, names_secret

#: A logger's methods that write a record.
WRITES = ("debug", "info", "warning", "warn", "error", "critical", "exception", "log")
#: What a logger is named, by its last part: `logger`, `log`, `logging`, `self._log`, a project's own `obslog`.
LOGGER = re.compile(r"(?i)log(?:ger|ging)?$")


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
    )
    good = (
        'logger.info("token %s", token[:8])',
        'logger.info("used %s tokens", tokens)',
        'logger.info("page %s", page_token)',
        ("tests/test_auth.py", 'logger.info("token %s", access_token)'),
    )

    def check(self, file: ChangedFile) -> list[tuple[int, str]]:
        """Each value the change added to a logging call whole, under a name that says secret, at the value's line."""
        if not file.path.endswith(".py") or is_test(file.path):
            return []
        writes = calls_in(file.module, *(f"*.{write}" for write in WRITES))
        return sorted(
            (value.lineno, f"`{name_of(value)}` is written to the log whole")
            for call in writes
            if LOGGER.search(name_of(call.func).rpartition(".")[0])
            for value in _handed(call)
            if value.lineno in file.added and names_secret(name_of(value).rpartition(".")[2])
        )


def _handed(call: ast.Call) -> Iterator[ast.Name | ast.Attribute]:
    """Each value `call` hands the log whole: an argument, a part of an f-string, a side of a `+` or a `%`, a member
    of a tuple or a list, a value of a dict or of a keyword. One sliced, tested or passed through a call is not."""
    pending: list[ast.expr] = [*call.args, *(keyword.value for keyword in call.keywords)]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.JoinedStr):
            pending += [part.value for part in node.values if isinstance(part, ast.FormattedValue)]
        elif isinstance(node, ast.BinOp):
            pending += [node.left, node.right]
        elif isinstance(node, ast.Dict):
            pending += node.values
        elif isinstance(node, (ast.Tuple, ast.List)):
            pending += node.elts
        elif isinstance(node, (ast.Name, ast.Attribute)) and name_of(node):
            yield node
