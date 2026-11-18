"""Tests for the front end's Azure DevOps side, `tui/tui/azure*.py`: a pull request's address and a finding's links,
the comment drafted from a finding, the thread it is posted as, the login's token, the client and what is kept.

Every test is offline: a fake Azure DevOps answers on a local port, and a fake `az` gives the token. Each test imports
what it reads where it runs, as `tests/test_tui.py` says why."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def azure() -> ModuleType:
    """`tui.azure`, imported where the test runs."""
    return importlib.import_module("tui.azure")


def _pr(azure: ModuleType, origin: str = "https://dev.azure.com/contoso"):  # the module's own type
    return azure.PullRequest(origin, "Web", "payments", 123)


class TestAPullRequestsAddress:
    """`parse_pr_url`: what the browser shows is read, and anything else is refused with what to paste."""

    @pytest.mark.parametrize(
        "pasted",
        [
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/123",
            "  https://dev.azure.com/contoso/Web/_git/payments/pullrequest/123/  ",
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/123?_a=files&path=%2Fa.py#x",
            "HTTPS://DEV.AZURE.COM/contoso/Web/_git/payments/PullRequest/123",
        ],
    )
    def test_a_pull_requests_address_is_read_as_the_browser_shows_it(self, azure: ModuleType, pasted: str) -> None:
        assert azure.parse_pr_url(pasted) == _pr(azure)

    def test_the_older_host_a_default_collection_and_an_unnamed_project_are_read(self, azure: ModuleType) -> None:
        old = _pr(azure, "https://contoso.visualstudio.com")
        read = azure.parse_pr_url
        assert read("https://contoso.visualstudio.com/Web/_git/payments/pullrequest/123") == old
        assert read("https://contoso.visualstudio.com/DefaultCollection/Web/_git/payments/pullrequest/123") == old
        assert read("https://dev.azure.com/contoso/_git/payments/pullrequest/7").project == "payments"

    def test_a_name_with_a_space_is_decoded_once_and_encoded_once(self, azure: ModuleType) -> None:
        pr = azure.parse_pr_url("https://dev.azure.com/contoso/My%20Project/_git/pay%20ments/pullrequest/7")
        assert (pr.project, pr.repository) == ("My Project", "pay ments")
        assert pr.web == "https://dev.azure.com/contoso/My%20Project/_git/pay%20ments/pullrequest/7"
        assert pr.api("threads") == (
            "https://dev.azure.com/contoso/My%20Project/_apis/git/repositories/pay%20ments/pullRequests/7/threads"
        )

    @pytest.mark.parametrize(
        "pasted",
        [
            "",
            "payments",
            "https://github.com/contoso/payments/pull/1",
            "http://dev.azure.com/contoso/Web/_git/payments/pullrequest/1",  # no TLS
            "https://dev.azure.com.evil.test/contoso/Web/_git/payments/pullrequest/1",
            "https://evil.test/dev.azure.com/contoso/Web/_git/payments/pullrequest/1",
            "https://a.b.visualstudio.com/Web/_git/payments/pullrequest/1",
            "https://dev.azure.com:8443/contoso/Web/_git/payments/pullrequest/1",
            "https://dev.azure.com:port/contoso/Web/_git/payments/pullrequest/1",
            "https://dev.azure.com/contoso/Web/_git/payments",  # a repository, no pull request
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/12abc",
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/0",
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/١٢",  # digits, and not ASCII's
            "https://dev.azure.com/contoso/Web/_git/../pullrequest/1",
            "https://dev.azure.com/contoso/Web/_git/pay%2Fments/pullrequest/1",
            "https://dev.azure.com/contoso/Web/extra/_git/payments/pullrequest/1",
            "https://dev.azure.com/_git/payments/pullrequest/1",  # no organization
        ],
    )
    def test_any_other_address_is_refused_with_what_to_paste(self, azure: ModuleType, pasted: str) -> None:
        with pytest.raises(ValueError, match=r"pullrequest/<number>"):
            azure.parse_pr_url(pasted)

    def test_a_server_of_ones_own_is_said_to_be_unread(self, azure: ModuleType) -> None:
        with pytest.raises(ValueError, match="Azure DevOps Server is not read yet"):
            azure.parse_pr_url("https://tfs.example.test/tfs/DefaultCollection/Web/_git/payments/pullrequest/1")
        with pytest.raises(ValueError, match="not a pull request") as other:
            azure.parse_pr_url("https://github.com/contoso/payments/pull/1")
        assert "Server" not in str(other.value), "said only where the address reads as Azure DevOps'"

    def test_an_address_holding_a_credential_is_refused_and_never_repeated(self, azure: ModuleType) -> None:
        """A clone's address may hold a token before its host: such a paste is refused, and its text is in no
        sentence the screen then shows."""
        login = ":".join(("someone", "hunter2secret"))  # joined: no line holds a login in a URL
        with pytest.raises(ValueError, match=r"pullrequest/<number>") as refused:
            azure.parse_pr_url(f"https://{login}@dev.azure.com/contoso/Web/_git/payments/pullrequest/1")
        assert "hunter2secret" not in str(refused.value) and "someone" not in str(refused.value)

    def test_each_sentence_is_short_and_one_thing(self, azure: ModuleType) -> None:
        for pasted in ("", "https://tfs.example.test/tfs/Web/_git/payments/pullrequest/1"):
            with pytest.raises(ValueError, match="not a pull request") as refused:
                azure.parse_pr_url(pasted)
            for sentence in str(refused.value).split(". "):
                assert len(sentence.split()) <= 20 and ";" not in sentence, sentence


class TestAFindingsLinks:
    """The three links, each built from the address's parts and never from a model's text."""

    def test_the_line_link_pins_the_commit_and_covers_whole_lines(self, azure: ModuleType) -> None:
        sha = "a" * 40
        assert azure.line_link(_pr(azure), sha, "src/my file#1&2.py", 42, 57) == (
            "https://dev.azure.com/contoso/Web/_git/payments?path=%2Fsrc%2Fmy%20file%231%262.py"
            f"&version=GC{sha}&line=42&lineEnd=58&lineStartColumn=1&lineEndColumn=1&_a=contents"
        )
        assert "&line=42&lineEnd=43&" in azure.line_link(_pr(azure), sha, "a.py", 42, 42)
        assert "path=%2Fsrc%2F%C3%B6.py" in azure.line_link(_pr(azure), sha, "src/ö.py", 1, 1)
        assert f"version=GC{'b' * 64}&" in azure.line_link(_pr(azure), "b" * 64, "a.py", 1, 1), "a SHA-256 commit"

    @pytest.mark.parametrize("commit", ["main", "main; rm -rf", "a" * 39, "A" * 40, "a" * 40 + "&x=1", ""])
    def test_a_commit_that_is_no_hash_makes_no_link(self, azure: ModuleType, commit: str) -> None:
        with pytest.raises(ValueError, match="commit"):
            azure.line_link(_pr(azure), commit, "a.py", 1, 1)

    def test_the_files_tab_and_a_threads_link(self, azure: ModuleType) -> None:
        pr = _pr(azure)
        assert azure.files_link(pr, "src/app.py") == pr.web + "?_a=files&path=%2Fsrc%2Fapp.py"
        assert azure.files_link(pr, "a&b=c#d.py") == pr.web + "?_a=files&path=%2Fa%26b%3Dc%23d.py"
        assert azure.thread_link(pr, 148) == pr.web + "?discussionId=148"


@pytest.fixture
def findings() -> ModuleType:
    """`tui.findings`, imported where the test runs."""
    return importlib.import_module("tui.findings")


def _report(folder: Path, found: list[dict], reviewed: object = None) -> Path:
    """A review's JSON report in `folder`, holding `found` and what it says it reviewed."""
    path = folder / "report.json"
    path.write_text(json.dumps({"schema": 2, "findings": found, "reviewed": reviewed}), encoding="utf-8")
    return path


class TestWhatAReportSaysOfAFinding:
    """A finding's identity and lines, and the commit reviewed: what a link, a draft and a thread are built from."""

    def test_a_row_holds_the_findings_fingerprint_lines_and_whether_the_change_added_them(
        self, findings: ModuleType, tmp_path: Path
    ) -> None:
        found = {"check": "rule.lint", "file": "a.py", "line": 42, "severity": "major", "title": "t"}
        found |= {"fingerprint": "ab" * 8, "lines": [42, 57], "on_added_line": True}
        row = findings.load_findings_from_json(_report(tmp_path, [found]))[0]
        assert (row.fingerprint, row.lines, row.on_added_line, row.span()) == ("ab" * 8, (42, 57), True, (42, 57))

    @pytest.mark.parametrize(
        "lines", [None, "42-57", [42], [42, 57, 60], [57, 42], [0, 3], [True, 2], ["1", "2"], [1.0, 2.0], {"a": 1}]
    )
    def test_lines_that_are_no_range_are_none_and_the_span_falls_back_to_the_line(
        self, findings: ModuleType, tmp_path: Path, lines: object
    ) -> None:
        row = findings.load_findings_from_json(_report(tmp_path, [{"file": "a.py", "line": 9, "lines": lines}]))[0]
        assert row.lines is None and row.span() == (9, 9)

    def test_a_finding_with_no_line_has_no_span_and_an_old_report_reads_as_before(
        self, findings: ModuleType, tmp_path: Path
    ) -> None:
        old = {"file": "a.py", "on_added_line": "yes", "fingerprint": 7}
        row = findings.load_findings_from_json(_report(tmp_path, [old]))[0]
        assert (row.span(), row.fingerprint, row.on_added_line, row.lines) == (None, "", None, None)
        zero = findings.load_findings_from_json(_report(tmp_path, [{"file": "b.py", "line": 0}]))[0]
        assert zero.span() is None, "no file has a line 0"

    def test_the_reviewed_commit_is_read_from_the_report(self, findings: ModuleType, tmp_path: Path) -> None:
        good = {"repository": "/work/payments", "head": "feature/orders", "head_sha": "a" * 40}
        read = findings.load_reviewed(_report(tmp_path, [], good))
        assert read == findings.Reviewed("/work/payments", "feature/orders", "a" * 40)
        for bad in ({**good, "head_sha": "main"}, {**good, "head": 7}, {**good, "repository": None}, None, [good]):
            assert findings.load_reviewed(_report(tmp_path, [], bad)) is None, bad
        assert findings.load_reviewed(tmp_path / "missing.json") is None
        (tmp_path / "broken.json").write_text("{", encoding="utf-8")
        assert findings.load_reviewed(tmp_path / "broken.json") is None
