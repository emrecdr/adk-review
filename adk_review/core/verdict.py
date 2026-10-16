"""Everything between the guidelines' raw answers and a verdict: grounding, folding, deciding. Pure, and counted.

Every narrowing is counted and printed, including zeros, so a reader can see
what was removed and why. Nothing here calls a model or a process.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from .findings import RANK, Checked, Finding, Review, at_or_above
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
    return squash("\n".join(_unmarked(line) for line in redacted.splitlines()))


#: A redaction's placeholder, its label or none: to grounding one stand-in, since the label is the scrubber's, never
#: the code's. Measured live on Gemini: shown `[REDACTED:aws_secret_access_key]`, a guideline quoted the key's lines
#: with `[REDACTED:secret_access_key]`, and its finding dropped, the review a hole.
_PLACEHOLDER = re.compile(r"\[REDACTED(?::[^\]\n]*)?\]")


@lru_cache(maxsize=512)
def seen_lines(text: str, markers: bool = True) -> tuple[str, ...]:
    """`as_seen` line by line, with every space gone rather than flattened: secrets redacted, each placeholder one,
    a diff marker gone, blank lines left out. Measured on Gemini: a guideline quoted three whole lines with the breaks
    between them lost, joined by a space or by nothing, so where a line ends is read off the file, never off the
    quote. `markers` off keeps a line's leading `+` or `-`: in a quote it may be the line's own text, a YAML item's
    dash."""
    redacted, _count = scrub(text)
    return _flat(_PLACEHOLDER.sub("[REDACTED]", redacted), markers)


def _flat(text: str, markers: bool = True) -> tuple[str, ...]:
    """Each line of `text` with its diff marker gone, what no reader sees gone and every space gone, blank lines left
    out; nothing redacted."""
    lines = ("".join(_unmarked(line, markers).split()) for line in text.splitlines())
    return tuple(line for line in lines if line)


def _unmarked(line: str, markers: bool = True) -> str:
    """A line of a diff as a reader sees it, the fold and grounding alike: its marker gone where `markers` says so,
    and what no reader sees gone."""
    return _visible(line[1:] if markers and line[:1] in "+- " else line)


#: The variation selectors, which pick a glyph's form and are no character a reader sees.
_SELECTORS = frozenset((*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)))


def _visible(text: str) -> str:
    """`text` as a model can quote it, without what no reader sees: Unicode's format characters, the tag characters a
    whole sentence can hide in among them, and the variation selectors. Measured live on Gemini: a payload in tag
    characters left its comment quoted as `#`, and the quote of the shell call beside it dropped, the review a hole.
    A text in ASCII alone, nearly every line of a diff, is passed over."""
    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf" and ord(ch) not in _SELECTORS)


def _whole_lines(quote: str, lines: tuple[str, ...]) -> Iterator[tuple[int, int]]:
    """Each place `quote` starts and ends as a run of consecutive `lines`, each whole: it starts where one begins and
    ends where one ends. Measured by review at the 16,000-character cap: a framed 100-line quote took 30 ms to place
    with a run built from every line, and 6 ms with none built where the quote cannot start."""
    for start in range(len(lines)):
        if not quote.startswith(lines[start]):  # no line is empty: a run from this one is never the quote
            continue
        run, end = "", start
        for end in range(start, len(lines)):
            run += lines[end]
            if len(run) >= len(quote):
                break
        if run == quote:
            yield start, end


#: A fence a model wraps a quote in of its own accord, ```` ```python ```` or `~~~`, read as grounding reads a line:
#: every space gone.
_OWN_FENCE = re.compile(r"(?:`{3,}|~{3,}).*")


def _quotes(evidence: str, around: Sequence[str]) -> set[str]:
    """`evidence` each way grounding reads it, its lines joined with every space gone: as given, since a Markdown
    file's diff holds fences and headings of its own; and without the lines its file was framed in as the guidelines
    were shown it — `around`, wherever the quote carries one — nor a fence of the model's own at either end. Each read
    with a line's leading `+`, `-` or space as a diff marker, and as the line's own text: measured, `- run: …` and
    `++i;` quoted as the file has them lost their first character.

    Measured live on Gemini: the injection on `sql-from-input` quoted inside its fence, the blocker dropped and the
    review a hole where it should have stopped the change; on Copilot, 2 of 5 findings dropped quoted their lines under
    the hunk's `@@` header. What is left must still be whole lines of the diff: the framing goes, and alone grounds
    nothing, and only the file's own goes, never another file's heading."""
    quotes = set()
    for markers in (True, False):
        lines = seen_lines(evidence, markers)
        shown = set(seen_lines("\n".join(around), markers))
        inside = [line for line in lines if line not in shown]
        while inside and _OWN_FENCE.fullmatch(inside[0]):
            del inside[0]
        while inside and _OWN_FENCE.fullmatch(inside[-1]):
            del inside[-1]
        quotes |= {"".join(lines), "".join(inside)}
    return quotes - {""}


def drop_ungrounded(
    review: Review,
    quotable: dict[str, list[list[tuple[int, str]]]],
    framing: Mapping[str, Sequence[str]],
) -> tuple[list[tuple[Finding, Lines]], list[Finding]]:
    """Split findings into those that quote their own file's diff — whole lines, in order, as the guideline saw them,
    whatever whitespace joined them — and the rest, each kept one with the lines the diff places it on. `quotable` is
    each changed file's hunks, their lines numbered, and `framing` the lines each was framed in as the guidelines were
    shown it, both from `collect_evidence`.

    Measured with the rule it replaced, a substring of the whole rendered
    block: a `### <path>` heading, the fence, a hunk header, git's
    `diff --git` line and the one word `import` were each grounded, and a
    finding's `file` was never read. The radius had been one member of that
    class: out of the block, it no longer carries the safety, this rule does.
    Measured live on both arms against the planted branch: every guideline quote
    is grounded by it. A kept finding's line is where its quote starts, read
    off the diff: measured live on Copilot, the guidelines named a wrong line for
    4 of 5 findings. And its evidence is the lines its quote stands on, as the
    file reads them, secrets redacted: measured by review, the same two lines
    quoted plain and inside their fence under their `@@` header were two
    fingerprints, and `--previous` named one finding resolved and new at once.
    """
    kept: list[tuple[Finding, Lines]] = []
    dropped: list[Finding] = []
    for finding in review.findings:
        path = _meant(finding.file, quotable)
        if (placed := _placed(finding, quotable.get(path, []), framing.get(path, ()))) is None:
            dropped.append(finding)
            continue
        lines, stood = placed
        update = {"file": path, "line": lines[0]}
        if stood is not None:  # where the redaction joined lines, the model's quote is all there is
            update["evidence"] = scrub(stood)[0]
        kept.append((finding.model_copy(update=update), lines))
    return kept, dropped


def _meant(path: str, quotable: dict[str, list[list[tuple[int, str]]]]) -> str:
    """The changed file a finding names: as given, or with git's `b/` taken off where only that names one, as the
    diff's own header spells the path."""
    if path not in quotable and path.startswith("b/") and path[2:] in quotable:
        return path[2:]
    return path


def _placed(
    finding: Finding, hunks: list[list[tuple[int, str]]], around: Sequence[str]
) -> tuple[Lines, str | None] | None:
    """The first and last line of `finding`'s quote in its own file's diff, where it is whole lines of one hunk, in
    order, as the guideline saw them, and those lines as the file reads them; `(None, None)` where the redaction joined
    lines, so no line is named rather than a wrong one, and no text but the quote; None where the diff does not bear
    the quote out.

    Each hunk is read three ways: as git prints it, as the new file reads it (its context and added lines) and as
    the old file read it (its context and removed lines). Measured before, the hunk as git prints it alone: a correct
    quote of the new code, running from a context line into a new one across a replaced line, was dropped, and as a
    blocker the run degraded. Never across two hunks: measured before, the last line of one hunk joined to the first
    of the next, twenty lines apart, grounded as one span, and a finding on the lines between folded into it."""
    quotes = _quotes(finding.evidence, around)
    placed: list[tuple[tuple[int, int], str]] = []
    joined = False
    for hunk in hunks:
        for kept in ("+- ", "+ ", "- "):  # as git prints it, as the new file reads, as the old file read
            view = [(number, raw) for number, raw in hunk if raw[:1] in kept]
            lines = seen_lines("\n".join(raw for _number, raw in view))
            runs = {run for quote in quotes for run in _whole_lines(quote, lines)}
            if not runs:
                continue
            # Each seen line's number and the line of the view it is, counted unredacted: a redaction never adds or
            # removes a line of its own. Where the redaction joined lines, a private key's block one line as the
            # guideline saw it, the counts differ.
            at = [(number, index) for index, (number, raw) in enumerate(view) for _line in _flat(raw)]
            if len(at) != len(lines):
                joined = True
                continue
            for start, end in runs:
                (first, since), (last, until) = at[start], at[end]
                placed.append(((first, last), "\n".join(raw[1:] for _number, raw in view[since : until + 1])))
    if not placed:
        return ((None, None), None) if joined else None
    # A short quote can stand in several places: measured by review, `return None` at lines 2 and 22, a guideline naming
    # 22 was placed at 2. The diff decides where a quote can stand; the guideline's line only picks among those places,
    # the first where it named none and on a tie.
    return min(placed, key=lambda place: (abs(place[0][0] - finding.line) if finding.line else 0, place))


def _overlaps(x: str, x_lines: Lines, y: str, y_lines: Lines) -> bool:
    """Two findings about the same lines. Where the diff places both, one's lines hold the other's: measured by
    review, folded by text alone, a minor quoting `return None` at line 2 held a blocker whose quote ends in another
    `return None` at line 6, and took it. Where either is not placed, one quote, neither empty, contains the other."""
    if x_lines and y_lines and None not in (*x_lines, *y_lines):
        (a, b), (c, d) = x_lines, y_lines
        return (a <= c and d <= b) or (c <= a and b <= d)
    return bool(x and y) and (x in y or y in x)


def from_rules(answers: Mapping[str, Checked]) -> tuple[list[Sourced], dict[str, str]]:
    """The rules' answers as the verdict reads them, a review's and `--try`'s alike: each finding under its check's id
    where the diff places it — its one line, or nowhere for one at a path, as a tree rule's is — and why each rule
    that could not look did not."""
    found = [
        Sourced(check, f, None if f.line is None else (f.line, f.line))
        for check, answer in answers.items()
        for f in answer.findings
    ]
    return found, {check: answer.could_not_look for check, answer in answers.items() if answer.could_not_look}


def not_read(files: list[dict], guidelines: Collection[str]) -> dict[str, str]:
    """What no one could read, by path with why. A cut is the guidelines' limit: with none on, the rules
    read every added line, and only what no one can read — binary, a submodule, nothing shown — is a hole."""
    return {f["path"]: f["unread"] for f in files if f["unread"] and (guidelines or not f["cut"])}


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


def fold(outcome: Outcome, rules: list[Sourced], order: Sequence[str]) -> None:
    """One finding per span of the diff, the first check on a tie: `order` is the report's, the rules before the
    guidelines, so the fold and the report never disagree on which check a line belongs to."""
    kept = sorted(rules + outcome.kept, key=lambda item: order.index(item.source))  # stable: each check's own order
    outcome.kept, outcome.folded = dedupe(kept)


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
