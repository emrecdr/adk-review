"""Tests for the front end's Azure DevOps side, `tui/tui/azure*.py`: a pull request's address and a finding's links,
the comment drafted from a finding, the thread it is posted as, the login's token, the client and what is kept.

Every test is offline: a fake Azure DevOps answers on a local port, and a fake `az` gives the token. Each test imports
what it reads where it runs, as `tests/test_tui.py` says why."""

from __future__ import annotations

import importlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from conftest import FakeAdo, FakeToken, _long, _ready_to_comment


@pytest.fixture
def azure() -> ModuleType:
    """`tui.azure`, imported where the test runs."""
    return importlib.import_module("tui.azure")


def _short_sentences(said: str) -> None:
    """What the front end shows a person is ASD-STE100, as CLAUDE.md asks, by the suite's own guard."""
    assert _long([("the front end", said)]) == []


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
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/\u0661\u0662",  # digits, and not ASCII's
            "https://dev.azure.com/contoso/Web/_git/../pullrequest/1",
            "https://dev.azure.com/contoso/Web/_git/pay%2Fments/pullrequest/1",
            "https://dev.azure.com/contoso/Web/extra/_git/payments/pullrequest/1",
            "https://dev.azure.com/_git/payments/pullrequest/1",  # no organization
            "https://dev.azure.com/contoso/Web/_git/pay%1B%5B2Jments/pullrequest/1",  # an escape sequence in a name
            "https://dev.azure.com/contoso/We%00b/_git/payments/pullrequest/1",
            "https://dev.azure.com/con%0Atoso/Web/_git/payments/pullrequest/1",
            "https://dev.azure.com/contoso/Web/_git/pay%E2%80%AEments/pullrequest/1",  # a right-to-left override
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
        login = ":".join(("someone", "hunter2secret"))  # joined here: no line of this file holds a login in a URL
        with pytest.raises(ValueError, match=r"pullrequest/<number>") as refused:
            azure.parse_pr_url(f"https://{login}@dev.azure.com/contoso/Web/_git/payments/pullrequest/1")
        assert "hunter2secret" not in str(refused.value) and "someone" not in str(refused.value)

    def test_each_sentence_is_short_and_one_thing(self, azure: ModuleType) -> None:
        for pasted in ("", "https://tfs.example.test/tfs/Web/_git/payments/pullrequest/1"):
            with pytest.raises(ValueError, match="not a pull request") as refused:
                azure.parse_pr_url(pasted)
            _short_sentences(str(refused.value))


class TestAFindingsLinks:
    """The three links, each built from the address's parts and never from a model's text."""

    def test_the_line_link_pins_the_commit_and_covers_whole_lines(self, azure: ModuleType) -> None:
        sha = "a" * 40
        assert azure.line_link(_pr(azure), sha, "src/my file#1&2.py", 42, 57) == (
            "https://dev.azure.com/contoso/Web/_git/payments?path=%2Fsrc%2Fmy%20file%231%262.py"
            f"&version=GC{sha}&line=42&lineEnd=58&lineStartColumn=1&lineEndColumn=1&_a=contents"
        )
        assert "&line=42&lineEnd=43&" in azure.line_link(_pr(azure), sha, "a.py", 42, 42)
        assert "path=%2Fsrc%2F%C3%B6.py" in azure.line_link(_pr(azure), sha, "src/\u00f6.py", 1, 1)
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
        assert findings.load_report(_report(tmp_path, [], good))[1] == "a" * 40
        assert findings.load_report(_report(tmp_path, [], {"head_sha": "b" * 64}))[1] == "b" * 64, "its commit alone"
        for bad in ({**good, "head_sha": "main"}, {**good, "head_sha": 7}, {**good, "head_sha": None}, None, [good]):
            assert findings.load_report(_report(tmp_path, [], bad))[1] is None, bad
        assert findings.load_report(tmp_path / "missing.json") == ([], None)
        (tmp_path / "broken.json").write_text("{", encoding="utf-8")
        assert findings.load_report(tmp_path / "broken.json") == ([], None)


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

    def test_code_the_reviewer_cut_or_hid_a_credential_in_is_never_a_suggestion(self, azure: ModuleType, row) -> None:
        """The report cuts long code where it says so, and hides a credential behind a placeholder. "Apply changes"
        would write either into the file."""
        from adk_review.core.text import CUT_MARK

        for code in (f"x = 1\ny = [{CUT_MARK}1,200 characters]", 'key = "[REDACTED:github_token]"', "k = [REDACTED]"):
            shown = replace(row, code=code)
            assert not azure.suggests(shown), code
            assert "```suggestion" not in azure.draft(shown) and f"Corrected code:\n\n```\n{code}\n```" in azure.draft(
                shown
            )

    @pytest.mark.parametrize(
        "written",
        [
            'echo "##vso[task.setvariable variable=tag]$(v)"',
            "    ::set-output name=tag::v1",
            "x = 1  # <!-- kept for the template -->",
            "echo '##[group]build'",
        ],
    )
    def test_code_the_report_made_inert_is_never_a_suggestion(self, azure: ModuleType, row, written: str) -> None:
        """Reproduced by review: the report breaks a pipeline's command openers and an HTML comment's in every line
        of corrected code, a space put in. A suggestion drafted from that text would commit the broken line. Each
        case is made by the report's own rule: where that rule changes, and the front end's reading of it does not,
        this test fails."""
        from adk_review.deliver.report import safe_code

        reported = safe_code(written)
        assert reported != written, "the report changed it"
        shown = replace(row, code=reported)
        assert not azure.suggests(shown) and "```suggestion" not in azure.draft(shown)
        assert azure.suggests(replace(row, code=written)), "as written, which a report never holds, it would apply"

    def test_a_blank_line_that_opens_the_code_is_kept(self, azure: ModuleType, row) -> None:
        opened = replace(row, code="\n    total = with_tax(subtotal)\n\n")
        assert "```suggestion\n\n    total = with_tax(subtotal)\n```" in azure.draft(opened)

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
        head = _committed(tmp_path, "src/a b.py", "\n\nx = '\u00f6'\r\ny = 2\n".encode())
        assert file_lines(str(tmp_path), head, "src/a b.py") == ["", "", "x = '\u00f6'", "y = 2"]
        ragged = _committed(tmp_path, "ragged.py", b"a\nb")
        assert file_lines(str(tmp_path), ragged, "ragged.py") == ["a", "b"], "no newline ends the file: two lines too"
        empty = _committed(tmp_path, "empty.py", b"")
        assert file_lines(str(tmp_path), empty, "empty.py") == [], "a file's last newline starts no line"
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


class TestTheThreadAComment:
    """`thread_payload`: what one comment is posted as, in the shape Microsoft's own sample sends."""

    def test_the_payload_is_microsofts_sample_shape_over_whole_lines(self, azure: ModuleType) -> None:
        assert azure.thread_payload("hello", azure.Place("src/app.py", 42, 57, "    return total"), 3, 7) == {
            "comments": [{"parentCommentId": 0, "content": "hello", "commentType": 1}],
            "status": 1,
            "threadContext": {
                "filePath": "/src/app.py",
                "leftFileStart": None,
                "leftFileEnd": None,
                "rightFileStart": {"line": 42, "offset": 1},
                "rightFileEnd": {"line": 57, "offset": 17},
            },
            "pullRequestThreadContext": {
                "changeTrackingId": 7,
                "iterationContext": {"firstComparingIteration": 3, "secondComparingIteration": 3},
            },
        }

    @pytest.mark.parametrize(
        ("last_line", "offset"), [("", 1), ("x = '\U0001f600'", 9), ("x = 1\r", 6), ("\u00f6", 2), ("\t\tx", 4)]
    )
    def test_the_end_is_the_last_lines_length_in_utf_16_units_plus_one(
        self, azure: ModuleType, last_line: str, offset: int
    ) -> None:
        """A place that ends where it starts is empty: "Apply changes" then puts the suggestion before the line
        and keeps the old one, as two tools' authors found. The end is past the line's last character, counted as
        Azure DevOps counts, in UTF-16 units."""
        context = azure.thread_payload("t", azure.Place("/a.py", 1, 1, last_line), 1, 1)["threadContext"]
        assert context["rightFileEnd"] == {"line": 1, "offset": offset} and context["filePath"] == "/a.py"


@pytest.fixture
def client() -> ModuleType:
    """`tui.azure_client`, imported where the test runs."""
    return importlib.import_module("tui.azure_client")


class FakeAz:
    """The Azure CLI as `AzLogin` runs it: each call counted and kept, a new token each time, or what `answer` says."""

    def __init__(self) -> None:
        self.calls = 0
        self.argv: list[str] = []
        self.expires_on: object = 4600
        self.answer: dict = {}

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        self.argv = argv
        if "raises" in self.answer:
            raise self.answer["raises"]
        token = {"accessToken": f"tok-{self.calls}", "expires_on": self.expires_on, "tokenType": "Bearer"}
        out = self.answer.get("stdout", json.dumps(token))
        return subprocess.CompletedProcess(argv, self.answer.get("returncode", 0), out, self.answer.get("stderr", ""))


@pytest.fixture
def fake_az(client: ModuleType, monkeypatch: pytest.MonkeyPatch) -> FakeAz:
    """A fake `az`, found on PATH at `/bin/az`."""
    monkeypatch.setattr(client, "on_path", lambda name: f"/bin/{name}")
    return FakeAz()


class TestTheLoginsToken:
    """`AzLogin`: the person's own Azure CLI login, asked with fixed arguments, its token kept in memory alone."""

    def test_az_is_asked_with_fixed_arguments_and_no_shell(self, client: ModuleType, fake_az: FakeAz) -> None:
        """On Windows `az` is `az.cmd`, which runs through cmd.exe: no argument is text a person or a model wrote."""
        assert client.AzLogin(run=fake_az, clock=lambda: 1000.0)() == "Bearer tok-1"
        resource = "499b84ac-1321-427f-aa17-267ca6975798"
        assert fake_az.argv == ["/bin/az", "account", "get-access-token", "--resource", resource, "--output", "json"]

    def test_the_real_az_on_path_is_run_and_its_token_read(
        self, client: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No fake between `AzLogin` and the program: on Windows `az` is a batch file, found by `PATHEXT` and run
        through cmd.exe, and elsewhere a script. The one on PATH here prints what `az` prints."""
        from conftest import _script

        folder = tmp_path / "bin"
        folder.mkdir()
        printed = '{"accessToken": "tok-real", "expires_on": 4600, "tokenType": "Bearer"}'
        if os.name == "nt":
            (folder / "az.cmd").write_text(f"@echo off\r\necho {printed}\r\n", encoding="utf-8", newline="")
        else:
            _script(folder / "az", f"echo '{printed}'\n")
        monkeypatch.setenv("PATH", str(folder))
        assert client.AzLogin(clock=lambda: 1000.0)() == "Bearer tok-real"

    def test_an_az_that_never_answers_is_stopped_whole_and_said(
        self, client: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`az` starts a Python of its own. On Windows, stopping the batch file alone leaves that child holding the
        output open, and the screen would wait on it past any timeout, a comment under way for good."""
        from conftest import _script

        folder = tmp_path / "bin"
        folder.mkdir()
        if os.name == "nt":
            (folder / "az.cmd").write_text("@echo off\r\nping -n 30 127.0.0.1 >NUL\r\n", encoding="utf-8", newline="")
        else:
            _script(folder / "az", "sleep 30\n")
        monkeypatch.setenv("PATH", os.pathsep.join([str(folder), os.environ.get("PATH", "")]))
        monkeypatch.setattr(client, "AZ_TIMEOUT_S", 1)
        started = time.monotonic()
        with pytest.raises(client.AzureError, match="did not answer within 1 s"):
            client.AzLogin()()
        assert time.monotonic() - started < 15, "the wait ends with the timeout, not with the program"

    def test_a_token_is_kept_until_two_minutes_before_it_expires(self, client: ModuleType, fake_az: FakeAz) -> None:
        now = [1000.0]
        login = client.AzLogin(run=fake_az, clock=lambda: now[0])
        assert login() == "Bearer tok-1"
        now[0] = 4479.0
        assert login() == "Bearer tok-1" and fake_az.calls == 1
        now[0] = 4481.0
        assert login() == "Bearer tok-2" and fake_az.calls == 2
        login.forget()
        assert login() == "Bearer tok-3" and fake_az.calls == 3

    @pytest.mark.parametrize("expires_on", [None, "4600", 4600.5, True])
    def test_a_token_that_names_no_whole_second_to_expire_at_is_kept_three_minutes(
        self, client: ModuleType, fake_az: FakeAz, expires_on: object
    ) -> None:
        """Reproduced by review: an older Azure CLI names no `expires_on`, and such a token was asked for again at
        each of a post's five calls, seconds each. `az` gives no token with less than five minutes left."""
        fake_az.expires_on = expires_on
        now = [1000.0]
        login = client.AzLogin(run=fake_az, clock=lambda: now[0])
        assert (login(), login()) == ("Bearer tok-1", "Bearer tok-1")
        now[0] = 1179.0
        assert login() == "Bearer tok-1"
        now[0] = 1181.0
        assert login() == "Bearer tok-2"

    @pytest.mark.parametrize(
        ("answer", "said"),
        [
            ({"returncode": 1, "stderr": "Please run 'az login' to setup account."}, "Run `az login`"),
            ({"returncode": 0, "stdout": "not json"}, "Run `az login`"),
            ({"returncode": 0, "stdout": '{"tokenType": "Bearer"}'}, "Run `az login`"),
            ({"returncode": 0, "stdout": '{"accessToken": ""}'}, "Run `az login`"),
            ({"returncode": 0, "stdout": '{"accessToken": "a\\r\\nX-Evil: 1"}'}, "Run `az login`"),
            ({"returncode": 0, "stdout": '["accessToken"]'}, "Run `az login`"),
            ({"returncode": 1, "stdout": '{"accessToken": "tok-old"}'}, "Run `az login`"),
            ({"raises": subprocess.TimeoutExpired("az", 30)}, "did not answer within 30 s"),
            ({"raises": PermissionError(13, "Permission denied")}, "could not start: Permission denied"),
        ],
    )
    def test_each_way_az_fails_is_a_sentence_that_names_the_next_step(
        self, client: ModuleType, fake_az: FakeAz, answer: dict, said: str
    ) -> None:
        fake_az.answer = answer
        with pytest.raises(client.AzureError, match=said) as failed:
            client.AzLogin(run=fake_az)()
        _short_sentences(str(failed.value))

    def test_az_missing_from_path_is_said(
        self, client: ModuleType, fake_az: FakeAz, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client, "on_path", lambda name: name)  # as the reviewer's finder answers a name not found
        with pytest.raises(client.AzureError, match="not on PATH"):
            client.AzLogin(run=fake_az)()
        assert fake_az.calls == 0, "a bare name is never run: Windows would look in the current folder too"

    def test_a_token_is_in_no_error_and_no_repr(self, client: ModuleType, fake_az: FakeAz) -> None:
        login = client.AzLogin(run=fake_az, clock=lambda: 1000.0)
        login()
        assert "tok-1" not in repr(login) and "tok-1" not in str(login)
        fake_az.answer = {"returncode": 1, "stderr": "ERROR: tok-1 expired", "stdout": "tok-1"}
        login.forget()
        with pytest.raises(client.AzureError) as failed:
            login()
        assert "tok-1" not in str(failed.value), "az's own words are never repeated"


def _closed_port() -> int:
    """A local port nothing listens on: bound, read, then closed."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TestTheClient:
    """`Client`: each call to Azure DevOps, and each refusal as a sentence that names the next step."""

    def test_each_call_carries_the_token_and_the_api_version(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        fake_ado.answer("GET", "", {"status": "active"})
        assert client.Client(fake_ado.pr, FakeToken()).status() == "active"
        seen = fake_ado.seen[0]
        assert (seen.method, seen.path, seen.query) == ("GET", fake_ado.root, {"api-version": "7.1"})
        assert seen.headers["authorization"] == "Bearer tok" and seen.headers["accept"] == "application/json"
        assert seen.headers["user-agent"] == "adk-review-tui", "a plain name: nothing of the person's goes with a call"

    def test_the_iterations_are_read_oldest_first(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        pushed = [
            {"id": 2, "sourceRefCommit": {"commitId": "b" * 40}},
            {"id": 1, "sourceRefCommit": {"commitId": "a" * 40}},
        ]
        fake_ado.answer("GET", "/iterations", {"value": [*pushed, {"id": "3"}, {"id": 4, "sourceRefCommit": None}, 7]})
        assert client.Client(fake_ado.pr, FakeToken()).iterations() == [(1, "a" * 40), (2, "b" * 40)]

    def test_a_files_change_is_found_on_a_later_page_and_asked_for_once(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        first = {
            "changeEntries": [{"changeTrackingId": 1, "item": {"path": "/a.py"}}],
            "nextSkip": 2000,
            "nextTop": 2000,
        }
        renamed = {"changeTrackingId": 9, "item": {"path": "/src/new name.py"}, "originalPath": "/src/old.py"}
        fake_ado.answer("GET", "/iterations/3/changes", first)
        fake_ado.answer("GET", "/iterations/3/changes", {"changeEntries": [renamed, {"item": None}], "nextSkip": 0})
        asked = client.Client(fake_ado.pr, FakeToken())
        found = (asked.change_of(3, "src/new name.py"), asked.change_of(3, "/a.py"), asked.change_of(3, "no.py"))
        assert found == (9, 1, None) and asked.change_of(3, "src/old.py") is None, "a moved file is its new path"
        assert [(seen.query["$skip"], seen.query["$top"]) for seen in fake_ado.seen] == [
            ("0", "2000"),
            ("2000", "2000"),
        ]

    def test_a_page_that_does_not_move_on_ends_the_read(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        fake_ado.answer("GET", "/iterations/3/changes", {"changeEntries": [], "nextSkip": 0, "nextTop": 2000})
        assert client.Client(fake_ado.pr, FakeToken()).change_of(3, "a.py") is None and len(fake_ado.seen) == 1
        fake_ado.answer("GET", "/iterations/4/changes", {"changeEntries": [], "nextSkip": 2000})  # the same, each time
        assert client.Client(fake_ado.pr, FakeToken()).change_of(4, "a.py") is None and len(fake_ado.seen) == 3

    def test_a_thread_is_found_by_its_mark_and_a_deleted_one_is_not(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        live = {"id": 5, "comments": [{"content": "x\n\n*adk-review, rule.lint, finding " + "ab" * 8 + "*"}]}
        gone = {"id": 6, "isDeleted": True, "comments": [{"content": "adk-review, rule.lint, finding " + "cd" * 8}]}
        wiped = {"id": 7, "comments": [{"isDeleted": True, "content": "adk-review, rule.lint, finding " + "ef" * 8}]}
        system = {"id": 8, "comments": [{"commentType": "system", "content": None}, "no comment", None]}
        fake_ado.answer("GET", "/threads", {"value": [system, wiped, gone, live, None]})
        asked = client.Client(fake_ado.pr, FakeToken())
        assert [asked.thread_of(mark * 8) for mark in ("ab", "cd", "ef")] == [5, None, None]
        assert asked.thread_of("") is None and len(fake_ado.seen) == 3, "a finding with no identity asks nothing"

    def test_a_post_sends_the_payload_as_utf_8_and_answers_the_threads_id(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        fake_ado.answer("POST", "/threads", {"id": 148})
        fake_ado.answer("POST", "/threads", {"id": "148"})  # the second post's answer
        payload = {"comments": [{"content": "\u00f6 \U0001f600"}]}
        assert client.Client(fake_ado.pr, FakeToken()).post(payload) == 148
        sent = fake_ado.seen[0]
        assert (sent.method, sent.path, sent.query) == ("POST", fake_ado.root + "/threads", {"api-version": "7.1"})
        assert json.loads(sent.body.decode("utf-8")) == payload
        assert sent.headers["content-type"] == "application/json"
        with pytest.raises(client.AzureError, match="no thread"):
            client.Client(fake_ado.pr, FakeToken()).post(payload)

    @pytest.mark.parametrize(
        ("status", "headers", "raw", "said"),
        [
            (401, {}, b"", "did not accept your login. Run `az login`, then post again"),
            (
                401,
                {"X-VSS-ResourceTenant": "72f988bf-86f1-41af-91ab-2d7cd011db47"},
                b"",
                "Run `az login --tenant 72f988bf-86f1-41af-91ab-2d7cd011db47`, then post again",
            ),
            (401, {"X-VSS-ResourceTenant": "x; rm -rf"}, b"", "Run `az login`, then post again"),
            (401, {"X-VSS-ResourceTenant": "00000000-0000-0000-0000-000000000000"}, b"", "Run `az login`, then"),
            (203, {"Content-Type": "text/html"}, b"<html>Sign in</html>", "Run `az login`, then post again"),
            (200, {"Content-Type": "text/html"}, b"<html>Sign in</html>", "Run `az login`, then post again"),
            (200, {}, b"[1, 2]", "Run `az login`, then post again"),
            (403, {}, b"", 'Ask for the "Contribute to pull requests" permission'),
            (404, {}, b"", "found no such pull request. Check the address"),
            (
                500,
                {},
                b'{"message": "TF400898: An Internal\\nError Occurred."}',
                "answered 500: TF400898: An Internal Error",
            ),
            (500, {}, b"<html>oops</html>", "answered 500: Internal Server Error. Nothing was posted"),
        ],
    )
    def test_each_refusal_is_a_sentence_that_names_the_next_step(
        self, client: ModuleType, fake_ado: FakeAdo, status: int, headers: dict[str, str], raw: bytes, said: str
    ) -> None:
        fake_ado.answer("GET", "", status=status, headers=headers, raw=raw)
        with pytest.raises(client.AzureError, match=re.escape(said)) as refused:
            client.Client(fake_ado.pr, FakeToken("Bearer tok-secret")).status()
        assert "tok-secret" not in str(refused.value) and "rm -rf" not in str(refused.value)
        assert len(fake_ado.seen) == 1, "asked once"

    def test_a_refused_login_is_forgotten_so_the_next_post_asks_az_again(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        """The sentence says "run az login, then post again": the token kept in memory is the one just refused."""
        token = FakeToken("Bearer stale")
        fake_ado.answer("GET", "", status=401)
        fake_ado.answer("GET", "/threads", raw=b"<html>Sign in</html>", status=203)
        fake_ado.answer("GET", "/iterations", status=403)
        asked = client.Client(fake_ado.pr, token)
        for call in (asked.status, lambda: asked.thread_of("ab" * 8), asked.iterations):
            with pytest.raises(client.AzureError):
                call()
        assert token.forgot == 2, "a 403 is no refused login: the same token is right"

    def test_a_redirect_is_never_followed_so_the_token_goes_nowhere_else(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        """urllib sends every header again to a redirect's target, `Authorization` among them. A sign-in page is
        one redirect away, on another host."""
        token = FakeToken()
        fake_ado.answer("GET", "", status=302, headers={"Location": fake_ado.pr.origin + "/elsewhere"})
        with pytest.raises(client.AzureError, match="Run `az login`"):
            client.Client(fake_ado.pr, token).status()
        assert [seen.path for seen in fake_ado.seen] == [fake_ado.root] and token.forgot == 1

    def test_a_429_waits_once_as_retry_after_says_and_a_long_wait_is_said(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        waited: list[float] = []
        fake_ado.answer("GET", "", status=429, headers={"Retry-After": "2"})
        fake_ado.answer("GET", "", {"status": "active"})
        assert client.Client(fake_ado.pr, FakeToken(), sleep=waited.append).status() == "active" and waited == [2.0]
        fake_ado.answer("GET", "/threads", status=429, headers={"Retry-After": "120"})
        with pytest.raises(client.AzureError, match=re.escape("asked to wait 120 s. Post again after that")):
            client.Client(fake_ado.pr, FakeToken(), sleep=waited.append).thread_of("ab" * 8)
        fake_ado.answer("GET", "/iterations", status=429, headers={"Retry-After": "1"})  # and again after the wait
        with pytest.raises(client.AzureError, match="asked to wait 1 s"):
            client.Client(fake_ado.pr, FakeToken(), sleep=waited.append).iterations()
        assert waited == [2.0, 1.0]
        fake_ado.answer("GET", "/iterations/1/changes", status=429, headers={"Retry-After": "soon"})
        with pytest.raises(client.AzureError, match="asked to wait 60 s"):
            client.Client(fake_ado.pr, FakeToken(), sleep=waited.append).change_of(1, "a.py")

    def test_a_network_failure_is_a_sentence_and_no_error_holds_the_token(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        closed = replace(fake_ado.pr, origin=f"http://127.0.0.1:{_closed_port()}/contoso")
        with pytest.raises(client.AzureError, match="did not answer") as failed:
            client.Client(closed, FakeToken("Bearer tok-secret")).status()
        assert "tok-secret" not in str(failed.value) and "then post again" in str(failed.value)
        assert " c " not in str(failed.value), "the client names no key: the screen does"
        with pytest.raises(client.AzureError, match="did not answer"):
            client.Client(closed, FakeToken()).post({"comments": []})

    @pytest.mark.parametrize(
        "origin",
        [
            "http://example.test/contoso",
            "http://127.0.0.1.example.test:80/contoso",
            "http://localhost:8080/contoso",
            "ftp://dev.azure.com/contoso",
            "file:///contoso",
            "//dev.azure.com/contoso",
        ],
    )
    def test_a_token_goes_over_https_or_to_this_machine_alone(
        self, client: ModuleType, fake_ado: FakeAdo, origin: str
    ) -> None:
        """Whatever built the address: a pasted one is HTTPS by the time it is read, and this is the second lock.
        A call on any other road is refused before the login is asked for."""
        token = FakeToken()
        with pytest.raises(client.AzureError, match="not Azure DevOps' own address"):
            client.Client(replace(fake_ado.pr, origin=origin), token).status()
        assert token.asked == 0 and fake_ado.seen == []

    @pytest.mark.parametrize("broken", ["cut", "garbage"])
    @pytest.mark.parametrize("method", ["GET", "POST"])
    def test_an_answer_broken_on_its_way_is_a_sentence_and_never_ends_the_screen(
        self, client: ModuleType, fake_ado: FakeAdo, method: str, broken: str
    ) -> None:
        """A body shorter than it says, or no HTTP at all, as a proxy or a dropped line answers: neither is an
        error of the network's own kind, so neither was caught. The post's worker then failed, the screen ended,
        and its traceback printed the call's own headers."""
        fake_ado.answer(method, "/threads" if method == "POST" else "", {"id": 148, "status": "active"}, broken=broken)
        asked = client.Client(fake_ado.pr, FakeToken("Bearer tok-secret"))
        with pytest.raises(client.AzureError, match="did not answer") as failed:
            asked.post({"comments": []}) if method == "POST" else asked.status()
        assert "tok-secret" not in str(failed.value) and len(fake_ado.seen) == 1, "said once, and sent once"

    @pytest.mark.parametrize(
        ("answer", "said"),
        [
            ({"broken": "cut"}, "did not answer whole"),
            (
                {"status": 502, "raw": b"<html>Bad Gateway</html>"},
                "answered 502: Bad Gateway. The comment may be posted",
            ),
            ({"status": 504}, "answered 504: Gateway Timeout. The comment may be posted."),
        ],
    )
    def test_a_post_that_may_have_arrived_is_never_said_to_be_unposted(
        self, client: ModuleType, fake_ado: FakeAdo, answer: dict, said: str
    ) -> None:
        """Reproduced by review: the post reached Azure DevOps and its answer did not come back, and the screen
        said "nothing was posted". The person pasted the comment by hand: two threads for one finding."""
        fake_ado.answer("POST", "/threads", {"id": 148}, **answer)
        with pytest.raises(client.AzureError, match=re.escape(said)) as failed:
            client.Client(fake_ado.pr, FakeToken()).post({"comments": []})
        assert failed.value.unsure and "Nothing was posted" not in str(failed.value)

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 400])
    def test_a_post_azure_devops_refused_is_known_to_be_unposted(
        self, client: ModuleType, fake_ado: FakeAdo, status: int
    ) -> None:
        fake_ado.answer("POST", "/threads", status=status, headers={"Retry-After": "120"})
        with pytest.raises(client.AzureError) as failed:
            client.Client(fake_ado.pr, FakeToken()).post({"comments": []})
        assert not failed.value.unsure

    def test_a_read_that_failed_posted_nothing(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        fake_ado.answer("GET", "", status=502)
        with pytest.raises(
            client.AzureError, match=re.escape("answered 502: Bad Gateway. Nothing was posted")
        ) as failed:
            client.Client(fake_ado.pr, FakeToken()).status()
        assert not failed.value.unsure

    def test_a_refusals_own_body_broken_on_its_way_is_still_its_sentence(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        fake_ado.answer("GET", "", status=500, raw=b'{"message": "TF4', broken="cut")
        with pytest.raises(client.AzureError, match="answered 500: Internal Server Error"):
            client.Client(fake_ado.pr, FakeToken()).status()

    def test_a_login_that_az_cannot_give_ends_the_call_before_it_is_made(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        class NoLogin(FakeToken):
            def __call__(self) -> str:
                raise client.AzureError("The Azure CLI gave no token. Run `az login`, then post again.")

        with pytest.raises(client.AzureError, match="gave no token"):
            client.Client(fake_ado.pr, NoLogin()).status()
        assert fake_ado.seen == []


def _place(path: str = "src/app.py"):  # the module's own type
    """Where the tests' comment goes: lines 42 to 57 of `path`, the last of them `    return total`."""
    return importlib.import_module("tui.azure").Place(path, 42, 57, "    return total")


class TestOnePost:
    """`post_comment`: the checks before a post, in order, each refusal a sentence and nothing posted."""

    def test_a_comment_is_posted_once_on_the_latest_push(
        self, azure: ModuleType, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        _ready_to_comment(fake_ado, "b" * 40)
        asked = client.Client(fake_ado.pr, FakeToken())
        posted = client.post_comment(asked, text="hello", place=_place(), commit="b" * 40, fingerprint="ab" * 8)
        assert posted == client.Posted(148, already=False)
        posts = [seen for seen in fake_ado.seen if seen.method == "POST"]
        assert len(posts) == 1
        assert json.loads(posts[0].body) == azure.thread_payload("hello", _place(), 2, 7)
        tails = [seen.path.removeprefix(fake_ado.root) for seen in fake_ado.seen]
        assert tails == ["", "/iterations", "/threads", "/iterations/2/changes", "/threads"], "the cheap checks first"

    @pytest.mark.parametrize(
        ("change", "said"),
        [
            ({"status": "completed"}, "The pull request is completed. Nothing was posted."),
            ({"status": "abandoned"}, "The pull request is abandoned. Nothing was posted."),
            ({"commit": "a" * 40}, "The pull request has a newer push, bbbbbbb. Fetch, review again, then comment."),
            ({"commit": "c" * 40}, "The pull request never held commit ccccccc. Check the address and the branch"),
            ({"path": "docs/x.md"}, "The pull request does not change docs/x.md. No line can hold the comment."),
        ],
    )
    def test_each_refusal_posts_nothing(
        self, client: ModuleType, fake_ado: FakeAdo, change: dict[str, str], said: str
    ) -> None:
        _ready_to_comment(fake_ado, "b" * 40, status=change.get("status", "active"))
        with pytest.raises(client.AzureError, match=re.escape(said)) as refused:
            client.post_comment(
                client.Client(fake_ado.pr, FakeToken()),
                text="hello",
                place=_place(change.get("path", "src/app.py")),
                commit=change.get("commit", "b" * 40),
                fingerprint="ab" * 8,
            )
        assert all(seen.method == "GET" for seen in fake_ado.seen)
        _short_sentences(str(refused.value))

    def test_a_pull_request_with_no_push_listed_takes_no_comment(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        fake_ado.answer("GET", "", {"status": "active"})
        fake_ado.answer("GET", "/iterations", {"value": []})
        with pytest.raises(client.AzureError, match="never held commit bbbbbbb"):
            client.post_comment(
                client.Client(fake_ado.pr, FakeToken()), text="t", place=_place(), commit="b" * 40, fingerprint=""
            )

    def test_a_finding_already_posted_is_found_and_not_posted_again(
        self, client: ModuleType, fake_ado: FakeAdo, azure: ModuleType, row
    ) -> None:
        """Post pressed twice, or a post that timed out and still arrived: one thread, never two. The mark in the
        thread's own text says so, whatever the screen remembers."""
        fake_ado.answer("GET", "/threads", {"value": [{"id": 5, "comments": [{"content": azure.mark(row)}]}]})
        _ready_to_comment(fake_ado, "b" * 40)
        posted = client.post_comment(
            client.Client(fake_ado.pr, FakeToken()),
            text="hello",
            commit="b" * 40,
            fingerprint=row.fingerprint,
            place=_place(),
        )
        assert posted == client.Posted(5, already=True)
        assert all(seen.method == "GET" for seen in fake_ado.seen)

    def test_a_finding_with_no_identity_is_posted_with_no_search_for_its_like(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        _ready_to_comment(fake_ado, "b" * 40)
        asked = client.Client(fake_ado.pr, FakeToken())
        assert client.post_comment(asked, text="t", place=_place(), commit="b" * 40, fingerprint="").thread == 148
        assert [seen.method for seen in fake_ado.seen].count("POST") == 1
        assert ("GET", fake_ado.root + "/threads") not in [(seen.method, seen.path) for seen in fake_ado.seen]


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """`tui.azure_state`, keeping what it keeps in a folder of the test's own: never a person's state folder."""
    monkeypatch.setattr(importlib.import_module("tui.config"), "STATE_DIR", tmp_path / "state")
    return importlib.import_module("tui.azure_state")


class TestWhatTheScreenRemembers:
    """`azure.json` in the state folder: a pull request's address a branch, and each thread posted."""

    def test_an_address_is_kept_per_repository_and_branch_and_forgotten_when_emptied(
        self, state: ModuleType, tmp_path: Path
    ) -> None:
        assert state.pull_request_url("/work/payments", "feature/orders") == "", "nothing kept, and no file yet"
        state.keep_pull_request_url(
            "/work/payments", "feature/orders", "https://dev.azure.com/contoso/a/_git/b/pullrequest/1"
        )
        state.keep_pull_request_url(
            "/work/payments", "feature/tax", "https://dev.azure.com/contoso/a/_git/b/pullrequest/2"
        )
        state.keep_pull_request_url(
            "/work/other", "feature/orders", "https://dev.azure.com/contoso/a/_git/c/pullrequest/3"
        )
        assert state.pull_request_url("/work/payments", "feature/orders").endswith("/1")
        assert state.pull_request_url("/work/payments", "feature/tax").endswith("/2")
        assert state.pull_request_url("/work/other", "feature/orders").endswith("/3")
        state.keep_pull_request_url("/work/payments", "feature/orders", "")
        state.keep_pull_request_url("/work/other", "feature/orders", "")
        assert state.pull_request_url("/work/payments", "feature/orders") == ""
        kept = json.loads((tmp_path / "state" / "azure.json").read_text(encoding="utf-8"))
        assert list(kept["pull_requests"]) == ["/work/payments"], "a repository with no address left is not listed"

    def test_a_posted_thread_is_kept_per_pull_request_and_finding(self, state: ModuleType, azure: ModuleType) -> None:
        one, other = _pr(azure), replace(_pr(azure), number=124)
        assert state.posted_threads(one).get("ab" * 8) is None
        state.keep_posted(one, "ab" * 8, 148)
        state.keep_posted(other, "ab" * 8, 7)
        state.keep_posted(one, "cd" * 8, 149)
        found = [
            state.posted_threads(one).get("ab" * 8),
            state.posted_threads(other).get("ab" * 8),
            state.posted_threads(one).get("cd" * 8),
        ]
        assert found == [148, 7, 149]

    @pytest.mark.parametrize(
        "damaged", [b"{", b"[]", b'{"pull_requests": 7, "posted": "x"}', b"\xff\xfe\x00", b'{"posted": {"k": [1]}}']
    )
    def test_a_damaged_file_reads_as_empty_and_the_next_write_repairs_it(
        self, state: ModuleType, azure: ModuleType, tmp_path: Path, damaged: bytes
    ) -> None:
        """The marks kept here are a convenience: the thread's own text is what stops a second post."""
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "azure.json").write_bytes(damaged)
        assert (
            state.pull_request_url("/work/payments", "main") == ""
            and state.posted_threads(_pr(azure)).get("ab" * 8) is None
        )
        state.keep_posted(_pr(azure), "ab" * 8, 148)
        assert state.posted_threads(_pr(azure)).get("ab" * 8) == 148
        assert set(json.loads((tmp_path / "state" / "azure.json").read_text(encoding="utf-8"))) == {
            "pull_requests",
            "posted",
        }

    def test_an_address_is_found_again_however_the_repositorys_path_is_spelled(
        self, state: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read by review: every other thing the front end keeps for a repository is kept under its path as the
        file system reads it. On Windows, a root spelled in another case lost the address."""
        monkeypatch.setattr(state.os.path, "normcase", lambda path: path.replace("/", "\\").lower())  # as Windows'
        address = "https://dev.azure.com/c/a/_git/b/pullrequest/1"
        state.keep_pull_request_url("C:/Work/Payments", "feature/orders", address)
        assert state.pull_request_url("c:\\work\\payments", "feature/orders") == address
        state.keep_pull_request_url("C:\\WORK\\PAYMENTS", "feature/orders", "")
        assert state.pull_request_url("C:/Work/Payments", "feature/orders") == ""

    def test_a_posted_thread_is_forgotten_alone(self, state: ModuleType, azure: ModuleType) -> None:
        one = _pr(azure)
        state.keep_pull_request_url("/work/payments", "feature/orders", one.web)
        state.keep_posted(one, "ab" * 8, 148)
        state.keep_posted(one, "cd" * 8, 149)
        state.forget_posted(one, "ab" * 8)
        state.forget_posted(one, "ee" * 8)  # none kept: nothing to forget
        assert state.posted_threads(one) == {"cd" * 8: 149}
        assert state.pull_request_url("/work/payments", "feature/orders") == one.web, "the address stays"

    def test_a_thread_that_is_no_whole_number_is_none(
        self, state: ModuleType, azure: ModuleType, tmp_path: Path
    ) -> None:
        held = {"posted": {_pr(azure).web: {"aa": "148", "bb": True, "cc": 12}}, "pull_requests": {"/r": {"b": 7}}}
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "azure.json").write_text(json.dumps(held), encoding="utf-8")
        assert [state.posted_threads(_pr(azure)).get(name) for name in ("aa", "bb", "cc")] == [None, None, 12]
        assert state.pull_request_url("/r", "b") == ""

    def test_only_what_was_given_is_written(self, state: ModuleType, azure: ModuleType, tmp_path: Path) -> None:
        """The screen gives the pull request's own address, as Azure DevOps shows it, never the text a person
        pasted: a paste can hold a credential."""
        state.keep_pull_request_url("/work/payments", "feature/orders", _pr(azure).web)
        written = (tmp_path / "state" / "azure.json").read_text(encoding="utf-8")
        assert json.loads(written) == {
            "pull_requests": {"/work/payments": {"feature/orders": _pr(azure).web}},
            "posted": {},
        }
        assert written.endswith("\n") and "\r" not in written


def _a_root_and_its_certificate(folder: Path) -> tuple[Path, Path, Path]:
    """A root of the test's own, and a certificate it signed for `127.0.0.1`, as a company's proxy signs a host's:
    the root's file, then the server's certificate and key."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.UTC)
    since, until = now - datetime.timedelta(days=1), now + datetime.timedelta(days=2)
    root_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "a test's own root")])
    signs = x509.KeyUsage(
        digital_signature=True,
        key_cert_sign=True,
        crl_sign=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        encipher_only=False,
        decipher_only=False,
    )
    root = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(since)
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(signs, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    served = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(since)
        .not_valid_after(until)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    files = folder / "root.pem", folder / "served.pem", folder / "served.key"
    files[0].write_bytes(root.public_bytes(pem))
    files[1].write_bytes(served.public_bytes(pem))
    plain = serialization.NoEncryption()
    files[2].write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, plain))
    return files


#: An HTTPS server in a process of its own, on a port it prints: its certificate and key are its two arguments. In the
#: suite's own process the reviewer's engine, once a test loads it, puts truststore's context in place of Python's, a
#: client's, and a server built from that one resets each connection. Seen in a full run, never in this file alone.
_HTTPS_SERVER = """
import http.server, ssl, sys

class Answers(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        sent = b'{"status": "active"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(sent)))
        self.end_headers()
        self.wfile.write(sent)

    def log_message(self, *args):
        pass

class Quiet(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a client that trusts no such root ends the handshake, as the test asks

server = Quiet(("127.0.0.1", 0), Answers)
secure = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
secure.load_cert_chain(sys.argv[1], sys.argv[2])
server.socket = secure.wrap_socket(server.socket, server_side=True)
print(server.server_address[1], flush=True)
server.serve_forever()
"""


class TestACompanysOwnRoot:
    """HTTPS through a proxy that signs each host with the company's own root: trusted where the person says so."""

    def test_a_bundle_ssl_cert_file_names_is_trusted_and_without_it_the_sentence_names_it(
        self, client: ModuleType, azure: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduced by review on macOS: the client's own context read `SSL_CERT_FILE` on Linux alone, while its
        sentence for a certificate nobody trusts told the person to set it."""
        pytest.importorskip("cryptography")
        from conftest import _no_proxy

        root, served, key = _a_root_and_its_certificate(tmp_path)
        server = subprocess.Popen(
            [sys.executable, "-I", "-c", _HTTPS_SERVER, str(served), str(key)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
        )
        try:
            assert server.stdout is not None
            port = int(server.stdout.readline())
            _no_proxy(monkeypatch)
            pr = azure.PullRequest(f"https://127.0.0.1:{port}/contoso", "Web", "payments", 123)
            monkeypatch.delenv("SSL_CERT_FILE", raising=False)
            monkeypatch.delenv("SSL_CERT_DIR", raising=False)
            with pytest.raises(client.AzureError, match=re.escape("certificate was not trusted. Set SSL_CERT_FILE")):
                client.Client(pr, FakeToken()).status()
            monkeypatch.setenv("SSL_CERT_FILE", str(root))
            assert client.Client(pr, FakeToken()).status() == "active"
        finally:
            server.kill()
            server.wait(timeout=10)
            if server.stdout is not None:
                server.stdout.close()


class TestAReportReadOnce:
    """`load_report`: a report's findings and what it reviewed, from one read."""

    def test_the_findings_and_the_commit_come_from_one_read(
        self, findings: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduced by review: the screen read the report twice, off its own loop each time, and between the two
        its findings were the new review's and its commit the last one's."""
        good = {"repository": "/work/payments", "head": "feature/orders", "head_sha": "a" * 40}
        report = _report(tmp_path, [{"file": "a.py", "line": 3, "fingerprint": "ab" * 8}], good)
        reads = []
        read = Path.read_text
        monkeypatch.setattr(Path, "read_text", lambda path, **how: reads.append(path) or read(path, **how))
        rows, reviewed = findings.load_report(report)
        assert reads == [report]
        assert (rows[0].fingerprint, reviewed) == ("ab" * 8, "a" * 40)
        assert findings.load_report(tmp_path / "missing.json") == ([], None)
