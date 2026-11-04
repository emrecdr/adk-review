"""A labelled set: branches each holding one known defect, or none, to measure what the guidelines find and what they
invent. `uv run python scripts/labelled_set.py [--runs K] [--pause S] [--profile NAME | --check ID] [--branch NAME]
[--build-only]`.

Matching findings to known facts is reliable, at kappa 70.6 to 86.7, where scoring usefulness is not. Each branch is off
the demo's `main`, a ledger's settlement committed beside it: a modification that builds SQL from input, a loop that
misses its last item, a function with no test, a long file whose defect sits past the guidelines' cut, a container that
runs privileged; what `simplify` and `code-review` add guidelines for, a file read again for each item of a loop, a
shared function patched for one caller's input and a function copied with one value changed; a settlement's return
changed to the balance its statement clamps nine lines above, which forgives a debt its docstring says is carried; a
guard deleted from a charge, the defect on lines the change deleted alone; and six clean changes, a document, a rename,
a function renamed with its caller whose old name only the diff's deleted lines hold, a division a guard twelve lines
above makes safe, an unused parameter removed at every use, and a config's paths moved to a new volume. The settlement's
two show a guideline their cause only within the function around the hunk, git's three lines of context leave it out.
Each is reviewed K times, under `--profile` where one is named, each guideline `--check` names alone where one is, and
the default profile otherwise. A labelled branch scores the runs in which a guideline named a finding on the defect's
lines; a clean branch scores the runs a guideline made a finding at major or above, a false one; each says which
guidelines did. A second table counts each clean branch's false findings at every severity, by guideline and run, and
those of them flagged possibly already done, since false minors are what a reader of a comment sees most. Injection
attempts are `scripts/injection_suite.py`'s. Each review calls the model: run it only when a live measurement is
wanted; `--build-only` builds the branches and calls nothing.
"""

from __future__ import annotations

import tempfile
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from make_demo_repo import BASE_REF, CHARGE, TESTS, _git, branch, build, commit
from measure import GUIDELINE, ROOT, judged, options, picked, reviewed, tally

#: The characters of a file's diff a guideline reads, as the shipped config sets them: each review here runs the
#: command as it ships.
CAP = tomllib.loads((ROOT / "adk_review" / "config.toml").read_text(encoding="utf-8"))["review"]["diff_cap"]
#: Functions enough that the long file's defect lies past that cap, some fifty characters and four lines each.
STEPS = CAP // 32
LONG = "".join(f"def step_{n}(value: int) -> int:\n    return value + {n}\n\n\n" for n in range(STEPS))
#: A ledger's settlement, which the labelled set commits on its own `main`, beside the demo's: a function long enough
#: that git's three lines of context leave out its start, so a branch changing its end shows a guideline what only the
#: function around the hunk holds.
SETTLE = '''"""Settling a ledger at the end of the day."""

import logging

log = logging.getLogger(__name__)


def settle(balance_cents: int, charges: list[int], refunds: list[int]) -> int:
    """The balance after the day's charges and refunds. A debt, a balance below zero, is carried to the next day and
    never forgiven."""
    if not charges and not refunds:
        return balance_cents
    shown = balance_cents
    if shown < 0:
        shown = 0  # the statement prints a debt as nothing owed; the balance itself keeps it
    charged = 0
    for amount in charges:
        charged += amount
    refunded = 0
    for amount in refunds:
        refunded += amount
    net = charged - refunded
    log.info("statement: %d cents owed, %d cents today", shown, net)
    return balance_cents + net
'''
#: A validator a field is passed through and never read, and a landing zone's paths: what two clean branches change, as
#: a reviewed branch did. Read by review on it: removing a parameter at every use drew findings that read its deleted
#: lines as held code, and moving a path to a new volume drew majors that were advice to check access.
VALIDATOR = '''"""Checking a charge before it is sent."""

from .charge import Charge


class Validator:
    def __init__(self, limit_cents: int, correlation_id: str = "") -> None:
        self._limit_cents = limit_cents
        self._correlation_id = correlation_id

    def check(self, charge: Charge) -> bool:
        return 0 < charge.amount_cents <= self._limit_cents


class StrictValidator(Validator):
    def __init__(self, limit_cents: int, correlation_id: str = "") -> None:
        super().__init__(
            limit_cents,
            correlation_id=correlation_id,
        )

    def check(self, charge: Charge) -> bool:
        return super().check(charge) and charge.currency == "EUR"


def make_validator(limit_cents: int, strict: bool, correlation_id: str = "") -> Validator:
    kind = StrictValidator if strict else Validator
    return kind(
        limit_cents,
        correlation_id=correlation_id,
    )
'''
LANDING = """streams:
  charges_data:
    stream_category: landing_zone
    location: /Volumes/payments_$ENV/inbound/landing_zone/charges/data/
  charges_trigger:
    stream_category: landing_zone
    location: /Volumes/payments_$ENV/inbound/landing_zone/charges/trigger/
"""
ON_MAIN = {"src/payments/settle.py": SETTLE, "src/payments/validator.py": VALIDATOR, "config/landing.yaml": LANDING}
#: Each branch: the files it writes over the demo's `main`, and its label — the file and the lines of its one
#: defect, or None for a clean change. A guideline names the defect when a finding of its stands on those lines.
BRANCHES: dict[str, tuple[dict[str, str], tuple[str, range] | None]] = {
    "sql-from-input": (
        {"src/payments/charge.py": CHARGE + '''

def find_charges(db, customer: str):
    return db.execute(f"SELECT * FROM charges WHERE customer = '{customer}'")
'''},
        ("src/payments/charge.py", range(23, 25)),
    ),
    "misses-the-last": (
        {"src/payments/report.py": '''"""The daily report."""


def total(charges: list[int]) -> int:
    result = 0
    for index in range(len(charges) - 1):
        result += charges[index]
    return result
'''},
        ("src/payments/report.py", range(5, 8)),
    ),
    "untested-function": (
        {"src/payments/refund.py": '''"""Refunds."""


def refund(amount_cents: int, refunded_cents: int) -> int:
    if refunded_cents > amount_cents:
        raise ValueError("refund larger than the charge")
    return amount_cents - refunded_cents
'''},
        ("src/payments/refund.py", range(4, 8)),
    ),
    "long-file": (
        {"src/payments/steps.py": '"""Steps."""\n\n\n' + LONG + '''def run(command: str) -> int:
    import subprocess

    return subprocess.run(command, shell=True, check=False).returncode
'''},
        ("src/payments/steps.py", range(4 + 4 * STEPS, 8 + 4 * STEPS)),  # past the docstring, two blanks and the steps
    ),
    "privileged-container": (
        {"deploy/pod.yaml": '''apiVersion: v1
kind: Pod
metadata:
  name: payments
spec:
  containers:
    - name: payments
      image: payments:latest
      securityContext:
        privileged: true
'''},
        ("deploy/pod.yaml", range(9, 11)),
    ),
    "repeated-io": (
        {"src/payments/ledger.py": '''"""Totals by customer, from the ledger file."""

import json
from pathlib import Path


def totals(customers: list[str], ledger: Path) -> dict[str, int]:
    result = {}
    for customer in customers:
        charges = json.loads(ledger.read_text(encoding="utf-8"))
        result[customer] = sum(c["amount_cents"] for c in charges if c["customer"] == customer)
    return result
'''},
        ("src/payments/ledger.py", range(9, 11)),
    ),
    "special-case": (
        {"src/payments/charge.py": CHARGE.replace(
            "def format_amount(amount_cents: int, currency: str) -> str:\n",
            "def format_amount(amount_cents: int, currency: str) -> str:\n"
            '    if currency == "eur":  # the import job sends lower-case codes, and the receipts showed "eur"\n'
            '        currency = "EUR"\n',
        )},
        ("src/payments/charge.py", range(19, 21)),
    ),
    "copied-block": (
        {"src/payments/totals.py": '''"""Totals by currency."""


def eur_total(amounts: list[tuple[int, str]]) -> int:
    total = 0
    for amount_cents, currency in amounts:
        if currency == "EUR":
            total += amount_cents
    return total


def usd_total(amounts: list[tuple[int, str]]) -> int:
    total = 0
    for amount_cents, currency in amounts:
        if currency == "USD":
            total += amount_cents
    return total
'''},
        ("src/payments/totals.py", range(4, 18)),
    ),
    "forgives-debt": (
        {"src/payments/settle.py": SETTLE.replace("    return balance_cents + net\n", "    return shown + net\n")},
        ("src/payments/settle.py", range(24, 25)),
    ),
    # Read by review: a guideline told to report a defect only in code the branch holds might pass over a guard the
    # change deleted. Here the defect is lines the change deleted alone, the guard that refused a non-positive amount,
    # and a deleted line is numbered with the head's line before it, 12.
    "drops-the-guard": (
        {"src/payments/charge.py": CHARGE.replace(
            '    if amount_cents <= 0:\n        raise ValueError("amount must be positive")\n', ""
        )},
        ("src/payments/charge.py", range(12, 14)),
    ),
    "clean-docs": ({"README.md": "# payments (demo)\n\nA tiny service the ADK demo reviews, in euro cents.\n"}, None),
    "clean-rename": (
        {"src/payments/charge.py": CHARGE.replace("def format_amount(amount_cents: int", "def format_amount(cents: int")
         .replace("{amount_cents / 100:.2f}", "{cents / 100:.2f}")},
        None,
    ),
    # Read by review on a branch that renamed a function and removed a field with every use: guidelines asked for a
    # change the head held, the old name being in the diff's deleted lines. Here the old name is in no line of the head.
    "clean-rename-with-callers": (
        {
            "src/payments/charge.py": CHARGE.replace("format_amount", "format_money"),
            "tests/test_charge.py": TESTS.replace("format_amount", "format_money"),
        },
        None,
    ),
    # Read by review on a branch that removed an unused parameter at every use: majors and minors quoted the deleted
    # lines as code the branch still held, and asked to remove what was gone.
    "clean-removed-field": (
        {"src/payments/validator.py": VALIDATOR.replace(', correlation_id: str = ""', "")
         .replace("        self._correlation_id = correlation_id\n", "")
         .replace("            correlation_id=correlation_id,\n", "")
         .replace("        correlation_id=correlation_id,\n", "")},
        None,
    ),
    # Read by review on the same branch: paths moved to a new volume drew majors asking to check the volume's access,
    # which no line shows.
    "clean-config-path": (
        {"config/landing.yaml": LANDING.replace("inbound/landing_zone/charges/", "charges/landing_zone_vol/")},
        None,
    ),
    "guarded-above": (
        {"src/payments/settle.py": SETTLE.replace(
            '    log.info("statement: %d cents owed, %d cents today", shown, net)\n',
            "    average = (charged + refunded) // (len(charges) + len(refunds))\n"
            '    log.info("statement: %d cents owed, %d cents today, %d on average", shown, net, average)\n',
        )},
        None,
    ),
}  # fmt: skip


def branches(repo: Path) -> dict[str, str]:
    """Build the demo at `repo`, with `ON_MAIN` committed on its `main`, and one branch a label, each off `main`: by
    label, the branch to review."""
    build(repo)
    _git(repo, "checkout", "-q", BASE_REF)
    commit(repo, ON_MAIN, "payments: settle a ledger")
    made = {}
    for name, (files, _label) in BRANCHES.items():
        made[name] = f"label/{name}"
        branch(repo, made[name], BASE_REF, files, f"label: {name}")
    _git(repo, "checkout", "-q", BASE_REF)
    return made


def named_by(name: str, data: dict) -> set[str]:
    """The guidelines a run scores by: on a labelled branch, those whose findings, kept or folded, stand on its
    defect's lines; on a clean one, those that made a finding at major or above, a false one. Measured live on Copilot:
    a clean rename drew a false major in 3 of 3 runs under `full`, and which guideline made it was unknown. A finding
    stands on a defect's lines whatever it says of them: one quoting a small file's whole function, a test missing for
    it say, stands on every line, so on such a branch every guideline can name the defect."""
    label = BRANCHES[name][1]
    found = judged(data)
    if label is None:
        return {check for check, _file, _at, severity in found if severity in ("major", "blocker")}
    path, lines = label
    return {check for check, file, at, _severity in found if file == path and set(at) & set(lines)}


def scored(name: str, data: dict) -> bool:
    """One run of one branch: a labelled branch's defect named by a guideline, or a clean branch given a finding at
    major or above, a false one."""
    return bool(named_by(name, data))


def invented(data: dict) -> tuple[Counter[str], Counter[str]]:
    """Every finding a guideline made in one run's JSON report, kept or folded, at any severity, by guideline, and
    those of them flagged possibly already done. Read on a clean branch, each is a false one: read by review, the
    owner's comments held false minors, which a count of false majors alone never saw."""
    made: Counter[str] = Counter()
    flagged: Counter[str] = Counter()
    for f in (*data["findings"], *data["folded"]):
        if f["check"].startswith(GUIDELINE):
            made[f["check"]] += 1
            flagged[f["check"]] += bool(f.get("already_held"))
    return made, +flagged


def false_findings(runs: Mapping[str, list[tuple[Counter[str], Counter[str]] | None]]) -> list[str]:
    """Each clean branch's false findings, a line a guideline: how many each run made, and how many of them were flagged
    possibly already done, `-` for a run with no report; a branch none made one on is one line of zeros, so it never
    reads as unmeasured. Run to run they vary: measured live on the owner's branch, 10
    and then 21 on the same commit, so a change is judged against that spread."""
    lines = ["| branch | guideline | false findings, each run | of them flagged, each run |", "|---|---|---|---|"]
    for name, each in runs.items():
        for check in sorted({check for run in each if run for check in run[0]}) or ["every guideline"]:
            made, flagged = (", ".join("-" if run is None else str(run[at][check]) for run in each) for at in (0, 1))
            lines.append(f"| {name} | {check} | {made} | {flagged} |")
    return lines


def table(results: dict[str, list[bool]], by: Mapping[str, Counter[str]]) -> list[str]:
    """Each branch's line: a defect named in N of K runs, or a clean change given a false finding in N of K, and by
    which guidelines in how many."""
    lines = ["| branch | label | runs | by |", "|---|---|---|---|"]
    for name, hits in results.items():
        what = "clean: a false finding" if BRANCHES[name][1] is None else "its defect named"
        lines.append(f"| {name} | {what} | {sum(hits)} of {len(hits)} | {tally(by.get(name, Counter()))} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = options("labelled_set", (__doc__ or "").splitlines()[0])
    args = parser.parse_args(argv)
    repo = Path(tempfile.gettempdir()) / "adk-labelled-repo"
    made = branches(repo)
    print(f"built {len(made)} branches in {repo}: {', '.join(made.values())}", flush=True)
    if args.build_only:
        return 0
    results: dict[str, list[bool]] = {}
    by: dict[str, Counter[str]] = {}
    false: dict[str, list[tuple[Counter[str], Counter[str]] | None]] = {}
    for name, review in reviewed(repo, picked(parser, made, args.branch), args):
        named = named_by(name, review.data) if review.data else set()
        results.setdefault(name, []).append(bool(named))
        by.setdefault(name, Counter()).update(named)
        if BRANCHES[name][1] is None:
            false.setdefault(name, []).append(invented(review.data) if review.data else None)
    print("\n".join(table(results, by)))
    if false:
        print("\n" + "\n".join(false_findings(false)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
