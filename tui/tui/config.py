"""The front end's config and where it and its state live: `tui/.tui.toml` in a checkout, which the person edits,
and for an installed copy `tui.toml` in the person's own config folder; the state in their own state folder.

Reproduced on a copy installed with `uv tool install`: the config and the saved reviews lived inside the tool's own
environment, which an uninstall removed, choices and all; and its first run named that environment's `lib/python3.14`
as the folder of repositories. Reported by the owner: in a checkout, the `.tui.toml` they edited was read no more once
the config lived in their own folder there too, and the old folder in the reviewer's package stayed after the move.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from adk_review.core.findings import RANK
from adk_review.judge.catalog import CONFIG, load_config

#: The folder each of the front end's folders is named after.
APP = "adk-review-tui"


def _home(variable: str, *fallback: str) -> Path:
    """The folder `variable` names where it names an absolute one, as the XDG spec reads it; else `~/<fallback>`."""
    named = os.environ.get(variable, "")
    return Path(named) if named and os.path.isabs(named) else Path.home().joinpath(*fallback)


def config_home(*, windows: bool = os.name == "nt") -> Path:
    """Where a person's own config lives: `%APPDATA%` on Windows, `$XDG_CONFIG_HOME` or `~/.config` elsewhere."""
    if windows:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return _home("XDG_CONFIG_HOME", ".config")


def state_home(*, windows: bool = os.name == "nt") -> Path:
    """Where a person's own state lives: `%LOCALAPPDATA%` on Windows, `$XDG_STATE_HOME` or `~/.local/state`
    elsewhere."""
    if windows:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return _home("XDG_STATE_HOME", ".local", "state")


TUI_DIR = Path(__file__).resolve().parent


def _checkout() -> Path | None:
    """The adk-review checkout the front end runs from: its root holds the reviewer's project file and `tui/` the
    front end's, which no install holds, with `.git` or without, as a ZIP download has none. None for an installed
    copy. Read by review: known by `.git` alone, a ZIP download was read as an installed copy, its config elsewhere."""
    root = TUI_DIR.parent.parent
    return root if (TUI_DIR.parent / "pyproject.toml").is_file() and (root / "pyproject.toml").is_file() else None


def default_config_path() -> Path:
    """Where the config is read when no `--config` names one: in a checkout, `tui/.tui.toml`, beside the front end,
    where the person edits it; in an installed copy, which has no checkout, `tui.toml` in the person's own config
    folder, which a reinstall leaves alone. Reported by the owner: with the config in their own folder in a checkout
    too, the `.tui.toml` they edited was read no more, and the screen named no file."""
    return TUI_DIR.parent / ".tui.toml" if _checkout() else config_home() / APP / "tui.toml"


DEFAULT_CONFIG_PATH = default_config_path()
#: The config in the person's own folder: an installed copy's, and from bd92a0a a checkout's too.
USER_CONFIG_PATH = config_home() / APP / "tui.toml"
#: Where a checkout kept the config when the front end was a part of the reviewer's package. Git moved the front
#: end's files, never this one, which it ignores: a checkout's first run takes it, or the person's own if edited later.
LEGACY_CONFIG_PATH = TUI_DIR.parent.parent / "adk_review" / "tui" / ".tui.toml"
#: The package's folder when it was named `adk_review_tui`. Git removed its files, never Python's cache in it, which
#: it ignores: a checkout's first run removes it.
OLD_PACKAGE_DIR = TUI_DIR.parent / "adk_review_tui"
#: An old `output_dir`, a path inside the package however it was written, which the move leaves out so the default
#: holds. Read by review: before bd92a0a a saved config held it as the full path, macOS's or Windows'.
_LEGACY_OUTPUT_DIR = re.compile(
    r'(?m)^output_dir\s*=\s*"[^"\n]*adk_review[/\\]+tui[/\\]+\.state[/\\]+outputs"[ \t]*\n?'
)
STATE_DIR = state_home() / APP
CACHE_DIR = STATE_DIR / "cache"
BASELINES_DIR = STATE_DIR / "baselines"
OUTPUTS_DIR = STATE_DIR / "outputs"


@dataclass(frozen=True)
class RootDir:
    """One root projects folder in the chain of selects, with a short label and expanded path."""

    label: str
    path: str


@dataclass(frozen=True)
class ReviewDefaults:
    """Default review option toggles loaded from `[defaults]` in `tui.toml`."""

    profile: str = "default"
    fail_on: str = "blocker"
    comment: bool = True
    handoff: bool = False
    verify: bool = False
    use_cache: bool = True
    auto_previous: bool = True


@dataclass(frozen=True)
class RepoPreset:
    """Saved per-repository review selections under `[projects.<name>]` in `tui.toml`."""

    base: str = ""
    profile: str = ""
    fail_on: str = ""
    comment: bool | None = None
    handoff: bool | None = None
    verify: bool | None = None
    path: tuple[str, ...] = ()
    path_exclude: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    diff_cap: int = 0


@dataclass(frozen=True)
class TuiConfig:
    """Complete parsed configuration from `tui.toml`."""

    config_path: Path
    root_dirs: tuple[RootDir, ...]
    scan_depth: int = 2
    fetch_timeout_s: int = 15
    #: Whether picking a repository fetches the branches its remote got since, removing none: F5 prunes.
    fetch_on_select: bool = True
    output_dir: str = str(OUTPUTS_DIR)
    defaults: ReviewDefaults = field(default_factory=ReviewDefaults)
    projects: dict[str, RepoPreset] = field(default_factory=dict)


def default_root_folder() -> str:
    """The folder of repositories a first run names: in a checkout, the folder that holds it, where its neighbours
    are; in an installed copy, the folder the command starts in."""
    checkout = _checkout()
    return str(checkout.parent) if checkout else str(Path.cwd())


def _toml(text: str) -> str:
    """`text` as a TOML basic string: `\\` and `"` escaped, each control character as `\\uXXXX`, every other character
    as it is. Reproduced by review: the JSON writer used before wrote an emoji as two surrogate escapes, which TOML
    refuses, and every start after a review of `feature/🚀-launch` stopped with exit 2."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + "".join(f"\\u{ord(c):04X}" if ord(c) < 0x20 or ord(c) == 0x7F else c for c in escaped) + '"'


def _toml_list(items: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml(item) for item in items) + "]"


def ensure_tui_config(path: Path | None = None) -> Path:
    """`path`, by default `DEFAULT_CONFIG_PATH`, written where it is missing: in a checkout from the config edited
    last, the old `adk_review/tui/.tui.toml` or the person's own, else with the defaults. The old file moves, never
    copied, so one file holds the config; the person's own stays, for an installed copy. Then the old folder goes.
    Reported by the owner: a copy left the old file to edit, the screen read the other, and the old folder stayed."""
    target = (path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    default = target == DEFAULT_CONFIG_PATH.expanduser().resolve()
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        if default and (source := _last_edited(target)) is not None:
            write_atomic(target, _LEGACY_OUTPUT_DIR.sub("", source.read_text(encoding="utf-8-sig")))
            with contextlib.suppress(OSError):  # read-only, or held by another program: the screen names its file
                LEGACY_CONFIG_PATH.unlink(missing_ok=True)
        else:
            write_atomic(target, _defaults_text())
    # In a checkout alone, and only once its old config has moved: read by review, the old package's folder was
    # cleared in an installed copy's own environment too.
    if default and _checkout() and not LEGACY_CONFIG_PATH.exists():
        for old in (LEGACY_CONFIG_PATH.parent, OLD_PACKAGE_DIR):
            _clear_old_folder(old)
    return target


def _last_edited(target: Path) -> Path | None:
    """The config a first run takes where none is at `target`: the old checkout's or the person's own, the one edited
    last, and never `target` itself. Read by review: the person's own, edited since bd92a0a, was dropped in silence."""
    found = [one for one in (LEGACY_CONFIG_PATH, USER_CONFIG_PATH) if one.is_file() and one.resolve() != target]
    return max(found, key=lambda one: one.stat().st_mtime) if found else None


def _clear_old_folder(old: Path) -> None:
    """An old folder, `adk_review/tui/` or `tui/adk_review_tui/` in a checkout, emptied of what the front end left
    there and removed: each saved file moved into the state folder where none is in its place, Python's old cache
    removed, and every folder left empty. A file it does not know stays, and its folder with it; nothing here fails a
    start."""
    if not old.is_dir():
        return
    with contextlib.suppress(OSError):
        saved = old / ".state"
        for file in sorted(saved.rglob("*")) if saved.is_dir() else []:
            if file.is_file() and not (place := STATE_DIR / file.relative_to(saved)).exists():
                place.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(file, place)
        for compiled in list(old.rglob("__pycache__/*.pyc")):
            compiled.unlink()
        folders = sorted((one for one in old.rglob("*") if one.is_dir()), key=lambda one: len(one.parts), reverse=True)
        for folder in [*folders, old]:
            if not any(folder.iterdir()):
                folder.rmdir()


def _defaults_text() -> str:
    """The config a first run writes where it has none to take."""
    root = default_root_folder().replace("\\", "/")
    return (
        "# Configuration for `adk-review-tui`.\n"
        "# Add multiple paths to `root_dirs` to select a root workspace folder first.\n\n"
        "[workspace]\n"
        "root_dirs = [\n"
        f"    {_toml(root)},\n"
        "]\n"
        "scan_depth = 2\n"
        "fetch_timeout_s = 15\n"
        "fetch_on_select = true\n\n"
        "[defaults]\n"
        'profile = "default"\n'
        'fail_on = "blocker"\n'
        "comment = true\n"
        "handoff = false\n"
        "verify = false\n"
        "use_cache = true\n"
        "auto_previous = true\n\n"
        "[projects]\n"
    )


def load_tui_config(path: Path | None = None) -> TuiConfig:
    """Read the config at `path`, by default `DEFAULT_CONFIG_PATH`, written first where it is missing. Read whole, as
    the reviewer reads its own: a table or a setting it does not know is said and named, never left out in silence."""
    target = ensure_tui_config(path)
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8-sig"))  # as Notepad and PowerShell 5 save it, a BOM first
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read {target}: {exc}") from None
    try:
        _known(raw)
        workspace = raw.get("workspace") if isinstance(raw.get("workspace"), dict) else {}
        roots = _parse_roots(workspace)
        depth = _number("[workspace]", "scan_depth", workspace.get("scan_depth"), 2, 1, 5)
        timeout = _number("[workspace]", "fetch_timeout_s", workspace.get("fetch_timeout_s"), 15, 3, 120)
        fetch_on_select = bool(_switch("[workspace]", "fetch_on_select", workspace.get("fetch_on_select"), True))
        output_dir = _resolve_output_dir(workspace.get("output_dir"), target.parent)
        defaults = _parse_defaults(raw.get("defaults"))
        projects = _parse_projects(raw.get("projects"))
    except ValueError as exc:
        raise ValueError(f"{target}: {exc}") from None
    return TuiConfig(
        config_path=target,
        root_dirs=roots,
        scan_depth=depth,
        fetch_timeout_s=timeout,
        fetch_on_select=fetch_on_select,
        output_dir=output_dir,
        defaults=defaults,
        projects=projects,
    )


def _number(table: str, name: str, value: object, default: int, low: int, high: int) -> int:
    """`table`'s whole number `name`, `default` where it is not given, from `low` to `high`; anything else is said.
    Read by review: one out of range was clamped, and a word replaced by the default, in silence."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    raise ValueError(f"{table} {name} is {value!r}. Write a whole number from {low} to {high}")


def _switch(table: str, name: str, value: object, default: bool | None) -> bool | None:
    """`table`'s switch `name`, `default` where it is not given. Read by review: `verify = "no"` turned `--verify` on,
    a string being true, and its model calls spent."""
    if value is None or isinstance(value, bool):
        return default if value is None else value
    raise ValueError(f"{table} {name} is {value!r}. Write true or false")


def _fail_on(value: object, table: str, default: str) -> str:
    """`table`'s `fail_on` where a severity names it, and `default` where the table gives none. Any other value is
    refused and named with what to give: the screen's selector takes a severity alone, and read by review, `critical`
    there raised from inside the screen, with a traceback."""
    if value is None or value == "":
        return default
    if not isinstance(value, str) or value not in RANK:
        *most, last = sorted(RANK, key=RANK.__getitem__, reverse=True)  # the most severe first
        raise ValueError(f"{table} fail_on is {value!r}. Give {', '.join(most)} or {last}")
    return value


def _profile(value: object, table: str, default: str) -> str:
    """`table`'s `profile` where one of the reviewer's profiles names it, and `default` where the table gives none.
    Any other value is refused and named with the profiles there are. Read by an audit, and reproduced: `"typo"` and
    `123` were taken as written, and the screen opened on the first profile it lists, with no word."""
    if value is None or value == "":
        return default
    names = sorted(load_config(CONFIG)["profiles"])
    if not isinstance(value, str) or value not in names:
        *most, last = names
        raise ValueError(f"{table} profile is {value!r}. Give {', '.join(most)} or {last}")
    return value


def _resolve_output_dir(out_raw: object, beside: Path) -> str:
    """Where the reviews' files go: the state folder's `outputs` where the config names none, and a relative path
    read from the config's own folder, `beside`. Read by review: it was read from the checkout's root, which an
    installed copy has not, and its reviews landed inside its own environment."""
    if not isinstance(out_raw, str) or not out_raw.strip():
        return str(OUTPUTS_DIR)
    candidate = Path(out_raw.strip()).expanduser()
    return str(candidate if candidate.is_absolute() else (beside / candidate).resolve())


#: The settings each table takes, as `.tui.toml` spells them: a key the config does not know is said, never left out.
_WORKSPACE = ("root_dirs", "projects_dir", "scan_depth", "fetch_timeout_s", "fetch_on_select", "output_dir")


def _known(raw: dict) -> None:
    """`ValueError` naming a table or a setting the config does not know, or a table that is none, with what to write.
    Read by review: a setting misspelt, `root_dir`, fell back in silence to the default, and the screen listed another
    folder; `[[workspace]]` and `defaults = 3` were left out the same way, and `projects = "x"` raised a traceback."""
    defaults = tuple(ReviewDefaults.__dataclass_fields__)
    tables = {"workspace": _WORKSPACE, "defaults": defaults, "projects": None}
    for key, value in raw.items():
        if key in tables:
            if not isinstance(value, dict):
                raise ValueError(f"[{key}] must be a table. Write [{key}] on a line of its own, its settings below it")
            if (settings := tables[key]) is not None:
                _settings_known(f"[{key}]", value, settings)
        elif isinstance(value, dict):
            raise ValueError(f"the config has no table {key!r}. Its tables are [workspace], [defaults] and [projects]")
        elif key in _WORKSPACE or key in defaults:
            raise ValueError(
                f"{key} belongs under [{'workspace' if key in _WORKSPACE else 'defaults'}]. Write it there"
            )
        else:
            raise ValueError(f"the config has no setting {key!r}. A setting goes under [workspace] or [defaults]")
    for name, preset in raw.get("projects", {}).items():
        if not isinstance(preset, dict):
            raise ValueError(f'[projects.{name}] must be a table. Write [projects."{name}"], its settings below it')
        _settings_known(f"[projects.{name}]", preset, tuple(RepoPreset.__dataclass_fields__))


def _settings_known(table: str, given: dict, settings: tuple[str, ...]) -> None:
    if (unknown := next((key for key in given if key not in settings), None)) is not None:
        *most, last = (s for s in settings if s != "projects_dir")
        raise ValueError(f"{table} has no setting {unknown!r}. Its settings are {', '.join(most)} and {last}")


def _parse_roots(workspace: dict) -> tuple[RootDir, ...]:
    """The folders of repositories, `root_dirs`, or the default where none is named: one folder written as a string
    is one folder. Read by review: one that was no list fell back in silence to the default folder."""
    name = "projects_dir" if "root_dirs" not in workspace and "projects_dir" in workspace else "root_dirs"
    given = workspace.get(name)
    if given is None:
        given = [default_root_folder()]
    elif isinstance(given, str):
        given = [given]
    if not isinstance(given, list) or not given:
        raise ValueError(
            f"{name} lists the folders that hold your repositories. Write it under [workspace] as root_dirs = "
            '["~/Projects"]'
        )
    roots: list[RootDir] = []
    seen: set[str] = set()
    for item in given:
        if (root := _one_root(item)) is None:
            raise ValueError(
                f'{name} holds {item!r}, which names no folder. Write a path, or {{ label = "...", path = "..." }}'
            )
        if (key := os.path.normcase(root.path)) not in seen:
            seen.add(key)
            roots.append(root)
    return tuple(roots)


def _one_root(item: object) -> RootDir | None:
    """Convert one string or `{label, path}` dict entry into a `RootDir`."""
    if isinstance(item, str) and item.strip():
        expanded = str(Path(item.strip()).expanduser().resolve())
        name = Path(expanded).name or expanded
        return RootDir(label=name, path=expanded)
    if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
        expanded = str(Path(item["path"].strip()).expanduser().resolve())
        label = str(item.get("label") or Path(expanded).name or expanded).strip()
        return RootDir(label=label, path=expanded)
    return None


def _parse_defaults(section: object) -> ReviewDefaults:
    """Parse `[defaults]` table from `tui.toml`."""
    if not isinstance(section, dict):
        return ReviewDefaults()
    return ReviewDefaults(
        profile=_profile(section.get("profile"), "[defaults]", "default"),
        fail_on=_fail_on(section.get("fail_on"), "[defaults]", "blocker"),
        comment=bool(_switch("[defaults]", "comment", section.get("comment"), True)),
        handoff=bool(_switch("[defaults]", "handoff", section.get("handoff"), False)),
        verify=bool(_switch("[defaults]", "verify", section.get("verify"), False)),
        use_cache=bool(_switch("[defaults]", "use_cache", section.get("use_cache"), True)),
        auto_previous=bool(_switch("[defaults]", "auto_previous", section.get("auto_previous"), True)),
    )


def _parse_projects(section: object) -> dict[str, RepoPreset]:
    """Parse `[projects]` table from `tui.toml`."""
    if not isinstance(section, dict):
        return {}
    presets: dict[str, RepoPreset] = {}
    for name, data in section.items():
        table = f"[projects.{name}]"
        presets[str(name)] = RepoPreset(
            base=str(data.get("base", "")),
            profile=_profile(data.get("profile"), table, ""),
            fail_on=_fail_on(data.get("fail_on"), f"[projects.{name}]", ""),
            comment=_switch(table, "comment", data.get("comment"), None),
            handoff=_switch(table, "handoff", data.get("handoff"), None),
            verify=_switch(table, "verify", data.get("verify"), None),
            path=_str_tuple(data.get("path")),
            path_exclude=_str_tuple(data.get("path_exclude")),
            checks=_str_tuple(data.get("checks")),
            diff_cap=_number(table, "diff_cap", data.get("diff_cap"), 0, 0, 10_000_000),
        )
    return presets


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def save_repo_preset(config: TuiConfig, repo_name: str, preset: RepoPreset) -> TuiConfig:
    """Persist `preset` for `repo_name` into `config.config_path` and return the updated `TuiConfig`."""
    # Every other setting as it was: read by review, built field by field here, a new setting went back to its
    # default each time a review saved its preset.
    new_config = replace(config, projects={**config.projects, repo_name: preset})
    write_atomic(config.config_path, _serialize_config(new_config))
    return new_config


def _serialize_config(config: TuiConfig) -> str:
    """Format `TuiConfig` back into clean TOML text."""
    roots_lines = [f"    {{ label = {_toml(r.label)}, path = {_toml(r.path)} }}," for r in config.root_dirs]
    if all(r.label == (Path(r.path).name or r.path) for r in config.root_dirs):
        roots_lines = [f"    {_toml(r.path)}," for r in config.root_dirs]
    d = config.defaults
    lines = [
        "# Configuration for `adk-review-tui`.",
        "# Add multiple paths to `root_dirs` to select a root workspace folder first.",
        "",
        "[workspace]",
        "root_dirs = [",
        *roots_lines,
        "]",
        f"scan_depth = {config.scan_depth}",
        f"fetch_timeout_s = {config.fetch_timeout_s}",
        f"fetch_on_select = {'true' if config.fetch_on_select else 'false'}",
        *([f"output_dir = {_toml(config.output_dir)}"] if config.output_dir != str(OUTPUTS_DIR) else []),
        "",
        "[defaults]",
        f"profile = {_toml(d.profile)}",
        f"fail_on = {_toml(d.fail_on)}",
        f"comment = {'true' if d.comment else 'false'}",
        f"handoff = {'true' if d.handoff else 'false'}",
        f"verify = {'true' if d.verify else 'false'}",
        f"use_cache = {'true' if d.use_cache else 'false'}",
        f"auto_previous = {'true' if d.auto_previous else 'false'}",
    ]
    for name, preset in sorted(config.projects.items()):
        lines.extend(_serialize_preset(name, preset))
    return "\n".join(lines) + "\n"


def _serialize_preset(name: str, preset: RepoPreset) -> list[str]:
    """Serialize one `[projects.<name>]` section."""
    rows = ["", f"[projects.{_toml(name)}]"]
    if preset.base:
        rows.append(f"base = {_toml(preset.base)}")
    if preset.profile:
        rows.append(f"profile = {_toml(preset.profile)}")
    if preset.fail_on:
        rows.append(f"fail_on = {_toml(preset.fail_on)}")
    if preset.comment is not None:
        rows.append(f"comment = {'true' if preset.comment else 'false'}")
    if preset.handoff is not None:
        rows.append(f"handoff = {'true' if preset.handoff else 'false'}")
    if preset.verify is not None:
        rows.append(f"verify = {'true' if preset.verify else 'false'}")
    rows.append(f"path = {_toml_list(preset.path)}")
    rows.append(f"path_exclude = {_toml_list(preset.path_exclude)}")
    if preset.checks:
        rows.append(f"checks = {_toml_list(preset.checks)}")
    if preset.diff_cap > 0:
        rows.append(f"diff_cap = {preset.diff_cap}")
    return rows


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to a sibling temp file and atomically replace `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        try:
            handle.write(text)
            handle.close()
            os.replace(handle.name, path)
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
