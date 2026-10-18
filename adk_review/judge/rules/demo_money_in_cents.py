"""The payments project's own rule: money stays in whole cents, from the charge to the receipt."""

from __future__ import annotations

import re
import tokenize

from ..rule_helpers import Rule, tokens

#: Where one word of a name ends: `amount_cents`, `amountCents` and `AMOUNT_CENTS` each hold the word `cents`.
_WORDS = re.compile(r"_|(?<=[a-z])(?=[A-Z])")


class MoneyInCents(Rule):
    """Money is whole cents here. Dividing them by a number turns them into a float, where 0.1 + 0.2 is not 0.3; a
    receipt formats cents with integer arithmetic (`divmod`) or `Decimal`."""

    id = "demo-money-in-cents"
    severity = "major"
    title = "cents divided into a float"
    fix = "Keep money in whole cents: format them with divmod or Decimal, never a float division."
    bad = ('    return f"{amount_cents / 100:.2f} {currency}"', "total_cents /= 100", "float(amount_cents) / 100")
    good = (
        "whole = amount_cents // 100",
        "rate = percents / 100",
        "amount_cents / Decimal(100)",
        ("tests/test_a.py", "cents / 100"),
    )

    def check(self, path: str, line: str) -> bool:
        if not path.endswith(".py") or "cents" not in line.lower():
            return False
        code = [token for token in tokens(line) if token.string != ")"]  # `float(amount_cents) / 100` divides too
        return any(
            name.type == tokenize.NAME
            and _holds_cents(name.string)
            and op.string in ("/", "/=")
            and by.type == tokenize.NUMBER
            for name, op, by in zip(code, code[1:], code[2:], strict=False)
        )


def _holds_cents(name: str) -> bool:
    return "cents" in (word.lower() for word in _WORDS.split(name))
