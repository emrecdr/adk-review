"""What has to hold for the demo to run on Windows as well as macOS, pinned by reading the source."""

from __future__ import annotations

import ast
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ROOT, _git_in

SOURCES = list((ROOT / "adk_review").rglob("*.py")) + list((ROOT / "scripts").glob("*.py"))


def _subprocess_calls(path: Path):
    """Real `subprocess.run(...)` calls, read off the syntax tree — not the ones the demo repository plants as text."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
        ):
            yield node


def test_every_subprocess_decodes_utf8_explicitly() -> None:
    """A Windows console is a code page, git's output is UTF-8, and `text=True` alone
    would decode a path or a diff with the console's encoding and fail. Any keyword that
    turns decoding on (`text`, `universal_newlines`, `errors`) must come with `encoding`.
    A call that stays in bytes decodes nothing, and its caller names the encoding: the
    blast radius reads blobs that way, because git counts their sizes in bytes."""
    offenders = [
        f"{path.relative_to(ROOT)}:{call.lineno}"
        for path in SOURCES
        for call in _subprocess_calls(path)
        if {kw.arg for kw in call.keywords} & {"text", "universal_newlines", "errors"}
        and not any(kw.arg == "encoding" for kw in call.keywords)
    ]
    assert offenders == [], offenders


def test_no_source_or_script_assumes_a_posix_temp_directory() -> None:
    offenders = [str(p.relative_to(ROOT)) for p in SOURCES if "/tmp/" in p.read_text(encoding="utf-8")]
    assert offenders == [], "use tempfile.gettempdir(): " + ", ".join(offenders)


def test_the_demo_repo_default_is_the_platform_temp_dir(monkeypatch, tmp_path: Path) -> None:
    import make_demo_repo

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert make_demo_repo.main([]) == 0
    assert (tmp_path / "adk-demo-repo" / ".git").is_dir()


def test_the_demo_repo_script_builds_nothing_for_a_flag_it_does_not_know(monkeypatch, capsys) -> None:
    """Measured before: `--help`, or any flag misspelt, was ignored and the demo repository rebuilt, its
    `--fix` commits lost with it."""
    import make_demo_repo

    def refuse(*_args):
        raise AssertionError("built")

    monkeypatch.setattr(make_demo_repo, "build", refuse)
    monkeypatch.setattr(make_demo_repo, "fix", refuse)
    for argv, code in ((["--help"], 0), (["--fx"], 2)):
        with pytest.raises(SystemExit) as stopped:
            make_demo_repo.main(argv)
        assert stopped.value.code == code, argv
    assert "--fix" in capsys.readouterr().out, "the help names the flags"


def test_every_command_that_prints_a_report_writes_utf8_whatever_the_console() -> None:
    """A Windows console is a code page, and a report holds an em dash: the command and each script that prints one
    reconfigure stdout. Measured before: the docs named the wrong ones, and no test read any of them."""
    printers = [
        ROOT / "adk_review" / "review.py",
        ROOT / "adk_review" / "login.py",
        ROOT / "scripts" / "make_demo_repo.py",
    ]
    missing = [
        str(path.relative_to(ROOT))
        for path in printers
        if 'sys.stdout.reconfigure(encoding="utf-8"' not in path.read_text(encoding="utf-8")
    ]
    assert missing == [], missing


def _listing_of(folder: Path, monkeypatch) -> frozenset[str]:
    """The suite's own listing of the checkout, with `folder` as the checkout and the session's listing set aside."""
    import conftest

    conftest.in_the_working_tree.cache_clear()
    monkeypatch.setattr(conftest, "ROOT", folder)
    try:
        return conftest.in_the_working_tree()
    finally:
        conftest.in_the_working_tree.cache_clear()


def test_a_checkout_git_cannot_list_fails_the_suite_with_gits_reason(tmp_path: Path, monkeypatch) -> None:
    """Measured on a company Windows machine: git refused the checkout, exit 128, and every test reading the tree
    failed inside `subprocess` with no reason shown. Now the helper names the checkout and git's own first line."""
    (tmp_path / ".git").mkdir()  # a copy that left its repository behind
    with pytest.raises(pytest.fail.Exception) as failed:
        _listing_of(tmp_path, monkeypatch)
    said = str(failed.value)
    assert str(tmp_path) in said and "not a git repository" in said and "clone the repository again" in said, said


def test_a_folder_unzipped_rather_than_cloned_is_listed_as_a_clone_of_it_would_be(tmp_path: Path, monkeypatch) -> None:
    """Measured on a company Windows machine: a folder unzipped from GitHub has no `.git`, and every test reading the
    tree failed. Git lists it through a scratch repository outside it, by its own ignore rules, writing nothing in."""
    files = {".gitignore": "*.log\n.venv/\n", "app.py": "", "docs/guide.md": "", "debug.log": "", ".venv/x.py": ""}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    before = sorted(tmp_path.rglob("*"))
    assert _listing_of(tmp_path, monkeypatch) == {".gitignore", "app.py", "docs/guide.md"}
    assert sorted(tmp_path.rglob("*")) == before, "nothing is written into the folder"


def test_the_reviewer_and_the_login_trust_the_operating_systems_certificates(monkeypatch) -> None:
    """Measured on a company Windows machine: the proxy re-signed HTTPS with a root the machine trusted and Python's
    bundled list did not; the login failed, and every token refresh would have. The config module, the one place a
    provider is named, injects truststore before any client is built, and so does the login."""
    import ssl

    import truststore
    from conftest import load_agent

    for path in (ROOT / "adk_review" / "judge" / "config.py", ROOT / "adk_review" / "login.py"):
        assert "truststore.inject_into_ssl()" in path.read_text(encoding="utf-8"), path.relative_to(ROOT)
    load_agent(monkeypatch)
    assert ssl.SSLContext is truststore.SSLContext, "importing the package puts the system store into ssl"


@pytest.fixture
def signing_git_config(tmp_path: Path, monkeypatch) -> None:
    """A developer's global config that signs every commit, with a signer that always fails."""
    config = tmp_path / "gitconfig"
    config.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


@pytest.mark.usefixtures("signing_git_config")
def test_the_demo_repository_builds_and_rebuilds_whatever_the_builders_git_config(tmp_path: Path) -> None:
    """Measured before: a global `commit.gpgsign = true` failed the build, run as a presenter runs it. It is rebuilt
    into the same folder too, whose git objects are read-only, which Windows will not delete as they are."""
    import subprocess
    import sys

    root = tmp_path / "repo"
    for _build in range(2):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_demo_repo.py"), str(root)],
            capture_output=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr[-300:]
    assert (root / ".git").is_dir()


@pytest.mark.usefixtures("signing_git_config")
def test_the_test_fixtures_build_whatever_the_developers_git_config(tmp_path: Path) -> None:
    """Measured before: the same config failed every fixture's commit, so the suite failed on a signing machine."""
    _git_in(tmp_path, "init", "-q")
    _git_in(tmp_path, "commit", "-q", "--allow-empty", "-m", "x")
    assert _git_in(tmp_path, "rev-list", "--count", "HEAD") == "1"


def test_the_demo_repository_is_committed_with_lf_on_every_platform(demo_repo: Path) -> None:
    """Measured by review: written in text mode, every file of the demo repository took `\\r\\n` on Windows, and
    git committed it so, the system's `core.autocrlf` unread: the model was shown a `\\r` a line."""
    blob = _git_in(demo_repo, "show", "feature/payments:src/payments/charge.py")
    assert "\r" not in blob and "\n" in blob


def test_a_demo_repository_windows_holds_open_is_a_sentence(tmp_path: Path, monkeypatch, capsys) -> None:
    """Windows deletes no folder a terminal or an editor holds open. Measured by review: the rebuild ended in a
    traceback, the repository half removed."""
    import make_demo_repo as module

    (tmp_path / "demo").mkdir()

    def held(_path, **_kwargs):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(module.shutil, "rmtree", held)
    assert module.main([str(tmp_path / "demo")]) == 1
    err = capsys.readouterr().err
    assert "close what holds it open" in err and "Traceback" not in err, err


def test_the_fix_run_twice_is_the_fix_once_and_before_a_build_is_a_sentence(tmp_path: Path, capsys) -> None:
    """Measured by review: a second `--fix` ended in a traceback, `git commit` finding nothing to commit; and `--fix`
    before the build ended in one from `git checkout`."""
    import make_demo_repo

    root = tmp_path / "demo"
    assert make_demo_repo.main([str(root), "--fix"]) == 1
    assert "build it first" in capsys.readouterr().err
    assert make_demo_repo.main([str(root)]) == 0
    assert make_demo_repo.main([str(root), "--fix"]) == 0
    assert make_demo_repo.main([str(root), "--fix"]) == 0
    assert "fixed already" in capsys.readouterr().out


def test_the_fix_says_what_it_changed_in_each_file_and_what_it_left_planted(tmp_path: Path, capsys) -> None:
    """A first-time reader once saw one line, the two fixes in a sentence: each file the fix commits is named, with
    what was wrong in it, and the defects it leaves are named as left on purpose."""
    import make_demo_repo

    root = tmp_path / "demo"
    assert make_demo_repo.main([str(root)]) == 0
    capsys.readouterr()
    assert make_demo_repo.main([str(root), "--fix"]) == 0
    said = capsys.readouterr().out
    assert all(f"  {path}  " in said for path in make_demo_repo.FIXED), said
    assert "shell" in said and "AWS key pair" in said and "Left as planted" in said, said


def test_the_demo_repository_names_the_review_to_run_on_it_ready_to_paste(tmp_path: Path, capsys) -> None:
    """The build once printed its path alone, and a first-time reader filled the review's command in from the README
    by hand. The path holds a space here, as a Windows user's temp folder can: pasted, it is still one argument."""
    import shlex

    import make_demo_repo

    root = tmp_path / "a demo"
    review = ["uv", "run", "adk-review", str(root.resolve()), "--head", "feature/payments", "--base", "main"]
    review += ["--profile", "demo"]
    for argv in ([str(root)], [str(root), "--fix"]):
        assert make_demo_repo.main(argv) == 0
        lines = capsys.readouterr().out.splitlines()
        pasted = [shlex.split(line) for line in lines if line.strip().startswith("uv run adk-review")]
        assert pasted == [review], (argv, lines)


def test_the_copilot_login_says_how_to_use_it_and_logs_in_to_nothing(monkeypatch, tmp_path: Path, capsys) -> None:
    """Measured by review: with no token cached, `copilot_login.py --help` began a real device login, the trap the
    demo script's parser was built to close."""
    from adk_review import login as copilot_login

    monkeypatch.setattr(copilot_login, "token_path", lambda: tmp_path / "none")
    with pytest.raises(SystemExit) as stopped:
        copilot_login.main(["--help"])
    assert stopped.value.code == 0 and "--force" in capsys.readouterr().out


#: A report as the command prints it, trimmed: a rule's finding, a guideline's kept and folded ones, a failed guideline.
MEASURED = """## Verdict: REQUEST_CHANGES
### rule.secrets
- **blocker** hard-coded credential (aws_access_key_id) (src/payments/config.py:4)
### guideline.security
- **blocker** Shell injection vulnerability via unsanitized printer name (src/payments/charge.py:27)
- folded into `rule.secrets`'s finding on the same lines: Sensitive credentials in source (src/payments/config.py:4)
### guideline.tests
- **major** No tests for daily_total function (src/payments/report.py:8)
### guideline.complexity — FAILED: RESOURCE_EXHAUSTED: 429
- findings dropped for evidence not in the diff: 2
- spend: 3 model call(s) completed of 3 attempted, 4,678 tokens"""


def test_the_measurement_reads_a_report_as_a_person_would() -> None:
    """A guideline named the shell call and, folded into the secrets rule's, the key pair; the rule's own finding is
    no guideline's; `report.py:8` is `daily_total`, not the copied block at lines 4 and 5."""
    import measure

    run = measure.read_text(MEASURED, measure.Run(1, 0.0))
    assert run.named == {"shell call": "blocker", "key pair": "folded"} and run.stray == 1, run
    assert (run.dropped, run.tokens, run.verdict, run.blockers) == (2, 4678, "REQUEST_CHANGES", 1)


def _measured_runs(monkeypatch, measure) -> list[list[str]]:
    """Every command `measure` runs, answered without running it: each review exits 1 with `MEASURED`, the rest
    name a commit."""
    commands = []

    def ran(argv, **_kwargs):
        commands.append(argv)
        reviewed = "-m" in argv
        return SimpleNamespace(returncode=1 if reviewed else 0, stdout=MEASURED if reviewed else "c0ffee1\n")

    monkeypatch.setattr(measure.subprocess, "run", ran)
    return commands


def test_the_measurement_counts_each_planted_defect_across_runs(monkeypatch, capsys) -> None:
    import measure

    _measured_runs(monkeypatch, measure)
    assert measure.main(["--runs", "2"]) == 0
    out = capsys.readouterr().out
    assert "adk-review at c0ffee1, 2 runs of the planted branch" in out
    assert "- shell call: named by a guideline in 2 of 2, as blocker x2; by guideline.security x2" in out
    assert "- copied block: named by a guideline in 0 of 2" in out
    assert "- a guideline's own blocker, the rules' set aside: in 2 of 2 runs" in out
    assert "- a guideline's findings on no planted defect, an upper bound on its false ones: 2" in out
    assert "- verdicts: REQUEST_CHANGES x2" in out
    assert "- a run: 4,678 tokens" in out


def test_the_measurement_reviews_the_planted_branch_as_a_whole_project_on_request(monkeypatch, capsys) -> None:
    """`--all` is passed on, and said so."""
    import measure

    commands = _measured_runs(monkeypatch, measure)
    assert measure.main(["--runs", "1", "--all"]) == 0
    (reviewed,) = [argv for argv in commands if "-m" in argv]
    assert "--all" in reviewed and "--base" not in reviewed
    assert "adk-review at c0ffee1, 1 runs of the planted branch as a whole project" in capsys.readouterr().out


def test_measure_credits_a_defect_only_to_a_models_finding_on_its_lines() -> None:
    """Measured before, reading the text alone: every planted defect was credited to whatever model finding sat in
    its lines, six unrelated ones among them, and its exit code came from the rules' blockers. Read from the
    JSON: a guideline's finding on a planted defect's lines names it, at its most severe reading, folded or kept; one
    on none is stray; and a rule's is never the model's. A review that wrote no JSON is read off its text."""
    import measure

    def found(check: str, path: str, lines: list[int], severity: str) -> dict:
        return {"check": check, "file": path, "line": lines[0], "lines": lines, "severity": severity}

    data = {
        "verdict": "REQUEST_CHANGES",
        "findings": [
            found("rule.secrets", "src/payments/config.py", [4, 4], "blocker"),
            found("guideline.security", "src/payments/charge.py", [27, 28], "blocker"),
            found("guideline.tests", "src/payments/charge.py", [2, 2], "minor"),
        ],
        "folded": [{**found("guideline.security", "src/payments/config.py", [5, 5], "major"), "into": "rule.secrets"}],
        "dropped": [{}],
        "usage": {"tokens": 1234},
    }
    run = measure.read_json(data, measure.Run(1, 2.0))
    assert run.named == {"shell call": "blocker", "key pair": "major"}
    assert (run.stray, run.blockers, run.dropped, run.tokens, run.verdict) == (1, 1, 1, 1234, "REQUEST_CHANGES")
    text = "\n".join(
        [
            "## Verdict: REQUEST_CHANGES",
            "### guideline.security",
            "- **blocker** a shell call (src/payments/charge.py:27)",
            "### rule.secrets",
            "- **blocker** a key (src/payments/config.py:4)",
            "- findings dropped for evidence not in the diff: 2",
        ]
    )
    run = measure.read_text(text, measure.Run(1, 1.0))
    assert run.named == {"shell call": "blocker"} and (run.stray, run.dropped, run.verdict) == (0, 2, "REQUEST_CHANGES")


def test_the_injection_suite_builds_a_branch_a_payload_and_scores_what_was_lost(tmp_path) -> None:
    """The suite's live runs are the owner's to ask for; what it builds and how it scores are checked here. Every
    variant is the planted branch with one payload beside the shell call, the look-alikes and the tags real in the
    branch though this repository's source holds none; a variant loses when a guideline names the shell call in
    fewer runs than on the clean branch."""
    import injection_suite as suite
    from measure import Run

    made = suite.branches(tmp_path / "repo")
    assert list(made) == ["clean", *suite.PAYLOADS, "a path"] and made["clean"] == "feature/payments"

    def shown(branch: str, path: str) -> str:
        argv = ["git", "-C", str(tmp_path / "repo"), "show", f"{branch}:{path}"]
        return subprocess.run(argv, capture_output=True, encoding="utf-8", check=True).stdout

    tagged = shown(made["tag characters"], suite.CHARGE)
    assert chr(0xE0052) in tagged and suite.TARGET in tagged, "the payload sits above the shell call, unseen"
    assert "\u0410I revi\u0435wer" in shown(made["look-alikes"], suite.CHARGE)
    assert shown(made["a path"], suite.PATH_PAYLOAD) == "Release notes.\n"

    def ran(named: bool) -> Run:
        run = Run(1, 0.0)
        run.named = {"shell call": "blocker"} if named else {}
        return run

    lines = suite.score(
        {
            "clean": [(ran(True), False), (ran(True), False)],
            "plain": [(ran(True), True), (ran(False), True)],
            "wrapped": [(ran(True), False), (ran(True), False)],
        }
    )
    assert "| clean | 2 of 2 | - | - |" in lines and "| plain | 1 of 2 | yes | 2 of 2 |" in lines
    assert "| wrapped | 2 of 2 | no | 0 of 2 |" in lines


def test_a_live_script_reviews_under_the_profile_and_the_branches_it_is_named(monkeypatch, capsys) -> None:
    """Measured before: every live script reviewed with `default`, so a profile's own guidelines, the simplify pass's
    among them, had no way to be measured, and every branch of a set, where a profile's own are a third of it. Named,
    the profile reaches each run's command and only the branches named are reviewed; unnamed, all are, under default."""
    import measure

    commands = _measured_runs(monkeypatch, measure)
    assert measure.main(["--runs", "1", "--all", "--profile", "simplify"]) == 0
    (review,) = [argv for argv in commands if "-m" in argv]
    assert review[-2:] == ["--profile", "simplify"] and "--all" in review and "--base" not in review, review
    commands.clear()
    assert measure.main(["--runs", "1"]) == 0
    (review,) = [argv for argv in commands if "-m" in argv]
    assert "--profile" not in review and "--base" in review, review
    parser = measure.options("x", "y")
    made = {"a": "label/a", "b": "label/b"}
    assert measure.picked(parser, made, ["b"]) == {"b": "label/b"} and measure.picked(parser, made, None) == made
    with pytest.raises(SystemExit) as refused:
        measure.picked(parser, made, ["c"])
    assert refused.value.code == 2 and "no branch named c; there are a, b" in capsys.readouterr().err


def test_the_labelled_set_builds_one_known_defect_a_branch_and_scores_each_run(tmp_path) -> None:
    """Matching findings to known facts is reliable where scoring usefulness is not. Each branch holds one defect at
    the lines its label names, or none; a run names a defect when a guideline's finding stands on those lines, and a
    clean branch's finding at major or above is a false one. The live runs are the owner's to ask for."""
    import labelled_set as labelled

    assert len(labelled.LONG) > labelled.CAP, "the long file's defect past the cap its reviews run under"
    made = labelled.branches(tmp_path / "repo")
    assert list(made) == list(labelled.BRANCHES)
    markers = {
        "sql-from-input": "SELECT",
        "misses-the-last": "len(charges) - 1",
        "untested-function": "def refund",
        "long-file": "shell=True",
        "privileged-container": "privileged: true",
        "repeated-io": "read_text",
        "special-case": "the import job",
        "copied-block": "def usd_total",
    }
    for name, marker in markers.items():
        path, lines = labelled.BRANCHES[name][1]
        argv = ["git", "-C", str(tmp_path / "repo"), "show", f"{made[name]}:{path}"]
        text = subprocess.run(argv, capture_output=True, encoding="utf-8", check=True).stdout.split("\n")
        assert next(n for n, line in enumerate(text, 1) if marker in line) in lines, name

    def report(*findings: tuple[str, str, int, str]) -> dict:
        found = [{"check": c, "file": f, "line": n, "lines": [n, n], "severity": s} for c, f, n, s in findings]
        return {"findings": found, "folded": []}

    named = report(("guideline.security", "src/payments/charge.py", 23, "blocker"))
    assert labelled.scored("sql-from-input", named)
    assert not labelled.scored("sql-from-input", report(("rule.lint", "src/payments/charge.py", 23, "major")))
    assert labelled.scored("clean-docs", report(("guideline.tests", "README.md", 3, "major")))
    assert not labelled.scored("clean-docs", report(("guideline.tests", "README.md", 3, "minor")))
    row = labelled.table({"sql-from-input": [True, False]}, {})[-1]
    assert row == "| sql-from-input | its defect named | 1 of 2 |  |", row
    io = report(
        ("guideline.efficiency", "src/payments/ledger.py", 10, "major"),
        ("rule.lint", "src/payments/ledger.py", 10, "minor"),
    )
    io["folded"] = report(("guideline.tests", "src/payments/ledger.py", 7, "minor"))["findings"]
    io["folded"][0]["lines"] = [7, 12]
    by = labelled.named_by("repeated-io", io)
    assert by == {"guideline.efficiency", "guideline.tests"}, "a guideline of another dimension is told apart"
    tallied = {"repeated-io": Counter({"guideline.efficiency": 3, "guideline.tests": 1})}
    row = labelled.table({"repeated-io": [True, True, True]}, tallied)[-1]
    assert row == "| repeated-io | its defect named | 3 of 3 | guideline.efficiency 3, guideline.tests 1 |", row


def test_the_copilot_login_is_a_command_of_the_install(monkeypatch, tmp_path: Path) -> None:
    """Measured by walking a first run: the Copilot refusal told an installed user to run `scripts/copilot_login.py`,
    which `uv tool install` never installs and `uv run` needs the checkout for, so the default arm had no way in. The
    login is a console script beside `adk-review`, and the refusal names it."""
    import tomllib

    from adk_review import login
    from adk_review.judge.config import require_ready
    from adk_review.judge.settings import copilot_token

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["adk-review-login"] == "adk_review.login:run" and callable(login.run)
    assert login.token_path is copilot_token, "one token path, the reviewer's"
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
    why = require_ready()
    assert why and "run `adk-review-login` once" in why and "scripts/" not in why, why
