"""Build the throwaway repository the reviewer is tried on, its defects planted.

A clean `main`, and a `feature/payments` branch that changes four files, three of them new, each carrying one
obvious defect: a `subprocess.run(..., shell=True)`, a hard-coded AWS key pair (Amazon's own documented example
values, so nothing real is here), a function nothing tests, and a copy-pasted block. What code finds is found every
run: the key pair, ruff's shell call and the money rule inside the copied block. A model found the shell call
in all seven live runs on Copilot; the untested function and the copy itself are the guidelines' to notice: one named
the function in three runs of seven and the copy in none, and in five of five once the tests guideline was asked what
a change leaves untested; `scripts/measure.py` measures it again. No network,
repeatable — a golden repository in one script. Idempotent: the target is removed and rebuilt.

    uv run python scripts/make_demo_repo.py            # into the platform temp dir (see the printed path)
    uv run python scripts/make_demo_repo.py --fix      # both blockers fixed

`--fix` is the fix-and-rerun moment: it commits a receipt that is
plain text (printing is the caller's job, so no process is spawned at all) and
a config that reads its keys from the environment, so the same review command
moves from exit 1 to exit 0.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "demo",
    "GIT_AUTHOR_EMAIL": "demo@example.com",
    "GIT_COMMITTER_NAME": "demo",
    "GIT_COMMITTER_EMAIL": "demo@example.com",
    # A throwaway repository, none of the builder's own config: measured, a global `commit.gpgsign` failed the build.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

CHARGE = '''"""Charging a card, the boring way."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Charge:
    amount_cents: int
    currency: str


def charge(amount_cents: int, currency: str = "EUR") -> Charge:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    return Charge(amount_cents, currency)


def format_amount(amount_cents: int, currency: str) -> str:
    return f"{amount_cents / 100:.2f} {currency}"
'''

CHARGE_WITH_RECEIPT = (
    CHARGE
    + '''

def print_receipt(charge: Charge, printer: str) -> int:
    """Send the receipt to a printer by name."""
    import subprocess

    # The printer name comes straight from the request.
    command = f"lp -d {printer} <<< '{format_amount(charge.amount_cents, charge.currency)}'"
    return subprocess.run(command, shell=True, check=False).returncode
'''
)

CONFIG = '''"""Settings for the payments service."""

REGION = "eu-west-1"
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
'''

REFUND = '''"""Refunds, added in a hurry."""

from .charge import Charge


def refund(original: Charge, amount_cents: int) -> Charge:
    """A partial or full refund of a charge. Nothing tests this yet."""
    if amount_cents > original.amount_cents:
        amount_cents = original.amount_cents
    return Charge(-amount_cents, original.currency)
'''

REPORT = '''"""Daily totals."""


def format_total(amount_cents: int, currency: str) -> str:
    return f"{amount_cents / 100:.2f} {currency}"


def daily_total(charges) -> str:
    total = sum(c.amount_cents for c in charges)
    currency = charges[0].currency if charges else "EUR"
    return format_total(total, currency)
'''

CHARGE_FIXED = (
    CHARGE
    + '''

def receipt(charge: Charge) -> str:
    """The receipt text. Printing is the caller's job: this module never spawns a process."""
    return f"receipt: {format_amount(charge.amount_cents, charge.currency)}"
'''
)

CONFIG_FIXED = '''"""Settings for the payments service."""

import os

REGION = "eu-west-1"


def _required(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"{name} is not set; the payments service needs it") from None


AWS_ACCESS_KEY_ID = _required("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _required("AWS_SECRET_ACCESS_KEY")
'''

TESTS = """from payments.charge import charge, format_amount


def test_charge_keeps_the_amount():
    assert charge(1250).amount_cents == 1250


def test_a_zero_charge_is_refused():
    try:
        charge(0)
    except ValueError:
        return
    raise AssertionError("zero was accepted")


def test_format_amount():
    assert format_amount(1250, "EUR") == "12.50 EUR"
"""

BASE_REF, HEAD_REF = "main", "feature/payments"

BASE = {
    "README.md": "# payments (demo)\n\nA tiny service the ADK demo reviews.\n",
    "src/payments/__init__.py": '"""The payments package."""\n',
    "src/payments/charge.py": CHARGE,
    "tests/test_charge.py": TESTS,
}

HEAD = {
    "src/payments/charge.py": CHARGE_WITH_RECEIPT,
    "src/payments/config.py": CONFIG,
    "src/payments/refund.py": REFUND,
    "src/payments/report.py": REPORT,
}

#: What the fix-and-rerun moment commits on top of HEAD: both blockers gone, the other two defects kept.
FIXED = {
    "src/payments/charge.py": CHARGE_FIXED,
    "src/payments/config.py": CONFIG_FIXED,
}


def _git(root: Path, *args: str, check: bool = True) -> int:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        env=ENV,
        check=check,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    ).returncode


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")  # `\r\n` on Windows else, committed as written


def _writable(function, path: str, _error: BaseException) -> None:
    """Git makes its objects read-only, and Windows will not delete a read-only file: make it writable, then again."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def branch(root: Path, name: str, start: str, files: dict[str, str], message: str) -> None:
    """A branch `name` off `start`, holding `files` in one commit."""
    _git(root, "checkout", "-q", "-b", name, start)
    _write(root, files)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)


def build(root: Path) -> Path:
    """Build the repository at `root`, replacing whatever was there."""
    if root.exists():
        shutil.rmtree(root, onexc=_writable)
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", BASE_REF)
    _write(root, BASE)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "payments: charge and format")
    branch(root, HEAD_REF, BASE_REF, HEAD, "payments: receipts, refunds, daily report")
    return root


def fix(root: Path) -> bool:
    """Commit the two fixes on the feature branch of a repository `build` made: True, or False when they are
    committed already, so a second fix changes nothing. Measured by review: the second
    `--fix` ended in a traceback, `git commit` finding nothing to commit."""
    _git(root, "checkout", "-q", HEAD_REF)
    _write(root, FIXED)
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet", check=False) == 0:
        return False
    _git(root, "commit", "-qm", "payments: print receipts without a shell, read keys from the environment")
    return True


def demo_repo() -> Path:
    """Where the repository is built when no path is named: `adk-demo-repo` in the temp folder, /tmp on Linux,
    /var/folders on macOS, %TEMP% on Windows. The measurement reads it from here, to name it to the
    reviewer, which assumes no repository."""
    return Path(tempfile.gettempdir()) / "adk-demo-repo"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is not UTF-8 by default
    # A flag it does not know stops here, never a rebuild: `--help` once rebuilt the repository, `--fix` commits lost.
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", type=Path, help="where to build it (default: adk-demo-repo in the temp dir)")
    parser.add_argument("--fix", action="store_true", help="commit the two fixes on feature/payments")
    args = parser.parse_args(argv)
    root = args.path.expanduser().resolve() if args.path else demo_repo()
    if args.fix:
        if not (root / ".git").exists():  # measured: `git checkout` ended in a traceback
            print(
                f"no demo repository at {root}: build it first, `uv run python scripts/make_demo_repo.py`",
                file=sys.stderr,
            )
            return 1
        if fix(root):
            print(f"fixed {root}: feature/payments now spawns no process and reads its keys from the environment")
        else:
            print(f"{root}: feature/payments is fixed already")
        return 0
    try:
        build(root)
    except OSError as exc:  # Windows deletes no folder a terminal or an editor holds open
        print(
            f"could not replace {root} ({exc.strerror or exc}): close what holds it open and run this again",
            file=sys.stderr,
        )
        return 1
    print(f"built {root}: main, feature/payments (four planted defects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
