"""Tests for the terminal front end, `tui/tui/`: its config, its git reads, its options and its screen.

Each test imports what it reads where it runs, as the suite's other files do: `load_agent` loads the package afresh
for the tests before these, and a name bound when this file was imported is the old package's. Measured: after one
such test, three of these failed, the catalog reading no rule as a rule and a patch landing on another module."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: The front end's package, by its path: where it is not installed, as with the reviewer alone, its tests find it here.
FRONT_END = Path(__file__).resolve().parents[1] / "tui" / "tui"


@pytest.fixture(autouse=True)
def _state_apart(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The saved reports and the cache in a folder of the test's own. Read by review: tests wrote and deleted saved
    reports in the package's real state folder, beside a person's own."""
    import importlib

    state = tmp_path_factory.mktemp("tui-state")
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):  # read where a module loads afresh
        monkeypatch.setenv(name, str(state / name.lower()))
    options = importlib.import_module("tui.options")
    config = importlib.import_module("tui.config")
    monkeypatch.setattr(options, "BASELINES_DIR", state / "baselines")
    monkeypatch.setattr(options, "CACHE_DIR", state / "cache")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", state / "config" / "tui.toml")
    monkeypatch.setattr(config, "OUTPUTS_DIR", state / "outputs")
    # Never a person's own: the config a checkout kept before, and the one in their own folder, which a first run reads.
    monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", state / "no-checkout" / ".tui.toml")
    monkeypatch.setattr(config, "USER_CONFIG_PATH", state / "user-config" / "tui.toml")
    monkeypatch.setattr(config, "OLD_PACKAGE_DIR", state / "no-checkout" / "adk_review_tui")
    monkeypatch.setattr(config, "STATE_DIR", state)


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
    (seed / "src" / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
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
    """`tui/.tui.toml` loading, multi-root workspace parsing, and preset persistence."""

    def test_the_config_and_the_state_live_in_the_persons_own_folders(self, tmp_path: Path, monkeypatch) -> None:
        """Reproduced on a copy installed with `uv tool install`: its config and its saved reviews lived inside the
        tool's own environment, which an uninstall removed, choices and all, and where no one finds a comment. They
        live in the person's own folders now, as each system names them."""
        from tui.config import config_home, state_home

        monkeypatch.setenv("APPDATA", "C:\\Users\\me\\AppData\\Roaming")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\me\\AppData\\Local")
        assert config_home(windows=True) == Path("C:\\Users\\me\\AppData\\Roaming")
        assert state_home(windows=True) == Path("C:\\Users\\me\\AppData\\Local")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        assert (config_home(windows=False), state_home(windows=False)) == (tmp_path / "config", tmp_path / "state")
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/no")  # the XDG spec takes an absolute path alone
        monkeypatch.delenv("XDG_STATE_HOME")
        assert config_home(windows=False) == Path.home() / ".config"
        assert state_home(windows=False) == Path.home() / ".local" / "state"
        # As a fresh process computes them: in this checkout, `tui/.tui.toml`, and the state never inside the package.
        said = "from tui import config as c; print(c.DEFAULT_CONFIG_PATH); print(c.STATE_DIR)"
        found = os.pathsep.join(filter(None, [str(FRONT_END.parent), os.environ.get("PYTHONPATH")]))
        done = subprocess.run(
            [sys.executable, "-c", said],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            env={**os.environ, "PYTHONPATH": found},  # where the front end is not installed, as with the reviewer alone
        )
        default, state = map(Path, done.stdout.splitlines())
        assert default == FRONT_END.parent / ".tui.toml", default
        assert not state.is_relative_to(FRONT_END) and state.name == "adk-review-tui", state

    def test_a_checkout_reads_its_own_tui_toml_and_an_installed_copy_the_persons_folder(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Reported by the owner: they set their folder of repositories in `.tui.toml`, the config they know, and the
        screen went on listing the old one. Since the config moved to the person's own folder, `.tui.toml` was copied
        there once and never read again, and the screen said nothing of which file it read. In a checkout the config
        is `tui/.tui.toml` again, beside the front end, where the person edits it; an installed copy, which has no
        checkout, keeps it in the person's own folder, which a reinstall leaves alone."""
        from tui import config

        checkout = tmp_path / "adk-review"
        (checkout / "tui").mkdir(parents=True)  # no `.git`: a ZIP download is a checkout too
        (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (checkout / "tui" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        monkeypatch.setattr(config, "TUI_DIR", checkout / "tui" / "tui")
        assert config.default_config_path() == checkout / "tui" / ".tui.toml"
        installed = tmp_path / "tools" / "adk-review-tui" / "lib" / "python3.14" / "site-packages"
        monkeypatch.setattr(config, "TUI_DIR", installed / "tui")
        assert config.default_config_path() == config.config_home() / "adk-review-tui" / "tui.toml"

    def test_an_installed_copy_names_the_folder_it_starts_in_as_its_folder_of_repositories(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Reproduced on a copy installed with `uv tool install`: its first run named its own environment's
        `lib/python3.14` as the folder of repositories, and the screen found none. A checkout still names the folder
        that holds it, where its neighbours are; an installed copy names the folder it starts in."""
        from tui import config

        checkout = tmp_path / "projects" / "adk-review"
        (checkout / "tui").mkdir(parents=True)
        (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (checkout / "tui" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        monkeypatch.setattr(config, "TUI_DIR", checkout / "tui" / "tui")
        assert config.default_root_folder() == str(tmp_path / "projects")
        installed = tmp_path / "tools" / "adk-review-tui" / "lib" / "python3.14" / "site-packages"
        monkeypatch.setattr(config, "TUI_DIR", installed / "tui")
        (tmp_path / "work").mkdir()
        monkeypatch.chdir(tmp_path / "work")
        assert config.default_root_folder() == str(tmp_path / "work")

    def test_a_checkouts_old_config_moves_to_the_new_place_once(self, tmp_path: Path, monkeypatch) -> None:
        """A checkout kept its config as `adk_review/tui/.tui.toml`, when the front end lived in the reviewer's package.
        The first run that finds no config where it reads one moves it there, its saved choices kept and its old
        `output_dir`, a path inside the package, left out. Moved, never copied: read by the owner, a copy left the old
        file to edit, and the screen read the other."""
        from tui import config

        legacy = tmp_path / "checkout" / ".tui.toml"
        legacy.parent.mkdir()
        legacy.write_text(
            f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\noutput_dir = "adk_review/tui/.state/outputs"\n\n'
            '[projects."my-service"]\nbase = "develop"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", legacy)
        target = config.ensure_tui_config()
        assert target == config.DEFAULT_CONFIG_PATH.resolve() and not legacy.exists(), "moved, one file to edit"
        loaded = config.load_tui_config(target)
        assert loaded.projects["my-service"].base == "develop" and loaded.output_dir == str(config.OUTPUTS_DIR)
        assert loaded.root_dirs[0].path == str(tmp_path.resolve())

    def test_a_folder_of_repositories_or_a_setting_it_cannot_read_is_one_sentence_and_exit_2(
        self, tmp_path: Path, capsys
    ) -> None:
        """Read by review: a `root_dirs` that was no list, or a setting misspelt, fell back in silence to the default
        folder, and the screen listed another folder's repositories than the one the person wrote. One folder written
        as a string is read as one folder; anything else is said, with what to write."""
        from tui import main as tui_main
        from tui.config import load_tui_config

        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = "{tmp_path.as_posix()}"\n', encoding="utf-8")
        assert [root.path for root in load_tui_config(cfg).root_dirs] == [str(tmp_path.resolve())]
        for body, said in (
            ("[workspace]\nroot_dirs = 3\n", "root_dirs lists the folders that hold your repositories"),
            ("[workspace]\nroot_dirs = []\n", "root_dirs lists the folders that hold your repositories"),
            ('[workspace]\nroot_dir = ["~/Projects"]\n', "[workspace] has no setting 'root_dir'"),
            ('[defaults]\nfail_om = "major"\n', "[defaults] has no setting 'fail_om'"),
            ("[worksapce]\n", "has no table 'worksapce'"),
        ):
            cfg.write_text(body, encoding="utf-8")
            assert tui_main(["--config", str(cfg)]) == 2, body
            err = capsys.readouterr().err
            assert said in err and "Traceback" not in err, err

    def test_a_relative_output_dir_is_read_from_the_configs_own_folder(self, tmp_path: Path) -> None:
        """Read by review: a relative `output_dir` was read from the checkout's root, which an installed copy has not,
        and its reviews landed inside its own environment. It is read from the config's folder now."""
        from tui.config import load_tui_config

        cfg = tmp_path / "mine" / "tui.toml"
        cfg.parent.mkdir()
        cfg.write_text(
            f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\noutput_dir = "reviews"\n', encoding="utf-8"
        )
        assert load_tui_config(cfg).output_dir == str((cfg.parent / "reviews").resolve())

    def test_ensure_and_load_tui_config_supports_multiple_root_dirs_and_presets(self, tmp_path: Path) -> None:
        from tui.config import RepoPreset, TuiConfig, ensure_tui_config, load_tui_config, save_repo_preset

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

        # A number the config cannot use is said, never clamped or replaced in silence (see the review's tests below)
        cfg_file.write_text(
            '[workspace]\nroot_dirs = ["' + root_a.as_posix() + '"]\nscan_depth = "invalid"\n', encoding="utf-8"
        )
        with pytest.raises(ValueError, match="scan_depth is 'invalid'"):
            load_tui_config(cfg_file)

    def test_a_config_the_screen_cannot_show_is_one_sentence_and_exit_2(self, tmp_path: Path, capsys) -> None:
        """Read by review, and reproduced: a `fail_on` no severity names raised `InvalidSelectValueError` with a
        traceback, at the start for `[defaults]` and at a repository's choice for its preset; and a file that is no
        TOML raised from inside the screen. Each is refused before the screen opens, with the value and what to give."""
        from tui import main as tui_main

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
        from tui.git_sync import (
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
        from tui.git_sync import fast_forward_local_branch, fetch_remote

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
        from tui.git_sync import changed_folders, discover_projects, is_dirty, list_branches

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
        from tui.options import (
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
        from tui.options import ReviewSelection, build_review_argv, parse_csv_checks, parse_diff_cap

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
        from tui import git_sync
        from tui.git_sync import BranchInfo, discover_projects, fetch_remote, inspect_branch_sync
        from tui.options import format_cli_command, parse_csv_paths

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
        from tui import main as tui_main

        cfg = tmp_path / ".tui.toml"
        assert tui_main(["--init-config", "--config", str(cfg)]) == 0
        assert cfg.is_file()
        assert str(cfg.resolve()) in capsys.readouterr().out

        monkeypatch.setitem(sys.modules, "textual", None)
        monkeypatch.delitem(sys.modules, "tui.app", raising=False)
        assert tui_main(["--config", str(cfg)]) == 2
        err = capsys.readouterr().err
        assert "textual is not installed" in err and "run `uv sync --locked --all-packages`" in err, err
        assert "Install the front end as its own tool" in err, err

    @pytest.mark.asyncio
    async def test_textual_app_chain_of_selects_mounts_and_updates_cli_preview(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

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
    async def test_the_screen_names_the_config_it_reads_and_the_folder_of_repositories(self, tmp_path: Path) -> None:
        """Reported by the owner: the screen listed another folder's repositories than the one they set, and named
        neither the file it read nor the folder. Its first line names both now."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / "mine" / ".tui.toml"
        cfg.parent.mkdir()
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(200, 45)) as pilot:
            await pilot.pause()
            shown = str(app.query_one("#config-line", Static).render())
            assert f"Config: {cfg.resolve()}" in shown and str((tmp_path / "workspace").resolve()) in shown, shown
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()

    @pytest.mark.asyncio
    async def test_textual_app_collapsible_sections_and_multi_root(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Collapsible

        from tui.app import AdkReviewTuiApp

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
            assert not repo_box.display and app.query_one("#dock-repo-box").display, "minimized, it docks"
            assert head_box.size.width > width_before, "and the open panels take its room"
            head_box.collapsed = True
            app.query_one("#base-box", Collapsible).collapsed = True
            await pilot.pause()
            assert app.query_one("#chain-row").size.height == 0, "a row with no open panel goes"
            repo_box.collapsed = False
            await pilot.pause()
            assert repo_box.display and "Local Repository" in repo_box.title

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

        from tui.app import AdkReviewTuiApp

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
    async def test_picking_a_repository_fetches_its_new_branches_and_removes_none(self, tmp_path: Path) -> None:
        """Measured on the owner's repositories: 5 of 13 lists were out of step with their remote, a branch pushed
        since the last fetch not listed, until F5. Picking a repository fetches, with `--no-prune`, and then lists its
        branches: a branch the remote no longer holds stays until F5 removes it, and picked again, the branches chosen
        stay chosen. The repository the screen picks itself, as it opens, is not fetched."""
        pytest.importorskip("textual")
        from textual.widgets import OptionList

        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "update-ref", "refs/remotes/origin/gone", "HEAD")  # a branch the remote no longer holds
        cfg = _screen_config(tmp_path)
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            assert "origin/feat-remote" not in [b.name for b in app.branches], "opening fetched nothing"
            await _pick(app, pilot)
            names = [b.name for b in app.branches]
            assert "origin/feat-remote" in names, "picking the repository fetched the branch the remote got since"
            assert "origin/gone" in names, "and removed none: F5 prunes"
            head = app.query_one("#head-list", OptionList)
            assert "origin/feat-remote" in [str(head.get_option_at_index(at).id) for at in range(head.option_count)]
            app._sync_chosen_branch("base", "origin/feat-remote")
            await app.workers.wait_for_complete()
            await _pick(app, pilot)  # picked again: fetched again, the target kept
            assert app.selected_base == "origin/feat-remote", app.selected_base
            await pilot.press("f5")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert "origin/gone" not in [b.name for b in app.branches], "F5 is the fetch that prunes"

    @pytest.mark.asyncio
    async def test_with_fetch_on_select_off_picking_a_repository_fetches_nothing(self, tmp_path: Path) -> None:
        """`fetch_on_select = false`, for a person offline or on a metered line: picking a repository lists it as it
        holds it, as before, and F5 still fetches."""
        pytest.importorskip("textual")
        from textual.widgets import OptionList

        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        root = (tmp_path / "workspace").as_posix()
        cfg.write_text(f'[workspace]\nroot_dirs = ["{root}"]\nfetch_on_select = false\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            repo = app.query_one("#repo-list", OptionList)
            repo.highlighted = 0
            repo.action_select()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert not (local / ".git" / "FETCH_HEAD").exists(), "nothing fetched"
            assert "origin/feat-remote" not in [b.name for b in app.branches]
            await pilot.press("f5")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert "origin/feat-remote" in [b.name for b in app.branches], "and F5 still fetches"

    @pytest.mark.asyncio
    async def test_a_fetch_that_fails_on_picking_leaves_the_lists_as_the_repository_holds_them(
        self, tmp_path: Path
    ) -> None:
        """A remote that cannot be reached, offline or gone: the lists stay as the repository holds them, and a notice
        says the fetch failed and that F5 fetches again, as F5's own failure says it."""
        pytest.importorskip("textual")

        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "remote", "set-url", "origin", str(tmp_path / "no-such-remote.git"))
        held = sorted(_git(local, "for-each-ref", "--format=%(refname)").split())
        cfg = _screen_config(tmp_path)
        app = AdkReviewTuiApp(config_path=cfg)
        said: list[str] = []
        app.notify = lambda message, **_options: said.append(str(message))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            listed = sorted(b.name for b in app.branches)
            await _pick(app, pilot)
            assert sorted(b.name for b in app.branches) == listed, "the lists as the repository holds them"
            assert sorted(_git(local, "for-each-ref", "--format=%(refname)").split()) == held
            assert any("Fetch warning" in line and "F5" in line for line in said), said
            assert any("fetch_on_select = false" in line for line in said), "it names the setting that turns it off"

    @pytest.mark.asyncio
    async def test_the_screen_draws_a_square_bracket_as_it_is_written(self, tmp_path: Path) -> None:
        """Textual reads `[name]` in a string as markup. Read by review, and reproduced: each section's title lost the
        choice it names, "Branch Under Review [main]" drawn with no branch; a repository `my[service]` was listed as
        `my`; and a finding titled "The path [/tmp] is shared" raised `MarkupError` and ended the screen."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, OptionList, Static, TabbedContent

        from tui.app import AdkReviewTuiApp

        repo = tmp_path / "workspace" / "my[service]"
        (repo / "src").mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "dev@example.com")
        _git(repo, "config", "user.name", "Dev")
        (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
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

        from tui.findings import (
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
        # Reported by the owner: a major read as one short line here, its reason and its corrected code, which the
        # JSON holds, never shown.
        report.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "findings": [
                        {
                            "fingerprint": "f",
                            "check": "guideline.correctness",
                            "file": "a.py",
                            "line": 3,
                            "severity": "major",
                            "title": "Tax added twice",
                            "evidence": "total = with_tax(total)",
                            "suggestion": "Remove the second call.",
                            "why": "The coupon path adds the tax again, so the customer pays 2 % more.",
                            "code": "def charge(total):\n    return total",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        explained = format_finding_detail(load_findings_from_json(report)[0])
        assert "Why it matters: The coupon path adds the tax again" in explained, explained
        assert "Corrected code:\n    def charge(total):\n        return total" in explained, explained
        assert "rule.secrets" in explain_check_markdown("rule.secrets")
        assert "Unknown Check" in explain_check_markdown("rule.does_not_exist")

    @pytest.mark.asyncio
    async def test_textual_app_findings_explorer_hotkeys_and_summary(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        import json

        from textual.widgets import Button, Collapsible, DataTable, Input, Static

        from tui.app import AdkReviewTuiApp
        from tui.modals import ExplainCheckModal

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
        from tui.options import baseline_path

        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, Input, TabbedContent

        from tui.app import AdkReviewTuiApp

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

        from tui.options import NOT_PASTED, format_cli_command

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

        from tui.app import AdkReviewTuiApp
        from tui.git_sync import SyncStatus
        from tui.modals import SyncBranchModal

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
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert all(box.collapsed for box in shown), "nor Ctrl+O restore them behind it"

    @pytest.mark.asyncio
    async def test_ctrl_t_minimizes_every_panel_and_ctrl_o_restores_every_one(self, tmp_path: Path) -> None:
        """Asked by the owner: the panels took most of the screen. One key that folded or unfolded them all, by how it
        found them, gave no sure way to either. Ctrl+T minimizes every panel, pressed twice too, and Ctrl+O restores
        every one, wherever the focus is: a text field takes neither key."""
        pytest.importorskip("textual")
        from textual.widgets import Collapsible, Input

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = _screen_config(tmp_path)
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            shown = [box for box in app.query(Collapsible) if not box.has_class("hidden")]
            app.query_one("#repo-filter", Input).focus()
            await pilot.press("ctrl+t", "ctrl+t")
            await pilot.pause()
            assert all(box.collapsed for box in shown), "Ctrl+T twice leaves every panel minimized"
            await pilot.press("ctrl+o", "ctrl+o")
            await pilot.pause()
            assert not any(box.collapsed for box in shown), "Ctrl+O twice leaves every panel open"
            shown[1].collapsed = True
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert not any(box.collapsed for box in shown), "Ctrl+O restores a panel minimized alone too"
            assert app.query_one("#repo-filter", Input).value == "", "and the text field took no key"

    @pytest.mark.asyncio
    async def test_minimized_panels_dock_into_one_line_and_the_results_take_their_rows(self, tmp_path: Path) -> None:
        """Asked by the owner: six minimized panels took two rows of boxes, each labelled with initials such as
        `2.BUR`, and the summary below them repeated their choices. Measured on 140x45: they used 8 rows, and the
        results had 28. A minimized panel leaves its row for one chip in a single line, which names its step and its
        choice; a row with no open panel goes, and so does the summary, while the dock carries the choices."""
        pytest.importorskip("textual")
        from textual.widgets import Button, TabbedContent

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = _screen_config(tmp_path)
        mark = chr(0x25B6)
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            assert not app.query_one("#panel-dock").display, "no dock while every panel is open"
            await pilot.press("ctrl+t")
            await pilot.pause()
            chips = [chip for chip in app.query(".dock-chip").results(Button) if chip.display]
            names = [f"{mark} {n} {name}" for n, name in enumerate(("Repo", "Review", "Target"), 1)]
            names += [f"{mark} {n} {name}" for n, name in enumerate(("Folders", "Profile", "Outputs"), 4)]
            assert [str(chip.label).split(":")[0] for chip in chips] == names, [str(c.label) for c in chips]
            assert len({chip.region.y for chip in chips}) == 1, "one line"
            assert app.query_one("#chain-row").region.height == 0 and app.query_one("#mid-row").region.height == 0
            assert not app.query_one("#selection-summary").display, "the dock carries the choices"
            dock, banner = app.query_one("#panel-dock").region, app.query_one("#status-banner").region
            run_bar, results = app.query_one("#cmd-bar").region, app.query_one("#output-tabs", TabbedContent).region
            assert dock.height == 1 and banner.y == dock.bottom and run_bar.y == banner.bottom, (dock, banner, run_bar)
            assert results.y == run_bar.bottom, "the panels take one row, and the results every row below the run bar"

            app.set_focus(None)
            await pilot.press("2")  # one panel restored: it takes its row, the others stay docked
            await pilot.pause()
            assert app.query_one("#head-box").display and not app.query_one("#repo-box").display
            assert app.query_one("#chain-row").region.height > 0 and app.query_one("#mid-row").region.height == 0
            assert len([chip for chip in app.query(".dock-chip") if chip.display]) == 5
            await pilot.click("#dock-base-box")  # a chip restores its own panel
            await pilot.pause()
            assert app.query_one("#base-box").display and not app.query_one("#dock-base-box").display
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert not app.query_one("#panel-dock").display and app.query_one("#selection-summary").display

    @pytest.mark.asyncio
    async def test_the_dock_keeps_every_step_on_a_narrow_screen_and_draws_a_name_as_written(
        self, tmp_path: Path
    ) -> None:
        """At 100 columns the six chips still fit one line, each with its step's number: the names give way to the
        choices, cut the longest first, and each chip's tooltip names its panel. A repository named `my[svc]` reads as
        written: Textual reads `[svc]` in a string as a tag."""
        pytest.importorskip("textual")
        from textual.widgets import Button

        from tui.app import AdkReviewTuiApp

        repo = tmp_path / "workspace" / "my[svc]-with-a-rather-long-name-for-a-narrow-screen"
        repo.mkdir(parents=True)
        _git(repo, "init", "-b", "main")
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        _git(repo, "add", ".")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "base")
        _git(repo, "branch", "feature/a-branch-whose-name-runs-on-and-on")
        cfg = _screen_config(tmp_path)
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(100, 40)) as pilot:
            await _wait_for_branches(app, pilot)
            await pilot.press("ctrl+t")
            await pilot.pause()
            chips = [chip for chip in app.query(".dock-chip").results(Button) if chip.display]
            assert len(chips) == 6 and len({chip.region.y for chip in chips}) == 1, [str(c.label) for c in chips]
            assert all(chip.region.right <= 100 for chip in chips), [(str(c.label), c.region) for c in chips]
            first = str(chips[0].label)
            assert first.startswith(f"{chr(0x25B6)} 1 my[svc]") and first.endswith(chr(0x2026)), first
            assert str(chips[0].tooltip).startswith("1. Local Repository [my[svc]"), "the tooltip names the panel"

    @pytest.mark.asyncio
    async def test_a_reviews_text_reaches_the_screen_as_written_on_windows_too(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A review the screen runs writes to pipes, and on Windows Python writes a pipe in the system's code page,
        cp1252, where the screen reads UTF-8. Reproduced with that code page set: a progress line's `ü` and `—` read
        as replacement marks, and the report's `→` stopped the review with an error. Each review runs with UTF-8
        streams now."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

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
        person quitting with `q` during a scan met the same. Reproduced here with a scan that ends only once the screen
        has closed: read on a slow macOS runner, a scan that slept half a second ended first, and the test failed."""
        pytest.importorskip("textual")
        import threading

        from tui import app as screen

        closed = threading.Event()

        def slow(*_args: object) -> list:
            closed.wait(10)  # never forever, where a change makes the screen wait for it
            return []

        monkeypatch.setattr(screen, "discover_projects", slow)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(
            f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}", "{(tmp_path / "b").as_posix()}"]\n', encoding="utf-8"
        )
        app = screen.AdkReviewTuiApp(config_path=cfg)
        try:
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                assert app.query_one("#repo-list").loading is True, "the scan still runs as the screen closes"
        finally:
            closed.set()

    def test_copying_uses_windows_own_clipboard_and_the_terminals(self, monkeypatch) -> None:
        """Textual copies through the terminal alone (OSC 52), which Windows Terminal reads and the classic console
        window does not: there Ctrl+Y said "Copied" and copied nothing. On Windows the text goes to Windows' own
        clipboard too, its lines ended as Windows ends them, and the screen says which way it went."""
        from types import SimpleNamespace

        from tui import clipboard

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

        from tui.clipboard import _windows_copy

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
        from tui.options import review_command

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
        from tui import options

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
        from tui.options import baseline_path

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
        from tui.config import RepoPreset, load_tui_config, save_repo_preset

        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
        preset = RepoPreset(base='feature/\U0001f680-launch "q" \\ ü', path=("My Docs", "src/\U0001f680"))
        save_repo_preset(load_tui_config(cfg), "repo \U0001f680", preset)
        assert load_tui_config(cfg).projects["repo \U0001f680"] == preset

    def test_a_path_typed_by_hand_keeps_its_spaces(self) -> None:
        """Read by review: the typed paths were split at every space too, so `My Docs` left out `My` and `Docs`, two
        folders the person never named. A comma or a line parts them now."""
        from tui.options import parse_csv_paths

        assert parse_csv_paths("My Docs, src/api ,\ndocs/reports") == ["My Docs", "src/api", "docs/reports"]

    def test_a_fetch_past_its_timeout_stops_with_every_process_it_started(self) -> None:
        """Reproduced by review: past the timeout git was killed, but its children, ssh or git's https helper, held its
        output open, and the screen waited on them, 6 s against a timeout of 1 s, and on a remote that hangs for
        minutes or for ever. The whole group is stopped now, as the whole tree is on Windows."""
        import os
        import time

        from tui.git_sync import _bounded

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

        from tui.commands import highlighted

        for label in ("Repo: my[service]  (main, clean)", "Repo: x[/old]"):
            assert highlighted(Matcher("old"), label).plain == label

    @pytest.mark.asyncio
    async def test_the_screen_keeps_working_through_an_error_and_a_long_line(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, both reproduced: a config the screen could not write raised inside the review's worker,
        which ended the whole screen with a traceback; and one line of a review's progress past 64 KiB, a provider's
        error body, made reading it raise. The screen says what failed, and reads a line of any length."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from tui import app as screen

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

        from tui.app import AdkReviewTuiApp

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
        from tui.app import AdkReviewTuiApp

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

        from tui.app import AdkReviewTuiApp

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
        from tui.git_sync import fast_forward_local_branch

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

        folder = FRONT_END  # by its path: the screen needs Textual, which the reviewer alone lacks
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

        from tui.app import AdkReviewTuiApp

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


class TestTheConfigReview:
    """A code review of the config's move into the checkout, and the owner's report that the old folder stayed."""

    def test_the_first_run_leaves_no_trace_of_the_old_folder(self, tmp_path: Path, monkeypatch) -> None:
        """Reported by the owner: `adk_review/adk_review/tui/` was still there after the front end moved to `tui/`.
        It held only what git ignores: the old config, the state's folders and Python's cache. The first run moves
        the config, moves each saved file into the state folder, removes Python's old cache and every folder left
        empty, and the old folder is gone; a file it does not know stays, with its folder."""
        from tui import config

        old = tmp_path / "checkout" / "adk_review" / "tui"
        (old / ".state" / "outputs").mkdir(parents=True)
        (old / ".state" / "cache").mkdir()
        (old / "__pycache__").mkdir()
        (old / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\0")
        (old / ".state" / "outputs" / "comment-old.md").write_text("an old comment\n", encoding="utf-8")
        (old / ".tui.toml").write_text('[workspace]\nroot_dirs = ["~/Projects"]\n', encoding="utf-8")
        monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", old / ".tui.toml")
        target = config.ensure_tui_config()
        assert 'root_dirs = ["~/Projects"]' in target.read_text(encoding="utf-8")
        assert not old.exists(), sorted(p.name for p in old.rglob("*"))
        assert (config.STATE_DIR / "outputs" / "comment-old.md").read_text(encoding="utf-8") == "an old comment\n"
        # A folder left with only its empty folders, its config moved by an earlier run, goes too.
        (old / ".state" / "baselines").mkdir(parents=True)
        config.ensure_tui_config()
        assert not old.exists()
        (old).mkdir(parents=True)
        (old / "notes.txt").write_text("mine\n", encoding="utf-8")
        config.ensure_tui_config()
        assert (old / "notes.txt").is_file(), "a file it does not know is never removed"

    def test_an_installed_copy_leaves_the_folders_of_its_environment_alone(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: the first run cleared the old package's folder in an installed copy too, inside the tool's
        own environment, where the code's own comment named a checkout alone."""
        from tui import config

        installed = tmp_path / "lib" / "python3.14" / "site-packages"
        compiled = installed / "adk_review_tui" / "__pycache__" / "app.cpython-314.pyc"
        compiled.parent.mkdir(parents=True)
        compiled.write_bytes(b"\0")
        monkeypatch.setattr(config, "TUI_DIR", installed / "tui")
        monkeypatch.setattr(config, "OLD_PACKAGE_DIR", installed / "adk_review_tui")
        config.ensure_tui_config()
        assert compiled.is_file()

    def test_the_first_run_leaves_no_trace_of_the_packages_old_name(self, tmp_path: Path, monkeypatch) -> None:
        """Reproduced by review on a clone: renamed from `adk_review_tui` to `tui`, the package left
        `tui/adk_review_tui/` in each checkout that had run it. It held Python's cache alone, which git ignores, so git
        never removed it. The first run removes it."""
        from tui import config

        old = tmp_path / "checkout" / "tui" / "adk_review_tui"
        (old / "__pycache__").mkdir(parents=True)
        (old / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"\0")
        monkeypatch.setattr(config, "OLD_PACKAGE_DIR", old)
        config.ensure_tui_config()
        assert not old.exists(), sorted(p.name for p in old.rglob("*"))

    def test_a_checkout_takes_the_config_last_edited_in_the_persons_own_folder(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by review: from bd92a0a the config of a checkout lived in the person's own folder, where they edited
        it; after it moved into the checkout, that file was read no more and named nowhere, its folder of
        repositories and its presets lost. The first run takes the one edited last, the old checkout's or the person's
        own, and leaves the person's own file, which an installed copy reads."""
        import os

        from tui import config

        user = config.USER_CONFIG_PATH
        user.parent.mkdir(parents=True)
        user.write_text('[workspace]\nroot_dirs = ["~/Projects"]\n\n[projects."my-service"]\nbase = "develop"\n')
        target = config.ensure_tui_config()
        assert config.load_tui_config(target).projects["my-service"].base == "develop" and user.is_file()
        target.unlink()
        legacy = tmp_path / "checkout" / ".tui.toml"
        legacy.parent.mkdir()
        legacy.write_text('[workspace]\nroot_dirs = ["~/Old"]\n', encoding="utf-8")
        os.utime(legacy, (1_000_000_000, 1_000_000_000))  # edited before the person's own
        monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", legacy)
        target = config.ensure_tui_config()
        assert '"~/Projects"' in target.read_text(encoding="utf-8") and not legacy.exists()

    def test_an_old_output_dir_inside_the_package_is_dropped_however_it_was_written(
        self, monkeypatch, tmp_path
    ) -> None:
        """Read by review: before bd92a0a a saved config held `output_dir` as the full path into the package, which the
        move kept, and each review's files went on landing in `adk_review/tui/`. Written relative or absolute, on
        macOS or Windows, it is dropped, and the state folder's `outputs` holds."""
        from tui import config

        legacy = tmp_path / "old" / ".tui.toml"
        legacy.parent.mkdir()
        for written in (
            "adk_review/tui/.state/outputs",
            "/Users/me/Projects/adk-review/adk_review/tui/.state/outputs",
            "C:\\\\Users\\\\me\\\\adk-review\\\\adk_review\\\\tui\\\\.state\\\\outputs",
        ):
            legacy.parent.mkdir(exist_ok=True)  # the run before removed it, emptied
            legacy.write_text(f'[workspace]\nroot_dirs = ["~/Projects"]\noutput_dir = "{written}"\n', encoding="utf-8")
            monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", legacy)
            config.DEFAULT_CONFIG_PATH.unlink(missing_ok=True)
            target = config.ensure_tui_config()
            assert config.load_tui_config(target).output_dir == str(config.OUTPUTS_DIR), written

    def test_an_old_config_that_cannot_be_removed_still_starts_the_screen(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: removing the old file after the move could fail, on Windows a read-only file or one another
        program holds, and the start ended in a bare `WinError`, the new config written. It starts, and says nothing:
        the screen names the file it reads."""
        from tui import config

        legacy = tmp_path / "old" / ".tui.toml"
        legacy.parent.mkdir()
        legacy.write_text('[workspace]\nroot_dirs = ["~/Projects"]\n', encoding="utf-8")
        monkeypatch.setattr(config, "LEGACY_CONFIG_PATH", legacy)
        unlink = Path.unlink

        def held(self: Path, *args: object, **kwargs: object) -> None:
            if self == legacy:
                raise PermissionError(13, "The process cannot access the file", str(self))
            unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", held)
        assert config.ensure_tui_config().is_file()

    def test_a_config_it_cannot_read_is_one_sentence_naming_what_to_write(self, tmp_path: Path, capsys) -> None:
        """Read by review, each reproduced: `projects = "x"` ended in a traceback; `[[workspace]]`, `workspace = "x"`
        and `defaults = 3` were left out, the default folder listed in silence; `verify = "no"` turned `--verify` on, a
        string being true; a number out of range was clamped, a word replaced; an empty key passed; a setting before
        any table was called a table; and a bad `projects_dir` was reported as `root_dirs`."""
        from tui import main as tui_main

        cfg = tmp_path / ".tui.toml"
        for body, said in (
            ('projects = "x"\n', "[projects] must be a table"),
            ("projects = [1]\n", "[projects] must be a table"),
            ("[projects]\nmy-service = 1\n", "[projects.my-service] must be a table"),
            ('[[workspace]]\nroot_dirs = ["/nowhere"]\n', "[workspace] must be a table"),
            ('workspace = "x"\n', "[workspace] must be a table"),
            ("defaults = 3\n", "[defaults] must be a table"),
            ('[defaults]\nverify = "no"\n', "[defaults] verify is 'no'. Write true or false"),
            ('[projects."a"]\ncomment = "false"\n', "[projects.a] comment is 'false'. Write true or false"),
            ("[workspace]\nscan_depth = 99\n", "[workspace] scan_depth is 99. Write a whole number from 1 to 5"),
            ('[workspace]\nscan_depth = "deep"\n', "[workspace] scan_depth is 'deep'"),
            ("[workspace]\nfetch_timeout_s = false\n", "[workspace] fetch_timeout_s is False"),
            ('[workspace]\n"" = 1\n', "[workspace] has no setting ''"),
            ("scan_dept = 2\n", "the config has no setting 'scan_dept'"),
            ("[workspace]\nprojects_dir = 3\n", "projects_dir lists the folders"),
        ):
            cfg.write_text(body, encoding="utf-8")
            assert tui_main(["--config", str(cfg)]) == 2, body
            err = capsys.readouterr().err
            assert said in err and "Traceback" not in err, (body, err)

    def test_fetch_on_select_is_a_switch_on_by_default_that_saving_a_preset_keeps(self, tmp_path: Path) -> None:
        """`[workspace] fetch_on_select` says whether picking a repository fetches its new branches: on unless the
        config says `false`, and anything but `true` or `false` is said. Saving a review's preset rewrites the config,
        and keeps it: read before, the preset's save built the config field by field, so a new setting went back to
        its default at the next review."""
        from tui.config import RepoPreset, load_tui_config, save_repo_preset

        cfg = tmp_path / ".tui.toml"
        workspace = f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\n'
        cfg.write_text(workspace, encoding="utf-8")
        assert load_tui_config(cfg).fetch_on_select is True
        cfg.write_text(workspace + 'fetch_on_select = "no"\n', encoding="utf-8")
        with pytest.raises(ValueError, match=r"fetch_on_select is 'no'\. Write true or false"):
            load_tui_config(cfg)
        cfg.write_text(workspace + "fetch_on_select = false\n", encoding="utf-8")
        save_repo_preset(load_tui_config(cfg), "svc", RepoPreset(base="develop"))
        assert load_tui_config(cfg).fetch_on_select is False, cfg.read_text(encoding="utf-8")

    def test_a_config_saved_with_a_byte_order_mark_is_read(self, tmp_path: Path) -> None:
        """Read by review: PowerShell 5's `Out-File -Encoding utf8` writes a BOM first, and the config could not be
        read: `Invalid statement`, with no hint. It is read as the reviewer reads its own, `utf-8-sig`."""
        from tui.config import load_tui_config

        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8-sig")
        assert load_tui_config(cfg).root_dirs[0].path == str(tmp_path.resolve())

    @pytest.mark.asyncio
    async def test_the_screen_shows_the_config_and_the_folders_at_80_columns(self, tmp_path: Path) -> None:
        """Read by review, measured: on one line of one row, the config's path cut the folders off at 80 and 120
        columns, the part the owner asked to see. Each has a line of its own."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / "a" / "rather" / "long" / "folder" / "for" / "the" / "config" / ".tui.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            line = app.query_one("#config-line")
            drawn = ["".join(segment.text for segment in line.render_line(y)) for y in range(line.size.height)]
            assert any(row.lstrip().startswith("Folders of repositories:") for row in drawn), drawn
            assert any("Config:" in row for row in drawn), drawn
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()


class TestThePickAndTheDockAsReviewed:
    """A code review of the fetch a pick starts and of the dock, each finding reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_the_fetch_a_pick_starts_prunes_nothing_where_the_persons_config_prunes(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: with `fetch.prune` set, as the guide advises for the push hook, the pick's
        fetch, which only left `--prune` out, pruned a remote's branch the person had chosen. It says `--no-prune`."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "config", "fetch.prune", "true")
        _git(local, "update-ref", "refs/remotes/origin/gone", "HEAD")
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await _pick(app, pilot)
            names = [b.name for b in app.branches]
            assert "origin/feat-remote" in names and "origin/gone" in names, names

    @pytest.mark.asyncio
    async def test_picking_the_repository_again_keeps_the_branches_chosen(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: picked again, the way to fetch its new branches, the repository's lists were
        drawn first with no fetch, which set the target back to its default, and the fetch's listing kept that."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        for name in ("release", "develop"):
            _git(local, "update-ref", f"refs/remotes/origin/{name}", "HEAD")
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            default = app.selected_base
            chosen = next(n for n in ("origin/release", "origin/develop") if n != default)  # never the default
            app._sync_chosen_branch("base", chosen)
            await app.workers.wait_for_complete()
            assert app.selected_base == chosen
            await _pick(app, pilot)
            assert app.selected_base == chosen, (app.selected_base, default)

    @pytest.mark.asyncio
    async def test_a_pick_lists_the_branches_once_after_its_fetch_and_runs_no_review_before(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by review: the pick listed the branches, fetched in the background, and listed them again. The second
        listing cleared a review's findings that ended in between, moved the highlight a person was moving, and came
        after a branch chosen meanwhile was checked against the remote as it stood before the fetch. The pick now
        fetches first, as F5 does, and lists once; no review starts while the branches load."""
        pytest.importorskip("textual")
        import threading

        from tui import app as screen
        from tui.git_sync import FetchResult

        _init_repo_with_remote(tmp_path)
        released, listed, ran = threading.Event(), [], []
        real_list = screen.list_branches

        def held_fetch(*_args: object, **_options: object) -> FetchResult:
            released.wait(10)
            return FetchResult(ok=True, message="Fetched")

        monkeypatch.setattr(screen, "list_branches", lambda path: listed.append(path) or real_list(path))
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            monkeypatch.setattr(screen, "fetch_remote", held_fetch)
            monkeypatch.setattr(screen.AdkReviewTuiApp, "_execute_review", lambda _app, *, dry_run: ran.append(dry_run))
            listed.clear()
            from textual.widgets import OptionList

            repo = app.query_one("#repo-list", OptionList)
            repo.highlighted = 0
            repo.action_select()
            await pilot.pause()
            assert listed == [], "nothing listed before the fetch ends"
            said: list[str] = []
            app.notify = lambda message, **_options: said.append(str(message))
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert any("loading" in line for line in said), "a review asked for meanwhile says why it waits"
            released.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(listed) == 1 and ran == [], (listed, ran)

    def test_two_fetches_of_one_repository_never_run_at_once(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, and reproduced: F5 pressed during a pick's fetch started a second fetch beside the first,
        which no cancel stops, and the two raced for the repository's ref locks. One waits for the other now."""
        import threading
        import time

        from tui import git_sync

        _remote, local = _init_repo_with_remote(tmp_path)
        spans: list[tuple[float, float]] = []

        def slow(_repo: str, args: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            start = time.monotonic()
            time.sleep(0.3)
            spans.append((start, time.monotonic()))
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(git_sync, "_run_git", slow)
        fetches = [
            threading.Thread(target=git_sync.fetch_remote, args=(str(local),), kwargs={"prune": prune})
            for prune in (False, True)  # F5's prune is no answer a pick's fetch gives: it runs, after it
        ]
        for one in fetches:
            one.start()
            time.sleep(0.05)  # the pick's first, as F5 pressed during it
        for one in fetches:
            one.join(10)
        (_first, first_end), (second_start, _second) = sorted(spans)
        assert second_start >= first_end, spans

    def test_git_the_screen_runs_has_no_console_to_prompt_on_windows(self, monkeypatch) -> None:
        """Read by review: on Windows the fetch ran in the screen's own console, so ssh could ask there for a host key
        or a passphrase, over the screen and racing it for the keys, until the timeout. Now every pick starts one. Git
        runs with no console there, as it runs in a session of its own elsewhere, and so finds none to prompt on."""
        from tui import git_sync

        seen: dict[str, object] = {}

        class Started:
            pid, returncode = 1, 0

            def __init__(self, _argv: list[str], **options: object) -> None:
                seen.clear()
                seen.update(options)

            def __enter__(self) -> Started:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def communicate(self, **_options: object) -> tuple[str, str]:
                return "", ""

        monkeypatch.setattr(git_sync.subprocess, "Popen", Started)
        monkeypatch.setattr(git_sync.sys, "platform", "win32")
        git_sync._bounded(["git", "fetch"], {}, 5)
        assert int(seen.get("creationflags") or 0) & 0x08000000 and not seen["start_new_session"], seen
        monkeypatch.setattr(git_sync.sys, "platform", "darwin")
        git_sync._bounded(["git", "fetch"], {}, 5)
        assert seen["start_new_session"] and not seen.get("creationflags"), seen

    @pytest.mark.asyncio
    async def test_the_dock_fits_the_new_width_after_a_resize(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: the resize handler ran before Textual stored the new size, so the chips
        were cut for the old width, and after a shrink from 160 to 100 columns two ended off the screen."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(160, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.resize_terminal(100, 40)
            await pilot.pause()
            await pilot.pause()
            chips = [chip for chip in app.query(".dock-chip") if chip.display]
            assert chips and all(chip.region.right <= 100 for chip in chips), [(str(c.label), c.region) for c in chips]

    @pytest.mark.asyncio
    async def test_an_open_panel_names_its_whole_title_on_hover(self, tmp_path: Path) -> None:
        """Read by review: each panel's tooltip held its whole title, which a narrow box cuts; the dock set one on its
        chips alone, so an open panel lost it."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            head = app.query_one("#head-box")
            assert str(head.tooltip or "").startswith("2. Branch Under Review ["), head.tooltip

    @pytest.mark.asyncio
    async def test_seven_chips_fit_80_columns_and_a_wide_character_counts_as_two(self, tmp_path: Path) -> None:
        """Read by review: with two folders of repositories, seven chips at 80 columns ran past the screen, the names
        and choices dropped still too long; and a character a terminal draws two columns wide was counted as one. The
        chips are measured in the terminal's columns, and keep only their numbers where nothing more fits."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        wide = tmp_path / "workspace" / "\u4ed3\u5e93-\u670d\u52a1-\u6d4b\u8bd5-\u4ed3\u5e93"
        wide.mkdir(parents=True)
        _git(wide, "init", "-b", "main")
        (wide / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        _git(wide, "add", ".")
        _git(wide, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "base")
        (tmp_path / "other").mkdir()
        cfg = _screen_config(tmp_path)
        both = cfg.read_text(encoding="utf-8").replace('"]', f'", "{(tmp_path / "other").as_posix()}"]', 1)
        cfg.write_text(both, encoding="utf-8")
        for size, roots in (((80, 40), 2), ((100, 40), 1)):
            if roots == 1:
                _screen_config(tmp_path)
            app = AdkReviewTuiApp(config_path=cfg)
            async with app.run_test(size=size) as pilot:
                await pilot.pause(0.3)
                await app.workers.wait_for_complete()
                await pilot.press("ctrl+t")
                await pilot.pause()
                chips = [chip for chip in app.query(".dock-chip") if chip.display]
                assert len(chips) == 5 + roots, [str(c.label) for c in chips]
                assert all(chip.region.right <= size[0] for chip in chips), [(str(c.label), c.region) for c in chips]

    def test_the_dock_keeps_only_the_numbers_where_nothing_more_fits(self) -> None:
        """Read by review: seven chips with long choices needed about 85 columns in the shortest form there was, so at
        80 the last ran off the screen. Where no choice fits, each chip keeps its step's number alone."""
        from rich.cells import cell_len

        from tui.app import DOCK_GAP, Panel, _dock_labels

        docked = [Panel(f"#p{n}", n, "Title", "Name", "a-choice-that-runs-on-and-on") for n in range(1, 8)]
        for width in (80, 60):
            labels = _dock_labels(docked, width)
            assert 2 + sum(cell_len(label) + DOCK_GAP for label in labels) <= width + 2, (width, labels)
        assert _dock_labels(docked, 60) == [f"{chr(0x25B6)} {n}" for n in range(1, 8)]
        named = (("Repo", "payments-service"), ("Review", "main"), ("Target", "develop"), ("Folders", "all"))
        named += (("Profile", "default, fail on blocker"), ("Outputs", "comment"))
        usual = [Panel(f"#p{n}", n, "Title", name, choice) for n, (name, choice) in enumerate(named, 1)]
        at_140 = _dock_labels(usual, 140)
        assert at_140[0] == f"{chr(0x25B6)} 1 Repo: payments-{chr(0x2026)}", "at 140 columns the names stay"
        assert at_140[4] == f"{chr(0x25B6)} 5 Profile: default, {chr(0x2026)}", at_140
        wide = [Panel("#p1", 1, "Title", "Repo", "\u4ed3\u5e93" * 10)]
        assert cell_len(_dock_labels(wide, 30)[0]) + DOCK_GAP + 2 <= 30 + 2, "a wide character counts as two"

    @pytest.mark.asyncio
    async def test_one_ctrl_t_lays_the_dock_out_at_most_once_a_panel(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, and measured: one Ctrl+T laid the titles and the dock out 13 times, each reading every
        choice again: an older handler took the same messages, and the actions laid it out once more themselves."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        read: list[int] = []
        real = AdkReviewTuiApp.current_selection
        monkeypatch.setattr(AdkReviewTuiApp, "current_selection", lambda self, **kw: read.append(1) or real(self, **kw))
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            read.clear()
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.pause()
            assert len(read) <= 6, len(read)


def _screen_config(tmp_path: Path, *extra: str) -> Path:
    """The front end's config listing the fixture's `workspace` folder, each of `extra` a line of `[workspace]`."""
    cfg = tmp_path / ".tui.toml"
    root = (tmp_path / "workspace").as_posix()
    cfg.write_text("\n".join([f'[workspace]\nroot_dirs = ["{root}"]', *extra]) + "\n", encoding="utf-8")
    return cfg


async def _pick(app, pilot) -> None:
    """The person's pick of the first repository listed, and what it starts."""
    from textual.widgets import OptionList

    repo = app.query_one("#repo-list", OptionList)
    repo.highlighted = 0
    repo.action_select()
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _repository(path: Path, branches: tuple[str, ...], checked_out: str) -> Path:
    """A repository at `path` holding `branches`, each at one commit, with `checked_out` checked out."""
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    (path / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "base")
    for name in branches:
        if name != "main":
            _git(path, "branch", name)
    _git(path, "switch", checked_out)
    return path


class TestTheSavedReport:
    """The report a target's next review reads with `--previous`."""

    def test_a_saved_report_moves_on_where_what_marks_findings_changed(self, tmp_path: Path) -> None:
        """Read by review: the saved report moved on only where its findings' fingerprints changed, while `--previous`
        reads its checks, holes, scope and unkept findings too. A review under another profile that found the same left
        the old checks in place, and a guideline's finding the next run read as not compared. It moves on where any of
        those changed, and stays where none did, so a review served from the cache stays one."""
        import json

        from tui.options import promote_baseline

        def report(path: Path, checks: list[str], words: str = "t") -> Path:
            found = [{"fingerprint": "a" * 16, "check": "rule.lint", "file": "a.py", "title": words}]
            path.write_text(json.dumps({"schema": 2, "findings": found, "checks": checks}), encoding="utf-8")
            return path

        saved = report(tmp_path / "saved.json", ["rule.lint"])
        promote_baseline({"json": report(tmp_path / "same.json", ["rule.lint"], "other words"), "baseline": saved}, 1)
        assert json.loads(saved.read_text(encoding="utf-8"))["findings"][0]["title"] == "t", "kept: nothing changed"
        wider = report(tmp_path / "wider.json", ["rule.lint", "guideline.security"])
        promote_baseline({"json": wider, "baseline": saved}, 1)
        assert json.loads(saved.read_text(encoding="utf-8"))["checks"] == ["rule.lint", "guideline.security"]


class TestThePickAsReviewedAgain:
    """A second code review of the pick's fetch, each finding reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_picking_another_repository_takes_its_own_branches_and_none_of_the_last(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by review, and reproduced: every pick fetched, and a fetch kept the branches chosen, so picking another
        repository kept the last one's head and base wherever it had branches of the same names, its own checked-out
        branch ignored. And while its fetch ran, the palette offered the last repository's branches."""
        pytest.importorskip("textual")
        import threading

        from textual.widgets import OptionList

        from tui import app as screen
        from tui.git_sync import FetchResult

        _repository(tmp_path / "workspace" / "alpha", ("main", "dev"), "dev")
        _repository(tmp_path / "workspace" / "beta", ("main", "dev", "feat"), "feat")
        released = threading.Event()

        def held(*_args: object, **_options: object) -> FetchResult:
            released.wait(10)
            return FetchResult(ok=True, message="Fetched")

        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            assert app.selected_project.name == "alpha" and app.selected_head == "dev", app.selected_head
            monkeypatch.setattr(screen, "fetch_remote", held)
            repo = app.query_one("#repo-list", OptionList)
            repo.highlighted = 1
            repo.action_select()
            await pilot.pause()
            assert app.branches == [], "no branch of the last repository is offered while the fetch runs"
            released.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert (app.selected_project.name, app.selected_head) == ("beta", "feat"), app.selected_head

    @pytest.mark.asyncio
    async def test_picking_the_same_repository_again_keeps_the_options_set(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: picked again, as a fetch of its new branches, the repository's saved preset
        set every option back, a switch turned on among them, and the folders ticked."""
        pytest.importorskip("textual")
        from textual.widgets import Switch

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            verify = app.query_one("#sw-verify", Switch)
            verify.value = not verify.value
            chosen = verify.value
            await _pick(app, pilot)
            assert verify.value is chosen, "the switch kept as set"

    @pytest.mark.asyncio
    async def test_no_review_starts_while_a_picked_repositorys_folders_load(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: the wait for a review ended as the branches were drawn, while the folders still loaded with
        the last repository's ticks, so a review started then ran with another repository's `--path`."""
        pytest.importorskip("textual")
        import asyncio

        from textual.widgets import OptionList

        from tui import app as screen

        _init_repo_with_remote(tmp_path)
        ran: list[bool] = []
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            gate, entered = asyncio.Event(), asyncio.Event()
            real = screen.AdkReviewTuiApp._refresh_folders

            async def held(self: screen.AdkReviewTuiApp) -> None:
                entered.set()
                await gate.wait()
                await real(self)

            monkeypatch.setattr(screen.AdkReviewTuiApp, "_refresh_folders", held)
            monkeypatch.setattr(screen.AdkReviewTuiApp, "_execute_review", lambda _app, *, dry_run: ran.append(dry_run))
            repo = app.query_one("#repo-list", OptionList)
            repo.highlighted = 0
            repo.action_select()
            for _ in range(80):  # until the branches are drawn and the folders wait
                await pilot.pause(0.05)
                if entered.is_set():
                    break
            assert entered.is_set(), "the folders load after the branches"
            await pilot.press("ctrl+r")
            await pilot.pause()
            gate.set()
            await app.workers.wait_for_complete()
            assert ran == [], ran

    def test_a_second_fetch_while_one_runs_takes_its_answer(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: a cancelled pick's fetch runs on in its thread, and each later fetch of the repository waited
        for it and then ran its own, so picks to an unreachable remote queued up one timeout each, and quitting waited
        for all of them. A fetch asked for while one runs takes that one's answer; F5's prune, which a pick's fetch
        does not do, runs after it. A linked worktree shares its repository's refs, and so its fetches."""
        import threading
        import time
        from dataclasses import dataclass, field

        from tui import git_sync

        _remote, local = _init_repo_with_remote(tmp_path)
        worktree = tmp_path / "worktree"
        _git(local, "worktree", "add", str(worktree), "-b", "elsewhere")
        ran: list[list[str]] = []
        entered, release, parked = threading.Event(), threading.Event(), threading.Condition()
        waiting = [0]

        def slow(_repo: str, args: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            ran.append(args)
            entered.set()
            release.wait(10)
            return subprocess.CompletedProcess(args, 0, "", "")

        class Counted(threading.Event):
            """An end the test sees each fetch wait on, so the running one is held until every other waits."""

            def wait(self, timeout: float | None = None) -> bool:
                with parked:
                    waiting[0] += 1
                    parked.notify_all()
                return super().wait(timeout)

        @dataclass
        class Watched(git_sync._Running):
            ended: threading.Event = field(default_factory=Counted)

        def held(count: int) -> None:
            with parked:
                assert parked.wait_for(lambda: waiting[0] == count, 10), waiting

        monkeypatch.setattr(git_sync, "_run_git", slow)
        monkeypatch.setattr(git_sync, "_Running", Watched)
        # Read by CI: on a slow runner one fetch's own git calls outlasted the first fetch, timed by sleeps, and it ran
        # its own. Each fetch is now held by events, and the worktree's lookup is slowed as that runner's was.
        keyed = git_sync._fetch_key
        monkeypatch.setattr(
            git_sync, "_fetch_key", lambda repo: (time.sleep(0.3 * ("worktree" in repo)), keyed(repo))[1]
        )
        answers: list[object] = []
        fetches = [
            threading.Thread(target=lambda path=path: answers.append(git_sync.fetch_remote(str(path))))
            for path in (local, local, worktree)
        ]
        fetches[0].start()
        assert entered.wait(10)
        for one in fetches[1:]:
            one.start()
        held(2)
        release.set()
        for one in fetches:
            one.join(10)
        assert len(ran) == 1 and len(answers) == 3, ran
        ran.clear()
        entered.clear()
        release.clear()
        waiting[0] = 0
        both = [threading.Thread(target=git_sync.fetch_remote, args=(str(local),), kwargs={"prune": p}) for p in (0, 1)]
        both[0].start()
        assert entered.wait(10)
        both[1].start()
        held(1)
        release.set()
        for one in both:
            one.join(10)
        assert ["--prune" in args for args in ran] == [False, True], ran

    def test_a_fetch_prunes_only_where_asked_and_starts_no_maintenance(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: `fetch_remote` pruned unless told not to, so a caller that left the flag out undid the
        pick's promise; and a fetch can start git's own maintenance, which a timeout then stops halfway, leaving its
        files behind. A fetch prunes only where asked, and starts no maintenance."""
        from tui import git_sync

        _remote, local = _init_repo_with_remote(tmp_path)
        seen: list[list[str]] = []

        def recorded(_repo: str, args: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            seen.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(git_sync, "_run_git", recorded)
        git_sync.fetch_remote(str(local))
        git_sync.fetch_remote(str(local), prune=True)
        assert [("--prune" in a, "--no-prune" in a, "--no-auto-maintenance" in a) for a in seen] == [
            (False, True, True),
            (True, False, True),
        ], seen


async def _wait_for_branches(app, pilot) -> None:
    """Until the first repository's branches are listed, and its folders too: a review waits for both. Seen on
    Windows in CI: a test that pressed Ctrl+D once the branches showed met the wait for the folders."""
    await pilot.pause()
    for _ in range(200):
        await pilot.pause(0.05)
        if app.branches and app._listing_for is None:
            break
