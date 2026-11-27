"""Tests for the terminal front end, `tui/tui/`: its config, its git reads, its options and its screen.

Each test imports what it reads where it runs, as the suite's other files do: `load_agent` loads the package afresh
for the tests before these, and a name bound when this file was imported is the old package's. Measured: after one
such test, three of these failed, the catalog reading no rule as a rule and a patch landing on another module."""

from __future__ import annotations

import asyncio
import contextlib
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
    # Nor the person's own clipboard: a screen test that copies runs pbcopy on a Mac, here a program no folder holds.
    monkeypatch.setattr(importlib.import_module("tui.clipboard"), "PBCOPY", str(state / "no-pbcopy"))
    # Nor the person's own Azure DevOps login, nor its host: the open dialog lists a project's pull requests as it
    # opens, and a test that clicked it asked the real `az` for a token and called dev.azure.com. A test that reads
    # Azure DevOps gives the screen a FakeToken and the fake's address.
    screen = importlib.import_module("tui.app")
    client = importlib.import_module("tui.azure_client")

    class NoLogin:
        def __call__(self) -> str:
            raise client.AzureError("A test asks no real az for a token. Give the screen a FakeToken.")

        def forget(self) -> None:
            pass

    monkeypatch.setattr(screen, "AzLogin", NoLogin)
    monkeypatch.setattr(screen, "ORIGIN", "http://127.0.0.1:9/{}")


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


#: A folder whose name goes past ASCII, `cekirdek` with a cedilla: git quotes such a path where it prints it whole.
FOLDER_PAST_ASCII = chr(0xE7) + "ekirdek"


def _branches_the_banner_misread(local: Path) -> None:
    """Three picks the banner once read apart from the review, made in `local` from `main`: `past-ascii` adds a file
    under `FOLDER_PAST_ASCII`; `unrelated` shares no history with `main`; and `crossed-base` and `crossed-head` each
    merged the other's first commit, so the two have two merge bases."""

    def commit(name: str) -> None:
        target = local / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8", newline="\n")
        _git(local, "add", "--", name)
        _git(local, "commit", "-q", "-m", f"add {name}")

    _git(local, "switch", "-q", "-c", "past-ascii", "main")
    commit(f"{FOLDER_PAST_ASCII}/b.py")
    _git(local, "switch", "-q", "--orphan", "unrelated")
    commit("alone.py")
    _git(local, "switch", "-q", "-c", "crossed-base", "main")
    commit("src/a.py")
    _git(local, "switch", "-q", "-c", "crossed-head", "main")
    commit("src/b.py")
    _git(local, "merge", "-q", "--no-ff", "--no-edit", "crossed-base")
    _git(local, "switch", "-q", "crossed-base")
    _git(local, "merge", "-q", "--no-ff", "--no-edit", "crossed-head~1")
    _git(local, "switch", "-q", "main")


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
            discover_projects,
            fast_forward_local_branch,
            fetch_remote,
            inspect_branch_sync,
            is_dirty,
            list_branches,
            preferred_base_branch,
            read_change,
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

        # 4. The change between `main` and `feat-remote`, and in `--staged` mode
        change = read_change(str(local), "main", "origin/feat-remote")
        folder_names = [f for f, _label in change.folders]
        assert change.statuses == ["A"] and "tests" in folder_names and "src" in folder_names
        assert change.commits == 1 and change.refused == "", change
        assert preferred_base_branch(str(local), list_branches(str(local)), "origin/feat-remote") == "main"

        (local / "src" / "staged_new.py").write_text("y = 1\n", encoding="utf-8")
        _git(local, "add", "src/staged_new.py")
        staged = read_change(str(local), "main", "feat-local", mode="staged")
        assert staged.statuses == ["A"] and staged.commits is None, staged
        assert ("src", "src  (1 changed)") in staged.folders

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
        from tui.git_sync import discover_projects, is_dirty, list_branches, read_change

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
        read_change(str(local), "origin/main", "main")
        read_change(str(local), None, "main", mode="staged")
        assert not ran.exists(), "no read started the program"
        assert (index.read_bytes(), index.stat().st_mtime_ns) == held, "and none rewrote the index"

    def test_a_tag_named_as_a_branch_never_stands_in_for_it(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: with a tag `release` beside the branch `release`, the screen read the tag
        where it meant the branch. Picking `origin/release` said the branch was behind where it held a commit of its
        own, and the answer moved it to the remote's, the commit left in the reflog alone; and the branch itself, which
        git lists as `heads/release`, was moved as a new branch of that name. Each is read as the branch's own ref."""
        from tui.git_sync import fast_forward_local_branch, inspect_branch_sync, list_branches

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "-q")
        _git(local, "update-ref", "refs/remotes/origin/release", "origin/main")
        _git(local, "tag", "release", "main")
        _git(local, "switch", "-q", "-c", "release", "main")
        _git(local, "branch", "-q", "-u", "origin/release")
        (local / "mine.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        _git(local, "add", "mine.py")
        _git(local, "commit", "-q", "-m", "a commit of its own")
        _git(local, "switch", "-q", "main")
        by = {branch.name: branch for branch in list_branches(str(local))}
        for picked in ("origin/release", "heads/release"):
            status = inspect_branch_sync(str(local), by[picked])
            assert (status.kind, status.ahead, status.behind) == ("diverged", 1, 1), (picked, status)
            assert status.local_branch == "heads/release", "the name a review reads the branch by, and no tag"

        _git(local, "branch", "-f", "release", "main")  # no commit of its own now: behind its remote
        for picked in ("origin/release", "heads/release"):
            status = inspect_branch_sync(str(local), by[picked])
            assert status.kind == "behind", (picked, status)
            done = fast_forward_local_branch(str(local), status.local_branch, status.upstream_branch)
            assert done.ok and done.resolved_ref == "heads/release", done
            assert _git(local, "rev-parse", "refs/heads/release") == _git(local, "rev-parse", "origin/release")
            assert _git(local, "for-each-ref", "--format=%(refname)", "refs/heads/heads") == "", "no branch made"
            _git(local, "branch", "-f", "release", "main")

    def test_the_target_origin_head_names_is_preferred_where_no_main_is_held(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: with no local `main` or `master`, the reviewer's own default target is
        the branch `origin/HEAD` names, by its full name, which the screen's list holds as `origin/main`; the screen
        then fell back to `develop`. It takes the default by the name its list shows."""
        from tui.git_sync import list_branches, preferred_base_branch

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "switch", "-q", "-c", "feat")
        _git(local, "branch", "-q", "develop")
        _git(local, "branch", "-q", "-D", "main")
        assert preferred_base_branch(str(local), list_branches(str(local)), "feat") == "origin/main"

    @pytest.mark.asyncio
    async def test_the_banner_tells_the_commits_and_each_kind_of_change_before_a_review_runs(
        self, tmp_path: Path
    ) -> None:
        """Asked by the owner: before a review runs, the screen says how many commits and files a review would read,
        and how many of them the change added, modified and deleted, from what git answers it already."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "-q")  # `main` one commit behind its remote, which `feat-remote` holds, and its own
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base, app.selected_head = "main", "origin/feat-remote"
            await app._refresh_folders()
            banner = str(app.query_one("#status-banner", Static).render())
        said = "(main -> origin/feat-remote): 2 commit(s), 2 changed file(s) (1 added, 1 modified) across "
        assert said in banner, banner

    @pytest.mark.asyncio
    async def test_the_banner_reads_the_change_as_the_review_does(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: the banner asked git with calls of its own, and three picks told what no
        review reads. A folder past ASCII came back quoted in octal, as git prints a path it is not told to keep whole;
        branches that share no history read as 0 files, where a review stops before it reads; and merges crossed both
        ways counted the commits past the target, where a review counts those past the merge base. The banner now
        reads the change through the review's own collector."""
        pytest.importorskip("textual")
        from textual.widgets import SelectionList, Static

        from adk_review.collect.git import preflight
        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _branches_the_banner_misread(local)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()

            async def banner(base: str, head: str) -> str:
                app.selected_base, app.selected_head = base, head
                await app._refresh_folders()
                return str(app.query_one("#status-banner", Static).render())

            past_ascii = await banner("main", "past-ascii")
            boxes = app.query_one("#path-list", SelectionList)
            folders = [boxes.get_option_at_index(at).value for at in range(boxes.option_count)]
            unrelated = await banner("main", "unrelated")
            crossed = await banner("crossed-base", "crossed-head")
        assert ": 1 commit(s), 1 changed file(s) (1 added) across " in past_ascii, past_ascii
        assert FOLDER_PAST_ASCII in folders and not any('"' in str(folder) for folder in folders), folders
        assert "'main' and 'unrelated' share no history: no change to review" in unrelated, unrelated
        review = preflight(str(local), "crossed-base", "crossed-head")["commits_ahead"]
        assert review != int(_git(local, "rev-list", "--count", "crossed-base..crossed-head")), "the merges cross"
        assert f": {review} commit(s), " in crossed, crossed


class TestOptionsAndCliBuilder:
    """Profile discovery and CLI flag construction with mutual-exclusivity guards."""

    def test_profiles_and_build_review_argv_respect_mutual_exclusivity(self, tmp_path: Path) -> None:
        from tui.options import (
            ReviewSelection,
            baseline_path,
            build_review_argv,
            load_profile_options,
            make_output_dir,
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
        assert not (tmp_path / "outputs").exists(), "building the command writes nothing"
        make_output_dir(sel, tmp_path / ".tui.toml")  # as a review does when it starts

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
            from textual.widgets import Input, OptionList, Select, SelectionList, Static, Switch

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
            # Its handler and the folders' refresh it starts run after it, as each tick's below does: waited for.
            await _until(pilot, lambda: app.query_one("#base-list", OptionList).disabled, "staged mode applied")
            # Each worker finished, a cancelled one among them: read by review, `wait_for_complete` raises for a refresh
            # the mode's handler cancelled while the list still holds it.
            await _until(pilot, lambda: all(worker.is_finished for worker in app.workers), "the folders' refresh")
            assert app.query_one("#head-list", OptionList).disabled is True

            # Selecting the same folder in #exclude-list deselects it from #path-list
            path_list = app.query_one("#path-list", SelectionList)
            excl_list = app.query_one("#exclude-list", SelectionList)
            if path_list.option_count:
                # Each tick waits for its handler, which acts after it: read on the Windows runner, a fixed 0.05 s
                # passed before the first ran, the include list's handler ran last and undid the exclude's tick.
                preview = app.query_one("#cli-preview", Static)
                target_folder = path_list.get_option_at_index(0).value
                path_list.select(target_folder)
                await _until(pilot, lambda: f"--path {target_folder}" in str(preview.render()), "the include tick")
                excl_list.select(target_folder)
                await _until(pilot, lambda: target_folder not in path_list.selected, "the include tick undone")
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
            load_report,
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
                    # Not found again, its lines unchanged at the head: never shown as resolved.
                    "unchanged": [
                        {
                            "check": "guideline.correctness",
                            "file": "e.py",
                            "line": 2,
                            "severity": "major",
                            "title": "Passed over",
                            "evidence": "total = int(price)",
                            "suggestion": "Round it.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        rows = load_report(report)[0]
        assert [r.tier for r in rows] == ["NEW", "Active", "Unchanged", "Old", "Resolved"]
        # Read by an outside audit: its detail read as an active finding's, with no word on why its lines open nothing.
        passed = next(r for r in rows if r.tier == "Unchanged")
        detail = format_finding_detail(passed).lower()
        assert "not found again" in detail and "earlier review" in detail, detail
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
        explained = format_finding_detail(load_report(report)[0][0])
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
        the focus. Ctrl+T folds the sections now, and each action key goes first wherever the focus is. Stop goes
        first while a review runs, and only then: with none running it stopped nothing and took the cut from every
        field, so the key is the field's own there."""
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
            assert ran == [True, False], f"no review runs, so there is none to stop: {ran}"
            app._reviewing = True
            await pilot.press("ctrl+x")
            assert ran == [True, False, "stop"], f"a review runs: Stop goes first, in a text field too: {ran}"
            app._reviewing = False
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
            assert ran == [] and isinstance(app.screen, SyncBranchModal), ran
            app._reviewing = True
            await pilot.press("ctrl+r", "ctrl+d", "ctrl+t", "ctrl+x")
            await pilot.pause()
            assert ran == ["stop"] and isinstance(app.screen, SyncBranchModal), ran
            app._reviewing = False
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
        monkeypatch.setattr(app, "_log_progress", lambda line, **_how: said.append(line))
        monkeypatch.setattr(app, "_set_banner", lambda _line, **_how: None)
        child = "import sys; sys.stderr.write('feat/f\\u00fcnf \\u2014 done\\n'); print('report \\u2192 ok')"
        code, out = await app._stream_subprocess([sys.executable, "-c", child])
        assert (code, said, out) == (0, ["feat/f\u00fcnf \u2014 done"], "report \u2192 ok\n"), (code, said, out)

    @pytest.mark.asyncio
    async def test_quitting_while_a_scan_runs_ends_cleanly(self, tmp_path: Path, monkeypatch) -> None:
        """Seen on Windows in CI, where git is slower: the screen closed while a scan of the repositories still ran, and
        the scan's last step set `loading` on a list whose screen was gone, `NoScreen`, raised as `WorkerFailed`. A
        person quitting during a scan met the same. Reproduced here with a scan that ends only once the screen
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slow", ["list_branches", "read_change", "is_file", "load_report"])
    async def test_a_listing_that_ends_as_the_screen_closes_draws_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow: str
    ) -> None:
        """Read on the Windows runner, in two of main's four runs and in a branch's: a test left its screen while git
        still read the repository's branches. Textual's test harness takes the widgets away before it cancels the
        app's workers, so the listing came back to a screen with no branch list, `NoMatches`, raised as `WorkerFailed`.
        A person's quit cancels the workers first, as a probe showed, and met none of it. Reproduced here with a
        reading that ends between the two: the branches', the folders', and the saved report's."""
        pytest.importorskip("textual")
        import asyncio

        from tui import app as screen

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        reading, widgets_gone = asyncio.Event(), asyncio.Event()
        in_a_thread = asyncio.to_thread

        async def held(call: object, /, *args: object, **kwargs: object) -> object:
            read = await in_a_thread(call, *args, **kwargs)
            if getattr(call, "__name__", "") == slow:
                reading.set()
                await widgets_gone.wait()
            return read

        monkeypatch.setattr(screen.asyncio, "to_thread", held)
        if slow == "load_report":  # read by a code review: the saved report's own read, after its file was found
            saved = tmp_path / "saved.json"
            saved.write_text('{"schema": 2, "findings": []}', encoding="utf-8")
            monkeypatch.setattr(screen, "baseline_path", lambda *_args: saved)
        app = screen.AdkReviewTuiApp(config_path=cfg)
        close_the_screens = app._close_all

        async def close_and_let_the_listing_end() -> None:
            await close_the_screens()
            widgets_gone.set()
            listings = [worker.wait() for worker in app.workers if worker.group == "git_load"]
            await asyncio.wait_for(asyncio.gather(*listings, return_exceptions=True), 10)

        monkeypatch.setattr(app, "_close_all", close_and_let_the_listing_end)
        async with app.run_test(size=(140, 45)):
            await asyncio.wait_for(reading.wait(), 10)

    @pytest.mark.asyncio
    async def test_a_listing_that_ends_behind_a_dialog_is_drawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other side of the test above: its check for a screen that closed must not take an open dialog for one.
        A listing that ends while a dialog is up is drawn behind it, the branches, the folders and the banner's count
        of changed files, which is written last."""
        pytest.importorskip("textual")
        import asyncio

        from textual.widgets import OptionList, Static

        from tui import app as screen
        from tui.modals import ExplainCheckModal

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        reading, dialog_up = asyncio.Event(), asyncio.Event()
        in_a_thread = asyncio.to_thread

        async def held(call: object, /, *args: object, **kwargs: object) -> object:
            read = await in_a_thread(call, *args, **kwargs)
            if call is screen.list_branches:
                reading.set()
                await dialog_up.wait()
            return read

        monkeypatch.setattr(screen.asyncio, "to_thread", held)
        app = screen.AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await asyncio.wait_for(reading.wait(), 10)
            app.push_screen(ExplainCheckModal("rule.secrets"))
            await pilot.pause()
            assert isinstance(app.screen, ExplainCheckModal)
            dialog_up.set()
            listings = [worker.wait() for worker in app.workers if worker.group == "git_load"]
            await asyncio.wait_for(asyncio.gather(*listings), 10)
            behind = app.screen_stack[0]
            assert behind.query_one("#head-list", OptionList).option_count > 0
            assert "changed file(s)" in str(behind.query_one("#status-banner", Static).render())

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
        assert clipboard.copy_text(app, "a\nb", windows=False, mac=False) is False, "the terminal's alone"
        assert sent == [("terminal", "a\nb"), ("terminal", "a\nb")], sent

    def test_copying_on_a_mac_uses_its_own_clipboard_and_the_terminals(self, monkeypatch) -> None:
        """macOS Terminal reads no OSC 52, so Ctrl+Y and the editor's Copy sent the text there and nothing pasted. On a
        Mac the text goes to its own clipboard too, through pbcopy, unless the screen runs over SSH: there the Mac's
        clipboard is the far machine's, and the terminal alone reaches the person's."""
        from types import SimpleNamespace

        from tui import clipboard

        sent: list[object] = []
        app = SimpleNamespace(copy_to_clipboard=lambda text: sent.append(("terminal", text)))
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.setattr(clipboard, "_mac_copy", lambda text: sent.append(("mac", text)) or True)
        assert clipboard.copy_text(app, "a\nb", windows=False, mac=True) is True
        assert sent == [("terminal", "a\nb"), ("mac", "a\nb")], sent
        sent.clear()
        monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
        assert clipboard.copy_text(app, "a\nb", windows=False, mac=True) is False
        assert sent == [("terminal", "a\nb")], "over SSH, the terminal alone"
        monkeypatch.delenv("SSH_CONNECTION")
        monkeypatch.setattr(clipboard, "_mac_copy", lambda _text: False)
        assert clipboard.copy_text(app, "a\nb", windows=False, mac=True) is False, "pbcopy refused it, say"

    @pytest.mark.skipif(os.name == "nt", reason="pbcopy is macOS' own, and Windows runs no shell script as a program")
    def test_the_mac_clipboard_program_is_given_the_text_in_utf8(self, tmp_path: Path, monkeypatch) -> None:
        """pbcopy reads its input in the locale's encoding: measured, with no locale or LANG=C it read UTF-8 as Mac
        Roman, an accented letter pasted as two others. It is told UTF-8 through LC_ALL, which wins over every other
        setting, and a program that refuses the text, or is not there, is no copy."""
        from conftest import _script

        from tui import clipboard

        body = 'cat > "$0.text"\nprintf %s "$LC_ALL" > "$0.locale"\nexit "${PBCOPY_EXIT:-0}"\n'
        monkeypatch.setattr(clipboard, "PBCOPY", str(_script(tmp_path / "pbcopy", body)))
        monkeypatch.setenv("LANG", "C")
        monkeypatch.setenv("LC_ALL", "C")
        text = "caf" + chr(0xE9) + " " + chr(0x2192) + " done\nsecond line"
        assert clipboard._mac_copy(text) is True
        assert (tmp_path / "pbcopy.text").read_bytes() == text.encode("utf-8")
        assert (tmp_path / "pbcopy.locale").read_text(encoding="utf-8") == "en_US.UTF-8"
        monkeypatch.setenv("PBCOPY_EXIT", "1")
        assert clipboard._mac_copy(text) is False, "the program refused it"
        monkeypatch.setattr(clipboard, "PBCOPY", str(tmp_path / "missing"))
        assert clipboard._mac_copy(text) is False, "no such program"

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

        from tui.git_sync import bounded

        grandchild = "import time; time.sleep(30)"
        child = (
            f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
        )
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            bounded([sys.executable, "-c", child], dict(os.environ), timeout_s=1)
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
            monkeypatch.setattr(app, "_log_progress", lambda line, **_how: said.append(line))
            long = "import sys; sys.stderr.write('x' * 100_000 + '\\nafter\\n')"
            code, _out = await app._stream_subprocess([sys.executable, "-c", long])
            assert code == 0 and said[-1] == "after" and len(said[0]) >= 64_000, [len(line) for line in said]

    @pytest.mark.asyncio
    async def test_a_review_saves_its_choices_under_the_repository_it_reviews(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read by an audit: the choices a review saves were filed under the repository picked when the save's thread
        ran, not the one the review started for, so a pick made while it waited took the review's choices. The name is
        taken as the review starts now, and the thread writes the file alone."""
        pytest.importorskip("textual")
        import dataclasses

        from tui import app as screen

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        saved: list[str] = []

        def saved_as(config: object, name: str, _preset: object) -> object:
            saved.append(name)
            return config

        def stop(*_args: object) -> None:
            raise PermissionError(13, "Permission denied", str(cfg))

        monkeypatch.setattr(screen, "save_repo_preset", saved_as)
        monkeypatch.setattr(screen, "make_output_dir", stop)
        app = screen.AdkReviewTuiApp(config_path=cfg)
        in_a_thread = screen.asyncio.to_thread

        async def picked_meanwhile(call: object, /, *args: object, **kwargs: object) -> object:
            # Another repository picked while each thread waits to run, whatever the thread does.
            app.selected_project = dataclasses.replace(app.selected_project, name="another-repo")
            return await in_a_thread(call, *args, **kwargs)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base = "origin/main"
            monkeypatch.setattr(screen.asyncio, "to_thread", picked_meanwhile)
            await pilot.press("ctrl+r")
            await pilot.pause()
            await app.workers.wait_for_complete()
        assert saved == ["my-service"], saved

    @pytest.mark.asyncio
    async def test_results_stay_with_the_target_they_came_from(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review: a change of branch kept the last review's comment and hand-off, marked ready, which Ctrl+Y
        then copied for another branch's pull request; and a review that ended after the person chose another target
        filled that target's tabs. A change of target clears them now, and a review whose target changed is named,
        its files where they are, and not shown."""
        pytest.importorskip("textual")
        import asyncio

        from textual.widgets import Static, TabbedContent

        from tui.app import AdkReviewTuiApp

        _remote, local = _init_repo_with_remote(tmp_path)
        _git(local, "fetch", "origin")  # the screen fetches only at F5
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        comment, report = tmp_path / "comment.md", tmp_path / "report.json"
        comment.write_text("the last review's comment\n", encoding="utf-8")
        report.write_text('{"schema": 2, "findings": []}', encoding="utf-8")  # a decided review writes its report
        app = AdkReviewTuiApp(config_path=cfg)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await app._load_generated_files({"json": report, "comment": comment})
            assert app.last_comment_md and "ready" in str(app.query_one(TabbedContent).get_tab("tab-comment").label)
            app._sync_chosen_branch("head", "origin/feat-remote")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.selected_head == "origin/feat-remote"
            assert app.last_comment_md == "", "another branch's comment is never shown as this one's"
            assert "ready" not in str(app.query_one(TabbedContent).get_tab("tab-comment").label)

            async def moved_on(cmd: list[str]) -> tuple[int, str]:
                app.selected_head = "main"  # the person chose another target while it ran
                report = _report("REQUEST_CHANGES", ("its own",))
                await asyncio.to_thread(Path(cmd[cmd.index("--json") + 1]).write_text, report, "utf-8")
                return 1, "the report of origin/feat-remote"

            monkeypatch.setattr(app, "_stream_subprocess", moved_on)
            app.selected_head = "origin/feat-remote"
            app._execute_review(dry_run=False)
            await pilot.pause()
            await app.workers.wait_for_complete()
            banner = str(app.query_one("#status-banner", Static).render())
            assert app.last_report_md == "" and app.findings_rows == [], "its results fill no other target's tabs"
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

    @pytest.mark.asyncio
    async def test_ctrl_x_stops_a_running_review_and_is_a_text_fields_cut_otherwise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The owner's decision: Stop went first wherever the focus was, a review running or none. With none running
        it stopped nothing, wrote "Review stopped by user." and took Ctrl+X from every text field, a filter's and
        the pull request's address among them: no text could be cut. It goes first while a review runs alone now,
        and the line under the screen shows it then alone, as it shows Run and Dry Run while none runs."""
        pytest.importorskip("textual")
        import asyncio

        from textual.widgets import Input
        from textual.widgets._footer import FooterKey

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        started: list[list[str]] = []
        stopped: list[str] = []
        release = asyncio.Event()

        async def slow(cmd: list[str]) -> tuple[int, str]:
            started.append(cmd)
            await release.wait()
            return 0, ""

        monkeypatch.setattr(AdkReviewTuiApp, "action_stop_review", lambda _self: stopped.append("stop"))
        app = AdkReviewTuiApp(config_path=cfg)

        def shown() -> set[str]:
            return {key.action for key in app.query(FooterKey)} & {"stop_review", "run_review", "run_dry"}

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base = "origin/main"
            monkeypatch.setattr(app, "_stream_subprocess", slow)
            field = app.query_one("#repo-filter", Input)
            field.focus()
            field.value = "my"  # part of the repository's name: the list keeps it
            field.select_all()
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert (field.value, stopped) == ("", []), "no review runs: the field's own cut"
            assert shown() == {"run_review", "run_dry"}, "and the line under the screen offers no Stop"
            await pilot.press("ctrl+r")
            await pilot.pause(0.2)
            assert len(started) == 1 and shown() == {"stop_review"}, shown()
            field.value = "serv"
            field.select_all()
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert (field.value, stopped) == ("serv", ["stop"]), "a review runs: Stop goes first, and nothing is cut"
            release.set()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert shown() == {"run_review", "run_dry"}, "the review ended: Run and Dry Run again, and no Stop"

    @pytest.mark.asyncio
    async def test_a_review_the_closing_screen_cuts_short_ends_cleanly(self, tmp_path: Path, monkeypatch) -> None:
        """The line under the screen is redrawn where a review ends, and a review ends too where the closing screen
        cancels it. Measured: on a screen that closed, Textual's redraw raises `ScreenStackError`, which a worker
        turns into `WorkerFailed`. A test's harness cancels a worker after it closes the screens, as here; nothing is
        redrawn there."""
        pytest.importorskip("textual")
        import asyncio

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        cfg = tmp_path / ".tui.toml"
        cfg.write_text(f'[workspace]\nroot_dirs = ["{(tmp_path / "workspace").as_posix()}"]\n', encoding="utf-8")
        started: list[list[str]] = []

        async def never_ends(cmd: list[str]) -> tuple[int, str]:
            started.append(cmd)
            await asyncio.Event().wait()
            return 0, ""

        app = AdkReviewTuiApp(config_path=cfg)
        close_the_screens = app._close_all

        async def close_then_cancel_the_review() -> None:
            await close_the_screens()
            reviews = [worker for worker in app.workers if worker.group == "review_exec"]
            app.workers.cancel_group(app, "review_exec")
            await asyncio.wait_for(asyncio.gather(*(w.wait() for w in reviews), return_exceptions=True), 10)

        monkeypatch.setattr(app, "_close_all", close_then_cancel_the_review)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.selected_base = "origin/main"
            monkeypatch.setattr(app, "_stream_subprocess", never_ends)
            await pilot.press("ctrl+r")
            await pilot.pause(0.2)
            assert len(started) == 1 and app._reviewing
        assert app._reviewing is False

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

    def test_a_projects_folders_and_checks_may_be_one_name_written_alone(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: `path = "src"`, one folder written as a string where a list was meant,
        read as no folder at all, with no word, and so did `path_exclude` and `checks`. One name stands for a list of
        one, as `root_dirs` reads it."""
        from tui.config import load_tui_config

        cfg = tmp_path / ".tui.toml"
        body = '[projects."a"]\npath = "src"\npath_exclude = "tests"\nchecks = "rule.secrets"\n'
        cfg.write_text(body, encoding="utf-8")
        preset = load_tui_config(cfg).projects["a"]
        assert (preset.path, preset.path_exclude, preset.checks) == (("src",), ("tests",), ("rule.secrets",))

    def test_the_example_config_names_every_setting_the_loader_reads(self) -> None:
        """Read by an outside audit, and reproduced: the example says it holds every setting, and a repository's
        `checks` and `diff_cap`, which the loader reads, stood in none of its lines. Held to the loader's own names
        now, each on a line of its table or a commented one, the name it still reads for an old config aside; and the
        loader reads the example as it reads a config."""
        import re
        import tomllib

        from tui import config

        path = Path(config.__file__).resolve().parents[1] / "tui.example.toml"
        text = path.read_text(encoding="utf-8")
        tables = {
            "workspace": set(config._WORKSPACE) - {"projects_dir"},
            "defaults": set(config.ReviewDefaults.__dataclass_fields__),
            "projects": set(config.RepoPreset.__dataclass_fields__),
        }
        for table, settings in tables.items():
            start = text.index(f"\n[{table}")
            end = text.find("\n[", start + 1)
            named = set(re.findall(r"^#? ?(\w+) = ", text[start : end if end > 0 else None], re.MULTILINE))
            assert settings <= named, (table, sorted(settings - named))
        assert set(tomllib.loads(text)) == set(tables), "and no other table"
        assert config.load_tui_config(path).projects, "the loader reads it whole"

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
            ('[projects."a"]\npath = 3\n', "[projects.a] path is 3. Write a list of names"),
            ('[projects."a"]\nchecks = [1]\n', "[projects.a] checks is [1]. Write a list of names"),
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
        git_sync.bounded(["git", "fetch"], {}, 5)
        assert int(seen.get("creationflags") or 0) & 0x08000000 and not seen["start_new_session"], seen
        monkeypatch.setattr(git_sync.sys, "platform", "darwin")
        git_sync.bounded(["git", "fetch"], {}, 5)
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


async def _reviewed_once(app, pilot) -> None:
    """One review run as the person's Ctrl+R runs it, waited on to its end and the screen drawn after it."""
    await pilot.press("ctrl+r")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _wait_for_branches(app, pilot) -> None:
    """Until the first repository's branches are listed, and its folders too: a review waits for both. Seen on
    Windows in CI: a test that pressed Ctrl+D once the branches showed met the wait for the folders."""
    await pilot.pause()
    for _ in range(200):
        await pilot.pause(0.05)
        if app.branches and app._listing_for is None:
            break


#: A pull request's address as its page shows it, with the placeholder names every example uses.
PR_ADDRESS = "https://dev.azure.com/contoso/Web/_git/payments/pullrequest/123"


def _pull_request_report(folder: Path, head_sha: str = "a" * 40, *, resolved: bool = False, **changed: object) -> Path:
    """A review's JSON report in `folder`: one major on `src/app.py`, lines 42 to 57, of the commit `head_sha`, or,
    `resolved`, that finding as one an earlier review found and this one did not."""
    import json

    found = {
        "check": "guideline.correctness",
        "file": "src/app.py",
        "line": 42,
        "lines": [42, 57],
        "suggestable": True,
        "severity": "major",
        "title": "`charge()` adds tax twice",
        "evidence": "+ total = with_tax(total)",
        "suggestion": "Add the tax once.",
        "why": "The coupon path taxes a taxed total.",
        "code": "total = with_tax(apply_coupon(subtotal, coupon))",
        "fingerprint": "1a2b3c4d5e6f7a8b",
        **changed,
    }
    reviewed = {"repository": str(folder / "workspace" / "payments"), "head": "feature/orders", "head_sha": head_sha}
    path = folder / "report.json"
    held = {"findings": [], "resolved": [found]} if resolved else {"findings": [found]}
    path.write_text(json.dumps({"schema": 2, **held, "reviewed": reviewed}), encoding="utf-8")
    return path


async def _show_findings(app, pilot, report: Path) -> None:
    """`report`'s findings on the Findings tab, the table holding the focus, as after a review."""
    from textual.widgets import DataTable, TabbedContent

    await app._load_generated_files({"json": report})
    app.query_one("#output-tabs", TabbedContent).active = "tab-findings"
    await pilot.pause()
    app.query_one("#findings-table", DataTable).focus()
    await pilot.pause()


def _payments_screen(tmp_path: Path):  # the application, not yet running
    """The screen over one repository, `payments`, its branch `feature/orders` checked out."""
    from tui.app import AdkReviewTuiApp

    _repository(tmp_path / "workspace" / "payments", ("main", "feature/orders", "feature/tax"), "feature/orders")
    return AdkReviewTuiApp(config_path=_screen_config(tmp_path))


def _two_clones(tmp_path: Path) -> Path:
    """A workspace of two repositories, `notes` listed first and `payments` second, whose remote is the fake pull
    request's repository: its branches as the remote holds them, `origin/main` and `origin/feature/tax`. The screen
    never fetches: the remote's address is Azure DevOps', which no test reaches."""
    _repository(tmp_path / "workspace" / "anotes", ("main",), "main")
    clone = _repository(
        tmp_path / "workspace" / "payments", ("main", "feature/orders", "feature/tax"), "feature/orders"
    )
    _git(clone, "remote", "add", "origin", "https://dev.azure.com/contoso/Web/_git/payments")
    for branch in ("main", "feature/tax"):
        _git(clone, "update-ref", f"refs/remotes/origin/{branch}", branch)
    return _screen_config(tmp_path, "fetch_on_select = false")


def _opened(fake, commit: str, **changed: object) -> None:
    """The fake pull request merging `feature/tax` into `main`, its last push `commit`, from now on."""
    fake.instead(
        "GET",
        "",
        {
            "status": "active",
            "sourceRefName": "refs/heads/feature/tax",
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": commit},
            **changed,
        },
    )


class TestAzureDevOpsOnTheScreen:
    """The pull request's address, pasted once a branch, and the keys that open a finding's place in Azure DevOps."""

    @pytest.mark.asyncio
    async def test_a_pull_request_opened_chooses_its_repository_and_both_its_branches(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """One paste chooses what a review of the pull request reads: the repository whose remote names its
        repository, and its source and target branches as the remote holds them, never a local copy, which can hold
        work not pushed. Its address is kept for that branch, and for no other: the one chosen before keeps its own."""
        from conftest import FakeToken
        from textual.widgets import Input, Select, Static

        from tui import azure_state
        from tui.app import AdkReviewTuiApp

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            before = app._pair()
            assert app.selected_project is not None and app.selected_project.name == "anotes"
            app._credential = FakeToken()
            app.query_one("#mode-select", Select).value = "staged"  # opened, a pull request is a branch's review
            await pilot.pause()
            listed = [command.title for command in app.get_system_commands(app.screen)]
            clone = tmp_path / "workspace" / "payments"
            _opened(fake_ado, _git(clone, "rev-parse", "feature/tax").strip())
            app._open_pull_request(fake_ado.pr)
            await _until(
                pilot, lambda: app.selected_head == "origin/feature/tax" and app._listing_for is None, "opened"
            )
            await app.workers.wait_for_complete()
            await pilot.pause()
            chosen = (app.selected_project.name, app.selected_head, app.selected_base, app.selected_mode)
            field = app.query_one("#pr-url", Input).value
            banner = str(app.query_one("#status-banner", Static).render())
            pair = app._pair()
        assert chosen == ("payments", "origin/feature/tax", "origin/main", "branch"), chosen
        assert field == fake_ado.pr.web and azure_state.pull_request_url(*pair) == fake_ado.pr.web
        assert azure_state.pull_request_url(*before) == "", "the branch chosen before keeps none of it"
        assert "Pull request 123" in banner and "F5" not in banner, banner
        assert "Open pull request" in listed, "the palette, Ctrl+P, opens one too"

    @pytest.mark.asyncio
    async def test_a_pull_request_opened_on_the_branch_already_chosen_shows_its_own_address(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """Read by review, and reproduced: with its branch already the one under review, the address was kept but the
        field kept what it showed, none or another pull request's, and a comment went to that one. The field shows
        the pull request opened."""
        from conftest import FakeToken
        from textual.widgets import Input

        import tui.app as screen
        from tui.app import AdkReviewTuiApp

        real = screen.parse_pr_url  # the fake's address is a local one, which the parser refuses by design
        monkeypatch.setattr(screen, "parse_pr_url", lambda text: fake_ado.pr if text == fake_ado.pr.web else real(text))
        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            clone = tmp_path / "workspace" / "payments"
            app._on_project_chosen(next(project for project in app.projects if project.name == "payments"))
            await _until(pilot, lambda: app.selected_project.name == "payments" and app._listing_for is None, "picked")
            await app.workers.wait_for_complete()
            app._sync_chosen_branch("head", "origin/feature/tax")
            await _until(pilot, lambda: app.selected_head == "origin/feature/tax", "the branch chosen by hand")
            await app.workers.wait_for_complete()
            assert app.query_one("#pr-url", Input).value == "", "none kept for it yet"
            _opened(fake_ado, _git(clone, "rev-parse", "feature/tax").strip())
            app._open_pull_request(fake_ado.pr)
            # Until the listing it starts has ended, which waiting for the workers there were does not cover: measured
            # on a Windows runner, and with the listing slowed on macOS, the field was read while it ran.
            await _until(pilot, lambda: app._pull_request is not None, "the pull request shown")
            shown = app.query_one("#pr-url", Input).value, app._pull_request
        assert shown == (fake_ado.pr.web, fake_ado.pr), shown

    @pytest.mark.asyncio
    async def test_a_pick_made_while_a_pull_request_opens_wins_and_leaves_nothing_of_it(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """Read by review, and reproduced: a pick made while the clone was sought was undone, its lists left empty;
        and one made while the clone's branches listed left the pull request waiting, to be chosen when the person
        picked that repository again by hand. The person's last pick wins, and the pull request is dropped."""
        import threading

        from conftest import FakeToken

        import tui.app as screen
        from tui.app import AdkReviewTuiApp

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        sought, released, real = threading.Event(), threading.Event(), screen._clone_of

        def slow(*args: object):
            sought.set()
            released.wait(2)  # until the person's pick is made, never forever
            return real(*args)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            await app.workers.wait_for_complete()  # the opening pick's folders listed: a pick would cancel them
            notes, payments = (next(p for p in app.projects if p.name == name) for name in ("anotes", "payments"))
            app._on_project_chosen(payments)  # chosen first, so the pick below is another
            await _until(pilot, lambda: app.selected_project is payments and app._listing_for is None, "chosen")
            await app.workers.wait_for_complete()
            _opened(fake_ado, "c" * 40)
            monkeypatch.setattr(screen, "_clone_of", slow)
            app._open_pull_request(fake_ado.pr)
            await _until(pilot, sought.is_set, "the clone sought")
            app._on_project_chosen(notes)  # the person's pick, while the clone is sought
            released.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            during = (app.selected_project.name, bool(app.branches))
            monkeypatch.setattr(screen, "_clone_of", real)
            app._opening = screen._Opening(
                str(payments.path), "origin", app._client(fake_ado.pr).pull_request(), fake_ado.pr
            )
            app._on_project_chosen(notes)  # a pick while a clone's branches list: the pull request waits no longer
            await app.workers.wait_for_complete()
            app._on_project_chosen(payments)
            await _until(pilot, lambda: app.selected_project.name == "payments" and app._listing_for is None, "picked")
            await app.workers.wait_for_complete()
            later = app.selected_head
        assert during == ("anotes", True), during
        assert later != "origin/feature/tax", "the pull request dropped at the pick"

    @pytest.mark.asyncio
    async def test_the_comments_and_work_items_are_read_listed_and_given_to_the_next_hand_off(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """The pull request's open comments and the work items it links, read on one click: listed on their tab, and
        written where the next review's hand-off quotes them, for its coding agent to answer the people and check the
        change against what it is for in the same pass. A review that writes no hand-off is given neither. Each read
        stands alone: one that fails gives the hand-off none of its own, never the last read's, and the tab says so.
        With no pull request chosen, it says so. Read by review, and reproduced: the tab's status line shared its id
        with the findings tab's, which took its words; one read cut short left the last read's other file given; and
        comments read but not kept were said to be quoted."""
        from conftest import FakeToken
        from textual.widgets import RichLog, Static, Switch
        from textual.worker import WorkerCancelled

        import tui.app as screen
        from adk_review.deliver.threads import read_threads
        from adk_review.deliver.work_items import read_work_items
        from tui.azure_client import Client
        from tui.options import build_review_argv

        def given(argv: list[str], flag: str) -> str | None:
            return argv[argv.index(flag) + 1] if flag in argv else None

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            app._read_from_pull_request()
            await pilot.pause()
            assert said and "pull request" in said[-1], "none chosen: said"
            app._credential = FakeToken()
            app._pull_request = fake_ado.pr
            comment = {"author": {"displayName": "Jane"}, "content": "Fix the rounding", "commentType": "text"}
            thread = {"id": 1, "status": "active", "comments": [comment],
                      "threadContext": {"filePath": "/src/app.py", "rightFileStart": {"line": 12}}}  # fmt: skip
            item = {"id": 12, "fields": {"System.WorkItemType": "User Story", "System.Title": "Sign in",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>A work account works</li></ul>"}}  # fmt: skip
            fake_ado.answer("GET", "/threads", {"value": [thread]})
            fake_ado.answer("GET", "/workitems", {"value": [{"id": "12", "url": "u"}]})
            fake_ado.answer("GET", "/wit/workitems", {"count": 1, "value": [item]}, under=fake_ado.project)

            async def read() -> tuple[list[str], str]:
                app._read_from_pull_request()
                with contextlib.suppress(WorkerCancelled):  # a read cut short, as one the person starts again is
                    await app.workers.wait_for_complete()
                await pilot.pause()
                app.query_one("#sw-handoff", Switch).value = True
                await pilot.pause()
                argv, _ = build_review_argv(app.current_selection())
                return argv, str(app.query_one("#tab-pr #read-status", Static).render())

            with_handoff, first_status = await read()
            findings_line = str(app.query_one("#pr-status", Static).render())
            listed = "\n".join(line.text for line in app.query_one("#read-log", RichLog).lines)
            app.query_one("#sw-handoff", Switch).value = False
            await pilot.pause()
            without, _ = build_review_argv(app.current_selection())
            # Read by review, and reproduced: a read that failed left the last read's file given, and its words.
            fake_ado.instead("GET", "/threads", {"message": "down"}, status=500)
            comments_failed, comments_status = await read()
            fake_ado.instead("GET", "/threads", {"value": [thread]})
            fake_ado.instead("GET", "/wit/workitems", {"message": "no access"}, status=403, under=fake_ado.project)
            items_failed, items_status = await read()
            fake_ado.instead("GET", "/wit/workitems", {"count": 1, "value": [item]}, under=fake_ado.project)
            kept = screen._kept_for_handoff

            def full_for_comments(folder: str, pr, kind: str, data: dict) -> Path:
                if kind == "threads":
                    raise OSError(28, "No space left on device")
                return kept(folder, pr, kind, data)

            monkeypatch.setattr(screen, "_kept_for_handoff", full_for_comments)
            unkept, unkept_status = await read()
            monkeypatch.setattr(screen, "_kept_for_handoff", kept)
            await read()  # both kept again

            def cut_short(_client: Client) -> list:
                raise asyncio.CancelledError

            monkeypatch.setattr(Client, "open_threads", cut_short)
            cut, _ = await read()
        assert "src/app.py:12" in listed and "Jane: Fix the rounding" in listed, listed
        assert "Work item 12, User Story: Sign in" in listed and "A work account works" in listed, listed
        threads, items = given(with_handoff, "--threads"), given(with_handoff, "--work-items")
        assert threads and [t.comments for t in read_threads(threads)] == [(("Jane", "Fix the rounding"),)]
        assert items and [w.title for w in read_work_items(items).items] == ["Sign in"]
        assert given(without, "--threads") is None and given(without, "--work-items") is None, "no hand-off, neither"
        assert given(comments_failed, "--threads") is None and given(comments_failed, "--work-items"), comments_failed
        assert "comments not read" in comments_status, comments_status
        assert given(items_failed, "--threads") and given(items_failed, "--work-items") is None, items_failed
        assert "work items not read" in items_status, items_status
        assert "comment" in first_status and "comment" not in findings_line, (first_status, findings_line)
        assert given(unkept, "--threads") is None and given(unkept, "--work-items"), unkept
        assert "comments not kept" in unkept_status and "quotes what was kept" in unkept_status, unkept_status
        assert given(cut, "--threads") is None and given(cut, "--work-items") is None, "cut short: neither file given"

    @pytest.mark.asyncio
    async def test_reading_comments_switches_on_the_hand_off_that_quotes_them(self, tmp_path: Path, fake_ado) -> None:
        """Read by an audit, and reproduced: with the hand-off switched off, as the default config has it, the tab
        said the next review's hand-off quoted the comments read, and the next review wrote no hand-off. The person
        read them for the hand-off, so the read switches it on, and says so."""
        from conftest import FakeToken
        from textual.widgets import Switch

        from tui.options import build_review_argv

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            app._credential = FakeToken()
            app._pull_request = fake_ado.pr
            before = app.query_one("#sw-handoff", Switch).value
            comment = {"author": {"displayName": "Jane"}, "content": "Fix the rounding", "commentType": "text"}
            fake_ado.answer("GET", "/threads", {"value": [{"id": 1, "status": "active", "comments": [comment]}]})
            fake_ado.answer("GET", "/workitems", {"value": []})
            app._read_from_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            argv, _ = build_review_argv(app.current_selection())
        assert before is False, "the default config's"
        assert "--handoff" in argv and "--threads" in argv, argv
        assert any("hand-off" in line and "switched on" in line for line in said), said

    @pytest.mark.asyncio
    async def test_the_chosen_findings_words_show_on_the_smallest_screen(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: at 80 columns by 24 rows, with the config line on two rows as a
        checkout's path makes it, the toolbars' fixed heights left the finding's detail under the footer, no word of
        its title, reason or fix shown. The toolbars take a row each."""
        import json

        from textual.widgets import Static, TabbedContent

        found = [
            {"check": "rule.lint", "file": "a.py", "line": n, "severity": "major", "title": f"finding {n}",
             "evidence": "+ x = 1", "suggestion": "Mend it.", "fingerprint": f"{n:016x}"}
            for n in range(1, 9)
        ]  # fmt: skip
        report = json.dumps({"schema": 2, "verdict": "REQUEST_CHANGES", "findings": found,
                             "reviewed": {"head_sha": "b" * 40}})  # fmt: skip

        async def reviewed(command: list[str]) -> tuple[int, str]:
            await asyncio.to_thread(Path(command[command.index("--json") + 1]).write_text, report, "utf-8")
            return 1, "## Verdict: REQUEST_CHANGES\n"

        app = _payments_screen(tmp_path)
        app._stream_subprocess = reviewed
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#config-line", Static).update("Config: ~/Projects/adk_review/tui/.tui.toml\nFolders: ~")
            await pilot.press("ctrl+r")
            await _until(pilot, lambda: not app._reviewing, "the review ended")
            await app.workers.wait_for_complete()
            app.query_one("#output-tabs", TabbedContent).active = "tab-findings"
            await pilot.pause(0.3)
            detail = app.query_one("#finding-detail-scroll").region
            footer = app.query_one("Footer").region
        assert footer.y - (detail.y + 1) >= 1, (detail, footer)  # a row of words under the detail's top border

    @pytest.mark.asyncio
    async def test_the_tab_counts_the_decided_threads_apart_from_the_open(self, tmp_path: Path, fake_ado) -> None:
        """A thread people closed as won't fix or by design is read for the hand-off, which tells its agent to leave
        what it declined: the tab counts it apart, never as an open comment."""
        from conftest import FakeToken
        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            app._pull_request = fake_ado.pr
            comment = {"author": {"displayName": "Jane"}, "content": "Keep it", "commentType": "text"}
            threads = [
                {"id": n, "status": status, "comments": [comment]} for n, status in ((1, "active"), (2, "wontFix"))
            ]
            fake_ado.answer("GET", "/threads", {"value": threads})
            fake_ado.answer("GET", "/workitems", {"value": []})
            app._read_from_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            status = str(app.query_one("#read-status", Static).render())
        assert "1 open comment thread, 1 decided thread," in status, status

    @pytest.mark.asyncio
    async def test_the_pull_request_tab_holds_only_the_chosen_pull_requests_reads(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: once no pull request, or another, was chosen, the tab still listed the
        last one's comments and said the hand-off quoted them, which Ctrl+Y copied; and a read that ended after the
        change took the screen over to the tab, showing the pull request no longer chosen."""
        import threading

        from conftest import FakeToken
        from textual.widgets import RichLog, Static, TabbedContent
        from textual.worker import WorkerCancelled

        from tui.azure_client import Client

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            app._pull_request = fake_ado.pr
            comment = {"author": {"displayName": "Jane"}, "content": "Fix the rounding", "commentType": "text"}
            fake_ado.answer("GET", "/threads", {"value": [{"id": 1, "status": "active", "comments": [comment]}]})
            fake_ado.answer("GET", "/workitems", {"value": []})
            app._read_from_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            read = list(app._pr_text)
            app._read_pull_request("", keep=False)  # the field emptied: no pull request chosen
            await pilot.pause()
            cleared = (list(app._pr_text), len(app.query_one("#read-log", RichLog).lines))
            status = str(app.query_one("#read-status", Static).render())
            # A read that ends after the choice changed.
            app._pull_request = fake_ado.pr
            tabs = app.query_one("#output-tabs", TabbedContent)
            tabs.active = "tab-report"
            gate, real = threading.Event(), Client.open_threads

            def held(client: Client) -> list:
                gate.wait(5)
                return real(client)

            monkeypatch.setattr(Client, "open_threads", held)
            app._read_from_pull_request()
            await pilot.pause()
            app._read_pull_request("", keep=False)
            gate.set()
            with contextlib.suppress(WorkerCancelled):
                await app.workers.wait_for_complete()
            await pilot.pause()
            late = (tabs.active, list(app._pr_text))
        assert any("Jane" in line for line in read), read
        assert cleared == ([], 0) and "quotes" not in status, (cleared, status)
        assert late == ("tab-report", []), late

    @pytest.mark.asyncio
    async def test_a_slow_read_of_an_earlier_target_never_lists_its_folders(self, tmp_path: Path, monkeypatch) -> None:
        """Read by an audit, and reproduced: a read of the change started for one head, and slower, ended after the
        read for the head picked next, and the folder lists showed the earlier head's folders, which a tick would pass
        as a `--path` the reviewed branch lacks."""
        import time

        from textual.widgets import Select, SelectionList

        import tui.app as screen

        repo = _repository(tmp_path / "workspace" / "payments", ("main",), "main")
        for name, folder in (("feature/one", "alpha"), ("feature/two", "beta")):
            _git(repo, "switch", "-q", "-c", name, "main")
            (repo / folder).mkdir()
            (repo / folder / "x.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
            _git(repo, "add", ".")
            _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
        _git(repo, "switch", "-q", "feature/one")
        real = screen.read_change

        def slow_for_one(path: str, base: str | None, head: str, mode: str = "branch"):
            if head == "feature/one":
                time.sleep(1.0)  # a larger change, read slower
            return real(path, base, head, mode)

        monkeypatch.setattr(screen, "read_change", slow_for_one)
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path, "fetch_on_select = false"))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            app.query_one("#mode-select", Select).value = "all"  # reads feature/one, slowly
            await pilot.pause(0.1)
            app._sync_chosen_branch("head", "feature/two")  # reads feature/two, at once
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            labels = {
                str(option.value): str(option.prompt) for option in app.query_one("#path-list", SelectionList)._options
            }
            head = app.selected_head
        # A folder on disk is offered too, as `(repo folder)`: the earlier head's own change must not be listed.
        assert head == "feature/two" and "changed" in labels.get("beta", ""), (head, labels)
        assert "changed" not in labels.get("alpha", ""), labels

    @pytest.mark.asyncio
    async def test_a_pick_of_another_repository_names_none_of_the_last_ones_branches(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: while a picked repository's fetch ran, the command line named the last
        repository's branch, and a mode changed meanwhile read that branch in the new repository and said it does not
        exist there."""
        import threading

        from textual.widgets import Select, Static

        import tui.app as screen
        from tui.git_sync import FetchResult

        gate = threading.Event()

        def held(_repo: str, _timeout_s: float = 15, *, prune: bool = False) -> FetchResult:  # noqa: ARG001
            gate.wait(15)
            return FetchResult(ok=True, message="Fetched")

        monkeypatch.setattr(screen, "fetch_remote", held)
        _repository(tmp_path / "workspace" / "anotes", ("main", "feature/notes-x"), "feature/notes-x")
        _repository(tmp_path / "workspace" / "payments", ("main", "release/2", "feature/orders"), "feature/orders")
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            payments = next(project for project in app.projects if project.name == "payments")
            app._on_project_chosen(payments)  # the person's pick: fetches, held
            await pilot.pause(0.3)
            during = (app.selected_head, str(app.query_one("#cli-preview", Static).render()))
            app.query_one("#mode-select", Select).value = "all"
            await pilot.pause(0.8)
            banner = str(app.query_one("#status-banner", Static).render())
            gate.set()
            await app.workers.wait_for_complete()
        assert during[0] == "" and "notes-x" not in during[1], during
        assert "does not exist" not in banner, banner

    @pytest.mark.asyncio
    async def test_a_review_that_kept_findings_opens_on_them(self, tmp_path: Path) -> None:
        """Read by an audit, and counted: after a review that kept findings, the Report tab showed and the focus stayed
        on the repository's chip, the findings table nine keys away. It opens on the table, the first finding chosen."""
        import json

        from textual.widgets import DataTable, TabbedContent

        found = [
            {"check": "rule.lint", "file": "a.py", "line": 1, "severity": "major", "title": "finding 1",
             "evidence": "+ x = 1", "suggestion": "Mend it.", "fingerprint": "1" * 16}
        ]  # fmt: skip
        report = json.dumps({"schema": 2, "verdict": "REQUEST_CHANGES", "findings": found,
                             "reviewed": {"head_sha": "b" * 40}})  # fmt: skip

        async def reviewed(command: list[str]) -> tuple[int, str]:
            await asyncio.to_thread(Path(command[command.index("--json") + 1]).write_text, report, "utf-8")
            return 1, "## Verdict: REQUEST_CHANGES\n"

        app = _payments_screen(tmp_path)
        app._stream_subprocess = reviewed
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await pilot.press("ctrl+r")
            await _until(pilot, lambda: not app._reviewing, "the review ended")
            await app.workers.wait_for_complete()
            await pilot.pause()
            shown = app.query_one("#output-tabs", TabbedContent).active
            focused = app.focused is app.query_one("#findings-table", DataTable)
            where = getattr(app.focused, "id", None)
        assert shown == "tab-findings" and focused, (shown, where)

    @pytest.mark.asyncio
    async def test_a_mode_chosen_while_a_repository_loads_lists_its_own_folders(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: a mode chosen while a picked repository's change was read was never read
        again, that read being dropped as stale, and the last repository's folders stayed listed and ticked, the
        review run with a `--path` the new repository lacks."""
        import threading

        from textual.widgets import Select, SelectionList

        import tui.app as screen

        for name, branch, folder in (("alpha", "feature/a", "olda"), ("beta", "feature/b", "newb")):
            repo = _repository(tmp_path / "workspace" / name, ("main",), "main")
            _git(repo, "switch", "-q", "-c", branch, "main")
            (repo / folder).mkdir()
            (repo / folder / "x.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
            _git(repo, "add", ".")
            _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
        real, gate, entered = screen.read_change, threading.Event(), threading.Event()

        def held_for_beta(path: str, base: str | None, head: str, mode: str = "branch"):
            if Path(path).name == "beta" and not gate.is_set():
                entered.set()
                gate.wait(10)
            return real(path, base, head, mode)

        monkeypatch.setattr(screen, "read_change", held_for_beta)
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path, "fetch_on_select = false"))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            path_list = app.query_one("#path-list", SelectionList)
            path_list.select("olda")
            await pilot.pause()
            app._on_project_chosen(next(project for project in app.projects if project.name == "beta"))
            await _until(pilot, entered.is_set, "beta's change being read")
            app.query_one("#mode-select", Select).value = "all"
            await pilot.pause(0.3)
            gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause(0.3)
            listed = [str(option.value) for option in path_list._options]
            chosen = app.current_selection(dry_run=False).paths
        assert "newb" in listed and "olda" not in listed and chosen == (), (listed, chosen)

    def test_two_pull_requests_of_one_name_and_number_keep_two_files(self, tmp_path: Path) -> None:
        """Read by review: the file was named by the repository and the number alone, and two organizations' pull
        requests of one name, or of names that differ in case or punctuation alone, wrote one file, so one's hand-off
        quoted the other's comments. The same pull request read again writes the same file."""
        from tui.app import _kept_for_handoff
        from tui.azure import parse_pr_url

        one = parse_pr_url("https://dev.azure.com/orgone/Shop/_git/api/pullrequest/7")
        two = parse_pr_url("https://dev.azure.com/orgtwo/Bank/_git/API/pullrequest/7")
        three = parse_pr_url("https://dev.azure.com/orgone/Shop/_git/my api/pullrequest/7")
        four = parse_pr_url("https://dev.azure.com/orgone/Shop/_git/my-api/pullrequest/7")
        paths = [_kept_for_handoff(str(tmp_path), pr, "threads", {}) for pr in (one, two, three, four)]
        assert len({str(path).lower() for path in paths}) == 4, paths
        assert _kept_for_handoff(str(tmp_path), one, "threads", {}) == paths[0], "the same pull request, the same file"
        assert _kept_for_handoff(str(tmp_path), one, "work-items", {}) != paths[0], "each kind its own file"

    @pytest.mark.asyncio
    async def test_the_open_dialog_lists_the_active_pull_requests_here_and_a_pick_opens_one(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """Asked by the owner, from the validated options: the address pasted from a browser was the one way in. The
        dialog lists the active pull requests of the repositories cloned here, read as it opens, one call a project,
        each by its repository, number, title and author; a pick opens it as its pasted address would."""
        from conftest import FakeToken
        from textual.widgets import OptionList

        import tui.app as screen
        from tui.modals import OpenPullRequestModal

        monkeypatch.setattr(screen, "ORIGIN", fake_ado.pr.origin.rsplit("/", 1)[0] + "/{}")
        app = screen.AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        opened: list = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            monkeypatch.setattr(app, "_open_pull_request", opened.append)
            item = {"pullRequestId": 123, "title": "Round the tax", "creationDate": "2026-11-20T00:00:00Z",
                    "repository": {"name": "payments", "project": {"name": "Web"}},
                    "createdBy": {"displayName": "Jane"}}  # fmt: skip
            fake_ado.answer("GET", "/git/pullrequests", {"value": [item]}, under=fake_ado.project)
            app._ask_for_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, OpenPullRequestModal), dialog
            options = dialog.query_one("#open-pr-list", OptionList)
            label = str(options.get_option_at_index(0).prompt)
            options.highlighted = 0
            options.action_select()
            await pilot.pause()
        assert "payments #123" in label and "Round the tax" in label and "Jane" in label, label
        assert [(pr.project, pr.repository, pr.number) for pr in opened] == [("Web", "payments", 123)]

    @pytest.mark.asyncio
    async def test_the_lists_are_built_in_one_go_and_an_empty_filter_says_so(self, tmp_path: Path, monkeypatch) -> None:
        """Measured by an audit: each branch was added to its list one at a time, 4,201 additions and 363 ms for 2,101
        branches, on each key typed in a filter; and a repository filter that matched nothing said no repository was
        found under the folder at all."""
        from textual.widgets import OptionList, Static

        from tui.git_sync import BranchInfo

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.branches = [BranchInfo(f"feature/b{n:04}", False, "", f"feature/b{n:04}", "") for n in range(500)]
            one_at_a_time: list[object] = []
            real = OptionList.add_option
            monkeypatch.setattr(
                OptionList, "add_option", lambda list_, *a, **k: one_at_a_time.append(1) or real(list_, *a, **k)
            )
            app._render_branch_lists()
            app._populate_repos("zzz")
            await pilot.pause()
            banner = str(app.query_one("#status-banner", Static).render())
        assert one_at_a_time == [], len(one_at_a_time)
        assert "No repository matches 'zzz'" in banner, banner

    @pytest.mark.asyncio
    async def test_a_paste_opening_on_a_blank_line_and_a_space_keep_to_the_dialog(self, tmp_path: Path) -> None:
        """Read by an outside audit, and reproduced: a paste opening on a blank line left the field empty, its first
        line taken, and a space with the list focused moved the focus to the field. The field takes the first line
        that holds text, and a space stays with the list."""
        from textual import events
        from textual.widgets import Input

        from tui.azure import PullRequest
        from tui.modals import OpenPullRequestModal

        app = _payments_screen(tmp_path)
        url = "https://dev.azure.com/acme/proj/_git/repo/pullrequest/42"
        one = [("payments #7 a title", PullRequest("https://dev.azure.com/acme", "proj", "repo", 7))]
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            seen: list[object] = []
            for act in (lambda: app.post_message(events.Paste(f"\n{url}\n")), lambda: pilot.press("space")):
                dialog = OpenPullRequestModal()
                await app.push_screen(dialog)
                await pilot.pause()
                dialog.listed(one, "One.")
                await pilot.pause()
                if (done := act()) is not None and not isinstance(done, bool):
                    await done
                await pilot.pause()
                seen.append((app.screen.query_one("#open-pr-url", Input).value, getattr(app.focused, "id", None)))
                app.pop_screen()
                await pilot.pause()
        assert seen == [(url, "open-pr-url"), ("", "open-pr-list")], seen

    def test_the_open_dialog_says_where_a_project_holds_more_than_it_lists(self) -> None:
        """Read by an outside audit: a project's list stopped at a thousand and said nothing. The note names it."""
        from tui.app import _open_note

        said = _open_note(3, True, [], ["acme/Web"])
        assert said.endswith("More are open in acme/Web: only the newest 1,000 per project are listed."), said
        assert "More are open" not in _open_note(3, True, []), "none capped, nothing said"

    @pytest.mark.asyncio
    async def test_a_paste_or_a_key_with_the_list_focused_reaches_the_address_field_as_one_line(
        self, tmp_path: Path
    ) -> None:
        """Read by an outside audit, and reproduced: a paste reached the field whole, its newline and a second line
        too, where Textual's own field keeps the first line alone; and a key typed with the list focused went nowhere.
        The field takes a paste's first line, and each character typed."""
        from textual import events
        from textual.widgets import Input

        from tui.azure import PullRequest
        from tui.modals import OpenPullRequestModal

        app = _payments_screen(tmp_path)
        url = "https://dev.azure.com/acme/proj/_git/repo/pullrequest/999"
        one = [("payments #7 a title", PullRequest("https://dev.azure.com/acme", "proj", "repo", 7))]
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            seen: list[object] = []
            for act in (
                lambda: app.post_message(events.Paste(url + "\nextra copied line")),
                lambda: pilot.press("h", "t"),
            ):
                dialog = OpenPullRequestModal()
                await app.push_screen(dialog)
                await pilot.pause()
                dialog.listed(one, "One.")
                await pilot.pause()
                if (done := act()) is not None and not isinstance(done, bool):
                    await done
                await pilot.pause()
                seen.append((app.screen.query_one("#open-pr-url", Input).value, getattr(app.focused, "id", None)))
                app.pop_screen()
                await pilot.pause()
        assert seen == [(url, "open-pr-url"), ("ht", "open-pr-url")], seen

    @pytest.mark.asyncio
    async def test_an_address_pasted_after_the_list_fills_is_the_one_opened(
        self, tmp_path: Path, fake_ado, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: once the list took the focus, a paste went to it and was dropped, and
        Enter opened the pull request listed first, never the one pasted."""
        from conftest import FakeToken
        from textual import events
        from textual.widgets import Input

        import tui.app as screen

        monkeypatch.setattr(screen, "ORIGIN", fake_ado.pr.origin.rsplit("/", 1)[0] + "/{}")
        app = screen.AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        opened: list = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            monkeypatch.setattr(app, "_open_pull_request", opened.append)
            item = {"pullRequestId": 999, "title": "Newest", "creationDate": "2026-11-20T00:00:00Z",
                    "repository": {"name": "payments", "project": {"name": "Web"}},
                    "createdBy": {"displayName": "Jane"}}  # fmt: skip
            fake_ado.answer("GET", "/git/pullrequests", {"value": [item]}, under=fake_ado.project)
            app._ask_for_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.post_message(events.Paste("https://dev.azure.com/contoso/Web/_git/payments/pullrequest/57"))
            await pilot.pause()
            field = app.screen.query_one("#open-pr-url", Input).value
            await pilot.press("enter")
            await pilot.pause()
        assert field.endswith("/pullrequest/57") and [pr.number for pr in opened] == [57], (field, opened)

    @pytest.mark.asyncio
    async def test_the_open_dialog_works_by_keys_alone(self, tmp_path: Path, fake_ado, monkeypatch) -> None:
        """Read by an audit, and reproduced: with pull requests listed, the focus stayed in the empty address field,
        Down did nothing, and Enter closed the dialog, opening nothing and saying nothing. The list takes the focus on
        its first pull request, and Enter opens it; Enter on an empty field with nothing to choose says what to do."""
        from conftest import FakeToken
        from textual.widgets import OptionList

        import tui.app as screen
        from tui.modals import OpenPullRequestModal

        monkeypatch.setattr(screen, "ORIGIN", fake_ado.pr.origin.rsplit("/", 1)[0] + "/{}")
        app = screen.AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        opened: list = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            app._credential = FakeToken()
            monkeypatch.setattr(app, "_open_pull_request", opened.append)
            item = {"pullRequestId": 123, "title": "Round the tax", "creationDate": "2026-11-20T00:00:00Z",
                    "repository": {"name": "payments", "project": {"name": "Web"}},
                    "createdBy": {"displayName": "Jane"}}  # fmt: skip
            fake_ado.answer("GET", "/git/pullrequests", {"value": [item]}, under=fake_ado.project)
            app._ask_for_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            on_list = app.focused is app.screen.query_one("#open-pr-list", OptionList)
            await pilot.press("enter")
            await pilot.pause()
            empty = OpenPullRequestModal()
            await app.push_screen(empty)
            await pilot.pause()
            empty.listed([], "No repository here has an Azure DevOps remote. Paste an address.")
            await pilot.press("enter")
            await pilot.pause()
            stayed = app.screen is empty
        assert on_list, "the list focused on its first pull request"
        assert [pr.number for pr in opened] == [123], opened
        assert stayed and said and "paste an address" in said[-1].lower(), said

    @pytest.mark.asyncio
    async def test_a_list_that_arrives_after_the_dialog_closed_is_dropped(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: a list that came as the dialog was taken down found no list to fill, and
        the screen ended."""
        from tui.app import AdkReviewTuiApp
        from tui.modals import OpenPullRequestModal

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            dialog = OpenPullRequestModal()
            await app.push_screen(dialog)
            await pilot.pause()
            dialog.dismiss(None)
            await pilot.pause()
            dialog.listed([], "late")

    def test_a_list_none_could_read_never_says_there_is_none(self) -> None:
        """Read by review, and reproduced: every project refused, and the note said the repositories had no open pull
        request, a list never read read as an empty one."""
        from tui.app import _open_note

        said = _open_note(0, True, ["contoso/Web: refused."])
        assert "could not be read" in said and "no open pull request" not in said, said

    @pytest.mark.asyncio
    async def test_a_login_that_gives_no_token_is_asked_once_and_said_once(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: a login that failed was asked again for each project, each time `az`, and
        its sentence said once a project, cut where the note ends. Asked once, it is said once; and at 80 columns by
        24 rows the dialog's buttons stay on the screen with a full list."""
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp
        from tui.azure_client import AzureError

        config = _two_clones(tmp_path)
        other = _repository(tmp_path / "workspace" / "billing", ("main",), "main")
        _git(other, "remote", "add", "origin", "https://dev.azure.com/contoso/Ops/_git/billing")

        class Refused:
            asked = 0

            def __call__(self) -> str:
                self.asked += 1
                raise AzureError("The Azure CLI gave no token. Run `az login`, then post again.")

            def forget(self) -> None:
                pass

        refused = Refused()
        app = AdkReviewTuiApp(config_path=config)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = refused
            app._ask_for_pull_request()
            await app.workers.wait_for_complete()
            await pilot.pause()
            note = str(app.screen.query_one("#open-pr-note", Static).render())
            title = "a title long enough to wrap the line of the dialog at eighty columns, " * 2
            choices = [(f"payments #{n}  {title} (Jane)", None) for n in range(12)]
            app.screen.listed(choices, note)  # the longest note: a refusal after the count
            await pilot.pause()
            bottom = app.screen.query_one("#btn-open-pr-no").region.bottom
        assert refused.asked == 1 and note.count("az login") == 1, (refused.asked, note)
        assert bottom <= 24, bottom

    @pytest.mark.asyncio
    async def test_a_pull_request_whose_last_push_is_not_here_says_to_fetch(self, tmp_path: Path, fake_ado) -> None:
        """The remote's branch here is older than the pull request's last push: a review of it would read the
        commits before it. The banner names the push and the key that fetches it."""
        from conftest import FakeToken
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            _opened(fake_ado, "d" * 40)
            app._open_pull_request(fake_ado.pr)
            await _until(
                pilot, lambda: app.selected_head == "origin/feature/tax" and app._listing_for is None, "opened"
            )
            await app.workers.wait_for_complete()
            await pilot.pause()
            banner = str(app.query_one("#status-banner", Static).render())
        assert "dddddddddddd" in banner and "F5" in banner, banner

    @pytest.mark.asyncio
    async def test_a_pull_request_no_repository_here_holds_or_a_forks_changes_nothing(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """A pull request from a fork has its branch in another repository, and one of a repository no folder of the
        workspace clones has none here: each is said, with what to do, and the choice stays as it was."""
        from conftest import FakeToken
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._credential = FakeToken()
            before = (app.selected_project, app.selected_head, app.selected_base)
            _opened(fake_ado, "c" * 40, forkSource={"name": "refs/heads/feature/tax"})
            app._open_pull_request(fake_ado.pr)
            await app.workers.wait_for_complete()
            await pilot.pause()
            fork = str(app.query_one("#status-banner", Static).render())
            _opened(fake_ado, "c" * 40)
            clone = tmp_path / "workspace" / "payments"
            _git(clone, "remote", "set-url", "origin", "https://dev.azure.com/contoso/Web/_git/billing")
            app._open_pull_request(fake_ado.pr)
            await app.workers.wait_for_complete()
            await pilot.pause()
            nowhere = str(app.query_one("#status-banner", Static).render())
            after = (app.selected_project, app.selected_head, app.selected_base)
        assert "fork" in fork and "by hand" in fork, fork
        assert "contoso/web/payments" in nowhere and "remote" in nowhere, nowhere
        assert after == before

    @pytest.mark.asyncio
    async def test_the_open_button_asks_for_an_address_and_refuses_one_that_names_no_pull_request(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """The button asks for the address in a dialog of its own, apart from the field kept for the branch chosen: a
        paste there would be kept for that branch before the pull request's own was known. An address that names no
        pull request is refused with what to paste, and nothing is asked of Azure DevOps."""
        from tui.app import AdkReviewTuiApp
        from tui.modals import OpenPullRequestModal

        app = AdkReviewTuiApp(config_path=_two_clones(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:  # the smallest screen: the button is there before a review
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            await pilot.click("#btn-open-pr")
            dialog = await _shown(app, pilot, OpenPullRequestModal)
            dialog.query_one("#open-pr-url").value = "https://github.com/contoso/payments/pull/1"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
        assert said and "pull request" in said[-1].lower(), said
        assert fake_ado.seen == [], "nothing asked of Azure DevOps"

    @pytest.mark.asyncio
    async def test_a_pasted_address_is_kept_for_its_branch_and_comes_back_after_a_restart(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Input, Static

        from tui.app import AdkReviewTuiApp

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            assert app.selected_head == "feature/orders"
            field = app.query_one("#pr-url", Input)
            field.value = f"  {PR_ADDRESS}?_a=files  "
            await pilot.pause()
            assert "Pull request 123 of payments." in str(app.query_one("#pr-status", Static).render())
            app._sync_chosen_branch("head", "feature/tax")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert field.value == "" and app._pull_request is None, "another branch has its own pull request"
            app._sync_chosen_branch("head", "feature/orders")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert field.value == PR_ADDRESS, "as Azure DevOps shows it: the query a paste carried is gone"
        again = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with again.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(again, pilot)
            assert again.query_one("#pr-url", Input).value == PR_ADDRESS
            assert again._pull_request is not None and again._pull_request.number == 123

    @pytest.mark.asyncio
    async def test_a_wrong_address_pasted_is_told_whole_where_the_bar_cuts_it(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: the address bar is one row, and its answer to a wrong address showed as
        "This is not a pull", the address to paste never in view. A paste, or Enter, tells it whole; a key typed
        does not, so an address typed by hand raises no notice at each key."""
        from textual import events
        from textual.widgets import Input

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            app.query_one("#pr-url", Input).focus()
            await pilot.press("h")
            typed = list(said)
            app.post_message(events.Paste("https://github.com/contoso/payments/pull/1"))
            await pilot.pause()
            pasted = list(said)
            await pilot.press("enter")
            await pilot.pause()
        assert typed == [], typed
        assert len(pasted) == 1 and "pullrequest/<number>" in pasted[0], pasted
        assert len(said) == 2 and said[1] == pasted[0], said

    @pytest.mark.asyncio
    async def test_a_wrong_address_shows_what_to_paste_and_keeps_nothing(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Input, Static

        from tui import azure_state

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            field, status = app.query_one("#pr-url", Input), app.query_one("#pr-status", Static)
            held = "https://" + ":".join(("u", "hunter2")) + "@dev.azure.com/c"  # joined: no line holds a login
            for wrong in ("https://github.com/contoso/payments/pull/1", "[bold]x[/]", held):
                field.value = wrong
                await pilot.pause()
                said = str(status.render())
                assert "pullrequest/<number>" in said and "hunter2" not in said, said
                assert app._pull_request is None
            assert azure_state.pull_request_url(app.selected_project.path, "feature/orders") == ""
            field.value = PR_ADDRESS
            await pilot.pause()
            field.value = PR_ADDRESS + "abc"  # an address being edited: the one kept stays until another is whole
            await pilot.pause()
            assert azure_state.pull_request_url(app.selected_project.path, "feature/orders") == PR_ADDRESS
            field.value = ""
            await pilot.pause()
            assert azure_state.pull_request_url(app.selected_project.path, "feature/orders") == "", "emptied: forgotten"
            assert str(status.render()) == ""

    @pytest.mark.asyncio
    async def test_o_opens_the_findings_lines_at_the_reviewed_commit(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Input

        from tui.azure import line_link, parse_pr_url

        app = _payments_screen(tmp_path)
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            await pilot.press("o")
            await pilot.pause()
            assert opened == [line_link(parse_pr_url(PR_ADDRESS), "a" * 40, "src/app.py", 42, 57)]

    @pytest.mark.asyncio
    async def test_o_typed_in_a_text_field_is_a_letter_and_off_the_findings_tab_nothing(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Input, TabbedContent

        app = _payments_screen(tmp_path)
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            search = app.query_one("#finding-search", Input)
            search.focus()
            await pilot.press("o", "p")
            await pilot.pause()
            assert search.value == "op" and opened == []
            app.query_one("#output-tabs", TabbedContent).active = "tab-report"
            await pilot.pause()
            app.query_one("#repo-list").focus()
            await pilot.press("o", "p")
            await pilot.pause()
            assert opened == [], "a finding the person cannot see is opened by no stray key"

    @pytest.mark.asyncio
    async def test_o_and_p_say_what_is_missing_where_nothing_can_open(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import DataTable, Input, TabbedContent

        app = _payments_screen(tmp_path)
        opened: list[str] = []
        said: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda url, **_how: opened.append(url)
            app.notify = _plainly(said)
            app.query_one("#output-tabs", TabbedContent).active = "tab-findings"
            await pilot.pause()
            app.query_one("#findings-table", DataTable).focus()
            await pilot.press("o", "p")
            await pilot.pause()
            assert said == ["Run a review first."] * 2
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            await pilot.press("o", "p")
            await pilot.pause()
            assert said[2:] == ["Paste the pull request's address above the table first."] * 2
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path, line=None, lines=None))
            await pilot.press("o")
            await pilot.pause()
            assert said[4:] == ["This finding names no line."] and opened == []

    @pytest.mark.asyncio
    async def test_p_opens_the_file_in_the_pull_request_and_the_thread_once_posted(self, tmp_path: Path) -> None:
        pytest.importorskip("textual")
        from textual.widgets import DataTable, Input

        from tui import azure_state
        from tui.azure import files_link, parse_pr_url, thread_link

        app = _payments_screen(tmp_path)
        opened: list[str] = []
        pr = parse_pr_url(PR_ADDRESS)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            table = app.query_one("#findings-table", DataTable)
            assert str(table.get_row_at(0)[-1]) == "", "not posted yet"
            await pilot.press("p")
            await pilot.pause()
            azure_state.keep_posted(pr, "1a2b3c4d5e6f7a8b", 148)
            app._refresh_posted()
            app._render_findings_table()
            await pilot.pause()
            assert str(table.get_row_at(0)[-1]) == "148", "the thread it was posted as"
            await pilot.press("p")
            await pilot.pause()
            assert opened == [files_link(pr, "src/app.py"), thread_link(pr, 148)]


#: The file a comment is placed in: its line 2 is the finding's, and "Apply changes" would replace it.
CODE = "def charge(total):\n    total = with_tax(total)\n    return total\n"
TAXED = "    total = with_tax(total)"


def _payments_with_code(tmp_path: Path):  # the application, not yet running, and the commit that holds the file
    """The screen over `payments`, its branch `feature/orders` holding `src/app.py` at the commit answered."""
    from conftest import _committed

    from tui.app import AdkReviewTuiApp

    repo = _repository(tmp_path / "workspace" / "payments", ("main", "feature/orders"), "feature/orders")
    return AdkReviewTuiApp(config_path=_screen_config(tmp_path)), _committed(repo, "src/app.py", CODE)


def _plainly(said: list[str]):  # what stands in for the screen's `notify`
    """A `notify` that keeps each notice in `said` and holds it to `markup=False`: a notice here can hold a
    repository's path or Azure DevOps' own message, and Textual reads `[name]` in one as a tag."""

    def notify(message: str, **options: object) -> None:
        assert options.get("markup") is False, f"drawn as markup: {message!r}"
        said.append(str(message))

    return notify


async def _ready_screen(app, pilot, tmp_path: Path, fake, sha: str, **changed: object) -> list[str]:
    """The finding of line 2 shown, the fake pull request taking a comment on `sha`, and a login that needs no
    `az`: the notices the screen then raises."""
    from conftest import FakeToken, _ready_to_comment

    said: list[str] = []
    await _wait_for_branches(app, pilot)
    app.notify = _plainly(said)
    app._credential = FakeToken()
    app._pull_request = fake.pr
    _ready_to_comment(fake, sha)
    await _show_findings(app, pilot, _pull_request_report(tmp_path, sha, **{"line": 2, "lines": [2, 2], **changed}))
    return said


async def _until(pilot, done, what: str):  # what `done()` gives once it holds
    """What `done()` gives once it is true, asked for 4 s, or `what` as the failure: the screen changes in steps, and a
    test that looked once looked between two of them on a loaded machine."""
    for _ in range(200):
        await pilot.pause(0.02)
        if found := done():
            return found
    raise AssertionError(what)


async def _stop(app, pilot, *, button: bool = False) -> None:
    """Stop pressed, its key or its button, and waited for until the screen says its run stopped. Waiting for a worker
    Stop cancelled raises `WorkerCancelled`: measured on a Windows runner, where the cancel had not ended as the wait
    began, four tests failed that passed on macOS and Linux."""
    from textual.worker import WorkerCancelled

    await (pilot.click("#btn-stop") if button else pilot.press("ctrl+x"))
    await _until(pilot, lambda: not app._reviewing, "the run stopped")
    with contextlib.suppress(WorkerCancelled):
        await app.workers.wait_for_complete()
    await pilot.pause()


async def _shown(app, pilot, kind):  # a dialog, ready for a click
    """Until a dialog of `kind` is the screen and built. Textual makes a dialog the screen before it builds its widgets:
    reproduced by reading a file's lines late, as a loaded machine does, a test that clicked in between found no
    button, and three tests failed in one run of the suite."""
    built = f"no {kind.__name__} opened"
    return await _until(pilot, lambda: isinstance(app.screen, kind) and app.screen.is_mounted and app.screen, built)


async def _editor(app, pilot):  # the dialog `c` opened
    """`c` pressed, and the editor it opens once the file's lines are read."""
    from tui.modals import CommentModal

    await pilot.press("c")
    return await _shown(app, pilot, CommentModal)


async def _posted(app, pilot) -> None:
    """Until the post's worker has ended and the screen has drawn what it answered."""
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _asked(app, pilot):  # the question Post asks
    """Post pressed in the editor, and the question it asks before anything is sent."""
    from tui.modals import ConfirmPostModal

    await pilot.click("#btn-comment-post")
    return await _shown(app, pilot, ConfirmPostModal)


async def _post_and_confirm(app, pilot) -> None:
    """Post pressed in the editor, yes said to the question after it, and the post's answer waited for."""
    await _asked(app, pilot)
    await pilot.click("#btn-post-yes")
    await _posted(app, pilot)


def _two_repositories(tmp_path: Path):  # the application, not yet running
    """The screen over two repositories: `a_payments`, which it picks as it opens, and `b_billing`."""
    from tui.app import AdkReviewTuiApp

    _repository(tmp_path / "workspace" / "a_payments", ("main", "feature/orders"), "feature/orders")
    _repository(tmp_path / "workspace" / "b_billing", ("main", "feature/bills"), "feature/bills")
    return AdkReviewTuiApp(config_path=_screen_config(tmp_path))


def _billing(app):  # the second repository, as the screen lists it
    """The repository a test picks to leave the first one."""
    return next(project for project in app.projects if project.name == "b_billing")


class TestACommentOnThePullRequest:
    """`c`: the draft, the person's edit, a confirmation, and one post, to the file's line in the pull request."""

    @pytest.mark.parametrize(("size", "spans"), [((80, 24), 3), ((80, 24), 12), ((100, 30), 12), ((140, 45), 12)])
    @pytest.mark.asyncio
    async def test_the_editors_buttons_and_some_of_its_text_show_on_a_small_screen(
        self, tmp_path: Path, size: tuple[int, int], spans: int
    ) -> None:
        """Read by an audit, and measured: at 80 by 24 the lines a finding replaces pushed Post, Copy and Cancel out
        of the dialog, none of their rows in view for 12 lines, and at 100 by 30 too. The buttons stay at its foot,
        the editor keeps rows to type in, and no replaced line is cut where it cannot be scrolled to."""
        from textual.widgets import Button

        from tui.modals import CommentModal

        app = _payments_screen(tmp_path)
        async with app.run_test(size=size) as pilot:
            await _wait_for_branches(app, pilot)
            lines = [f"    total = price * rate  # line {n}" for n in range(1, spans + 1)]
            app.push_screen(CommentModal("**major**: Round the tax", f"src/app.py, lines 1 to {spans}", lines))
            await _shown(app, pilot, CommentModal)
            await pilot.pause()
            inside = app.screen.query_one("#comment-dialog").region.shrink((1, 1, 1, 1))  # within its border
            hidden = [
                name
                for name in ("#btn-comment-post", "#btn-comment-copy", "#btn-comment-cancel")
                if inside.intersection(region := app.screen.query_one(name, Button).region) != region
            ]
            editor = app.screen.query_one("#comment-text").region.intersection(inside)
            rows = min(editor.bottom, app.screen.query_one("#modal-buttons").region.y) - editor.y
            text = app.screen.query_one("#comment-replaced")
            replaced, scrolls = text.region.height, getattr(text.parent, "max_scroll_y", 0)
        assert hidden == [] and rows >= 3, (hidden, rows)
        assert replaced >= spans or scrolls > 0, (replaced, scrolls)

    @pytest.mark.parametrize("kind", ["sync", "open", "diverged", "explain", "comment", "confirm"])
    @pytest.mark.asyncio
    async def test_every_dialogs_buttons_show_on_an_80_by_24_screen(self, tmp_path: Path, kind: str) -> None:
        """Read by two audits: on an 80 by 24 screen the findings detail, the comment editor's buttons and the address
        bar's answer were each cut, and each was found by hand. Every dialog, given long content, keeps each of its
        buttons whole inside it."""
        from textual.widgets import Button

        from tui import modals
        from tui.azure import PullRequest
        from tui.git_sync import SyncStatus

        long_branch = "feature/" + "a-long-branch-name-" * 4
        status = SyncStatus("behind", long_branch, "origin/" + long_branch, ahead=3, behind=7)
        target = "src/payments/" + "deep/" * 12 + "tax.py, lines 10 to 40, pull request 12345 of payments"
        built = {
            "sync": lambda: modals.SyncBranchModal("head", status),
            "open": modals.OpenPullRequestModal,
            "diverged": lambda: modals.DivergedBranchModal("head", status),
            "explain": lambda: modals.ExplainCheckModal("rule.lint"),
            "comment": lambda: modals.CommentModal("**major**: " + "word " * 80, target, ["    x = 1"] * 30),
            "confirm": lambda: modals.ConfirmPostModal(target),
        }
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            dialog = built[kind]()
            app.push_screen(dialog)
            await _shown(app, pilot, type(dialog))
            if kind == "open":
                pr = PullRequest("https://dev.azure.com/contoso", "Web", "payments", 7)
                dialog.listed([("payments #7 " + "a long title " * 8, pr)] * 8, "Eight open pull requests.")
            await pilot.pause()
            frame = app.screen.children[0].region.shrink((1, 1, 1, 1))  # within the dialog's border
            cut = [
                str(button.label)
                for button in app.screen.query(Button)
                if frame.intersection(button.region) != button.region
            ]
        assert cut == [], (kind, cut)

    @pytest.mark.asyncio
    async def test_c_opens_the_draft_and_cancel_sends_nothing(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Static, TextArea

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            text = editor.query_one("#comment-text", TextArea).text
            assert text.startswith("**major**: `charge()` adds tax twice") and "```suggestion" in text
            assert "src/app.py, line 2, pull request 123" in str(editor.query_one("#comment-target", Static).render())
            assert TAXED in str(editor.query_one("#comment-replaced", Static).render()), "the line it stands on"
            await pilot.click("#btn-comment-cancel")
            await pilot.pause()
            assert not isinstance(app.screen, CommentModal) and fake_ado.seen == []
            editor = await _editor(app, pilot)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, CommentModal) and fake_ado.seen == [], "Escape cancels too"

    @pytest.mark.asyncio
    async def test_each_dialog_fits_a_terminal_of_80_columns(self, tmp_path: Path, fake_ado) -> None:
        """macOS Terminal opens at 80 columns by 24 lines. Read by an audit, and measured: there the editor was 100
        columns wide, its Cancel button past the screen's edge and the right of the text being edited out of sight,
        and the check's own dialog cut its Close button."""
        pytest.importorskip("textual")
        from textual.widgets import Button, TextArea

        from tui.modals import ExplainCheckModal

        def fits(dialog, frame: str) -> None:
            for widget in (dialog.query_one(frame), *dialog.query(Button), *dialog.query(TextArea)):
                assert widget.region.right <= 80 and widget.region.bottom <= 24, (widget.id, widget.region)

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            app.action_explain_selected_check()
            fits(await _shown(app, pilot, ExplainCheckModal), "#explain-dialog")
            await pilot.press("escape")
            await _until(pilot, lambda: not isinstance(app.screen, ExplainCheckModal), "the check's dialog stayed")
            fits(await _editor(app, pilot), "#comment-dialog")
            fits(await _asked(app, pilot), "#modal-dialog")

    @pytest.mark.asyncio
    async def test_go_back_returns_to_the_editor_with_the_edit(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from textual.widgets import Static, TextArea

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "my own words"
            asked = str((await _asked(app, pilot)).query_one("#confirm-post", Static).render())
            assert asked == "Post this comment as you, on src/app.py, line 2, pull request 123?"
            assert app.focused is not None and app.focused.id == "btn-post-no", "Enter by habit posts nothing"
            await pilot.click("#btn-post-no")
            await _shown(app, pilot, CommentModal)
            assert app.screen.query_one("#comment-text", TextArea).text == "my own words"
            shown = str(app.screen.query_one("#comment-replaced", Static).render())
            assert TAXED in shown, "the lines stay in view: a suggestion typed in the editor replaces them too"
            assert fake_ado.seen == []

    @pytest.mark.asyncio
    async def test_post_and_confirm_send_the_edited_text_once(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, TextArea

        from tui import azure_state
        from tui.azure import Place, thread_payload

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "my own words"
            await _post_and_confirm(app, pilot)
            posts = [seen for seen in fake_ado.seen if seen.method == "POST"]
            assert len(posts) == 1
            assert json.loads(posts[0].body) == thread_payload("my own words", Place("src/app.py", 2, 2, TAXED), 2, 7)
            assert posts[0].headers["authorization"] == "Bearer tok"
            assert said[-1] == "Posted as thread 148. Press p to open it."
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == "148"
            assert azure_state.posted_threads(fake_ado.pr).get("1a2b3c4d5e6f7a8b") == 148
            held = {
                "id": 148,
                "comments": [
                    {"content": "my own words\n\n*adk-review, guideline.correctness, finding 1a2b3c4d5e6f7a8b*"}
                ],
            }
            fake_ado.instead("GET", "/threads", {"value": [held]})  # as Azure DevOps holds it now
            await pilot.press("c")
            await _posted(app, pilot)
            assert said[-1] == "This finding is posted as thread 148. Press p to open it."
            assert len([seen for seen in fake_ado.seen if seen.method == "POST"]) == 1, "no editor, and no second post"

    @pytest.mark.asyncio
    async def test_a_refused_post_says_why_and_keeps_the_edit(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from textual.widgets import DataTable, TextArea

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            fake_ado.answer("POST", "/threads", status=403)  # before the answer `_ready_screen` gives: served first
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "my own words"
            await _post_and_confirm(app, pilot)
            assert (
                said[-1] == 'Your account may not comment here. Ask for the "Contribute to pull requests" permission.'
            )
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == "", "nothing is posted"
            editor = await _editor(app, pilot)
            assert editor.query_one("#comment-text", TextArea).text == "my own words", "the edit is not lost"

    @pytest.mark.asyncio
    async def test_a_finding_posted_before_is_found_and_marked_with_no_second_post(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """The screen forgot it, or a teammate posted it: the thread's own mark says so at the post."""
        pytest.importorskip("textual")
        from textual.widgets import DataTable

        mark = "*adk-review, guideline.correctness, finding 1a2b3c4d5e6f7a8b*"
        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            fake_ado.answer("GET", "/threads", {"value": [{"id": 5, "comments": [{"content": mark}]}]})
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            await _editor(app, pilot)
            await _post_and_confirm(app, pilot)
            assert said[-1] == "This finding was posted before, as thread 5. Nothing was posted."
            assert all(seen.method == "GET" for seen in fake_ado.seen)
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == "5"

    @pytest.mark.asyncio
    async def test_copy_puts_the_edited_text_on_the_clipboard_and_posts_nothing(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from textual.widgets import TextArea

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "to paste by hand"
            await pilot.click("#btn-comment-copy")
            await pilot.pause()
            assert app.clipboard == "to paste by hand"
            assert isinstance(app.screen, CommentModal) and fake_ado.seen == [], "still open, and nothing sent"

    @pytest.mark.asyncio
    async def test_an_empty_comment_is_not_posted(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from textual.widgets import TextArea

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "  \n "
            await pilot.click("#btn-comment-post")
            await pilot.pause()
            assert isinstance(app.screen, CommentModal) and said[-1] == "The comment is empty. Write it, or cancel."

    @pytest.mark.asyncio
    async def test_a_second_c_while_a_post_runs_posts_once(self, tmp_path: Path, fake_ado) -> None:
        """A worker that is its group's only one would stop the post under way and start another: the screen waits
        for the one it sent."""
        pytest.importorskip("textual")
        import threading

        from conftest import FakeToken

        class Held(FakeToken):
            """A login that waits for the test's word, as `az` takes its seconds."""

            def __call__(self) -> str:
                asked.set()
                assert go.wait(timeout=20)
                return self.header

        asked, go = threading.Event(), threading.Event()
        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            app._credential = Held()
            await _editor(app, pilot)
            await _asked(app, pilot)
            await pilot.click("#btn-post-yes")
            await _until(pilot, asked.is_set, "the post never asked for the login")
            await pilot.press("c")
            await pilot.pause()
            assert said[-1] == "A comment is on its way. Wait for its answer."
            go.set()
            await _posted(app, pilot)
            assert [seen.method for seen in fake_ado.seen].count("POST") == 1
            assert said[-1] == "Posted as thread 148. Press p to open it."

    @pytest.mark.asyncio
    async def test_no_review_starts_behind_the_editor(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")
        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        started: list[bool] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            app._execute_review = lambda *, dry_run: started.append(dry_run)
            await _editor(app, pilot)
            await pilot.press("ctrl+r", "ctrl+d", "o", "p")
            await pilot.pause()
            assert started == [] and isinstance(app.screen, CommentModal)

    @pytest.mark.asyncio
    async def test_a_finding_no_comment_fits_gets_a_sentence_and_no_editor(self, tmp_path: Path, fake_ado) -> None:
        pytest.importorskip("textual")

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha, line=None, lines=None)
            await pilot.press("c")
            await pilot.pause()
            assert said[-1] == "This finding names no line."
            await _show_findings(app, pilot, _pull_request_report(tmp_path, sha, line=90, lines=[90, 91]))
            await pilot.press("c")
            await _posted(app, pilot)
            assert said[-1] == "This clone does not hold the reviewed commit's file. Fetch, then press c again."
            await _show_findings(app, pilot, _pull_request_report(tmp_path, "f" * 40, line=2, lines=[2, 2]))
            await pilot.press("c")
            await _posted(app, pilot)
            assert said[-1] == "This clone does not hold the reviewed commit's file. Fetch, then press c again."
            await _show_findings(app, pilot, _pull_request_report(tmp_path, sha, resolved=True))
            await pilot.press("c")
            await pilot.pause()
            assert said[-1] == "This finding is resolved. Its lines are of an earlier commit."
            assert not isinstance(app.screen, CommentModal) and fake_ado.seen == []

    @pytest.mark.asyncio
    async def test_text_that_looks_like_markup_is_drawn_as_written(self, tmp_path: Path, fake_ado) -> None:
        """A model's title, a repository's path and Azure DevOps' own message are text: Textual reads `[name]` as a
        tag, and one that closes nothing ends the screen."""
        pytest.importorskip("textual")
        from textual.widgets import Static, TextArea

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            fake_ado.answer("POST", "/threads", status=500, raw=b'{"message": "[/bold] closes [nothing"}')
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha, title="[bold]x[/] and [/red]")
            editor = await _editor(app, pilot)
            assert "[bold]x[/] and [/red]" in editor.query_one("#comment-text", TextArea).text
            assert "src/app.py" in str(editor.query_one("#comment-target", Static).render())
            await _post_and_confirm(app, pilot)
            check = "Press c again: one that arrived is found, and no second one is sent."
            assert said[-1] == f"Azure DevOps answered 500: [/bold] closes [nothing. The comment may be posted. {check}"


class TestWhatTheBranchsReviewFound:
    """What an independent review of the branch reproduced on the screen, each held here."""

    @pytest.mark.asyncio
    async def test_an_address_pasted_while_another_repository_loads_is_kept_for_none(self, tmp_path: Path) -> None:
        """Reproduced by review: after a pick the repository is the new one while the branch and the field are
        still the last one's, for the whole fetch. A paste then was kept as the new repository's, under the last
        one's branch, and the field was emptied when the load ended."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import Input

        from tui import config

        app = _two_repositories(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            assert app.selected_project.name == "a_payments"
            billing = _billing(app)
            app._on_project_chosen(billing, picked=False)
            field = app.query_one("#pr-url", Input)
            field.value = PR_ADDRESS  # typed before the new repository's branches are listed
            await pilot.pause()
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            kept = config.STATE_DIR / "azure.json"
            held = json.loads(kept.read_text(encoding="utf-8"))["pull_requests"] if kept.is_file() else {}
            assert held == {}, "kept for no repository: the pair it was pasted for was never whole"
            field.value = PR_ADDRESS  # and once the branches are listed, it is the new repository's
            await pilot.pause()
            held = json.loads(kept.read_text(encoding="utf-8"))["pull_requests"]
            assert held == {os.path.normcase(billing.path): {"feature/bills": PR_ADDRESS}}

    @pytest.mark.asyncio
    async def test_editing_the_address_leaves_the_chosen_finding_chosen(self, tmp_path: Path) -> None:
        """Reproduced by review: each key in the address field drew the table again and chose its first row, and
        the notice "paste the address first" leads the person straight into it."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import DataTable, Input

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            report = json.loads(_pull_request_report(tmp_path).read_text(encoding="utf-8"))
            second = {
                **report["findings"][0],
                "file": "src/later.py",
                "fingerprint": "ffffffffffffffff",
                "title": "later",
            }
            report["findings"].append(second)
            (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
            await _show_findings(app, pilot, tmp_path / "report.json")
            table = app.query_one("#findings-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            assert app._selected_finding.file == "src/later.py"
            field = app.query_one("#pr-url", Input)
            for typed in (PR_ADDRESS[:-1], PR_ADDRESS, PR_ADDRESS + "x", ""):
                field.value = typed
                await pilot.pause()
                assert (app._selected_finding.file, table.cursor_row) == ("src/later.py", 1), typed

    @pytest.mark.asyncio
    async def test_a_cancelled_edit_comes_back_and_an_untouched_draft_is_not_kept(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """Reproduced by review: Escape threw a written comment away with no question."""
        pytest.importorskip("textual")
        from textual.widgets import TextArea

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            drafted = editor.query_one("#comment-text", TextArea).text
            await pilot.press("escape")
            await pilot.pause()
            assert app._drafts == {}, "nothing was written: nothing is kept"
            editor = await _editor(app, pilot)
            editor.query_one("#comment-text", TextArea).text = "half a thought"
            await pilot.click("#btn-comment-cancel")
            await pilot.pause()
            editor = await _editor(app, pilot)
            assert editor.query_one("#comment-text", TextArea).text == "half a thought"
            editor.query_one("#comment-text", TextArea).text = "   "
            await pilot.press("escape")
            await pilot.pause()
            editor = await _editor(app, pilot)
            assert editor.query_one("#comment-text", TextArea).text == drafted, "emptied, then closed: the draft again"
            assert fake_ado.seen == []

    @pytest.mark.asyncio
    async def test_a_lines_escape_sequences_are_drawn_as_spaces_in_the_editor(self, tmp_path: Path, fake_ado) -> None:
        """Reproduced by review: the lines a suggestion replaces are the branch's own, and one that held a
        terminal's commands, a screen cleared or the clipboard written, reached the terminal as written."""
        pytest.importorskip("textual")
        from conftest import _committed
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        escape, bell = chr(27), chr(7)
        hostile = f"    total = 1  # {escape}[2J{escape}]52;c;aGk={bell} and {chr(0x202E)}gnp.exe"
        repo = _repository(tmp_path / "workspace" / "payments", ("main", "feature/orders"), "feature/orders")
        sha = _committed(repo, "src/app.py", f"def charge(total):\n{hostile}\n\treturn total\n")
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha, lines=[2, 3])
            editor = await _editor(app, pilot)
            shown = str(editor.query_one("#comment-replaced", Static).render())
            assert "total = 1  #  [2J ]52;c;aGk=  and  gnp.exe" in shown, shown
            assert not [letter for letter in shown if not letter.isprintable() and letter != "\n"], shown
            assert "    return total" in shown, "a tab is drawn as the spaces it stands for"

    @pytest.mark.asyncio
    async def test_findings_of_a_review_that_named_no_commit_say_so(self, tmp_path: Path) -> None:
        """Read by review: findings of what is staged have no commit, and each key answered "Run a review first",
        right after one."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import Input

        app = _payments_screen(tmp_path)
        said: list[str] = []
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.notify = _plainly(said)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            report = json.loads(_pull_request_report(tmp_path).read_text(encoding="utf-8"))
            (tmp_path / "report.json").write_text(json.dumps({**report, "reviewed": None}), encoding="utf-8")
            await _show_findings(app, pilot, tmp_path / "report.json")
            await pilot.press("o", "c")
            await pilot.pause()
            assert said == ["This review read no commit. Review the pull request's branch, then try again."] * 2
            assert opened == []

    @pytest.mark.asyncio
    async def test_the_palette_names_each_action_on_a_finding_and_runs_it(self, tmp_path: Path) -> None:
        """Read while checking the branch was whole: the palette named every other action of the screen, a review, a
        fetch, a check's explanation, and none of the three that act on a finding in its pull request."""
        pytest.importorskip("textual")
        from textual.widgets import Input

        from tui.azure import line_link, parse_pr_url
        from tui.commands import ReviewCommandProvider

        app = _payments_screen(tmp_path)
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            named = {label: run for label, run, _help in ReviewCommandProvider(app.screen)._entries()}
            assert {"Action: Open Finding's Lines (o)", "Action: Open Finding in Its Pull Request (p)"} <= set(named)
            assert "Action: Comment on Finding in Its Pull Request (c)" in named
            named["Action: Open Finding's Lines (o)"]()
            assert opened == [line_link(parse_pr_url(PR_ADDRESS), "a" * 40, "src/app.py", 42, 57)]


class TestWhatTheFirstRoundOfReviewFound:
    """What a code review of the branch reproduced on the screen, each held here."""

    @pytest.mark.asyncio
    async def test_a_post_whose_answer_did_not_come_is_never_said_to_be_unposted(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """Reproduced by review: the post arrived, a gateway answered 502 for it, and the screen said "not
        posted". The person then pastes the comment by hand, or posts again with the mark taken out: two threads."""
        pytest.importorskip("textual")
        from textual.widgets import DataTable, Static

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            fake_ado.answer("POST", "/threads", status=502, raw=b"<html>Bad Gateway</html>")
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            await _editor(app, pilot)
            await _post_and_confirm(app, pilot)
            check = "Press c again: one that arrived is found, and no second one is sent."
            assert said[-1] == f"Azure DevOps answered 502: Bad Gateway. The comment may be posted. {check}"
            banner = str(app.query_one("#status-banner", Static).render())
            assert "may be posted" in banner and "not posted" not in banner, banner
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == "", "not known to be posted"
            arrived = {
                "id": 77,
                "comments": [{"content": "*adk-review, guideline.correctness, finding 1a2b3c4d5e6f7a8b*"}],
            }
            fake_ado.instead("GET", "/threads", {"value": [arrived]})  # it had arrived
            await _editor(app, pilot)
            await _post_and_confirm(app, pilot)
            assert said[-1] == "This finding was posted before, as thread 77. Nothing was posted."
            assert [seen.method for seen in fake_ado.seen].count("POST") == 1

    @pytest.mark.asyncio
    async def test_a_thread_deleted_in_azure_devops_can_be_posted_again(self, tmp_path: Path, fake_ado) -> None:
        """Reproduced by review: what the screen kept was final, so a finding whose thread the person deleted in the
        browser could not be posted again, short of deleting the whole state file and every address with it. What is
        kept is a convenience: the pull request's own threads say what is posted."""
        pytest.importorskip("textual")
        from textual.widgets import DataTable

        from tui import azure_state

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            azure_state.keep_posted(fake_ado.pr, "1a2b3c4d5e6f7a8b", 148)
            app._refresh_posted()
            app._render_findings_table()
            await pilot.pause()
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == "148"
            await _editor(app, pilot)  # the fake holds no such thread: the editor opens
            assert said[-1] == "Thread 148 is gone from the pull request. You can post the finding again."
            assert azure_state.posted_threads(fake_ado.pr).get("1a2b3c4d5e6f7a8b") is None
            assert str(app.query_one("#findings-table", DataTable).get_row_at(0)[-1]) == ""
            assert all(seen.method == "GET" for seen in fake_ado.seen)

    @pytest.mark.asyncio
    async def test_a_finding_one_line_past_its_files_end_is_not_placed(self, tmp_path: Path, fake_ado) -> None:
        """Reproduced by review: the newline that ends a file was counted as a line of its own, so a finding one
        past the last line passed, and its comment was placed on a line the file does not have."""
        pytest.importorskip("textual")
        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha, line=4, lines=[4, 4])
            await pilot.press("c")
            await _posted(app, pilot)
            assert said[-1] == "This clone does not hold the reviewed commit's file. Fetch, then press c again."
            assert fake_ado.seen == []

    @pytest.mark.asyncio
    async def test_an_opened_link_is_shown_too_for_a_terminal_with_no_browser(self, tmp_path: Path) -> None:
        """Read by review: over SSH or in a container no browser opens, and the key then did nothing a person
        could see. The link stays on the screen, to copy."""
        pytest.importorskip("textual")
        from textual.widgets import Input, Static

        from tui.azure import files_link, line_link, parse_pr_url

        app = _payments_screen(tmp_path)
        pr = parse_pr_url(PR_ADDRESS)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.open_url = lambda _url, **_how: None  # as where no browser opens
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            await pilot.press("o")
            await pilot.pause()
            banner = app.query_one("#status-banner", Static)
            assert str(banner.render()) == "Opening in your browser: " + line_link(pr, "a" * 40, "src/app.py", 42, 57)
            await pilot.press("p")
            await pilot.pause()
            assert str(banner.render()) == "Opening in your browser: " + files_link(pr, "src/app.py")

    @pytest.mark.asyncio
    async def test_the_state_file_is_read_and_written_when_the_pull_request_changes_and_not_at_each_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured by review: each key in the findings' search field drew the table again, and each drawing read
        and parsed the state file on the screen's own loop. Each digit of a typed address wrote the file."""
        pytest.importorskip("textual")
        from textual.widgets import Input

        from tui import app as screen

        reads: list[str] = []
        writes: list[str] = []
        read, write = screen.posted_threads, screen.keep_pull_request_url
        monkeypatch.setattr(screen, "posted_threads", lambda pr: reads.append(pr.web) or read(pr))
        monkeypatch.setattr(screen, "keep_pull_request_url", lambda *kept: writes.append(kept[2]) or write(*kept))
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            field = app.query_one("#pr-url", Input)
            field.value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path))
            assert (reads, writes) == ([PR_ADDRESS], [PR_ADDRESS])
            search = app.query_one("#finding-search", Input)
            for typed in ("c", "ch", "cha", ""):
                search.value = typed
                await pilot.pause()
            field.value = PR_ADDRESS + "?_a=files"  # the same pull request, spelled another way
            await pilot.pause()
            field.value = "not an address"  # being edited: what is kept stays
            await pilot.pause()
            assert (reads, writes) == ([PR_ADDRESS], [PR_ADDRESS]), "one read and one write a pull request"
            field.value = ""
            await pilot.pause()
            assert writes == [PR_ADDRESS, ""], "emptied: forgotten, though the field held no address just before"

    @pytest.mark.asyncio
    async def test_another_repository_shows_none_of_the_last_pull_requests_threads(self, tmp_path: Path) -> None:
        """Reproduced by review: a pick of another repository emptied the address and kept the last pull request's
        posted threads, so a finding of the same identity there, as a second clone has, showed as posted."""
        pytest.importorskip("textual")
        from textual.widgets import Input

        from tui import azure_state
        from tui.azure import parse_pr_url

        app = _two_repositories(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            azure_state.keep_posted(parse_pr_url(PR_ADDRESS), "1a2b3c4d5e6f7a8b", 148)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await pilot.pause()
            assert app._posted_threads == {"1a2b3c4d5e6f7a8b": 148}
            app._on_project_chosen(_billing(app), picked=False)
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert (app._pull_request, app._posted_threads) == (None, {})


class TestWhatTheSecondRoundOfReviewFound:
    """What the second code review of the branch read or reproduced on the screen, each held here."""

    @pytest.mark.asyncio
    async def test_the_address_waits_for_a_repositorys_own_branch(self, tmp_path: Path) -> None:
        """Reproduced by review: while another repository loaded, a change of the mode ran the folders' refresh,
        which put the address back for the new repository under the last one's branch, and a paste then was kept
        there. Until the repository's own branch is chosen, the field takes nothing."""
        pytest.importorskip("textual")
        from textual.widgets import Input, Select

        app = _two_repositories(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            field = app.query_one("#pr-url", Input)
            assert not field.disabled
            billing = _billing(app)
            pairs: list[object] = []
            restore = app._restore_pull_request
            app._restore_pull_request = lambda: (restore(), pairs.append(app._pull_request_of))
            app._on_project_chosen(billing, picked=False)
            assert app._pull_request_of is None and field.disabled, "no pair yet: its branch is not chosen"
            app.query_one("#mode-select", Select).value = "all"  # its refresh of the folders runs while the list loads
            await pilot.pause()
            await _wait_for_branches(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert (billing.path, "feature/orders") not in pairs, "never the new repository under the last one's branch"
            assert pairs and pairs[-1] == (billing.path, "feature/bills") and not field.disabled

    @pytest.mark.asyncio
    async def test_o_on_a_resolved_finding_opens_nothing(self, tmp_path: Path) -> None:
        """Read by review: a resolved finding's lines were counted in an earlier review's commit, and the link
        showed that line number in this one's, which is other code."""
        pytest.importorskip("textual")

        from textual.widgets import Input

        app = _payments_screen(tmp_path)
        said: list[str] = []
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.notify = _plainly(said)
            app.open_url = lambda url, **_how: opened.append(url)
            app.query_one("#pr-url", Input).value = PR_ADDRESS
            await _show_findings(app, pilot, _pull_request_report(tmp_path, resolved=True))
            await pilot.press("o")
            await pilot.pause()
            assert said == ["This finding is resolved. Its lines are of an earlier commit."] and opened == []

    @pytest.mark.asyncio
    async def test_an_edit_kept_for_one_commit_does_not_come_back_for_another(self, tmp_path: Path, fake_ado) -> None:
        """Read by review: an edit a refused post left was kept by the finding alone. After the next review the
        finding is the same and its lines or its code may not be, and the old text, its suggestion with it, opened
        for the new lines."""
        pytest.importorskip("textual")
        from textual.widgets import TextArea

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            editor = await _editor(app, pilot)
            drafted = editor.query_one("#comment-text", TextArea).text
            editor.query_one("#comment-text", TextArea).text = "for the first commit"
            await pilot.press("escape")
            await pilot.pause()
            app._reviewed = "c" * 40  # as the next review's commit
            assert app._drafts and app._draft_key(app._selected_finding, fake_ado.pr, "c" * 40) not in app._drafts
            app._reviewed = sha
            editor = await _editor(app, pilot)
            assert editor.query_one("#comment-text", TextArea).text == "for the first commit"
            assert drafted != "for the first commit"

    @pytest.mark.asyncio
    async def test_no_comment_opens_while_a_review_runs_and_ctrl_x_cuts_in_the_editor(
        self, tmp_path: Path, fake_ado
    ) -> None:
        """Read by review: the editor is the screen's first field of many lines, where Ctrl+X cuts. The same key
        stops a running review from anywhere, so a cut in the editor ended a paid run and cut nothing."""
        pytest.importorskip("textual")
        from textual.widgets import TextArea

        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            app._reviewing = True
            await pilot.press("c")
            await pilot.pause()
            assert said[-1] == "A review is running. Comment when it ends." and not isinstance(app.screen, CommentModal)
            app._reviewing = False
            editor = await _editor(app, pilot)
            area = editor.query_one("#comment-text", TextArea)
            area.text = "cut me"
            area.select_all()
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert area.text == "", "the editor's own cut, since no review can run behind it"

    @pytest.mark.asyncio
    async def test_a_posted_thread_that_lost_its_mark_is_still_posted(self, tmp_path: Path, fake_ado) -> None:
        """Read by review: asked by its mark alone, a thread the person had posted with the mark taken out was said
        to be gone from the pull request, and forgotten. The screen holds the thread's own number: it asks for that."""
        pytest.importorskip("textual")
        from tui import azure_state
        from tui.modals import CommentModal

        app, sha = _payments_with_code(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            said = await _ready_screen(app, pilot, tmp_path, fake_ado, sha)
            azure_state.keep_posted(fake_ado.pr, "1a2b3c4d5e6f7a8b", 148)
            app._refresh_posted()
            fake_ado.instead("GET", "/threads", {"value": [{"id": 148, "comments": [{"content": "my own words"}]}]})
            await pilot.press("c")
            await _posted(app, pilot)
            assert said[-1] == "This finding is posted as thread 148. Press p to open it."
            assert not isinstance(app.screen, CommentModal)
            assert azure_state.posted_threads(fake_ado.pr) == {"1a2b3c4d5e6f7a8b": 148}


class TestWhatAnAuditFound:
    """What an outside audit of the screen named, each reproduced before its fix."""

    @pytest.mark.asyncio
    async def test_a_link_in_the_comment_or_the_hand_off_opens_nothing(self, tmp_path: Path, monkeypatch) -> None:
        """Read by an audit, and reproduced: the comment and the hand-off are drawn as Markdown, which makes a link of
        an address in a finding's words, and one click opened it in the browser, the widget's own default. Those
        words are a model's, and a steered one writes the address, what it read in its query. A finding's own tab
        was made plain text for this; these two tabs open nothing."""
        pytest.importorskip("textual")
        from textual.widgets import Markdown
        from textual.widgets.markdown import MarkdownBlock

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        opened: list[str] = []
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            monkeypatch.setattr(app, "open_url", lambda url, **_how: opened.append(url))
            for tab in ("#comment-md", "#handoff-md"):
                shown = app.query_one(tab, Markdown)
                await shown.update("1. The fix is at https://evil.example/?q=what-the-model-read")
                await pilot.pause()
                await shown.query(MarkdownBlock).first().action_link("https://evil.example/?q=what-the-model-read")
                await pilot.pause()
            assert opened == [], "a click on a link a model wrote opens nothing"

    @pytest.mark.asyncio
    async def test_the_command_shown_makes_no_folder_and_a_review_makes_its_own(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and counted: the line that shows the command built it with the folders a review writes
        to, so each key typed made the output folder and the cache's again, 22 calls for 11 keys. Showing a command
        writes nothing; the review makes the output folder as it starts, and the reviewer makes its own cache's, the
        operator's alone."""
        pytest.importorskip("textual")
        from textual.widgets import Input

        from tui import options
        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        outputs = tmp_path / "reviews" / "here"
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path, f'output_dir = "{outputs.as_posix()}"'))
        there: list[bool] = []

        async def ran(_cmd: list[str]) -> tuple[int, str]:
            there.append(outputs.is_dir())
            return 0, "## Verdict: APPROVED\n"

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#extra-path", Input).focus()
            await pilot.press(*"src")
            await pilot.pause()
            assert not outputs.exists() and not options.CACHE_DIR.exists(), "the command shown made a folder"
            monkeypatch.setattr(app, "_stream_subprocess", ran)
            app.selected_base = "origin/main"  # a target to review against
            await pilot.press("ctrl+r")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert there == [True], "the review's own folder, made before it ran"
            assert not options.CACHE_DIR.exists(), "the reviewer makes the cache's folder, for the operator alone"

    @pytest.mark.asyncio
    async def test_an_output_folder_that_cannot_be_made_is_said_and_the_screen_stays(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: with `output_dir` on a path no folder can be made at, a drive not there
        or a file in the way, the line that shows the command raised and the whole screen ended in a traceback as it
        opened. The screen opens, and the review that cannot write there says so, with the folder and the setting."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        in_the_way = tmp_path / "a-file"
        in_the_way.write_text("no folder\n", encoding="utf-8")
        outputs = in_the_way / "reviews"
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path, f'output_dir = "{outputs.as_posix()}"'))
        ran: list[list[str]] = []

        async def run(cmd: list[str]) -> tuple[int, str]:
            ran.append(cmd)
            return 0, ""

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = _plainly(said)
            assert "adk-review" in str(app.query_one("#cli-preview", Static).render()), "the command is still shown"
            monkeypatch.setattr(app, "_stream_subprocess", run)
            app.selected_base = "origin/main"  # a target to review against
            await pilot.press("ctrl+r")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert ran == [], "no review runs where its files have no folder"
            assert said and str(outputs) in said[-1] and "output_dir" in said[-1], said
            assert str(app.tui_config.config_path) in said[-1], "the file that holds the setting is named too"

    def test_a_branch_named_with_a_bar_is_listed(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: the branches were asked of git with `|` between a branch's name and its
        remote's, and a line was kept only where it split in four. Git allows `|` in a branch's name, so `feat/a|b`
        split in six and was left out of both lists, with no word. Packed here, as a fetch can leave a branch on any
        system: Windows holds no file named with a `|`, so `git branch` cannot make one there."""
        from tui.git_sync import list_branches

        repo = _repository(tmp_path / "payments", ("main", "feat/plain"), "main")
        _git(repo, "pack-refs", "--all")
        packed = repo / ".git" / "packed-refs"
        header, *refs = packed.read_text(encoding="utf-8").splitlines()
        refs.append(f"{_git(repo, 'rev-parse', 'main')} refs/heads/feat/a|b")
        refs.sort(key=lambda line: line.split(" ", 1)[1])  # git reads the file as sorted by name
        packed.write_text("\n".join([header, *refs]) + "\n", encoding="utf-8", newline="\n")
        assert "feat/a|b" in _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split("\n")
        assert [branch.name for branch in list_branches(str(repo))] == ["main", "feat/a|b", "feat/plain"]

    @pytest.mark.asyncio
    async def test_the_palette_finds_a_branch_past_the_fortieth(self, tmp_path: Path) -> None:
        """Read by an audit, and confirmed: the palette offered the first forty branches and searched those alone, so
        in a repository of the 306 to 354 branches the owner's own hold, a name typed in full found nothing for most
        of them. Every branch is searched; what the palette shows before a key is typed stays the first twelve."""
        pytest.importorskip("textual")
        from tui.app import AdkReviewTuiApp
        from tui.commands import ReviewCommandProvider
        from tui.git_sync import BranchInfo

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            names = [f"feature/n{number:03}" for number in range(60)]
            app.branches = [BranchInfo(name, False, "", name, "") for name in names]
            palette = ReviewCommandProvider(app.screen)
            found = [hit.text async for hit in palette.search("feature/n059")]
            assert "Head Branch: feature/n059" in found and "Base Branch: feature/n059" in found, found
            assert len([hit async for hit in palette.discover()]) == 12

    @pytest.mark.asyncio
    async def test_a_review_that_writes_no_comment_shows_none_and_a_dry_run_keeps_the_last(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and confirmed: a review of the same target that wrote no comment, its switch off, set
        the tab's name back and left the earlier review's comment under it, for Ctrl+Y to copy as this review's. The
        tab shows what the last review wrote, or that it wrote none. A dry run reviews nothing and writes none of the
        three: it leaves them as the last review left them, names and all."""
        pytest.importorskip("textual")
        from textual.widgets import Markdown, Switch, TabbedContent

        from tui.app import AdkReviewTuiApp

        _init_repo_with_remote(tmp_path)
        app = AdkReviewTuiApp(config_path=_screen_config(tmp_path))

        ran: list[list[str]] = []

        def written(cmd: list[str]) -> None:  # the files a decided review writes where its flags name them
            Path(cmd[cmd.index("--json") + 1]).write_text('{"schema": 2, "findings": []}', encoding="utf-8")
            for flag, words in (("--comment", "the comment of review"), ("--handoff", "the hand-off of review")):
                if flag in cmd:
                    Path(cmd[cmd.index(flag) + 1]).write_text(f"{words} {len(ran)}\n", encoding="utf-8")

        async def review(cmd: list[str]) -> tuple[int, str]:
            if refused or "--dry-run" in cmd:  # no review is decided: nothing is written
                ran.append(cmd)
                return (2 if refused else 0), "review: nothing was reviewed\n"
            written(cmd)
            ran.append(cmd)
            return 0, "## Verdict: APPROVED\n"

        refused: list[bool] = []

        async def run(key: str) -> None:
            await pilot.press(key)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            monkeypatch.setattr(app, "_stream_subprocess", review)
            app.selected_base = "origin/main"  # a target to review against
            tabs, comment = app.query_one("#output-tabs", TabbedContent), app.query_one("#comment-md", Markdown)
            app.query_one("#sw-comment", Switch).value = True
            app.query_one("#sw-handoff", Switch).value = True
            await run("ctrl+r")
            assert app.last_comment_md == "the comment of review 0\n" and app.last_handoff_md, "the first review's"
            await run("ctrl+d")
            assert app.last_comment_md == "the comment of review 0\n", "a dry run writes none, and takes none away"
            assert "ready" in str(tabs.get_tab("tab-comment").label) and "ready" in str(
                tabs.get_tab("tab-handoff").label
            )
            app.query_one("#sw-comment", Switch).value = False
            await run("ctrl+r")
            assert "--comment" not in ran[-1] and "ready" not in str(tabs.get_tab("tab-comment").label)
            assert app.last_comment_md == "", "the earlier review's comment, under a tab that says there is none"
            assert "generated yet" in comment.source, comment.source
            assert app.last_handoff_md == "the hand-off of review 2\n", "what this review did write is shown"
            # A review the reviewer refuses, exit 2, decides nothing and writes no report: it takes none away either.
            refused.append(True)
            await run("ctrl+r")
            assert len(ran) == 4 and app.last_handoff_md == "the hand-off of review 2\n", "a review that was refused"
            assert "ready" in str(tabs.get_tab("tab-handoff").label)

    @pytest.mark.asyncio
    async def test_ctrl_y_copies_the_open_tab_and_no_other(self, tmp_path: Path, monkeypatch) -> None:
        """Read by an audit, and confirmed: Ctrl+Y started from the report's text, so on the progress tab, and on the
        findings with none chosen, it copied the report and said it had copied "log content" or "findings content".
        Each tab copies its own text, the progress tab its lines, and a tab with nothing to copy says what to do."""
        pytest.importorskip("textual")
        from textual.widgets import TabbedContent

        from tui import app as screen

        _init_repo_with_remote(tmp_path)
        app = screen.AdkReviewTuiApp(config_path=_screen_config(tmp_path))
        copied: list[str] = []
        monkeypatch.setattr(screen, "copy_text", lambda _app, text: copied.append(text) or True)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            said: list[str] = []
            app.notify = lambda message, **_how: said.append(str(message))
            tabs = app.query_one("#output-tabs", TabbedContent)
            app.last_report_md = "the report\n"
            app._log_progress("$ adk-review payments")
            app._log_progress("guideline.security: started")

            async def copy_from(tab: str) -> str:
                tabs.active = tab
                await pilot.pause()
                await pilot.press("ctrl+y")
                await pilot.pause()
                return said[-1]

            assert "Copied" in await copy_from("tab-report") and copied == ["the report\n"]
            await copy_from("tab-log")
            assert copied[-1].endswith("$ adk-review payments\nguideline.security: started"), copied[-1]
            for tab, words in (("tab-findings", "No finding"), ("tab-comment", "comment"), ("tab-handoff", "hand-off")):
                assert words in await copy_from(tab) and len(copied) == 2, (tab, said[-1], copied)
                assert "Run a review" in said[-1], "what to do next, true whether a switch is on or off"
            assert "Read Comments and Work Items" in await copy_from("tab-pr") and len(copied) == 2, said[-1]

    def test_a_profile_no_review_has_is_refused_with_the_profiles_there_are(self, tmp_path: Path) -> None:
        """Read by an audit, and reproduced: `profile = "typo"` in `[defaults]`, and `profile = 123`, were taken as
        written, and the screen opened on the first profile it lists with no word; a preset's was passed over the same
        way. Each is refused as the config is read, as a `fail_on` no severity names is, with the profiles there are:
        the screen's start makes of it one sentence and exit 2."""
        from tui.config import load_tui_config

        cfg = tmp_path / ".tui.toml"
        for body, said in (
            ('[defaults]\nprofile = "typo"\n', r"\[defaults\] profile is 'typo'\. Give .*default-rules.* or "),
            ("[defaults]\nprofile = 123\n", r"\[defaults\] profile is 123\. Give "),
            ('[projects."my-service"]\nprofile = "rules"\n', r"\[projects\.my-service\] profile is 'rules'\. Give "),
        ):
            cfg.write_text(body, encoding="utf-8")
            with pytest.raises(ValueError, match=said):
                load_tui_config(cfg)
        cfg.write_text(
            '[defaults]\nprofile = "rules-only"\n\n[projects."my-service"]\nprofile = "full"\n', encoding="utf-8"
        )
        held = load_tui_config(cfg)
        assert held.defaults.profile == "rules-only" and held.projects["my-service"].profile == "full"
        cfg.write_text('[projects."my-service"]\nbase = "main"\n', encoding="utf-8")
        assert load_tui_config(cfg).projects["my-service"].profile == "", "a preset that names none"


class TestHowTheScreenSaysWhatAReviewDecided:
    """The verdict a review's JSON holds, read beside its findings. Read by an audit of the screen, which read the
    findings alone, so a review that did not look read as one that found nothing."""

    @pytest.mark.asyncio
    async def test_a_review_that_could_not_check_everything_never_reads_as_one_with_no_findings(
        self, tmp_path: Path
    ) -> None:
        """Read by an audit, and reproduced: a degraded review that kept no finding, a guideline's call refused,
        showed "This review has no findings." on the Findings tab, the words of a review that looked and found
        nothing. It says the review could not check everything. An approval with no finding reads as before."""
        pytest.importorskip("textual")
        import json

        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        degraded = _pull_request_report(tmp_path)
        data = json.loads(degraded.read_text(encoding="utf-8"))
        data.update(findings=[], verdict="DEGRADED", could_not_look={"guideline.security": "the model answered 429"})
        degraded.write_text(json.dumps(data), encoding="utf-8")
        approved = tmp_path / "approved.json"
        approved.write_text(json.dumps({**data, "verdict": "APPROVED", "could_not_look": {}}), encoding="utf-8")
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)  # the pick the screen makes as it opens clears the tab once
            await _show_findings(app, pilot, degraded)
            said = str(app.query_one("#finding-detail", Static).render())
            assert "could not check everything" in said and "has no findings" not in said, said
            await _show_findings(app, pilot, approved)
            assert str(app.query_one("#finding-detail", Static).render()) == "This review has no findings."

    @pytest.mark.asyncio
    async def test_a_screen_that_read_no_verdict_never_says_a_review_found_nothing(self, tmp_path: Path) -> None:
        """Read by review of the fix: the Findings tab said "This review has no findings." for every verdict but a
        degraded one, so a target no review had read yet, and a report the screen could not read, each said a review
        had looked and found nothing. Only an approval reads so; one with no verdict read asks for a review."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            unreviewed = str(app.query_one("#finding-detail", Static).render())
            await _show_findings(app, pilot, broken)
            unread = str(app.query_one("#finding-detail", Static).render())
        assert "has no findings" not in unreviewed and "has no findings" not in unread, (unreviewed, unread)
        # Read by the final code review: a report the screen could not read was given the verdict of one read whose
        # verdict it does not know, and the tab said the review kept no finding.
        assert "kept no finding" not in unread, unread

    @pytest.mark.asyncio
    async def test_a_review_that_ended_with_no_report_says_why_in_the_report_and_the_banner(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: a review the reviewer refused, exit 2 with no arm ready, as a first run
        with no login is, cleared the Report tab and showed it empty, and the banner said only "exit code 2". The
        reason was on the progress tab alone. What the review said is in the report, and its last line in the
        banner, read from a real child's error stream as a review's is."""
        pytest.importorskip("textual")

        from textual.widgets import RichLog, Static

        refused = "review: the Copilot arm is not ready. Run adk-review-login once"
        child = f"import sys; sys.stderr.write({refused!r} + '\\n'); sys.exit(2)"
        _child_runs(monkeypatch, child)
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            await _reviewed_once(app, pilot)
            report = "\n".join(line.text for line in app.query_one("#report-log", RichLog).lines)
            banner = str(app.query_one("#status-banner", Static).render())
            copied = app.last_report_md  # what Ctrl+Y copies from the Report tab
        assert refused in report, report
        assert refused in banner and "exit code 2" in banner, banner
        assert refused in copied, f"read by review: Ctrl+Y said there was nothing to copy: {copied!r}"

    @pytest.mark.asyncio
    async def test_a_run_with_no_report_is_said_whatever_its_code_and_only_its_own_last_lines(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by review of the fix: a reviewer that could not load, a broken install, exits 1 with Python's own
        traceback and no report, and an exit code of 1 read as a decided review left the Report tab empty again; a run
        cut short repeated every progress line in the report; and a run whose stream gave nothing would have shown the
        reason of the run before. The run is read by the report it wrote, its last 20 lines shown, its own alone."""
        pytest.importorskip("textual")

        from textual.widgets import Static

        lines = "[sys.stderr.write(f'progress {n}\\n') for n in range(30)]"
        crash = f"import sys; {lines}; sys.stderr.write('ModuleNotFoundError: no module x\\n'); sys.exit(1)"
        _child_runs(monkeypatch, crash)
        app = _payments_screen(tmp_path)

        async def silent(_cmd: list[str]) -> tuple[int, str]:
            return 2, ""

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            await _reviewed_once(app, pilot)
            report, banner = app.last_report_md, str(app.query_one("#status-banner", Static).render())
            assert "no module x" in report and "progress 29" in report and "progress 0\n" not in report, report
            assert "no module x" in banner and "exit code 1" in banner, banner
            monkeypatch.setattr(app, "_stream_subprocess", silent)
            await _reviewed_once(app, pilot)
            report, banner = app.last_report_md, str(app.query_one("#status-banner", Static).render())
        assert "no module x" not in report and "gave no reason" in report, report
        assert banner.endswith("ended with no report (exit code 2)."), f"the code once, with no reason given: {banner}"

    @pytest.mark.asyncio
    async def test_the_banner_names_the_verdict_the_review_reached(self, tmp_path: Path, monkeypatch) -> None:
        """Read by an audit: the banner named the exit code alone, a number the person had to know, where the JSON
        the screen had just read named the verdict. It names both."""
        pytest.importorskip("textual")

        from textual.widgets import Static

        app = _payments_screen(tmp_path)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1))
            app.selected_base = "main"
            await _reviewed_once(app, pilot)
            banner = str(app.query_one("#status-banner", Static).render())
        assert "REQUEST_CHANGES" in banner and "exit code 1" in banner, banner


def _child_runs(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Each review the screen runs as a Python child running `code`, in place of the reviewer's."""
    import sys

    import tui.app as screen

    monkeypatch.setattr(screen, "review_command", lambda _argv: [sys.executable, "-c", code])


def _report(verdict: str, titles: tuple[str, ...] = ()) -> str:
    """A review's JSON report: its verdict, and a major for each of `titles`."""
    import json

    found = [
        {
            "check": "rule.lint",
            "file": "a.py",
            "line": n,
            "severity": "major",
            "title": title,
            "evidence": "+ x = 1",
            "suggestion": "Mend it.",
            "fingerprint": f"{n:016x}",
        }
        for n, title in enumerate(titles, 1)
    ]
    return json.dumps({"schema": 2, "verdict": verdict, "findings": found, "reviewed": {"head_sha": "b" * 40}})


def _stubbed_run(verdict: str, code: int, titles: tuple[str, ...] = (), stdout: str | None = None):
    """A review run in place of the reviewer's child: `_report`'s JSON written where the command names it, and
    `stdout` as its text report, the verdict's heading where none is given."""
    import asyncio

    report = _report(verdict, titles)

    async def run(cmd: list[str]) -> tuple[int, str]:
        await asyncio.to_thread(Path(cmd[cmd.index("--json") + 1]).write_text, report, "utf-8")
        return code, f"## Verdict: {verdict}\n" if stdout is None else stdout

    return run


def _opened_panels(app) -> list[str]:
    """The panels the screen shows open, by id."""
    from textual.widgets import Collapsible

    return [str(c.id) for c in app.query(Collapsible) if not c.has_class("hidden") and not c.collapsed]


def _saved_report(app, report: str) -> None:
    """`report` as the saved report of the target the screen has chosen, as an earlier review leaves it."""
    from tui import options

    saved = options.baseline_path(app.selected_project.path, app.selected_base, app.selected_head, app.selected_mode)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(report, encoding="utf-8")


class TestWhatAReviewLeavesOnTheScreen:
    """What the tabs hold once a review ends, is stopped, or is followed by another pick. Read by an audit of the
    screen, each reproduced by a probe first."""

    @pytest.mark.asyncio
    async def test_a_fetch_after_a_review_keeps_that_review_over_an_older_saved_one(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """F5, or the same branch picked again, read the target's saved report over the review just shown. A degraded
        review is never saved, so its findings went from view, and where the saved one approved, the tab said "This
        review has no findings." beside a report that said DEGRADED."""
        pytest.importorskip("textual")

        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            _saved_report(app, _report("APPROVED"))
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("DEGRADED", 3, ("first", "second")))
            await _reviewed_once(app, pilot)
            assert [row.title for row in app.findings_rows] == ["first", "second"]
            app.action_refresh_repo()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            kept, verdict = [row.title for row in app.findings_rows], app._verdict
            said = str(app.query_one("#finding-detail", Static).render())
        assert kept == ["first", "second"] and verdict == "DEGRADED", (kept, verdict)
        assert "has no findings" not in said, said

    @pytest.mark.asyncio
    async def test_a_review_that_could_not_check_everything_says_so_beside_the_findings_it_kept(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A degraded review that kept findings looked like any other on the Findings tab: its verdict was in the
        banner alone, which the next copy or link replaced. The tab's name says it, in words, never colour alone."""
        pytest.importorskip("textual")
        from textual.widgets import TabbedContent

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            tabs = app.query_one("#output-tabs", TabbedContent)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("DEGRADED", 3, ("first",)))
            await _reviewed_once(app, pilot)
            degraded = str(tabs.get_tab("tab-findings").label)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("first",)))
            await _reviewed_once(app, pilot)
            decided = str(tabs.get_tab("tab-findings").label)
        assert "DEGRADED" in degraded and "(1" in degraded, degraded
        assert "DEGRADED" not in decided and "(1)" in decided, decided

    @pytest.mark.asyncio
    async def test_a_stopped_review_leaves_nothing_of_the_last_report_to_copy_and_says_it_stopped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Stop emptied the Report tab, but Ctrl+Y on it copied the review before, as the stopped run's own, and
        nothing but the banner said the run stopped. The tab says so, and that is what it copies."""
        pytest.importorskip("textual")

        from textual.widgets import RichLog

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            real = app._stream_subprocess
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",), "FIRST REPORT\n"))
            await _reviewed_once(app, pilot)
            assert "FIRST REPORT" in app.last_report_md
            child = "import sys, time; sys.stderr.write('review: running\\n'); sys.stderr.flush(); time.sleep(30)"
            _child_runs(monkeypatch, child)
            monkeypatch.setattr(app, "_stream_subprocess", real)
            await pilot.press("ctrl+r")
            await _until(pilot, lambda: app._said, "the run's first line")
            await _stop(app, pilot)
            copied = app.last_report_md
            report = "\n".join(line.text for line in app.query_one("#report-log", RichLog).lines)
        assert "FIRST REPORT" not in copied and "stopped" in copied, copied
        assert "stopped" in report and "Ctrl+R" in report, report

    @pytest.mark.asyncio
    async def test_a_review_stopped_or_failed_after_another_target_is_picked_writes_nothing_on_its_report(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by an audit, and reproduced: a review that ended after another pick was shown on none of its tabs, but
        one stopped or one that could not run wrote its note on the Report tab, for Ctrl+Y to copy as the new target's
        report and for Ctrl+R, which reviews the new target, to run again. The banner says it, and the tab is left."""
        pytest.importorskip("textual")
        import asyncio

        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            started = asyncio.Event()

            async def runs(_cmd: list[str]) -> tuple[int, str]:
                started.set()
                await asyncio.sleep(30)
                return 0, ""

            monkeypatch.setattr(app, "_stream_subprocess", runs)
            await pilot.press("ctrl+r")
            await _until(pilot, started.is_set, "the run started")
            app.selected_head = "feature/tax"  # another target, picked while the review runs
            await _stop(app, pilot)
            stopped = app.last_report_md, str(app.query_one("#status-banner", Static).render())

            async def fails(_cmd: list[str]) -> tuple[int, str]:
                app.selected_head = "feature/orders"
                raise OSError("no room left on the disk")

            monkeypatch.setattr(app, "_stream_subprocess", fails)
            await pilot.press("ctrl+r")
            await _until(
                pilot,
                lambda: not app._reviewing and "no room" in str(app.query_one("#status-banner", Static).render()),
                "the failure",
            )
            await app.workers.wait_for_complete()
            await pilot.pause()
            failed = app.last_report_md
        assert stopped[0] == "" and "stopped" in stopped[1], stopped
        assert failed == "", failed

    @pytest.mark.asyncio
    async def test_a_target_picked_while_a_review_is_read_is_shown_none_of_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Reproduced with a slow read: the screen checked the target before it read the report, the comment and the
        hand-off, so a branch picked meanwhile was shown the review of the one before, its findings and the commit a
        comment is posted against among them. The target is read again once all is read."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        import tui.app as screen

        app = _payments_screen(tmp_path)
        read = screen.load_report

        def picked_meanwhile(path: Path):
            app.call_from_thread(setattr, app, "selected_head", "feature/tax")  # the person's pick, as it is read
            return read(path)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(screen, "load_report", picked_meanwhile)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("old target's",)))
            await _reviewed_once(app, pilot)
            rows, banner = app.findings_rows, str(app.query_one("#status-banner", Static).render())
        assert rows == [], [row.title for row in rows]
        assert "another target" in banner, banner

    @pytest.mark.asyncio
    async def test_a_runs_error_lines_reach_the_screen_with_no_terminal_command_in_them(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A run's error lines reached the banner, the Report tab and what Ctrl+Y copies with their control
        characters, a terminal's escape among them, as a branch's own lines once did. Each is drawn as the report
        draws a model's line: no escape, no credential."""
        pytest.importorskip("textual")

        from textual.widgets import Static

        said = "review: \x1b[2J cleared \x9b31m red token=" + "ghp_" + "a1B2" * 9
        child = f"import sys; sys.stderr.write({said!r} + '\\n'); sys.exit(2)"
        _child_runs(monkeypatch, child)
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            await _reviewed_once(app, pilot)
            banner, copied = str(app.query_one("#status-banner", Static).render()), app.last_report_md
        assert "cleared" in copied and "cleared" in banner, (copied, banner)
        assert not [text for text in (banner, copied) if "\x1b" in text or "\x9b" in text], (banner, copied)
        # Read by the simplify pass: the escape's tail stayed on the screen, and git's own messages, a fetch's among
        # them, which can name a remote with its token, reached the banner and the progress tab as written.
        assert not [text for text in (banner, copied) if "[2J" in text or "ghp_" in text], (banner, copied)

    @pytest.mark.parametrize(
        ("nested", "titles"),
        [
            ("[" * 200_000 + "]" * 200_000, []),  # json's own read refuses it: 100,000 reads
            # A list is no title, however deep: 2,000 is read by json on every stack, where 100,000 a small one refuses.
            ('{"schema": 2, "findings": [{"check": "rule.lint", "title": ' + "[" * 2_000 + "]" * 2_000 + "}]}", [""]),
        ],
        ids=["the whole report", "a title"],
    )
    def test_a_report_nested_past_pythons_depth_closes_no_screen(
        self, tmp_path: Path, nested: str, titles: list[str]
    ) -> None:
        """A report nested past Python's depth raised `RecursionError`, which nothing caught, and the screen closed:
        read by an audit, in json's own read of the whole; read by a code review, in `str()` of a title json had read,
        100,000 lists deep. The first reads as no report; a title that is no text reads as none."""
        from tui.findings import load_report

        deep = tmp_path / "deep.json"
        deep.write_text(nested, encoding="utf-8")
        assert [row.title for row in load_report(deep)[0]] == titles


class TestTheKeysAndTheRoomTheScreenGives:
    """Where the keys act as the screen opens, which keys end it, and the room the results get. Read by an audit of the
    screen, each reproduced by a probe first."""

    @pytest.mark.asyncio
    async def test_the_screen_opens_with_the_keys_on_the_repository_list(self, tmp_path: Path) -> None:
        """The scan marks the list as loading, and a loading list cannot hold the focus, so the screen's own focus took
        the first widget it counts, a chip of the hidden dock: the arrow keys moved nothing, and Enter pressed a chip
        no one could see."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            focused = app.focused
        assert focused is not None and focused.id == "repo-list", focused

    @pytest.mark.asyncio
    async def test_the_dialog_for_a_branch_behind_its_remote_moves_no_branch_on_enter(self, tmp_path: Path) -> None:
        """The dialog opened on "Update Local Branch", so Enter by habit ran `git branch -f`. It opens on the answer
        that changes nothing, as the dialog before a post does."""
        pytest.importorskip("textual")
        from tui.git_sync import SyncStatus
        from tui.modals import SyncBranchModal

        app = _payments_screen(tmp_path)
        answers: list[bool] = []
        behind = SyncStatus("behind", "feature/orders", "origin/feature/orders", behind=2)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.push_screen(SyncBranchModal("head", behind), answers.append)
            await pilot.pause()
            focused = app.focused
            await pilot.press("enter")
            await pilot.pause()
        assert focused is not None and focused.id == "btn-sync-no", focused
        assert answers == [False], answers

    @pytest.mark.asyncio
    async def test_q_ends_no_running_review_and_ctrl_q_ends_the_screen(self, tmp_path: Path) -> None:
        """A plain `q` closed the screen from any list, the table or a chip, the review it ran with it. It quits a
        screen with no review running; while one runs it says how to stop it. Read by a code review: with `q` gone,
        a terminal that keeps Ctrl+Q for itself, as VS Code's does, had no key that quits. Ctrl+Q, Textual's own,
        quits whatever runs."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#repo-list").focus()
            app._reviewing = True  # as while a review runs
            await pilot.press("q")
            await pilot.pause()
            after_q = app.is_running
            told = [notice.message for notice in app._notifications]
            await pilot.press("ctrl+q")
            await pilot.pause()
            after_ctrl_q = app.is_running
        assert after_q and not after_ctrl_q, (after_q, after_ctrl_q)
        assert [said for said in told if "Ctrl+X" in said], told
        idle = _payments_screen(tmp_path / "idle")
        async with idle.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(idle, pilot)
            idle.query_one("#repo-list").focus()
            await pilot.press("q")
            await pilot.pause()
            assert not idle.is_running, "q quits a screen with no review running"

    @pytest.mark.asyncio
    async def test_a_review_that_ends_gives_its_results_the_room_of_the_panels(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """At 80 by 24 the results got one row while the panels stayed open, and at 140 by 45 five. A review that ends
        minimizes the panels, as Ctrl+T does, and Ctrl+O or a chip brings each back."""
        pytest.importorskip("textual")

        app = _payments_screen(tmp_path)

        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            before = _opened_panels(app)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",)))
            await _reviewed_once(app, pilot)
            after = _opened_panels(app)
            await pilot.press("ctrl+o")
            await pilot.pause()
            restored = _opened_panels(app)
        assert before and after == [] and restored == before, (before, after, restored)


class TestWhatTheCodeReviewOfTheAuditFound:
    """Read by the code review of the audit's branch, each reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_a_target_picked_while_the_comment_draws_is_given_none_of_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The target was checked once all was read, but the comment and the hand-off were drawn after it, a long
        comment for seconds, so a target picked meanwhile was given that review's comment and hand-off, ready to copy.
        They are drawn with no await now, so a pick lands before the check or after the drawing: picked while the
        hand-off is read, both tabs say the review wrote none."""
        pytest.importorskip("textual")
        from textual.widgets import TabbedContent

        app = _payments_screen(tmp_path)
        reads: list[object] = []

        async def written(path: object) -> str:
            reads.append(path)
            if len(reads) == 2:
                app.selected_head = "feature/tax"  # the person's pick, as the hand-off is read
            return "the old target's text"

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(app, "_written", written)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",)))
            await _reviewed_once(app, pilot)
            tabs = app.query_one("#output-tabs", TabbedContent)
            labels = [str(tabs.get_tab(f"tab-{flag}").label) for flag in ("comment", "handoff")]
            kept = (app.last_comment_md, app.last_handoff_md)
        assert kept == ("", ""), kept
        assert not [label for label in labels if "ready" in label], labels

    @pytest.mark.asyncio
    async def test_a_saved_report_read_while_a_review_ends_never_replaces_it(self, tmp_path: Path, monkeypatch) -> None:
        """F5's read of the saved report began while a review still ran, so the check that the tab shows this
        session's own review passed; the review ended degraded, which is never saved, and the read that came back
        after it put the older review over it. The check is made again once the read is back."""
        pytest.importorskip("textual")

        import tui.app as screen

        app = _payments_screen(tmp_path)
        degraded = tmp_path / "degraded.json"
        degraded.write_text(_report("DEGRADED", ("its own",)), encoding="utf-8")
        read = screen.load_report

        def review_ends_meanwhile(path: Path):
            if path != degraded:
                rows, reviewed, verdict = read(degraded)
                app.call_from_thread(app._hold, rows, reviewed, verdict, app._target())
            return read(path)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            _saved_report(app, _report("APPROVED"))
            monkeypatch.setattr(screen, "load_report", review_ends_meanwhile)
            app.action_refresh_repo()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            verdict, rows = app._verdict, [row.title for row in app.findings_rows]
        assert verdict == "DEGRADED" and rows == ["its own"], (verdict, rows)

    def test_a_report_holding_a_number_past_pythons_digits_reads_as_no_report(self, tmp_path: Path) -> None:
        """Python refuses an integer of more than 4,300 digits with a `ValueError` no narrower `except` caught, and the
        screen closed on a saved report that held one."""
        from tui.findings import load_report

        long = tmp_path / "long.json"
        long.write_text('{"schema": ' + "9" * 5_000 + "}", encoding="utf-8")
        assert load_report(long) == ([], None, None)

    @pytest.mark.asyncio
    async def test_a_failed_fetch_is_told_with_no_credential_and_no_escape(self, tmp_path: Path, monkeypatch) -> None:
        """git's own message reached the banner and the progress tab made safe, and the notice as git wrote it: a
        remote's address with its token, a terminal's escape."""
        pytest.importorskip("textual")
        import tui.app as screen
        from tui.git_sync import FetchResult

        said = "fatal: unable to access 'https://user:" + "ghp_" + "a1B2" * 9 + "@example.com/r.git/' \x1b[2J"
        monkeypatch.setattr(screen, "fetch_remote", lambda *_args, **_kwargs: FetchResult(False, said))
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.action_refresh_repo()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            told = [notice.message for notice in app._notifications]
        assert [said for said in told if "unable to access" in said], told
        assert not [said for said in told if "ghp_" in said or "\x1b" in said or "[2J" in said], told

    @pytest.mark.asyncio
    async def test_a_report_with_a_verdict_the_screen_does_not_know_is_never_called_no_report(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A review that wrote its report, with a verdict this screen does not know, a newer reviewer's, and said
        nothing on its standard output, was said to have ended with no report, its findings drawn beside the words."""
        pytest.importorskip("textual")

        from textual.widgets import Static

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("SOMETHING_NEW", 1, ("its own",), stdout=""))
            await _reviewed_once(app, pilot)
            banner, rows = str(app.query_one("#status-banner", Static).render()), app.findings_rows
        assert "no report" not in app.last_report_md and "no report" not in banner, (app.last_report_md, banner)
        assert [row.title for row in rows] == ["its own"], rows

    @pytest.mark.asyncio
    async def test_a_run_that_wrote_no_report_leaves_the_panels_open(self, tmp_path: Path, monkeypatch) -> None:
        """A review refused, as one for nothing under --path is, minimized every panel, the step to mend among them.
        Only a review that wrote its report gives the results the room."""
        pytest.importorskip("textual")

        _child_runs(monkeypatch, "import sys; sys.stderr.write('review: nothing under --path src\\n'); sys.exit(2)")
        app = _payments_screen(tmp_path)

        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            before = _opened_panels(app)
            await _reviewed_once(app, pilot)
            after = _opened_panels(app)
        assert before and after == before, (before, after)

    @pytest.mark.asyncio
    async def test_the_progress_tab_keeps_a_lines_own_spaces(self, tmp_path: Path) -> None:
        """Each progress line was made one line, so the command a review ran, copied from the progress tab, named
        `My  Docs` as `My Docs`, another folder, and a traceback lost its indentation."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app._log_progress("$ adk-review . --path 'My  Docs'")
            app._log_progress('  File "x.py", line 3')
            kept = app._progress[-2:]
        assert kept == ["$ adk-review . --path 'My  Docs'", '  File "x.py", line 3'], kept

    @pytest.mark.asyncio
    async def test_the_stop_button_stops_a_running_review(self, tmp_path: Path, monkeypatch) -> None:
        """Read by the second simplify pass, by all four of its reviews: a method added above `action_stop_review`
        took its decorator, so the Stop button, enabled only while a review runs, said to press Ctrl+X and stopped
        nothing. No test clicked it."""
        pytest.importorskip("textual")
        child = "import sys, time; sys.stderr.write('review: running\\n'); sys.stderr.flush(); time.sleep(8)"
        _child_runs(monkeypatch, child)
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            await pilot.press("ctrl+r")
            await _until(pilot, lambda: app._said, "the run's first line")
            await _stop(app, pilot, button=True)
            stopped = app.last_report_md
        assert "stopped" in stopped, stopped


class TestWhatTheSecondCodeReviewFound:
    """Read by the second code review of the audit's branch, each reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_textuals_own_notices_keep_their_markup(self, tmp_path: Path) -> None:
        """The screen's notify drew every notice with no markup, Textual's own too, so Ctrl+C said "Press
        [b]ctrl+q[/b] to quit the app". Only the screen's own notices, each sent with no markup, are made safe."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#repo-list").focus()
            await pilot.press("ctrl+c")
            await pilot.pause()
            told = [(notice.message, notice.markup) for notice in app._notifications]
        assert [said for said, markup in told if "to quit" in said and markup], told

    @pytest.mark.asyncio
    async def test_a_review_stopped_before_its_child_runs_says_so(self, tmp_path: Path, monkeypatch) -> None:
        """Stop works from the moment a review starts, but the note was written only for a stop while its child ran:
        stopped while its choices were saved, the Report tab kept the review before, for Ctrl+Y to copy as its own."""
        pytest.importorskip("textual")
        import threading

        import tui.app as screen

        app = _payments_screen(tmp_path)
        saving, released, real = threading.Event(), threading.Event(), screen.save_repo_preset

        def slow(*args: object) -> object:
            saving.set()
            released.wait(2)  # a slow disk until the stop is seen, never forever
            return real(*args)

        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",), "FIRST REPORT\n"))
            await _reviewed_once(app, pilot)
            monkeypatch.setattr(screen, "save_repo_preset", slow)
            await pilot.press("ctrl+r")
            await _until(pilot, saving.is_set, "the choices being saved")
            await _stop(app, pilot)
            copied = app.last_report_md
            released.set()  # read by the third simplify pass: the thread waited out its 2 s in every run
        assert "FIRST REPORT" not in copied and "stopped" in copied, copied

    @pytest.mark.asyncio
    async def test_a_report_read_as_the_screen_closes_draws_nothing(self, tmp_path: Path) -> None:
        """`_hold` drew nothing once the tabs were gone, but the comment's and the hand-off's tabs were drawn after it
        all the same, `NoMatches`, a worker's error where the screen closed during the report's read."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        report = tmp_path / "report.json"
        report.write_text(_report("APPROVED"), encoding="utf-8")
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
        assert await app._load_generated_files({"json": report}) is None

    @pytest.mark.asyncio
    async def test_q_keeps_a_comment_not_yet_posted(self, tmp_path: Path) -> None:
        """`q` refused to quit while a review ran, but closed the screen over a comment left for later, kept in memory
        alone, and over a post on its way. It says so, and Ctrl+Q quits without it."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.query_one("#repo-list").focus()
            app._drafts[("src/app.py", "42", "1a2b3c4d5e6f7a8b")] = "a comment left for later"
            await pilot.press("q")
            await pilot.pause()
            running = app.is_running
            told = [notice.message for notice in app._notifications]
            # Read by the third simplify pass: told to press Ctrl+Q, which VS Code's terminal keeps for itself, a person
            # there had no way out but to empty each draft. A second `q` quits without them.
            await pilot.press("q")
            await pilot.pause()
            after_twice = app.is_running
        assert running and [said for said in told if "q again" in said], (running, told)
        assert not after_twice, "a second q quits without the comment"


class TestWhatTheThirdSimplifyPassFound:
    """Read by the third /simplify pass over the audit's branch."""

    def test_every_notice_the_screen_sends_draws_no_markup(self) -> None:
        """A branch update's notice lost its `markup=False` when the screen's notify forced it, and kept no flag once
        notify left Textual's own notices their markup, so git's message was drawn as markup and never made safe. Each
        of the screen's own notices passes it; Textual's, which carry markup, keep theirs."""
        import ast

        loose = []
        for source in sorted(FRONT_END.glob("*.py")):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr != "notify" or isinstance(node.func.value, ast.Call):  # super().notify passes it on
                    continue
                flag = next((keyword.value for keyword in node.keywords if keyword.arg == "markup"), None)
                if not (isinstance(flag, ast.Constant) and flag.value is False):
                    loose.append(f"{source.name}:{node.lineno}")
        assert loose == [], loose


class TestWhatTheThirdCodeReviewFound:
    """Read by the third code review of the audit's branch, each reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_a_saved_review_with_no_findings_is_never_read_as_the_last_one(self, tmp_path: Path) -> None:
        """A saved approval with no finding showed no "prior" mark and said "This review has no findings.", so back on
        a target whose last review could not check everything, an older approval read as the latest review."""
        pytest.importorskip("textual")
        from textual.widgets import Static, TabbedContent

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            _saved_report(app, _report("APPROVED"))
            app.action_refresh_repo()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            label = str(app.query_one("#output-tabs", TabbedContent).get_tab("tab-findings").label)
            said = str(app.query_one("#finding-detail", Static).render())
        assert "prior" in label, label
        assert said != "This review has no findings." and "saved" in said, said

    @pytest.mark.asyncio
    async def test_a_review_whose_screen_closes_as_its_report_is_read_draws_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The loader drew nothing once the tabs were gone, but the run that called it chose the Report tab and folded
        the panels after it, `NoMatches`, a worker's error where the screen closed during the report's read."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            real = app._load_generated_files

            async def closing(outputs: dict, target: object = None) -> object:
                await app.query_one("#output-tabs").remove()  # as Textual's harness takes the widgets away first
                return await real(outputs, target)

            monkeypatch.setattr(app, "_load_generated_files", closing)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("APPROVED", 0))
            await _reviewed_once(app, pilot)
            assert app.is_running, "the screen is still open"


class TestWhatTheFourthCodeReviewFound:
    """Read by the fourth code review of the audit's branch, each reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_a_review_that_could_not_run_leaves_none_of_the_last_report_to_copy(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """An error while the choices were saved, or the output folder made, left the review before on the Report tab,
        for Ctrl+Y to copy as this one's, beside a banner that said this one could not run."""
        pytest.importorskip("textual")
        import tui.app as screen

        def gone(*_args: object) -> None:
            raise OSError("the output drive is gone")

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",), "FIRST REPORT\n"))
            await _reviewed_once(app, pilot)
            monkeypatch.setattr(screen, "make_output_dir", gone)
            await _reviewed_once(app, pilot)
            copied = app.last_report_md
        assert "FIRST REPORT" not in copied and "could not run" in copied and "drive is gone" in copied, copied

    @pytest.mark.asyncio
    async def test_outputs_cleared_as_the_screen_closes_draw_nothing(self, tmp_path: Path) -> None:
        """A branch's sync clears the outputs past its dialog and its update, and found the tabs gone where the screen
        closed meanwhile: one of its three calls was guarded, `NoMatches` from the other two."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
        app._reset_outputs()

    @pytest.mark.asyncio
    async def test_a_report_with_a_verdict_the_screen_does_not_know_never_asks_for_a_review(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A report whose verdict this screen does not know, with no finding kept, left the Findings tab asking for a
        review, right after one: it says the review kept no finding, and where its verdict is named."""
        pytest.importorskip("textual")
        from textual.widgets import Static

        from tui.app import RUN_FIRST

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("SOMETHING_NEW", 1, stdout=""))
            await _reviewed_once(app, pilot)
            said = str(app.query_one("#finding-detail", Static).render())
        assert said != RUN_FIRST and "Report tab" in said, said

    @pytest.mark.asyncio
    async def test_a_review_whose_report_is_not_kept_for_the_next_is_still_shown(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by the fifth simplify pass: the error a run cannot recover from was caught around the whole run, so a
        report that could not be kept for the next review's comparison, a full disk, said the review could not run
        and hid the review that had run. The review is shown, and the progress tab says what was not kept."""
        pytest.importorskip("textual")
        import tui.app as screen

        def full(*_args: object) -> None:
            raise OSError("no space left on the disk")

        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.selected_base = "main"
            monkeypatch.setattr(screen, "promote_baseline", full)
            monkeypatch.setattr(app, "_stream_subprocess", _stubbed_run("REQUEST_CHANGES", 1, ("x",), "THE REPORT\n"))
            await _reviewed_once(app, pilot)
            copied, rows, progress = app.last_report_md, app.findings_rows, app._progress
        assert "THE REPORT" in copied and [row.title for row in rows] == ["x"], copied
        assert [line for line in progress if "no space left" in line], progress


class TestWhatTheFinalCodeReviewFound:
    """Read by the fifth and final code review of the audit's branch, each reproduced by a test that failed first."""

    @pytest.mark.asyncio
    async def test_a_file_a_review_wrote_that_cannot_be_read_is_one_it_wrote_none_of(self, tmp_path: Path) -> None:
        """Reading the comment or the hand-off had no `except`, so a file a virus scan held ended the run as "could
        not run", over the review that had run. Such a file reads as none written, and the progress tab says so."""
        pytest.importorskip("textual")
        app = _payments_screen(tmp_path)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            held = tmp_path / "comment.md"
            held.write_bytes(b"\xff\xfe not UTF-8")  # a read that fails, as one a virus scan holds does
            read = await app._written(held)
            progress = app._progress
        assert read is None and [line for line in progress if "could not be read" in line], (read, progress)

    @pytest.mark.asyncio
    async def test_the_command_palette_opens_behind_no_dialog(self, tmp_path: Path) -> None:
        """Textual's Ctrl+P opened its palette over the dialog for a branch behind its remote, and its Run Review
        started a review behind the dialog, where the guides say only Ctrl+X and Ctrl+Q act."""
        pytest.importorskip("textual")
        from tui.git_sync import SyncStatus
        from tui.modals import SyncBranchModal

        app = _payments_screen(tmp_path)
        behind = SyncStatus("behind", "feature/orders", "origin/feature/orders", behind=2)
        async with app.run_test(size=(140, 45)) as pilot:
            await _wait_for_branches(app, pilot)
            app.push_screen(SyncBranchModal("head", behind))
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            on_top = type(app.screen).__name__
        assert on_top == "SyncBranchModal", on_top
