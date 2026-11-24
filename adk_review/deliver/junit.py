"""The review as JUnit XML, for a pipeline's test tab: Azure Pipelines' `PublishTestResults@2`, GitLab's and Jenkins'
reports read it with no add-on, on a private repository too, where SARIF's readers are paid for.

Read by a code review: in a pipeline and in the commit and push hooks, a finding below the `--fail-on` bar showed in a
log alone. Here each kept finding is a failed test, a security major among them, while the exit code still decides the
build: the task that publishes the file fails no build on a failed test unless it is told to. Each check that ran is a
suite: one that looked and found nothing passes, one that could not look is an error, as is a file no check read whole,
so "did not look" never reads as "found nothing". The verdict is a test of its own, failed or in error as the exit code
says. Built from the JSON report's data alone, written under the text report's safety rules, so no quote here is any
less safe than there.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from xml.etree import ElementTree

from ..core.findings import at_or_above
from ..core.text import cut_at

#: The longest full name a test tab takes, its check's and its own joined by a dot: Azure Pipelines' limit.
NAME_MAX_CHARS = 512
#: The suite the verdict is the test of, named after the reviewer.
VERDICT_SUITE = "adk-review"
#: The suite of the files no check read whole.
NOT_READ_SUITE = "not read"
#: Every character XML 1.0 cannot hold, each written as a space: a report's text makes what a reader cannot see a space
#: already, but a parser that meets one of these refuses the whole file.
_NOT_XML = re.compile(r"[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
#: What a finding set aside at the bar says, by the JSON's list it is in: each holds the verdict back, as exit 3 says.
SET_ASIDE = {
    "dropped": "set aside at the bar: the diff does not bear out its quote",
    "refuted": "set aside at the bar: a verifier refuted it",
}


def render_junit(data: Mapping) -> str:
    """The JSON report's `data` as a JUnit document: the verdict's suite first, then each check in report order, then
    the files not read."""
    root = ElementTree.Element("testsuites", name=VERDICT_SUITE)
    fail_on = data.get("fail_on") or "blocker"
    set_aside = [
        (found, said)
        for key, said in SET_ASIDE.items()
        for found in data.get(key) or []
        if _at_bar(found.get("severity"), fail_on)
    ]
    _suite(root, VERDICT_SUITE, [_verdict(data, len(set_aside))])
    judged = {
        found.get("fingerprint"): f"{key}: {found.get('reason') or ''}"
        for key in ("unverified", "disputed")
        for found in data.get(key) or []
    }
    holes: Mapping[str, str] = data.get("could_not_look") or {}
    for check in data.get("checks") or []:
        cases = [
            _finding(found, fail_on, judged.get(found.get("fingerprint")))
            for found in data.get("findings") or []
            if found.get("check") == check
        ]
        cases += [_set_aside(found, said) for found, said in set_aside if found.get("check") == check]
        if check in holes:
            cases.append(_case(check, "could not look", "error", holes[check], holes[check]))
        _suite(root, check, cases or [_case(check, "no finding")])
    unread: Mapping[str, str] = data.get("not_read") or {}
    if unread:
        _suite(root, NOT_READ_SUITE, [_case(NOT_READ_SUITE, path, "error", why, why) for path, why in unread.items()])
    _count(root, root.iter("testcase"))
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ElementTree.tostring(root, encoding="unicode") + "\n"


def _verdict(data: Mapping, set_aside: int) -> ElementTree.Element:
    """The verdict's own test: it passes where the review approved, fails where a finding sits at the bar, and is an
    error, naming what held it back, where the review could not check everything."""
    verdict, code = data.get("verdict") or "", data.get("exit_code")
    said = f"{verdict}, exit code {code}"
    if verdict == "APPROVED":
        return _case(VERDICT_SUITE, "verdict", out=said)
    if verdict == "REQUEST_CHANGES":
        return _case(VERDICT_SUITE, "verdict", "failure", said, said)
    held = [
        f"{len(data.get('could_not_look') or {})} checks could not look",
        f"{len(data.get('not_read') or {})} files not read whole",
        f"{set_aside} findings at the bar set aside",
    ]
    said = f"{said}: " + ", ".join(held)
    return _case(VERDICT_SUITE, "verdict", "error", said, said)


def _finding(found: Mapping, fail_on: str, judged: str | None = None) -> ElementTree.Element:
    """A kept finding as a failed test, named by its place, its title and its fingerprint, which stays the same from
    one review to the next, so the tab's history of a test is the finding's. A verifier that disputed it, or could not
    judge it, is said too."""
    check, severity, title = found.get("check") or "", found.get("severity") or "", found.get("title") or ""
    bar = "at" if _at_bar(severity, fail_on) else "below"
    text = "\n".join(
        [
            f"{severity}, {bar} the --fail-on bar, {fail_on}: {title}",
            f"Where: {_where(found)}",
            f"Quote: {found.get('evidence') or ''}",
            f"Change: {found.get('suggestion') or ''}",
            f"Why: {found.get('why') or ''}",
            *([f"Verifier: {judged}"] if judged else []),
            f"Fingerprint: {found.get('fingerprint') or ''}",
        ]
    )
    return _case(check, _named(found), "failure", f"{severity}: {title}", text, kind=severity)


def _set_aside(found: Mapping, said: str) -> ElementTree.Element:
    """A finding set aside at the bar as an error in its check's suite: its check is no passed test."""
    reason = found.get("reason") or ""
    message = f"{said}{f': {reason}' if reason else ''}. {found.get('severity') or ''}: {found.get('title') or ''}"
    return _case(found.get("check") or "", _named(found), "error", message, message)


def _named(found: Mapping) -> str:
    """A finding's test's name: its place, its title, and its fingerprint, which the cut never reaches."""
    mark = f" [{found.get('fingerprint') or ''}]"
    return _fit(f"{_where(found)} {found.get('title') or ''}", _room(found.get("check") or "") - _units(mark)) + mark


def _where(found: Mapping) -> str:
    return f"{found.get('file') or ''}:{found['line']}" if found.get("line") else found.get("file") or ""


def _at_bar(severity: object, fail_on: str) -> bool:
    return severity in ("blocker", "major", "minor") and at_or_above(str(severity), fail_on)


def _room(classname: str) -> int:
    """What a name may hold beside its check's, the two joined by a dot."""
    return NAME_MAX_CHARS - _units(classname) - 1


def _units(text: str) -> int:
    """`text`'s length as .NET counts it, which reads the names: UTF-16 units, two for a character past the
    basic plane."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _fit(text: str, room: int) -> str:
    """`text` in `room` units, its cut named where it is cut, as every cap in the reviewer names its cut."""
    if _units(text) <= room:
        return text
    kept = cut_at(text, max(room // 2 - 40, 0))  # half: a character may take two units; then the marker's length
    while _units(kept) > room:
        kept = kept[:-1]
    return kept


def _case(
    classname: str,
    name: str,
    outcome: str = "",
    message: str = "",
    text: str = "",
    *,
    kind: str = "",
    out: str = "",
) -> ElementTree.Element:
    """One test: passed with no `outcome`, else a `failure` or an `error` carrying `message` and `text`. Its name fits
    the tab's limit whatever it names: read by review, a path not read whole was named uncut."""
    case = ElementTree.Element("testcase", classname=_xml(classname), name=_xml(_fit(name, _room(classname))))
    if outcome:
        said = ElementTree.SubElement(case, outcome, message=_xml(message))
        if kind:  # a finding's severity, which a test tab can group by
            said.set("type", kind)
        said.text = _xml(text)
    if out:
        ElementTree.SubElement(case, "system-out").text = _xml(out)
    return case


def _suite(root: ElementTree.Element, name: str, cases: list[ElementTree.Element]) -> None:
    suite = ElementTree.SubElement(root, "testsuite", name=_xml(name))
    suite.extend(cases)
    _count(suite, cases)


def _count(element: ElementTree.Element, cases: Iterable[ElementTree.Element]) -> None:
    """The counts a test tab reads first, of `cases` under `element`."""
    every = list(cases)
    element.set("tests", str(len(every)))
    element.set("failures", str(sum(case.find("failure") is not None for case in every)))
    element.set("errors", str(sum(case.find("error") is not None for case in every)))
    element.set("skipped", "0")


def _xml(text: str) -> str:
    return _NOT_XML.sub(" ", text)
