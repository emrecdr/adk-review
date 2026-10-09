"""Everything between the guidelines' raw answers and a verdict: grounding, folding, deciding. Pure, and counted.

Every narrowing is counted and printed, including zeros, so a reader can see
what was removed and why. Nothing here calls a model or a process.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache

from .findings import RANK, Finding, Review, at_or_above
from .secrets import scrub
from .text import squash

EXIT_CLEAN, EXIT_FINDINGS, EXIT_USAGE, EXIT_DEGRADED = 0, 1, 2, 3
#: The shell's own code for a run Ctrl-C stopped: 128 and SIGINT's 2.
EXIT_INTERRUPTED = 130
#: And for one a job stopped: 128 and SIGTERM's 15.
EXIT_TERMINATED = 143
#: Where the diff places a finding: its first and last line, None when a line is not known.
Lines = tuple[int | None, int | None] | None


@dataclass
class Sourced:
    """A finding and the check it came from, by its id: `rule.secrets`, `guideline.security`."""

    source: str
    finding: Finding
    #: The first and last line the diff places it on: a rule's one line, a guideline's quote where grounding found it.
    lines: Lines = None


@dataclass
class Outcome:
    """What post-processing decided, in one place, so the verdict and the report read the same numbers."""

    kept: list[Sourced] = field(default_factory=list)
    dropped: list[Sourced] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # source -> why that rule or guideline could not look
    unread: dict[str, str] = field(default_factory=dict)  # path -> why no check that ran read it whole
    folded: list[tuple[Sourced, Sourced]] = field(default_factory=list)  # a finding, and the one it folded into
    redacted: int = 0
    verified: bool = False  # `--verify` ran
    refuted: list[tuple[Sourced, str]] = field(default_factory=list)  # dropped on the verifier's say-so, with why
    unverified: list[tuple[Sourced, str]] = field(default_factory=list)  # the verifier could not judge; kept, with why


@lru_cache(maxsize=512)
def as_seen(text: str) -> str:
    """Text the way a guideline saw it and quotes it: secrets redacted, diff markers gone, whitespace flat.

    Measured on Copilot with the drops named: a guideline quoted two added lines
    without their `+` markers and was dropped; another quoted the redaction
    placeholders it had been shown and was dropped. Both were grounded — in
    the text the model read, not in the raw diff. Cached: the fold and the
    verifier's choice compare each finding's quote with others on its file.
    """
    redacted, _count = scrub(text)
    stripped = [line[1:] if line[:1] in "+- " else line for line in redacted.splitlines()]
    return squash("\n".join(stripped))


@lru_cache(maxsize=512)
def seen_lines(text: str, markers: bool = True) -> tuple[str, ...]:
    """`as_seen` line by line, with every space gone rather than flattened: secrets redacted, a diff marker gone,
    blank lines left out. Measured on Gemini: a guideline quoted three whole lines with the breaks between them lost,
    joined by a space or by nothing, so where a line ends is read off the file, never off the quote. `markers`
    off keeps a line's leading `+` or `-`: in a quote it may be the line's own text, a YAML item's dash."""
    redacted, _count = scrub(text)
    return _flat(redacted, markers)


def _flat(text: str, markers: bool = True) -> tuple[str, ...]:
    """Each line of `text` with its diff marker gone and every space gone, blank lines left out; nothing redacted."""
    lines = ("".join((line[1:] if markers and line[:1] in "+- " else line).split()) for line in text.splitlines())
    return tuple(line for line in lines if line)


def _whole_lines(quote: str, lines: tuple[str, ...]) -> Iterator[tuple[int, int]]:
    """Each place `quote` starts and ends as a run of consecutive `lines`, each whole: it starts where one begins and
    ends where one ends."""
    for start in range(len(lines)):
        run, end = "", start
        for end in range(start, len(lines)):
            run += lines[end]
            if len(run) >= len(quote):
                break
        if run == quote:
            yield start, end


def drop_ungrounded(
    review: Review, quotable: dict[str, list[list[tuple[int, str]]]]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into those that quote their own file's diff — whole lines, in order, as the guideline saw them,
    whatever whitespace joined them — and the rest. `quotable` is each changed file's hunks, their lines numbered,
    from `collect_evidence`.

    Measured with the rule it replaced, a substring of the whole rendered
    block: a `### <path>` heading, the fence, a hunk header, git's
    `diff --git` line and the one word `import` were each grounded, and a
    finding's `file` was never read. The radius had been one member of that
    class: out of the block, it no longer carries the safety, this rule does.
    Measured live on both arms against the planted branch: every guideline quote
    is grounded by it. A kept finding's line is where its quote starts, read
    off the diff: measured live on Copilot, the guidelines named a wrong line for
    4 of 5 findings.
    """
    kept: list[Finding] = []
    dropped: list[Finding] = []
    for finding in review.findings:
        if (where := where_quoted(finding, quotable)) is None:
            dropped.append(finding)
        else:
            kept.append(finding.model_copy(update={"file": meant(finding.file, quotable), "line": where[0]}))
    return kept, dropped


def meant(path: str, quotable: dict[str, list[list[tuple[int, str]]]]) -> str:
    """The changed file a finding names: as given, or with git's `b/` taken off where only that names one, as the
    diff's own header spells the path."""
    if path not in quotable and path.startswith("b/") and path[2:] in quotable:
        return path[2:]
    return path


def where_quoted(finding: Finding, quotable: dict[str, list[list[tuple[int, str]]]]) -> Lines:
    """The first and last line of `finding`'s quote in its own file's diff, where it is whole lines of one hunk, in
    order, as the guideline saw them; `(None, None)` where the redaction joined lines, so no line is named rather than a
    wrong one; None where the diff does not bear the quote out.

    Each hunk is read three ways: as git prints it, as the new file reads it (its context and added lines) and as
    the old file read it (its context and removed lines). Measured before, the hunk as git prints it alone: a correct
    quote of the new code, running from a context line into a new one across a replaced line, was dropped, and as a
    blocker the run degraded. Never across two hunks: measured before, the last line of one hunk joined to the first
    of the next, twenty lines apart, grounded as one span, and a finding on the lines between folded into it."""
    # Read both ways: measured, `- run: …` and `++i;` quoted as the file has them lost their first character.
    quotes = {"".join(seen_lines(finding.evidence, markers)) for markers in (True, False)}
    placed: list[tuple[int, int]] = []
    joined = False
    for hunk in quotable.get(meant(finding.file, quotable), []):
        for kept in ("+- ", "+ ", "- "):  # as git prints it, as the new file reads, as the old file read
            view = [(number, raw) for number, raw in hunk if raw[:1] in kept]
            lines = seen_lines("\n".join(raw for _number, raw in view))
            runs = {run for quote in quotes for run in _whole_lines(quote, lines)}
            if not runs:
                continue
            # Each seen line's number, counted unredacted: a redaction never adds or removes a line of its own. Where
            # the redaction joined lines, a private key's block one line as the guideline saw it, the counts differ.
            numbers = [number for number, raw in view for _line in _flat(raw)]
            if len(numbers) != len(lines):
                joined = True
                continue
            placed += [(numbers[start], numbers[end]) for start, end in runs]
    if not placed:
        return (None, None) if joined else None
    # A short quote can stand in several places: measured by review, `return None` at lines 2 and 22, a guideline naming
    # 22 was placed at 2. The diff decides where a quote can stand; the guideline's line only picks among those places,
    # the first where it named none and on a tie.
    return min(placed, key=lambda span: (abs(span[0] - finding.line) if finding.line else 0, span))


def _overlaps(x: str, x_lines: Lines, y: str, y_lines: Lines) -> bool:
    """Two findings about the same lines. Where the diff places both, one's lines hold the other's: measured by
    review, folded by text alone, a minor quoting `return None` at line 2 held a blocker whose quote ends in another
    `return None` at line 6, and took it. Where either is not placed, one quote, neither empty, contains the other."""
    if x_lines and y_lines and None not in (*x_lines, *y_lines):
        (a, b), (c, d) = x_lines, y_lines
        return (a <= c and d <= b) or (c <= a and b <= d)
    return bool(x and y) and (x in y or y in x)


def to_verify(guidelines: list[Sourced], rules: list[Sourced]) -> list[Sourced]:
    """The guideline findings worth a verifier: all but those that only say again what a rule found — a rule's quote
    and its own overlap, in one file, and the rule's reading is at least as severe, so the fold keeps the rule's
    whatever were said of the guideline's. The fold still folds them, as it would without `--verify`. Each rule's quote
    is normalised once and compared only within its file, as `dedupe` does: normalised per pair, a thousand of
    ruff's findings in one file and thirty guideline findings measured 147 ms against 7 ms."""
    said: dict[str, list[tuple[int, str, Lines]]] = {}  # file -> (rank, quote as seen, lines) of each rule finding
    for rule in rules:
        seen = as_seen(rule.finding.evidence)
        said.setdefault(rule.finding.file, []).append((RANK[rule.finding.severity], seen, rule.lines))
    asked: list[Sourced] = []
    for item in guidelines:
        rank, seen = RANK[item.finding.severity], as_seen(item.finding.evidence)
        here = said.get(item.finding.file, ())
        if not any(at >= rank and _overlaps(seen, item.lines, quote, lines) for at, quote, lines in here):
            asked.append(item)
    return asked


def dedupe(kept: list[Sourced]) -> tuple[list[Sourced], list[tuple[Sourced, Sourced]]]:
    """One finding per span of the diff: the most severe reading first, whole, the first source on a tie.

    Measured on Copilot: three guidelines reported the shell finding, each quoting a
    different span of the same two lines, and the report carried it three
    times. Overlapping quotes in one file are one finding — across guidelines, and
    across rules. Whole, not merged: measured with ruff in front of the guidelines,
    keeping the first title at the higher severity made "line too long" a
    blocker. Most severe first, and each finding kept only if it overlaps none
    already kept: measured live, a guideline's blocker quoting a whole function
    replaced the one ruff finding it was compared with, and left the other,
    on the next line, standing beside it. The findings keep the order they came in.
    Each quote is normalised once and compared only within its file:
    the lint rule reads whole files, so a test-heavy branch brings findings by
    the hundred per file, and comparing every pair through `as_seen`'s cache
    measured 689 ms at 1,000 findings against 32 ms this way; most severe
    first costs what the order before it did, 12 ms each on a thousand.
    """
    held: dict[str, list[tuple[int, str, Lines]]] = {}  # file -> each kept finding's index, quote as seen, lines
    into: dict[int, int] = {}  # a folded finding's index -> the index of the finding it folded into
    # `sorted` is stable: on a tie the first source comes first.
    for index in sorted(range(len(kept)), key=lambda i: -RANK[kept[i].finding.severity]):
        item = kept[index]
        seen, here = as_seen(item.finding.evidence), held.setdefault(item.finding.file, [])
        survivor = next((at for at, quote, lines in here if _overlaps(seen, item.lines, quote, lines)), None)
        if survivor is None:
            here.append((index, seen, item.lines))
        else:
            into[index] = survivor
    return [item for index, item in enumerate(kept) if index not in into], [
        (kept[i], kept[into[i]]) for i in sorted(into)
    ]


def at_bar(items: Iterable[Sourced], fail_on: str) -> int:
    """How many of `items` sit at or above `fail_on`. Of the kept: said beside a degraded verdict, which outranks them,
    since a pipeline that lets exit 3 through would otherwise let them through unseen. Of the dropped: any is a hole,
    a finding at the bar whose quote the diff does not bear out."""
    return sum(at_or_above(item.finding.severity, fail_on) for item in items)


def decide(outcome: Outcome, fail_on: str) -> tuple[str, int]:
    """The verdict and the exit code, from the findings that survived and the guidelines that did not.

    Only kept findings count: one dropped for evidence that is not in the diff must not decide the build. A failed
    guideline is a hole, and so is a file no guideline read whole, and so is a finding at the bar that no quote bears
    out, since a real blocker dropped would approve: a hole never approves.
    """
    if outcome.errors or outcome.unread or at_bar(outcome.dropped, fail_on):
        return "DEGRADED", EXIT_DEGRADED
    return ("REQUEST_CHANGES", EXIT_FINDINGS) if at_bar(outcome.kept, fail_on) else ("APPROVED", EXIT_CLEAN)
