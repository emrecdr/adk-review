"""The git tools, against a real repository: every answer is an envelope, and a wrong branch is a sentence."""

from __future__ import annotations

import importlib
import os
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
    SEPARATED,
    UNKNOWN_USER_REPO,
    UNRELATED,
    _branch_off_empty,
    _committed,
    _git_in,
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
    model_tools.inspect_repository(str(repo), BASE, head, context)
    listed = model_tools.changed_files(context)["files"]
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
        THEIR_SETTINGS[setting](repo, home, monkeypatch)
        _files, diff = _listed_and_shown(repo, "change", "pay.py")
        assert diff.startswith("diff --git a/pay.py b/pay.py") and "Binary files" not in diff, diff
        assert sum(line.startswith(" ") for line in diff.splitlines()) == 6, diff

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
        out = importlib.import_module("adk_review.judge.tools").inspect_repository(str(repo), BASE, "change", context)
        assert out["status"] == "success" and out["base_sha"] == ours, out


def test_a_tools_docstring_speaks_to_the_model_in_words() -> None:
    """ADK sends a tool's docstring to the model as it is written. Measured by review: `show_diff` told the model its
    diff was "capped at DIFF_CAP_CHARS", a name only the code knows; the cap is a number the model can use."""
    import re

    model_tools = importlib.import_module("adk_review.judge.tools")
    for tool in (model_tools.inspect_repository, model_tools.changed_files):
        assert not re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", tool.__doc__), (tool.__name__, tool.__doc__[:120])


class TestInspectRepository:
    def test_a_missing_branch_is_an_error_envelope_not_an_exception(self, demo_repo: Path) -> None:
        """The commonest typo. Measured by coverage: the reviewer answered it in no test."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = model_tools.inspect_repository(str(demo_repo), "main", "feature/does-not-exist", *context)
        assert out["status"] == "error" and "feature/does-not-exist" in out["error_message"], out

    def test_a_directory_that_is_not_a_repository_is_named(self, tmp_path: Path) -> None:
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = model_tools.inspect_repository(str(tmp_path), "main", "main", *context)
        assert out["status"] == "error" and "not a git repository" in out["error_message"], out

    def test_a_home_of_a_user_this_machine_does_not_know_is_an_error_envelope(self) -> None:
        """`~name` for a user that does not exist is left as typed, so the repository check answers; `Path.expanduser`
        raised here once, and a tool never raises at the model."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        context = (tool_context(),)
        out = model_tools.inspect_repository(UNKNOWN_USER_REPO, "main", "main", *context)
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
        assert model_tools.inspect_repository(str(demo_repo), BASE, HEAD, context)["status"] == "success"
        out = model_tools.changed_files(context)
        assert [f["path"] for f in out["files"]] == ["src/payments/charge.py"] and out["count"] == 4, out
        assert out["cut"].startswith("3 more file(s) not listed"), out

    def test_a_failed_call_clears_the_scope_never_the_conversations_repository(self, demo_repo: Path) -> None:
        """The guardrail holds a conversation to the repository it first confirmed; a call that fails after it must
        not free the model to name another."""
        model_tools, agents = (importlib.import_module(f"adk_review.judge.{name}") for name in ("tools", "agents"))
        context = tool_context()
        assert model_tools.inspect_repository(str(demo_repo), BASE, HEAD, context)["status"] == "success"
        assert model_tools.inspect_repository(str(demo_repo), BASE, "no-such-branch", context)["status"] == "error"
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
        assert model_tools.inspect_repository(*scope)["status"] == "success"
        monkeypatch.setattr(git, "GIT_TIMEOUT_S", NO_TIME)
        # `changed_files` before the preflight, which clears the scope it fails.
        answers = [model_tools.changed_files(context), model_tools.inspect_repository(*scope)]
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
        answer = model_tools.inspect_repository(*scope)
        assert answer["status"] == "error" and "git is not on PATH" in answer["error_message"], answer

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
        out = model_tools.inspect_repository(str(unrelated_repo), BASE, UNRELATED, *scoped)
        assert out["status"] == "error" and "share no history" in out["error_message"], out

    @pytest.mark.parametrize("clone", ["shallow_clone", "misled_clone"])
    def test_every_tool_refuses_a_shallow_clone(self, clone: str, request) -> None:
        """Before any merge base is asked for: at depth 1 `merge-base` answers 1, as for no shared history, and in
        the misled clone it answers with the wrong commit."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = model_tools.inspect_repository(str(request.getfixturevalue(clone)), BASE, HEAD, *scoped)
        assert out["status"] == "error" and "shallow clone" in out["error_message"], out
        assert "share no history" not in out["error_message"], out

    def test_every_command_names_a_branch_sharing_no_history(self, unrelated_repo: Path, cli, capsys) -> None:
        assert cli.main([str(unrelated_repo), "--base", BASE, "--head", UNRELATED]) == 2
        err = capsys.readouterr().err
        assert "share no history" in err and "Traceback" not in err, err

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
        out = model_tools.inspect_repository(str(hostile_repo), BASE, spelling, *scoped)
        assert out["status"] == "error" and f"{spelling!r} is ambiguous" in out["error_message"], out

    def test_every_command_refuses_an_ambiguous_base(self, hostile_repo: Path, cli, capsys) -> None:
        assert cli.main([str(hostile_repo), "--base", "twin", "--head", "carriage"]) == 2
        err = capsys.readouterr().err
        assert "'twin' is ambiguous" in err and "Traceback" not in err, err

    def test_an_annotated_tag_names_the_commit_it_tags(self, hostile_repo: Path) -> None:
        """Measured before: the head's sha was the tag object's, a sha no reader of the report could check out."""
        model_tools = importlib.import_module("adk_review.judge.tools")
        scoped = (tool_context(),)
        out = model_tools.inspect_repository(str(hostile_repo), BASE, "v1", *scoped)
        assert out["status"] == "success" and out["head_sha"] == _git_in(hostile_repo, "rev-parse", "carriage"), out

    def test_a_name_that_is_only_a_tag_is_named_as_one(self, hostile_repo: Path, cli, capsys) -> None:
        """Git resolves a name that is only a tag to it, as it must for `--base v1.0`, so a fork's tag `origin/main`,
        with no such branch fetched, stood in for the branch unseen: the report now says which tag it read."""
        cli.main([str(hostile_repo), "--base", "release/base", "--head", "carriage"])
        header = [line for line in capsys.readouterr().out.splitlines() if line.startswith("- against")]
        assert header and "release/base (the tag refs/tags/release/base) @ " in header[0], header


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
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", branch]) == 3
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
            target = _git_in(repo, "hash-object", "-w", "--stdin", stdin="README.md")
            _git_in(repo, "update-index", "--add", "--cacheinfo", f"120000,{target},vault")
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
        assert cli.main([str(hostile_repo), "--base", BASE, "--head", "submodule"]) == 3
        out = capsys.readouterr().out
        assert "- not read     `vendor/lib`: a submodule" in out, out  # measured: once "git showed none of it"

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
        assert [line for line in out.splitlines() if line.startswith("## Verdict")] == ["## Verdict: REQUEST_CHANGES"]
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
        files, block, excluded = change.shown_to_guidelines(entries, diffs, diff.DIFF_CAP_CHARS, ["*.png"])
        shown, past, skipped = files
        assert shown["quotable"] and not shown["cut"] and shown["unread"] == "", shown
        assert past["quotable"] == [] and past["cut"] and past["unread"].startswith("past the"), past
        assert skipped["unread"] == "" and not skipped["cut"] and excluded == {"logo.png": "*.png"}, skipped
        assert block.startswith(first) and "b.py" not in block.split("\n\n[…")[0], block
        assert "[… 1 more file(s) not shown: past" in block and "the operator's skip list leaves them out" in block
        assert [sorted(f["added_at"]) for f in files] == [[1], [1], [1]], "every file's added lines, shown or not"

    def test_with_no_guideline_no_file_is_capped_or_shown_and_what_no_one_reads_is_still_named(self) -> None:
        """Measured: the view cost 2.3 s of a 10.5 s `default-rules` review of 2,683 files, the pre-push hook's
        profile, and nothing read it. The rules read every added line, so only what no one can read is named."""
        from adk_review.collect import change

        binary = "diff --git a/logo.png b/logo.png\nBinary files /dev/null and b/logo.png differ\n"
        entries = [{"path": p, "status": "A"} for p in ("a.py", "logo.png", "vendor.png")]
        diffs = [self._diff("a.py", "x" * 50), binary, binary]
        files, block, excluded = change.shown_to_guidelines(entries, diffs, 10, ["vendor*"], shown=False)
        code, image, _vendor = files
        assert code["quotable"] == [] and not code["cut"] and code["unread"] == "", "past the cap, cut for no one"
        assert image["unread"].startswith("binary"), "what no one can read is still named"
        assert excluded == {"vendor.png": "vendor*"} and "### " not in block, block
        assert [sorted(f["added_at"]) for f in files] == [[1], [], []], "every file's added lines, for the rules"
