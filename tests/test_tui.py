"""Tests for `adk_review/tui/`: configuration, multi-root discovery, zero-checkout branch sync, and CLI builder.

Each test imports what it reads where it runs, as the suite's other files do: `load_agent` loads the package afresh
for the tests before these, and a name bound when this file was imported is the old package's. Measured: after one
such test, three of these failed, the catalog reading no rule as a rule and a patch landing on another module."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _state_apart(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The saved reports and the cache in a folder of the test's own. Read by review: tests wrote and deleted saved
    reports in the package's real state folder, beside a person's own."""
    import importlib

    options = importlib.import_module("adk_review.tui.options")
    state = tmp_path_factory.mktemp("tui-state")
    monkeypatch.setattr(options, "BASELINES_DIR", state / "baselines")
    monkeypatch.setattr(options, "CACHE_DIR", state / "cache")


def _git(repo: Path, *args: str) -> str:
    from adk_review.collect.git import on_path

    done = subprocess.run(
        [on_path("git"), "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.strip()


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare `origin` remote and a cloned `local` repository with `main` and `origin/feat-remote`."""
    from adk_review.collect.git import on_path

    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "dev@example.com")
    _git(seed, "config", "user.name", "Dev")
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial main")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")

    local = tmp_path / "workspace" / "my-service"
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [on_path("git"), "clone", str(remote), str(local)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    _git(local, "config", "user.email", "dev@example.com")
    _git(local, "config", "user.name", "Dev")

    # Push a new commit to `main` and a new branch `feat-remote` from `seed` so `local` is behind on `main`
    (seed / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(seed, "commit", "-am", "advance main on remote")
    _git(seed, "push", "origin", "main")

    _git(seed, "switch", "-c", "feat-remote")
    (seed / "tests").mkdir()
    (seed / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "add remote feature")
    _git(seed, "push", "-u", "origin", "feat-remote")
    return remote, local


class TestTuiConfig:
    """`adk_review/tui/.tui.toml` loading, multi-root workspace parsing, and preset persistence."""

    def test_default_config_path_lies_inside_adk_review_tui(self) -> None:
        from adk_review.tui.config import DEFAULT_CONFIG_PATH

        assert DEFAULT_CONFIG_PATH.name == ".tui.toml"
        assert DEFAULT_CONFIG_PATH.parent.name == "tui"
        assert DEFAULT_CONFIG_PATH.parent.parent.name == "adk_review"

    def test_ensure_and_load_tui_config_supports_multiple_root_dirs_and_presets(self, tmp_path: Path) -> None:
        from adk_review.tui.config import RepoPreset, TuiConfig, ensure_tui_config, load_tui_config, save_repo_preset

        cfg_file = tmp_path / ".tui.toml"
        ensured = ensure_tui_config(cfg_file)
        assert ensured == cfg_file.resolve() and cfg_file.is_file()
        loaded = load_tui_config(cfg_file)
        assert isinstance(loaded, TuiConfig)
        assert len(loaded.root_dirs) >= 1

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        cfg_file.write_text(
            "[workspace]\n"
            "root_dirs = [\n"
            f'    "{root_a.as_posix()}",\n'
            f'    {{ label = "Work Repos", path = "{root_b.as_posix()}" }},\n'
            "]\n"
            "scan_depth = 3\n"
            "fetch_timeout_s = 20\n\n"
            "[defaults]\n"
            'profile = "full"\n'
            'fail_on = "major"\n'
            "comment = true\n"
            "handoff = true\n",
            encoding="utf-8",
        )
        multi = load_tui_config(cfg_file)
        assert [r.label for r in multi.root_dirs] == ["root_a", "Work Repos"]
        assert multi.scan_depth == 3 and multi.fetch_timeout_s == 20
        assert multi.defaults.profile == "full" and multi.defaults.handoff is True

        preset = RepoPreset(
            base="develop",
            profile="default-rules",
            fail_on="blocker",
            comment=False,
            handoff=True,
            verify=True,
            path=("src",),
            path_exclude=("tests", "docs/reports"),
            checks=("rule.secrets",),
            diff_cap=45000,
        )
        saved = save_repo_preset(multi, "payments-service", preset)
        reloaded = load_tui_config(cfg_file)
        assert reloaded.projects["payments-service"] == preset
        assert saved.projects["payments-service"] == preset

        # Non-numeric workspace ints fall back cleanly instead of raising
        cfg_file.write_text(
            '[workspace]\nroot_dirs = ["' + root_a.as_posix() + '"]\nscan_depth = "invalid"\nfetch_timeout_s = false\n',
            encoding="utf-8",
        )
        safe = load_tui_config(cfg_file)
        assert safe.scan_depth == 2 and safe.fetch_timeout_s == 15

    def test_a_config_the_screen_cannot_show_is_one_sentence_and_exit_2(self, tmp_path: Path, capsys) -> None:
        """Read by review, and reproduced: a `fail_on` no severity names raised `InvalidSelectValueError` with a
        traceback, at the start for `[defaults]` and at a repository's choice for its preset; and a file that is no
        TOML raised from inside the screen. Each is refused before the screen opens, with the value and what to give."""
        from adk_review.tui import main as tui_main

        cfg = tmp_path / ".tui.toml"
        for body, said in (
            ('[defaults]\nfail_on = "critical"\n', "[defaults] fail_on is 'critical'. Give blocker, major or minor"),
            ('[projects."my-service"]\nfail_on = "high"\n', "[projects.my-service] fail_on is 'high'. Give blocker"),
            ("[defaults\n", "could not read"),
        ):
            cfg.write_text(body, encoding="utf-8")
            assert tui_main(["--config", str(cfg)]) == 2, body
            err = capsys.readouterr().err
            assert err.startswith("tui: ") and said in err and "Traceback" not in err, err


class TestGitSyncEngine:
    """Multi-root project discovery and zero-checkout branch synchronization."""

    def test_discover_projects_and_zero_checkout_sync_preserve_dirty_working_tree(self, tmp_path: Path) -> None:
        from adk_review.collect.git import checked_out
        from adk_review.tui.git_sync import (
            changed_folders,
            discover_projects,
            fast_forward_local_branch,
            fetch_remote,
            inspect_branch_sync,
            is_dirty,
            list_branches,
            preferred_base_branch,
        )

        _remote, local = _init_repo_with_remote(tmp_path)
        projects = discover_projects(str(tmp_path / "workspace"), max_depth=2)
        assert len(projects) == 1
        assert projects[0].name == "my-service" and projects[0].branch == "main"

        # Create a local feature branch and leave uncommitted edits on disk
        _git(local, "switch", "-c", "feat-local")
        (local / "src" / "app.py").write_text("x = 999  # uncommitted work in progress\n", encoding="utf-8")
        assert is_dirty(str(local)) is True
        assert checked_out(str(local)) == "feat-local"

        # 1. Fetch remote refs
        res = fetch_remote(str(local), timeout_s=10)
        assert res.ok is True

        branches = list_branches(str(local))
        by_name = {b.name: b for b in branches}
        assert "main" in by_name and "origin/feat-remote" in by_name

        # 2. Local `main` is 1 commit behind `origin/main` while `feat-local` is checked out with dirty files
        main_status = inspect_branch_sync(str(local), by_name["main"])
        assert main_status.kind == "behind" and main_status.behind == 1

        ff_main = fast_forward_local_branch(str(local), "main", "origin/main")
        assert ff_main.ok is True and ff_main.resolved_ref == "main"
        assert checked_out(str(local)) == "feat-local", "never switched away from feat-local"
        assert "999" in (local / "src" / "app.py").read_text(encoding="utf-8"), "dirty working tree preserved"
        assert inspect_branch_sync(str(local), list_branches(str(local))[1]).kind == "up_to_date"

        # 3. A branch only the remote holds is reviewed by its remote name: no local branch is made for it
        remote_status = inspect_branch_sync(str(local), by_name["origin/feat-remote"])
        assert remote_status.kind == "remote_only" and remote_status.upstream_branch == "origin/feat-remote"
        assert "feat-remote" not in _git(local, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

        # 4. Changed folders between `main` and `feat-remote`, and in `--staged` mode
        folders, file_count = changed_folders(str(local), "main", "origin/feat-remote")
        folder_names = [f for f, _label in folders]
        assert file_count == 1 and "tests" in folder_names and "src" in folder_names
        assert preferred_base_branch(str(local), list_branches(str(local)), "origin/feat-remote") == "main"

        (local / "src" / "staged_new.py").write_text("y = 1\n", encoding="utf-8")
        _git(local, "add", "src/staged_new.py")
        staged_folders, staged_count = changed_folders(str(local), "main", "feat-local", mode="staged")
        assert staged_count == 1
        assert ("src", "src  (1 changed)") in staged_folders

    def test_a_checked_out_branch_behind_its_remote_is_left_as_it_is(self, tmp_path: Path) -> None:
        """The question a person answers says their files stay untouched. Read by review: on a clean checkout the
        answer ran `git merge --ff-only`, which rewrote the files of the working tree. The remote's branch is reviewed
        by its own name, and the local one is left where it was."""
        from adk_review.tui.git_sync import fast_forward_local_branch, fetch_remote

        _remote, local = _init_repo_with_remote(tmp_path)
        assert fetch_remote(str(local), timeout_s=10).ok is True
        before = _git(local, "rev-parse", "main")
        assert before != _git(local, "rev-parse", "origin/main"), "main is behind its remote"
        done = fast_forward_local_branch(str(local), "main", "origin/main")
        assert done.ok is True and done.resolved_ref == "origin/main", done
        assert _git(local, "rev-parse", "main") == before, "the checked-out branch did not move"
        assert (local / "src" / "app.py").read_text(encoding="utf-8") == "x = 1\n", "nor did its files"

    def test_reading_a_repository_starts_no_program_its_own_config_names(self, tmp_path: Path) -> None:
        """The scan reads every repository under a root, one a person was handed among them. Read by review, and
        reproduced: `git status` there started the program its `core.fsmonitor` names, the moment the screen opened.
        Each read runs under a review's own environment now, where that setting is fixed, and takes no lock."""
        from adk_review.tui.git_sync import changed_folders, discover_projects, is_dirty, list_branches

        _remote, local = _init_repo_with_remote(tmp_path)
        ran, script = tmp_path / "ran.txt", tmp_path / "named.py"
        script.write_text(f"open({str(ran)!r}, 'w').close()\n", encoding="utf-8")
        _git(local, "config", "core.fsmonitor", f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
        _git(local, "status")
        assert ran.exists(), "git starts it for a plain status, so this test can see it"
        ran.unlink()
        index = local / ".git" / "index"
        held = (index.read_bytes(), index.stat().st_mtime_ns)
        assert [project.name for project in discover_projects(str(tmp_path / "workspace"))] == ["my-service"]
        assert is_dirty(str(local)) is False
        assert list_branches(str(local))[0].name == "main"
        changed_folders(str(local), "origin/main", "main")
        changed_folders(str(local), None, "main", mode="staged")
        assert not ran.exists(), "no read started the program"
        assert (index.read_bytes(), index.stat().st_mtime_ns) == held, "and none rewrote the index"


class TestOptionsAndCliBuilder:
    """Profile discovery and CLI flag construction with mutual-exclusivity guards."""

    def test_profiles_and_build_review_argv_respect_mutual_exclusivity(self, tmp_path: Path) -> None:
        from adk_review.tui.options import (
            ReviewSelection,
            baseline_path,
            build_review_argv,
            load_profile_options,
            parse_csv_paths,
            promote_baseline,
        )

        profiles = load_profile_options()
        names = [p.name for p in profiles]
        assert "default" in names and "default-rules" in names and "full" in names
        assert parse_csv_paths(" src/api/ , tests , src/api ") == ["src/api", "tests"]

        sel = ReviewSelection(
            repo=str(tmp_path),
            mode="branch",
            head="feat/x",
            base="develop",
            profile="default-rules",
            fail_on="major",
            comment=True,
            handoff=True,
            verify=True,
            use_cache=True,
            use_previous=True,
            dry_run=False,
            paths=("src", "tests"),
            path_excludes=("tests", "docs/reports"),
            output_dir=str(tmp_path / "outputs"),
        )
        argv, outputs = build_review_argv(sel)
        assert argv[:5] == [str(tmp_path), "--head", "feat/x", "--base", "develop"]
        assert "--path" in argv and "src" in argv
        assert argv.count("tests") == 1, "excluded folder is stripped from --path automatically"
        assert "--profile" in argv and "default-rules" in argv
        assert "--verify" in argv and "--comment" in argv and "--handoff" in argv
        assert "comment" in outputs and "handoff" in outputs and "json" in outputs and "baseline" in outputs
        assert outputs["json"] != outputs["baseline"], "baseline is kept separate until the run succeeds"

        # Once baseline JSON file exists on disk, a subsequent run passes `--previous` while writing `--json` elsewhere
        base_file = baseline_path(str(tmp_path), "develop", "feat/x", "branch")
        base_file.parent.mkdir(parents=True, exist_ok=True)
        base_file.write_text('{"schema": 1, "old": true}', encoding="utf-8")
        try:
            argv_with_prev, next_outputs = build_review_argv(sel)
            assert "--previous" in argv_with_prev
            next_outputs["json"].write_text('{"schema": 1, "new": true}', encoding="utf-8")
            promote_baseline(next_outputs, 3)
            assert "old" in base_file.read_text(encoding="utf-8"), "degraded run must not overwrite prior baseline"
            promote_baseline(next_outputs, 2)
            assert "old" in base_file.read_text(encoding="utf-8"), "usage error must not overwrite prior baseline"
            promote_baseline(next_outputs, 1)
            assert "new" in base_file.read_text(encoding="utf-8"), "clean or findings run promotes baseline"
        finally:
            base_file.unlink(missing_ok=True)

        # `--dry-run` strips all review-only output flags so `_mode_alone` never rejects it
        dry_sel = ReviewSelection(
            repo=str(tmp_path),
            mode="staged",
            head="feat/x",
            base="develop",
            comment=True,
            handoff=True,
            dry_run=True,
        )
        dry_argv, dry_outputs = build_review_argv(dry_sel)
        assert dry_argv == [str(tmp_path), "--staged", "--fail-on", "blocker", "--no-verify", "--dry-run"]
        assert dry_outputs == {}

        # Branch mode rejects identical head and base branches
        same_sel = ReviewSelection(repo=str(tmp_path), mode="branch", head="main", base="main")
        with pytest.raises(ValueError, match="cannot be the same"):
            build_review_argv(same_sel)

    def test_checks_and_diff_cap_cli_builder(self, tmp_path: Path) -> None:
        from adk_review.tui.options import ReviewSelection, build_review_argv, parse_csv_checks, parse_diff_cap

        assert parse_csv_checks(" rule.secrets , guideline.correctness, rule.secrets ") == [
            "rule.secrets",
            "guideline.correctness",
        ]
        assert parse_diff_cap("50,000") == 50000
        assert parse_diff_cap("bad") == 0

        sel = ReviewSelection(
            repo=str(tmp_path),
            mode="branch",
            head="feat/x",
            base="main",
            profile="full",
            checks=("rule.secrets", "guideline.security"),
            diff_cap=50000,
            dry_run=True,
        )
        argv, _ = build_review_argv(sel)
        assert "--profile" not in argv
        assert "--check" in argv and "rule.secrets" in argv and "guideline.security" in argv
        assert "--diff-cap" in argv and "50000" in argv

    def test_windows_paths_quoting_and_missing_git_guard(self, tmp_path: Path, monkeypatch) -> None:
        from adk_review.tui import git_sync
        from adk_review.tui.git_sync import BranchInfo, discover_projects, fetch_remote, inspect_branch_sync
        from adk_review.tui.options import format_cli_command, parse_csv_paths

        assert parse_csv_paths(r"\src\api\, C:\Users\dev\src, sub\\nested\ ") == ["src/api", "sub/nested"]
        cmd = format_cli_command([r"C:\My Repos\app", "--head", "feat/x"], windows=True)
        assert cmd == 'adk-review "C:\\My Repos\\app" --head feat/x'

        from adk_review.collect import git as collector

        monkeypatch.setattr(git_sync, "on_path", lambda _name: "git")
        monkeypatch.setattr(collector, "on_path", lambda _name: "git")  # the reads' runner, which finds git itself
        with pytest.raises(OSError, match="not on PATH"):
            git_sync._run_git(str(tmp_path), ["status"])
        assert discover_projects(str(tmp_path)) == []
        assert fetch_remote(str(tmp_path)).ok is False
        remote_branch = BranchInfo(
            name="origin/feat", is_remote=True, remote_name="origin/feat", local_name="feat", upstream="origin/feat"
        )
        assert inspect_branch_sync(str(tmp_path), remote_branch).kind == "remote_only"

    def test_cli_init_config_and_missing_textual_guard(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from adk_review.tui import main as tui_main

        cfg = tmp_path / ".tui.toml"
        assert tui_main(["--init-config", "--config", str(cfg)]) == 0
        assert cfg.is_file()
        assert str(cfg.resolve()) in capsys.readouterr().out

        monkeypatch.setitem(sys.modules, "textual", None)
        monkeypatch.delitem(sys.modules, "adk_review.tui.app", raising=False)
        assert tui_main(["--config", str(cfg)]) == 2
        err = capsys.readouterr().err
        assert "textual is not installed" in err and "run `uv sync --locked --extra tui`" in err, err
        assert "An installed copy needs the `tui` extra" in err, err

    @pytest.mark.asyncio
    async def test_textual_app_chain_of_selects_mounts_and_updates_cli_preview(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from adk_review.tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(
            "[workspace]\n"
            f'root_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n'
            f'output_dir = "{(tmp_path / "outputs").as_posix()}"\n\n'
            '[defaults]\nprofile = "default-rules"\ncomment = true\nhandoff = false\n',
            encoding="utf-8",
        )
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            from textual.widgets import Input, OptionList, Select, SelectionList, Switch

            await _wait_for_branches(app, pilot)
            assert app.selected_project is not None and app.selected_project.path == str(local.resolve())
            base_list = app.query_one("#base-list", OptionList)
            assert app.selected_head not in [base_list.get_option_at_index(i).id for i in range(base_list.option_count)]
            assert app.selected_base != app.selected_head

            # Toggle handoff on, then type in #repo-filter; the active project and toggle must not reset
            app.query_one("#sw-handoff", Switch).value = True
            app.query_one("#repo-filter", Input).value = "my-service"
            await pilot.pause(0.1)
            assert app.query_one("#sw-handoff", Switch).value is True

            # Switching mode to --staged disables branch pickers and refreshes path lists
            app.query_one("#mode-select", Select).value = "staged"
            await pilot.pause(0.1)
            assert app.query_one("#base-list", OptionList).disabled is True
            assert app.query_one("#head-list", OptionList).disabled is True

            # Selecting the same folder in #exclude-list deselects it from #path-list
            path_list = app.query_one("#path-list", SelectionList)
            excl_list = app.query_one("#exclude-list", SelectionList)
            if path_list.option_count:
                target_folder = path_list.get_option_at_index(0).value
                path_list.select(target_folder)
                await pilot.pause(0.05)
                excl_list.select(target_folder)
                await pilot.pause(0.05)
                assert target_folder in excl_list.selected and target_folder not in path_list.selected

            sel = app.current_selection(dry_run=False)
            assert sel.repo == str(local.resolve()) and sel.profile == "default-rules" and sel.comment is True

    @pytest.mark.asyncio
    async def test_textual_app_collapsible_sections_and_multi_root(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Collapsible

        from adk_review.tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            root_box = app.query_one("#root-box", Collapsible)
            assert root_box.has_class("hidden") and root_box.display is False

            titles = [str(box.title) for box in app.query(Collapsible)]
            assert all(
                any(k in t for t in titles)
                for k in ("Branch Under Review", "Target Branch", "Review Profile", "Output Options")
            )

            repo_box = app.query_one("#repo-box", Collapsible)
            head_box = app.query_one("#head-box", Collapsible)
            width_before = head_box.size.width
            repo_box.collapsed = True
            await pilot.pause()
            assert repo_box.title == "1.LR"
            assert repo_box.size.width <= 10
            assert head_box.size.width > width_before
            head_box.collapsed = True
            app.query_one("#base-box", Collapsible).collapsed = True
            await pilot.pause()
            assert app.query_one("#chain-row").size.height <= 3
            repo_box.collapsed = False
            await pilot.pause()
            assert "Local Repository" in repo_box.title

        second_root = tmp_path / "workspace_two"
        second_root.mkdir()
        cfg.write_text(
            f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}", "{second_root.as_posix()}"]\n',
            encoding="utf-8",
        )
        multi_app = AdkReviewTuiApp(config_path=cfg)
        async with multi_app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            multi_box = multi_app.query_one("#root-box", Collapsible)
            assert not multi_box.has_class("hidden") and multi_box.display is True

    @pytest.mark.asyncio
    async def test_the_screen_writes_nothing_into_a_repository_until_the_person_asks(self, tmp_path: Path) -> None:
        """The reviewer writes nothing into the repository under review, and names the `git fetch` for the person to
        run. Read by review, and reproduced: opening the screen ran `git fetch --prune` on the first repository of the
        root, which deleted a remote-tracking ref, and choosing a remote's branch made a local branch, each unasked.
        F5 is the person's own fetch; a remote's branch is reviewed by its remote name."""
        pytest.importorskip("textual")
        from textual.widgets import OptionList

        from adk_review.tui.app import AdkReviewTuiApp

        remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "update-ref", "refs/remotes/origin/gone", "HEAD")  # a branch the remote no longer holds
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")

        def refs() -> list[str]:
            return _git(local, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()

        held = refs()
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            assert refs() == held and not (local / ".git" / "FETCH_HEAD").exists(), "opening fetched nothing"
            assert "F5" in str(app.query_one("#status-banner").render()), "and the banner names the key that does"

            await pilot.press("f5")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert (local / ".git" / "FETCH_HEAD").exists(), "F5 is the person's own fetch"
            assert "origin/feat-remote" in [branch.name for branch in app.branches]

            fetched = refs()
            head = app.query_one("#head-list", OptionList)
            names = [str(head.get_option_at_index(at).id) for at in range(head.option_count)]
            head.highlighted = names.index("origin/feat-remote")
            head.action_select()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.selected_head == "origin/feat-remote", "reviewed by its remote name"
            assert refs() == fetched, "and no local branch was made for it"
        assert remote.is_dir()

    @pytest.mark.asyncio
    async def test_the_screen_draws_a_square_bracket_as_it_is_written(self, tmp_path: Path) -> None:
        """Textual reads `[name]` in a string as markup. Read by review, and reproduced: each section's title lost the
        choice it names, "Branch Under Review [main]" drawn with no branch; a repository `my[service]` was listed as
        `my`; and a finding titled "The path [/tmp] is shared" raised `MarkupError` and ended the screen."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, OptionList, Static, TabbedContent

        from adk_review.tui.app import AdkReviewTuiApp

        repo = tmp_path / "workspace" / "my[service]"
        (repo / "src").mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "dev@example.com")
        _git(repo, "config", "user.name", "Dev")
        (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "one")
        _git(repo, "branch", "feature")
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")

        def drawn(widget, line: int = 0) -> str:
            return "".join(segment.text for segment in widget.render_line(line)).strip()

        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(200, 50)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Branch Under Review [main]" in str(app.query_one("#head-box CollapsibleTitle").render())
            assert "Local Repository [my[service]]" in str(app.query_one("#repo-box CollapsibleTitle").render())
            assert drawn(app.query_one("#repo-list", OptionList)).startswith("my[service]  (main, clean)")
            assert "Repo: my[service]" in str(app.query_one("#selection-summary", Static).render())
            app._set_banner("guideline.security [done] in src/[id]/page.py [/bold]")
            assert "[done] in src/[id]/page.py [/bold]" in str(app.query_one("#status-banner", Static).render())
            app.notify("closing tag [/bold] alone")
            findings = [
                {
                    "check": "rule.lint",
                    "file": "src/[id]/a.py",
                    "line": 3,
                    "severity": "minor",
                    "title": "Use list[int]",
                },
                {
                    "check": "guideline.security",
                    "file": "a.py",
                    "line": 9,
                    "severity": "major",
                    "title": "[/tmp] is shared",
                },
            ]
            report = tmp_path / "report.json"
            report.write_text(json.dumps({"schema": 2, "findings": findings}), encoding="utf-8")
            await app._load_generated_files({"json": report})
            app.query_one("#output-tabs", TabbedContent).active = "tab-findings"  # a hidden table draws no line
            await pilot.pause()
            table = app.query_one("#findings-table", DataTable)
            rows = [drawn(table, line) for line in (1, 2)]
            assert "[/tmp] is shared" in rows[0] and "src/[id]/a.py:3" in rows[1] and "Use list[int]" in rows[1], rows


class TestFindingsExplorer:
    """Structured findings parsing, filtering, check explanation, and interactive Textual explorer."""

    def test_load_filter_format_and_explain(self, tmp_path: Path) -> None:
        import json

        from adk_review.tui.findings import (
            explain_check_markdown,
            filter_findings,
            format_finding_detail,
            load_findings_from_json,
        )

        report = tmp_path / "report.json"
        report.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "findings": [
                        {
                            "check": "rule.secrets",
                            "file": "a.py",
                            "line": 12,
                            "severity": "blocker",
                            "title": "Hardcoded key",
                            "evidence": "API_KEY = 'secret'",
                            "suggestion": "Move to env var.",
                            "since_previous": "new",
                        },
                        {
                            "check": "rule.lint",
                            "file": "b.py",
                            "line": 5,
                            "severity": "minor",
                            "title": "Unused import",
                            "evidence": "import os",
                            "suggestion": "Remove it.",
                            "since_previous": "seen",
                        },
                    ],
                    "old_findings": [
                        {
                            "check": "rule.types",
                            "file": "c.py",
                            "line": 3,
                            "severity": "major",
                            "title": "Old type mismatch",
                            "evidence": "x: int = 'no'",
                            "suggestion": "Fix annotation.",
                        }
                    ],
                    "resolved": [
                        {
                            "check": "rule.pitfalls",
                            "file": "d.py",
                            "line": 1,
                            "severity": "minor",
                            "title": "Fixed trap",
                            "evidence": "utcnow()",
                            "suggestion": "Use UTC.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        rows = load_findings_from_json(report)
        assert [r.tier for r in rows] == ["NEW", "Active", "Old", "Resolved"]
        assert len(filter_findings(rows, severity="blocker")) == 1
        assert len(filter_findings(rows, tier="Old")) == 1
        assert len(filter_findings(rows, query="Hardcoded")) == 1
        detail = format_finding_detail(rows[0])
        assert "[BLOCKER] Hardcoded key" in detail and "Location: a.py:12" in detail
        assert "rule.secrets" in explain_check_markdown("rule.secrets")
        assert "Unknown Check" in explain_check_markdown("rule.does_not_exist")

    @pytest.mark.asyncio
    async def test_textual_app_findings_explorer_hotkeys_and_summary(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        import json

        from textual.widgets import Button, Collapsible, DataTable, Input, Static

        from adk_review.tui.app import AdkReviewTuiApp
        from adk_review.tui.modals import ExplainCheckModal

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            summary = str(app.query_one("#selection-summary", Static).render())
            assert "my-service" in summary

            # Number hotkey toggles step 1 when focus is outside an Input
            app.query_one("#repo-list").focus()
            await pilot.press("1")
            await pilot.pause()
            repo_box = app.query_one("#repo-box", Collapsible)
            assert repo_box.collapsed is True

            # Typing digits in `#diff-cap-input` must not toggle sections
            diff_input = app.query_one("#diff-cap-input", Input)
            diff_input.value = "50000"
            app.query_one("#check-input", Input).value = "rule.secrets"
            await pilot.pause(0.05)
            sel = app.current_selection(dry_run=True)
            assert sel.checks == ("rule.secrets",) and sel.diff_cap == 50000

            # Populate Findings Explorer from JSON report and open ExplainCheckModal
            json_out = tmp_path / "out.json"
            json_out.write_text(
                json.dumps(
                    {
                        "schema": 2,
                        "findings": [
                            {
                                "check": "rule.secrets",
                                "file": "src/app.py",
                                "line": 4,
                                "severity": "blocker",
                                "title": "Token exposed",
                                "evidence": "TOKEN = 'abc'",
                                "suggestion": "Use env var.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            await app._load_generated_files({"json": json_out})
            table = app.query_one("#findings-table", DataTable)
            assert table.row_count == 1
            assert not app.query_one("#btn-explain-check", Button).disabled
            app.action_explain_selected_check()
            await pilot.pause()
            assert isinstance(app.screen, ExplainCheckModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ExplainCheckModal)

    @pytest.mark.asyncio
    async def test_textual_app_preset_reset_highlight_and_prior_baseline(self, tmp_path: Path) -> None:
        from adk_review.tui.options import baseline_path

        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, Input, TabbedContent

        from adk_review.tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#check-input", Input).value = "rule.secrets"
            app.query_one("#diff-cap-input", Input).value = "50000"
            app._apply_repo_preset("other-repo")
            assert app.query_one("#check-input", Input).value == ""
            assert app.query_one("#diff-cap-input", Input).value == ""

            assert app.selected_project is not None
            app._sync_repo_list_highlight(app.selected_project.path)
            base_file = baseline_path(
                app.selected_project.path, app.selected_base, app.selected_head, app.selected_mode
            )
            base_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                base_file.write_text(
                    json.dumps(
                        {
                            "schema": 2,
                            "findings": [
                                {
                                    "check": "rule.secrets",
                                    "file": "src/app.py",
                                    "line": 4,
                                    "severity": "blocker",
                                    "title": "Token exposed",
                                    "evidence": "TOKEN = 'abc'",
                                    "suggestion": "Use env var.",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                await app._sync_prior_baseline_findings(app.selected_project, base_file, has_prev=True)
                assert app.query_one("#findings-table", DataTable).row_count == 1
                assert "1 prior" in str(app.query_one("#output-tabs", TabbedContent).get_tab("tab-findings").label)
            finally:
                base_file.unlink(missing_ok=True)


class TestWindowsAndItsShells:
    """The front end on Windows, from cmd and from PowerShell: the command it shows, its keys, the text a review sends
    it, and the clipboard. Each is tested on every system, the clipboard's own round trip on Windows alone."""

    def test_the_command_shown_reads_the_same_in_cmd_and_powershell(self) -> None:
        """Read by review: the command was quoted for the C runtime alone (`list2cmdline`), which leaves `&`, `;`, `$`,
        `%` and a backtick bare. Pasted, cmd runs `&` as a second command and PowerShell `;`, and PowerShell expands
        `$x`. Each argument is quoted now as the reviewer quotes its own commands for both shells; where no quoting
        keeps one, the screen says so, and its own run, which takes no shell, is unchanged."""
        import shlex

        from adk_review.tui.options import NOT_PASTED, format_cli_command

        assert subprocess.list2cmdline(["adk-review", "--head", "a&b;c"]) == "adk-review --head a&b;c", "as before"
        shown = format_cli_command(["C:\\My Repos\\app", "--head", "a&b;c"], windows=True)
        assert shown == 'adk-review "C:\\My Repos\\app" --head "a&b;c"', shown
        # PowerShell expands `$` and a backtick and reads a typographic quote as one; cmd expands `%` and `!`.
        for hostile in ("feat/$x", "50%off", "x`y", "a\u201db", "a!b"):
            assert format_cli_command(["repo", "--head", hostile], windows=True) == NOT_PASTED, hostile
        assert format_cli_command(["repo", "--head", "a\nb"], windows=False) == NOT_PASTED, "a line break"
        posix = format_cli_command(["/a b", "--head", "feat/$x"], windows=False)
        assert shlex.split(posix) == ["adk-review", "/a b", "--head", "feat/$x"], posix

    @pytest.mark.asyncio
    async def test_every_action_key_reaches_its_action_wherever_the_focus_is(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: Ctrl+M, Minimize/Expand All, is the Enter a terminal sends, which Textual reads as Enter
        and a list takes to choose its item, and which the classic Windows console keeps for its mark mode; and in a
        text field Textual's own Ctrl+D deletes and Ctrl+X cuts, so Dry Run and Stop did nothing while a filter had
        the focus. Ctrl+T folds the sections now, and each action key goes first wherever the focus is."""
        pytest.importorskip("textual")
        from textual.widgets import Collapsible, Input

        from adk_review.tui.app import AdkReviewTuiApp
        from adk_review.tui.git_sync import SyncStatus
        from adk_review.tui.modals import SyncBranchModal

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        ran: list[object] = []
        monkeypatch.setattr(AdkReviewTuiApp, "_execute_review", lambda _self, *, dry_run: ran.append(dry_run))
        monkeypatch.setattr(AdkReviewTuiApp, "action_stop_review", lambda _self: ran.append("stop"))
        # Keys a terminal sends as another key, or a Windows console keeps for itself: Ctrl+M Enter, Ctrl+I Tab,
        # Ctrl+H Backspace, Ctrl+J a new line; the classic console's Ctrl+A, Ctrl+C, Ctrl+F, Ctrl+M and Ctrl+V.
        taken = {"ctrl+m", "ctrl+i", "ctrl+h", "ctrl+j", "ctrl+a", "ctrl+c", "ctrl+f", "ctrl+v"}
        keys = {key for binding in AdkReviewTuiApp.BINDINGS for key in str(getattr(binding, "key", "")).split(",")}
        assert not keys & taken, keys & taken
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#repo-filter", Input).focus()
            await pilot.press("ctrl+d", "ctrl+x", "ctrl+r")
            assert ran == [True, "stop", False], ran
            app.query_one("#repo-list").focus()
            await pilot.press("ctrl+t")
            await pilot.pause()
            shown = [box for box in app.query(Collapsible) if not box.has_class("hidden")]
            assert shown and all(box.collapsed for box in shown), "Ctrl+T folds every section"
            # Measured: once the keys went first, Ctrl+R and Ctrl+D started a review behind a dialog. None runs there
            # now but Stop, which must always reach a running review.
            ran.clear()
            app.push_screen(SyncBranchModal("head", SyncStatus("behind", "main", "origin/main", 0, 2)))
            await pilot.pause()
            await pilot.press("ctrl+r", "ctrl+d", "ctrl+t", "ctrl+x")
            await pilot.pause()
            assert ran == ["stop"] and isinstance(app.screen, SyncBranchModal), ran
            assert all(box.collapsed for box in shown), "nor did Ctrl+T unfold the sections behind it"

    @pytest.mark.asyncio
    async def test_a_reviews_text_reaches_the_screen_as_written_on_windows_too(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A review the screen runs writes to pipes, and on Windows Python writes a pipe in the system's code page,
        cp1252, where the screen reads UTF-8. Reproduced with that code page set: a progress line's `ü` and `—` read
        as replacement marks, and the report's `→` stopped the review with an error. Each review runs with UTF-8
        streams now."""
        pytest.importorskip("textual")
        from adk_review.tui.app import AdkReviewTuiApp

        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
        monkeypatch.setenv("PYTHONIOENCODING", "cp1252")  # what a pipe gets on Windows
        app = AdkReviewTuiApp(config_path=cfg)
        said: list[str] = []
        monkeypatch.setattr(app, "_log_progress", said.append)
        monkeypatch.setattr(app, "_set_banner", lambda _line: None)
        child = "import sys; sys.stderr.write('feat/f\\u00fcnf \\u2014 done\\n'); print('report \\u2192 ok')"
        code, out = await app._stream_subprocess([sys.executable, "-c", child])
        assert (code, said, out) == (0, ["feat/f\u00fcnf \u2014 done"], "report \u2192 ok\n"), (code, said, out)

    @pytest.mark.asyncio
    async def test_quitting_while_a_scan_runs_ends_cleanly(self, tmp_path: Path, monkeypatch) -> None:
        """Seen on Windows in CI, where git is slower: the screen closed while a scan of the repositories still ran, and
        the scan's last step set `loading` on a list whose screen was gone, `NoScreen`, raised as `WorkerFailed`. A
        person quitting with `q` during a scan met the same. Reproduced here with a slow scan."""
        pytest.importorskip("textual")
        import time

        from adk_review.tui import app as screen

        def slow(*_args: object) -> list:
            time.sleep(0.5)
            return []

        monkeypatch.setattr(screen, "discover_projects", slow)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(
            f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}", "{(tmp_path / "b").as_posix()}"]\n', encoding="utf-8"
        )
        app = screen.AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert app.query_one("#repo-list").loading is True, "the scan still runs as the screen closes"

    def test_copying_uses_windows_own_clipboard_and_the_terminals(self, monkeypatch) -> None:
        """Textual copies through the terminal alone (OSC 52), which Windows Terminal reads and the classic console
        window does not: there Ctrl+Y said "Copied" and copied nothing. On Windows the text goes to Windows' own
        clipboard too, its lines ended as Windows ends them, and the screen says which way it went."""
        from types import SimpleNamespace

        from adk_review.tui import clipboard

        sent: list[object] = []
        app = SimpleNamespace(copy_to_clipboard=lambda text: sent.append(("terminal", text)))
        monkeypatch.setattr(clipboard, "_windows_copy", lambda text: sent.append(("windows", text)) or True)
        assert clipboard.copy_text(app, "a\nb", windows=True) is True
        assert sent == [("terminal", "a\nb"), ("windows", "a\r\nb")], sent
        sent.clear()
        monkeypatch.setattr(clipboard, "_windows_copy", lambda _text: False)
        assert clipboard.copy_text(app, "a\nb", windows=True) is False, "held open by another program, say"
        assert clipboard.copy_text(app, "a\nb", windows=False) is False, "the terminal's alone: no way to know"
        assert sent == [("terminal", "a\nb"), ("terminal", "a\nb")], sent

    @pytest.mark.skipif(sys.platform != "win32", reason="the Windows clipboard exists only on Windows")
    def test_the_windows_clipboard_holds_what_was_copied(self) -> None:
        """The round trip through Windows' own clipboard, read back as another program reads it."""
        import ctypes
        from ctypes import wintypes

        from adk_review.tui.clipboard import _windows_copy

        text = "review: f\u00fcnf \u2014 done\r\nsecond line"
        assert _windows_copy(text) is True
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        assert user32.OpenClipboard(None)
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            assert ctypes.wstring_at(kernel32.GlobalLock(handle)) == text
            kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()


class TestWhatTheCodeReviewFound:
    """Each finding of a code review of the front end, reproduced before its fix."""

    def test_a_review_runs_the_reviewer_whatever_folder_the_screen_started_in(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Reproduced by review: `python -m` puts the folder the screen started in first on the import path, so a
        `hashlib.py` there ran in place of Python's own, exit 9. With `-P` that folder is never on it."""
        from adk_review.tui.options import review_command

        (tmp_path / "hashlib.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        done = subprocess.run(
            review_command(["--version"]), capture_output=True, text=True, encoding="utf-8", timeout=120, check=False
        )
        assert (done.returncode, done.stdout.strip()) == (0, "adk-review 0.1.0"), done.stderr[-400:]

    def test_unchanged_reviews_tell_their_progress_and_are_served_from_the_cache(
        self, demo_repo: Path, tmp_path: Path
    ) -> None:
        """Read by review, both reproduced here: a review tells its progress only at a terminal or with `--progress`,
        and the screen gave neither, so its progress tab stayed empty however long a review ran; and the cache key
        reads the previous report, which the screen replaced after every review, so the same review run again was
        served from the cache only from its fourth run, each a model call under a guideline's profile. The screen
        asks for `--progress`, and keeps the saved report where a review found the same findings."""
        from adk_review.tui import options

        sel = options.ReviewSelection(
            repo=str(demo_repo), head="feature/payments", base="main", profile="demo", output_dir=str(tmp_path)
        )
        said = []
        for _run in range(3):
            argv, outputs = options.build_review_argv(sel)
            done = subprocess.run(
                options.review_command(argv), capture_output=True, text=True, encoding="utf-8", timeout=300, check=False
            )
            assert done.returncode == 1, done.stderr[-400:]
            options.promote_baseline(outputs, done.returncode)
            said.append(done.stderr)
        assert "reading feature/payments against main" in said[0], said[0][-400:]
        assert ["served from the cache" in err for err in said] == [False, False, True], [e[-200:] for e in said]

    def test_each_review_target_has_its_own_saved_report(self) -> None:
        """Reproduced by review: the saved report `--previous` reads was named by a slug cut at 40 characters, `/` read
        as `-`, the head left out under `--all` and the repository named by its folder alone, so two branches shared
        one, and each review marked the other's findings new or resolved."""
        from adk_review.tui.options import baseline_path

        long = "feature/PAY-1234-refund-retry-on-"
        for one, other in (
            (("/w/api", "develop", long + "timeout"), ("/w/api", "develop", long + "network-error")),
            (("/w/api", "main", "feature/a"), ("/w/api", "main", "feature-a")),
            (("/w/api", "", "feat/x", "all"), ("/w/api", "", "feat/y", "all")),
            (("/w/one/api", "main", "x"), ("/w/two/api", "main", "x")),
        ):
            assert baseline_path(*one) != baseline_path(*other), (one, other)
        assert baseline_path("/w/api", "main", "x") == baseline_path("/w/api", "main", "x")

    def test_a_saved_choice_reads_back_whatever_characters_it_holds(self, tmp_path: Path) -> None:
        """Reproduced by review: a preset was written with `json.dumps`, which writes an emoji as two surrogate
        escapes that TOML refuses, so after a review of `feature/🚀-launch` every start stopped with exit 2."""
        from adk_review.tui.config import RepoPreset, load_tui_config, save_repo_preset

        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
        preset = RepoPreset(base='feature/\U0001f680-launch "q" \\ ü', path=("My Docs", "src/\U0001f680"))
        save_repo_preset(load_tui_config(cfg), "repo \U0001f680", preset)
        assert load_tui_config(cfg).projects["repo \U0001f680"] == preset

    def test_a_path_typed_by_hand_keeps_its_spaces(self) -> None:
        """Read by review: the typed paths were split at every space too, so `My Docs` left out `My` and `Docs`, two
        folders the person never named. A comma or a line parts them now."""
        from adk_review.tui.options import parse_csv_paths

        assert parse_csv_paths("My Docs, src/api ,\ndocs/reports") == ["My Docs", "src/api", "docs/reports"]

    def test_a_fetch_past_its_timeout_stops_with_every_process_it_started(self) -> None:
        """Reproduced by review: past the timeout git was killed, but its children, ssh or git's https helper, held its
        output open, and the screen waited on them, 6 s against a timeout of 1 s, and on a remote that hangs for
        minutes or for ever. The whole group is stopped now, as the whole tree is on Windows."""
        import os
        import time

        from adk_review.tui.git_sync import _bounded

        grandchild = "import time; time.sleep(30)"
        child = (
            f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
        )
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            _bounded([sys.executable, "-c", child], dict(os.environ), timeout_s=1)
        assert time.monotonic() - started < 10, "the screen waited on a process git started"

    def test_the_palette_draws_a_name_with_brackets_as_written(self) -> None:
        """Read by review, and reproduced: Textual's `highlight` reads each name as markup, so `my[service]` was
        listed as `my`, and `x[/old]` raised `MarkupError`, which dropped the palette's later hits."""
        pytest.importorskip("textual")
        from textual.fuzzy import Matcher

        from adk_review.tui.commands import highlighted

        for label in ("Repo: my[service]  (main, clean)", "Repo: x[/old]"):
            assert highlighted(Matcher("old"), label).plain == label

    @pytest.mark.asyncio
    async def test_the_screen_keeps_working_through_an_error_and_a_long_line(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, both reproduced: a config the screen could not write raised inside the review's worker,
        which ended the whole screen with a traceback; and one line of a review's progress past 64 KiB, a provider's
        error body, made reading it raise. The screen says what failed, and reads a line of any length."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from adk_review.tui import app as screen

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")

        def unwritable(*_args: object) -> None:
            raise PermissionError(13, "Permission denied", str(cfg))

        monkeypatch.setattr(screen, "save_repo_preset", unwritable)
        app = screen.AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base = "origin/main"  # a target, which no fetch has listed others for
            await pilot.press("ctrl+r")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.is_running, "the screen is still open"
            banner = str(app.query_one("#status-banner", Static).render())
            assert "could not run" in banner and "Permission denied" in banner, banner
            said: list[str] = []
            monkeypatch.setattr(app, "_log_progress", said.append)
            long = "import sys; sys.stderr.write('x' * 100_000 + '\\nafter\\n')"
            code, _out = await app._stream_subprocess([sys.executable, "-c", long])
            assert code == 0 and said[-1] == "after" and len(said[0]) >= 64_000, [len(line) for line in said]

    @pytest.mark.asyncio
    async def test_results_stay_with_the_target_they_came_from(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: a change of branch kept the last review's comment and hand-off, marked ready, which Ctrl+Y
        then copied for another branch's pull request; and a review that ended after the person chose another target
        filled that target's tabs. A change of target clears them now, and a review whose target changed is named,
        its files where they are, and not shown."""
        pytest.importorskip("textual")
        from textual.widgets import Static, TabbedContent

        from adk_review.tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "origin")  # the screen fetches only at F5
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        comment = tmp_path / "comment.md"
        comment.write_text("the last review's comment\n", encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await app._load_generated_files({"comment": comment})
            assert app.last_comment_md and "ready" in str(app.query_one(TabbedContent).get_tab("tab-comment").label)
            app._sync_chosen_branch("head", "origin/feat-remote")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.selected_head == "origin/feat-remote"
            assert app.last_comment_md == "", "another branch's comment is never shown as this one's"
            assert "ready" not in str(app.query_one(TabbedContent).get_tab("tab-comment").label)

            loaded: list[object] = []

            async def moved_on(_cmd: list[str]) -> tuple[int, str]:
                app.selected_head = "main"  # the person chose another target while it ran
                return 1, "the report of origin/feat-remote"

            monkeypatch.setattr(app, "_stream_subprocess", moved_on)
            monkeypatch.setattr(app, "_load_generated_files", lambda outputs: loaded.append(outputs))
            app.selected_head = "origin/feat-remote"
            app._execute_review(dry_run=False)
            await pilot.pause()
            await app.workers.wait_for_complete()
            banner = str(app.query_one("#status-banner", Static).render())
            assert loaded == [] and app.last_report_md == "", "its results fill no other target's tabs"
            assert "origin/feat-remote" in banner and "not shown" in banner, banner

    @pytest.mark.asyncio
    async def test_f5_keeps_the_branches_chosen_and_a_remote_branch_is_reviewed_as_the_remote_holds_it(
        self, tmp_path: Path
    ) -> None:
        """Read by review: F5 set the branch under review back to the one checked out, and the target to the default,
        so a review after it read another pair; and choosing `origin/x` where a local `x` was ahead of it reviewed the
        local branch, unpushed work and all. F5 keeps the choice where the branch is still there, and a remote's
        branch is reviewed as the remote holds it."""
        pytest.importorskip("textual")
        from adk_review.tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "origin")
        _git(local, "switch", "-c", "feat-remote", "origin/feat-remote")
        (local / "local.txt").write_text("not pushed\n", encoding="utf-8")
        _git(local, "add", "local.txt")
        _git(local, "commit", "-m", "local work")  # the local copy one commit ahead of the remote's
        _git(local, "switch", "main")
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app._sync_chosen_branch("head", "origin/feat-remote")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.selected_head == "origin/feat-remote", "as the remote holds it, not the local copy ahead of it"
            app.selected_base = "origin/main"
            await pilot.press("f5")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert (app.selected_head, app.selected_base) == ("origin/feat-remote", "origin/main")

    @pytest.mark.asyncio
    async def test_a_running_review_is_never_restarted_by_its_own_key(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: the review's worker is exclusive, so Ctrl+R or Ctrl+D during a run stopped the review and
        started another, its model calls spent again. The Run button was disabled while it ran, its key was not.
        While a review runs, both keys wait for it now."""
        pytest.importorskip("textual")
        import asyncio

        from adk_review.tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        started: list[list[str]] = []
        release = asyncio.Event()

        async def slow(cmd: list[str]) -> tuple[int, str]:
            started.append(cmd)
            await release.wait()
            return 0, ""

        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base = "origin/main"
            monkeypatch.setattr(app, "_stream_subprocess", slow)
            await pilot.press("ctrl+r")
            await pilot.pause(0.2)
            await pilot.press("ctrl+r", "ctrl+d")
            await pilot.pause(0.2)
            assert len(started) == 1, f"{len(started)} reviews started"
            release.set()
            await pilot.pause()
            await app.workers.wait_for_complete()

    def test_moving_a_branch_to_its_remote_writes_no_tracking_into_its_config(self, tmp_path: Path) -> None:
        """Read by review: `git branch -f x origin/x` also made `x` track `origin/x`, a line in the repository's config
        the person never asked for, where `x` had no upstream and the screen inferred one. It moves the branch alone
        now."""
        from adk_review.tui.git_sync import fast_forward_local_branch

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "origin")
        _git(local, "branch", "--no-track", "feat-remote", "origin/feat-remote~1")
        done = fast_forward_local_branch(str(local), "feat-remote", "origin/feat-remote")
        assert done.ok and _git(local, "rev-parse", "feat-remote") == _git(local, "rev-parse", "origin/feat-remote")
        tracked = subprocess.run(
            ["git", "-C", str(local), "config", "--get", "branch.feat-remote.merge"], capture_output=True, check=False
        )
        assert tracked.returncode == 1, tracked.stdout

    def test_no_sentence_the_screen_shows_joins_two_with_a_semicolon(self) -> None:
        """The package's own words are ASD-STE100, as CLAUDE.md asks: one idea a sentence, and no `;` joining two.
        Read by review: three of the front end's did; the reviewer's own guard reads the command's words alone."""
        import ast

        from adk_review.tui import app

        folder = Path(app.__file__).parent
        joined = []
        for path in sorted(folder.glob("*.py")):
            if path.name == "styles.py":  # CSS, where `;` ends each rule
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and node.body
                and isinstance(node.body[0], ast.Expr)
            }
            joined += [
                f"{path.name}:{node.lineno}: {node.value[:80]}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and "; " in node.value
            ]
        assert joined == [], joined

    @pytest.mark.asyncio
    async def test_a_saved_path_is_one_choice_and_a_models_text_is_never_a_link(self, tmp_path: Path) -> None:
        """Read by review: a saved path came back both as a ticked folder and as typed text, so unticking it changed
        nothing; and a finding's title and fix, a model's words, were drawn as Markdown, where a branch that steered the
        model could make a link the person might click. A saved path that is a listed folder comes back ticked alone,
        and a finding is drawn as plain text."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import Input, SelectionList, TabbedContent

        from adk_review.tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(
            f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n\n'
            '[projects."my-service"]\npath = ["src", "not/listed"]\n',
            encoding="utf-8",
        )
        report = tmp_path / "report.json"
        finding = {"check": "guideline.security", "file": "a.py", "line": 3, "severity": "major"}
        finding |= {"title": "**Shout**", "suggestion": "See [the fix](https://attacker.example/?q=x)."}
        report.write_text(json.dumps({"schema": 2, "findings": [finding]}), encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            boxes = app.query_one("#path-list", SelectionList)
            assert "src" in boxes.selected and app.query_one("#extra-path", Input).value == "not/listed"
            boxes.deselect("src")
            await pilot.pause()
            assert app.current_selection().paths == ("not/listed",), "unticked, it is not reviewed"

            await app._load_generated_files({"json": report})
            app.query_one(TabbedContent).active = "tab-findings"
            await pilot.pause()
            shown = str(app.query_one("#finding-detail").render())
            assert "**Shout**" in shown and "[the fix](https://attacker.example/?q=x)" in shown, shown


async def _wait_for_branches(app, pilot) -> None:
    await pilot.pause()
    for _ in range(60):
        await pilot.pause(0.05)
        if app.branches:
            break
