"""Tests for the front end's Azure DevOps side, `tui/tui/azure*.py`: a pull request's address and a finding's links,
the comment drafted from a finding, the thread it is posted as, the login's token, the client and what is kept.

Every test is offline: a fake Azure DevOps answers on a local port, and a fake `az` gives the token. Each test imports
what it reads where it runs, as `tests/test_tui.py` says why."""

from __future__ import annotations

import http.server
import importlib
import json
import re
import socket
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

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
            "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/\u0661\u0662",  # digits, and not ASCII's
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
        head = _committed(tmp_path, "src/a b.py", "\n\nx = '\u00f6'\r\ny = 2\n".encode())
        assert file_lines(str(tmp_path), head, "src/a b.py") == ["", "", "x = '\u00f6'", "y = 2", ""]
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
        assert azure.thread_payload("hello", "src/app.py", (42, 57), "    return total", 3, 7) == {
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
        context = azure.thread_payload("t", "/a.py", (1, 1), last_line, 1, 1)["threadContext"]
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
        self.kwargs: dict = {}
        self.expires_on: object = 4600
        self.answer: dict = {}

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        self.argv, self.kwargs = argv, kwargs
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
        assert fake_az.kwargs == {
            "capture_output": True,
            "encoding": "utf-8",
            "errors": "replace",
            "stdin": subprocess.DEVNULL,
            "timeout": 30,
            "check": False,
        }

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
    def test_a_token_that_names_no_whole_second_to_expire_at_is_not_kept(
        self, client: ModuleType, fake_az: FakeAz, expires_on: object
    ) -> None:
        fake_az.expires_on = expires_on
        login = client.AzLogin(run=fake_az, clock=lambda: 1000.0)
        assert (login(), login()) == ("Bearer tok-1", "Bearer tok-2")

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
        for sentence in str(failed.value).split(". "):
            assert len(sentence.split()) <= 20 and ";" not in sentence, sentence

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


@dataclass(frozen=True)
class Seen:
    """One request the fake Azure DevOps got: its header names in lower case, as HTTP reads them."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes


class FakeAdo:
    """A local Azure DevOps holding one pull request, `pr`: `answer` says what a route answers, `seen` what came."""

    def __init__(self, azure: ModuleType, port: int) -> None:
        self.pr = azure.PullRequest(f"http://127.0.0.1:{port}/contoso", "Web", "payments", 123)
        self.root = "/contoso/Web/_apis/git/repositories/payments/pullRequests/123"
        self.seen: list[Seen] = []
        self._answers: dict[tuple[str, str], list[tuple[int, dict[str, str], bytes]]] = {}

    def answer(
        self,
        method: str,
        tail: str,
        body: object = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> None:
        """What `method` on the pull request's `tail` answers: `""`, `"/iterations"`, `"/iterations/3/changes"`,
        `"/threads"`. Several answers for one route are served in turn, and the last one again after that."""
        sent = raw if raw is not None else b"" if body is None else json.dumps(body).encode("utf-8")
        self._answers.setdefault((method, self.root + tail), []).append((status, headers or {}, sent))

    def answered(self, seen: Seen) -> tuple[int, dict[str, str], bytes]:
        """The answer to one request, which is kept; a route no test named is a 404, as Azure DevOps answers one."""
        self.seen.append(seen)
        waiting = self._answers.get((seen.method, seen.path))
        if not waiting:
            return 404, {}, b'{"message": "the fake holds no such route"}'
        return waiting.pop(0) if len(waiting) > 1 else waiting[0]


@pytest.fixture
def fake_ado(azure: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAdo]:
    """A fake Azure DevOps on a local port, reached with no proxy: a machine's own proxy settings name none for it."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")

    class Handler(http.server.BaseHTTPRequestHandler):
        def _serve(self) -> None:
            address = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            headers = {name.lower(): value for name, value in self.headers.items()}
            query = {name: values[-1] for name, values in parse_qs(address.query).items()}
            status, extra, sent = fake.answered(Seen(self.command, address.path, query, headers, body))
            self.send_response(status)
            for name, value in {"Content-Type": "application/json", **extra}.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(sent)))
            self.end_headers()
            self.wfile.write(sent)

        do_GET = do_POST = _serve  # noqa: N815 -- http.server's own names

        def log_message(self, format: str, *args: object) -> None:  # http.server's own names
            """Nothing printed: the suite's output stays its own."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    fake = FakeAdo(azure, server.server_address[1])
    # Asked often whether to stop: by default each test would wait half a second for the server to see it should.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FakeToken:
    """A login as the client asks it: `forgot` counts how often Azure DevOps refused it."""

    def __init__(self, header: str = "Bearer tok") -> None:
        self.header, self.forgot = header, 0

    def __call__(self) -> str:
        return self.header

    def forget(self) -> None:
        self.forgot += 1


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
        assert sent.headers["content-type"] == "application/json; charset=utf-8"
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
        assert "tok-secret" not in str(failed.value) and "press c again" in str(failed.value)
        with pytest.raises(client.AzureError, match="did not answer"):
            client.Client(closed, FakeToken()).post({"comments": []})

    def test_a_login_that_az_cannot_give_ends_the_call_before_it_is_made(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        class NoLogin(FakeToken):
            def __call__(self) -> str:
                raise client.AzureError("The Azure CLI gave no token. Run `az login`, then post again.")

        with pytest.raises(client.AzureError, match="gave no token"):
            client.Client(fake_ado.pr, NoLogin()).status()
        assert fake_ado.seen == []


def _ready(fake: FakeAdo, latest: str, *, status: str = "active") -> None:
    """A pull request that takes a comment: active, pushed twice, `latest` its newest commit, changing `src/app.py`,
    with no thread yet; a post is thread 148."""
    older = {"id": 1, "sourceRefCommit": {"commitId": "a" * 40}}
    fake.answer("GET", "", {"status": status})
    fake.answer("GET", "/iterations", {"value": [older, {"id": 2, "sourceRefCommit": {"commitId": latest}}]})
    fake.answer(
        "GET", "/iterations/2/changes", {"changeEntries": [{"changeTrackingId": 7, "item": {"path": "/src/app.py"}}]}
    )
    fake.answer("GET", "/threads", {"value": []})
    fake.answer("POST", "/threads", {"id": 148})


class TestOnePost:
    """`post_comment`: the checks before a post, in order, each refusal a sentence and nothing posted."""

    WHERE: ClassVar[dict] = {"path": "src/app.py", "span": (42, 57), "last_line": "    return total"}

    def test_a_comment_is_posted_once_on_the_latest_push(
        self, azure: ModuleType, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        _ready(fake_ado, "b" * 40)
        asked = client.Client(fake_ado.pr, FakeToken())
        posted = client.post_comment(asked, text="hello", commit="b" * 40, fingerprint="ab" * 8, **self.WHERE)
        assert posted == client.Posted(148, already=False)
        posts = [seen for seen in fake_ado.seen if seen.method == "POST"]
        assert len(posts) == 1
        assert json.loads(posts[0].body) == azure.thread_payload(
            "hello", "src/app.py", (42, 57), "    return total", 2, 7
        )
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
        _ready(fake_ado, "b" * 40, status=change.get("status", "active"))
        where = {**self.WHERE, "path": change.get("path", "src/app.py")}
        with pytest.raises(client.AzureError, match=re.escape(said)) as refused:
            client.post_comment(
                client.Client(fake_ado.pr, FakeToken()),
                text="hello",
                commit=change.get("commit", "b" * 40),
                fingerprint="ab" * 8,
                **where,
            )
        assert all(seen.method == "GET" for seen in fake_ado.seen)
        for sentence in str(refused.value).split(". "):
            assert len(sentence.split()) <= 20 and ";" not in sentence, sentence

    def test_a_pull_request_with_no_push_listed_takes_no_comment(self, client: ModuleType, fake_ado: FakeAdo) -> None:
        fake_ado.answer("GET", "", {"status": "active"})
        fake_ado.answer("GET", "/iterations", {"value": []})
        with pytest.raises(client.AzureError, match="never held commit bbbbbbb"):
            client.post_comment(
                client.Client(fake_ado.pr, FakeToken()), text="t", commit="b" * 40, fingerprint="", **self.WHERE
            )

    def test_a_finding_already_posted_is_found_and_not_posted_again(
        self, client: ModuleType, fake_ado: FakeAdo, azure: ModuleType, row
    ) -> None:
        """Post pressed twice, or a post that timed out and still arrived: one thread, never two. The mark in the
        thread's own text says so, whatever the screen remembers."""
        fake_ado.answer("GET", "/threads", {"value": [{"id": 5, "comments": [{"content": azure.mark(row)}]}]})
        _ready(fake_ado, "b" * 40)
        posted = client.post_comment(
            client.Client(fake_ado.pr, FakeToken()),
            text="hello",
            commit="b" * 40,
            fingerprint=row.fingerprint,
            **self.WHERE,
        )
        assert posted == client.Posted(5, already=True)
        assert all(seen.method == "GET" for seen in fake_ado.seen)

    def test_a_finding_with_no_identity_is_posted_with_no_search_for_its_like(
        self, client: ModuleType, fake_ado: FakeAdo
    ) -> None:
        _ready(fake_ado, "b" * 40)
        asked = client.Client(fake_ado.pr, FakeToken())
        assert client.post_comment(asked, text="t", commit="b" * 40, fingerprint="", **self.WHERE).thread == 148
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
        assert state.posted_thread(one, "ab" * 8) is None
        state.keep_posted(one, "ab" * 8, 148)
        state.keep_posted(other, "ab" * 8, 7)
        state.keep_posted(one, "cd" * 8, 149)
        found = [
            state.posted_thread(one, "ab" * 8),
            state.posted_thread(other, "ab" * 8),
            state.posted_thread(one, "cd" * 8),
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
            state.pull_request_url("/work/payments", "main") == "" and state.posted_thread(_pr(azure), "ab" * 8) is None
        )
        state.keep_posted(_pr(azure), "ab" * 8, 148)
        assert state.posted_thread(_pr(azure), "ab" * 8) == 148
        assert set(json.loads((tmp_path / "state" / "azure.json").read_text(encoding="utf-8"))) == {
            "pull_requests",
            "posted",
        }

    def test_a_thread_that_is_no_whole_number_is_none(
        self, state: ModuleType, azure: ModuleType, tmp_path: Path
    ) -> None:
        held = {"posted": {_pr(azure).web: {"aa": "148", "bb": True, "cc": 12}}, "pull_requests": {"/r": {"b": 7}}}
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "azure.json").write_text(json.dumps(held), encoding="utf-8")
        assert [state.posted_thread(_pr(azure), name) for name in ("aa", "bb", "cc")] == [None, None, 12]
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
