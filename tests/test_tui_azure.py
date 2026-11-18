"""Tests for the front end's Azure DevOps side, `tui/tui/azure*.py`: a pull request's address and a finding's links,
the comment drafted from a finding, the thread it is posted as, the login's token, the client and what is kept.

Every test is offline: a fake Azure DevOps answers on a local port, and a fake `az` gives the token. Each test imports
what it reads where it runs, as `tests/test_tui.py` says why."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
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


@pytest.fixture
def row(findings: ModuleType):  # the module's own type
    """A major a guideline found on a line the change added, with its reason and its corrected code."""
    return findings.FindingRow(
        severity="major",
        tier="NEW",
        check="guideline.correctness",
        file="src/app.py",
        line=42,
        title="`charge()` adds tax twice",
        evidence="+ total = with_tax(total)",
        suggestion="Add the tax once.",
        why="The coupon path taxes a taxed total.",
        code="total = with_tax(apply_coupon(subtotal, coupon))",
        fingerprint="1a2b3c4d5e6f7a8b",
        lines=(42, 42),
        on_added_line=True,
    )


class TestTheDraftOfAComment:
    """`draft`: a finding's own fields as a comment, its prose made safe and its corrected code left as it is."""

    def test_the_draft_of_a_major_with_code_on_an_added_line(self, azure: ModuleType, row) -> None:
        assert azure.draft(row) == (
            "**major**: `charge()` adds tax twice\n\n"
            "The coupon path taxes a taxed total.\n\n"
            "Suggested fix: Add the tax once.\n\n"
            "```suggestion\ntotal = with_tax(apply_coupon(subtotal, coupon))\n```\n\n"
            "*adk-review, guideline.correctness, finding 1a2b3c4d5e6f7a8b*"
        )

    def test_a_minor_with_no_reason_and_no_code_is_its_title_fix_and_mark(self, azure: ModuleType, row) -> None:
        minor = replace(row, severity="minor", why="  ", code="\n")
        assert azure.draft(minor).split("\n\n") == [
            "**minor**: `charge()` adds tax twice",
            "Suggested fix: Add the tax once.",
            azure.mark(minor),
        ]
        bare = replace(minor, title="", suggestion="", fingerprint="")
        assert azure.draft(bare) == "**minor**: guideline.correctness", "a finding with no title is named by its check"

    @pytest.mark.parametrize(
        "change", [{"on_added_line": False}, {"on_added_line": None}, {"lines": None, "line": None}]
    )
    def test_code_off_the_added_lines_is_shown_and_cannot_be_applied(
        self, azure: ModuleType, row, change: dict
    ) -> None:
        """A `suggestion` block replaces the lines its comment stands on, when the author applies it: only a finding
        on lines the change added gets one, as the owner chose."""
        text = azure.draft(replace(row, **change))
        assert not azure.suggests(replace(row, **change))
        assert "```suggestion" not in text and "Corrected code:\n\n```\ntotal = with_tax(" in text

    def test_code_that_holds_a_fence_is_indented_and_never_a_suggestion(self, azure: ModuleType, row) -> None:
        text = azure.draft(replace(row, code="```python\nx = 1\n```"))
        assert "```suggestion" not in text and "Corrected code:\n\n    ```python\n    x = 1\n    ```" in text

    def test_a_models_words_notify_no_one_and_the_code_keeps_every_character(self, azure: ModuleType, row) -> None:
        """Azure DevOps acts on `@name`, `#12` and `!7` in a comment, so prose has each broken, as the reviewer's
        own comment has. Inside the suggestion nothing may change: "Apply changes" writes it into the file, and
        `@dataclass` broken there is broken code."""
        code = "@dataclass\nclass A:  # see #12 and !7\n    items: list[T] = <T>"
        hostile = "@alice see #12, !7, ![x](http://e.test) <img src=x>"
        text = azure.draft(replace(row, title=hostile, why=hostile, suggestion=hostile, severity="@bob", code=code))
        *prose, block, _mark = text.split("\n\n")
        for part in prose:
            assert "@ " in part and "](" not in part and "<img" not in part, part
        assert "@ alice see # 12, ! 7" in prose[0] and prose[0].startswith("**@ bob**")
        assert block == f"```suggestion\n{code}\n```"

    def test_the_mark_is_found_again_however_the_person_framed_it(self, azure: ModuleType, row) -> None:
        assert azure.marked(azure.draft(row)) == "1a2b3c4d5e6f7a8b"
        assert azure.marked("> adk-review, rule.named-secrets, finding 00ff00ff00ff00ff (edited)") == "00ff00ff00ff00ff"
        assert azure.marked("no mark here") is None and azure.marked("") is None

    @pytest.mark.parametrize(
        "change", [{"fingerprint": ""}, {"fingerprint": "XYZ"}, {"fingerprint": "ab" * 9}, {"check": "a b*c"}]
    )
    def test_a_finding_that_cannot_be_found_again_gets_no_mark(self, azure: ModuleType, row, change: dict) -> None:
        """A mark nothing can read back is worse than none: it would say the comment can be found again."""
        unmarked = replace(row, **change)
        assert azure.mark(unmarked) == "" and "adk-review," not in azure.draft(unmarked)


class TestAFilesLinesAtACommit:
    """`file_lines`: the text a comment's place is counted in, as git holds it at the commit reviewed."""

    def test_a_files_lines_keep_their_numbers_and_lose_their_carriage_returns(self, tmp_path: Path) -> None:
        from conftest import _committed, _git_in

        from tui.git_sync import file_lines

        _git_in(tmp_path, "init", "-q", "-b", "main")
        head = _committed(tmp_path, "src/a b.py", "\n\nx = 'ö'\r\ny = 2\n".encode())
        assert file_lines(str(tmp_path), head, "src/a b.py") == ["", "", "x = 'ö'", "y = 2", ""]
        _committed(tmp_path, "src/a b.py", b"y = 3\n")
        assert file_lines(str(tmp_path), head, "src/a b.py")[3] == "y = 2", "the commit reviewed, not the branch's tip"

    def test_a_file_or_a_commit_git_does_not_hold_is_none(self, tmp_path: Path) -> None:
        from conftest import _committed, _git_in

        from tui.git_sync import file_lines

        _git_in(tmp_path, "init", "-q", "-b", "main")
        head = _committed(tmp_path, "a.py", "x = 1\n")
        assert file_lines(str(tmp_path), head, "missing.py") is None
        assert file_lines(str(tmp_path), "f" * 40, "a.py") is None
        assert file_lines(str(tmp_path), "--output=x", "a.py") is None, "a commit that reads as a flag is none"
        assert file_lines(str(tmp_path / "no-repository"), head, "a.py") is None
