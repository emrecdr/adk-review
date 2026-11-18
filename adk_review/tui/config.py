"""Configuration loader and preset persistence for `adk_review/tui/.tui.toml`."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core.findings import RANK

TUI_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = TUI_DIR / ".tui.toml"
STATE_DIR = TUI_DIR / ".state"
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
    """Default review option toggles loaded from `[defaults]` in `.tui.toml`."""

    profile: str = "default"
    fail_on: str = "blocker"
    comment: bool = True
    handoff: bool = False
    verify: bool = False
    use_cache: bool = True
    auto_previous: bool = True


@dataclass(frozen=True)
class RepoPreset:
    """Saved per-repository review selections under `[projects.<name>]` in `.tui.toml`."""

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
    """Complete parsed configuration from `adk_review/tui/.tui.toml`."""

    config_path: Path
    root_dirs: tuple[RootDir, ...]
    scan_depth: int = 2
    fetch_timeout_s: int = 15
    output_dir: str = str(OUTPUTS_DIR)
    defaults: ReviewDefaults = field(default_factory=ReviewDefaults)
    projects: dict[str, RepoPreset] = field(default_factory=dict)


def default_root_folder() -> str:
    """The parent directory of this repository checkout, used when initializing `.tui.toml`."""
    repo_root = TUI_DIR.parent.parent
    parent = repo_root.parent
    return str(parent if parent.is_dir() else repo_root)


def ensure_tui_config(path: Path | None = None) -> Path:
    """Return `path` (default `adk_review/tui/.tui.toml`), creating it if missing."""
    target = (path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    root = default_root_folder().replace("\\", "/")
    content = (
        "# Configuration for `adk-review-tui`.\n"
        "# Add multiple paths to `root_dirs` to select a root workspace folder first.\n\n"
        "[workspace]\n"
        "root_dirs = [\n"
        f"    {json.dumps(root)},\n"
        "]\n"
        "scan_depth = 2\n"
        "fetch_timeout_s = 15\n"
        'output_dir = "adk_review/tui/.state/outputs"\n\n'
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
    _write_atomic(target, content)
    return target


def load_tui_config(path: Path | None = None) -> TuiConfig:
    """Read and validate `adk_review/tui/.tui.toml`, creating a starter file if absent."""
    target = ensure_tui_config(path)
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read {target}: {exc}") from None
    workspace = raw.get("workspace") if isinstance(raw.get("workspace"), dict) else {}
    roots = _parse_roots(workspace)
    depth = _int_in_range(workspace.get("scan_depth", 2), 2, 1, 5)
    timeout = _int_in_range(workspace.get("fetch_timeout_s", 15), 15, 3, 120)
    out_raw = str(workspace.get("output_dir", "adk_review/tui/.state/outputs")).strip()
    output_dir = _resolve_output_dir(out_raw)
    try:
        defaults = _parse_defaults(raw.get("defaults"))
        projects = _parse_projects(raw.get("projects"))
    except ValueError as exc:
        raise ValueError(f"{target}: {exc}") from None
    return TuiConfig(
        config_path=target,
        root_dirs=roots,
        scan_depth=depth,
        fetch_timeout_s=timeout,
        output_dir=output_dir,
        defaults=defaults,
        projects=projects,
    )


def _int_in_range(value: object, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer config setting safely, returning `default` on non-numeric input."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


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


def _resolve_output_dir(out_raw: str) -> str:
    """Resolve `output_dir` relative to the repository root when given as a relative path."""
    if not out_raw or out_raw == ".":
        return str(Path.cwd())
    candidate = Path(out_raw).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((TUI_DIR.parent.parent / candidate).resolve())


def _parse_roots(workspace: dict) -> tuple[RootDir, ...]:
    """Parse `root_dirs` (or single `projects_dir`) into deduplicated `RootDir` entries."""
    given = workspace.get("root_dirs")
    if given is None and "projects_dir" in workspace:
        single = workspace["projects_dir"]
        given = [single] if isinstance(single, str) else single
    if not isinstance(given, list) or not given:
        given = [default_root_folder()]
    roots: list[RootDir] = []
    seen: set[str] = set()
    for item in given:
        root = _one_root(item)
        if root is not None and (key := os.path.normcase(root.path)) not in seen:
            seen.add(key)
            roots.append(root)
    return tuple(roots) or (RootDir(label=Path(default_root_folder()).name, path=default_root_folder()),)


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
    """Parse `[defaults]` table from `.tui.toml`."""
    if not isinstance(section, dict):
        return ReviewDefaults()
    return ReviewDefaults(
        profile=str(section.get("profile", "default")),
        fail_on=_fail_on(section.get("fail_on"), "[defaults]", "blocker"),
        comment=bool(section.get("comment", True)),
        handoff=bool(section.get("handoff", False)),
        verify=bool(section.get("verify", False)),
        use_cache=bool(section.get("use_cache", True)),
        auto_previous=bool(section.get("auto_previous", True)),
    )


def _parse_projects(section: object) -> dict[str, RepoPreset]:
    """Parse `[projects]` table from `.tui.toml`."""
    if not isinstance(section, dict):
        return {}
    presets: dict[str, RepoPreset] = {}
    for name, data in section.items():
        if not isinstance(data, dict):
            continue
        presets[str(name)] = RepoPreset(
            base=str(data.get("base", "")),
            profile=str(data.get("profile", "")),
            fail_on=_fail_on(data.get("fail_on"), f"[projects.{name}]", ""),
            comment=data["comment"] if isinstance(data.get("comment"), bool) else None,
            handoff=data["handoff"] if isinstance(data.get("handoff"), bool) else None,
            verify=data["verify"] if isinstance(data.get("verify"), bool) else None,
            path=_str_tuple(data.get("path")),
            path_exclude=_str_tuple(data.get("path_exclude")),
            checks=_str_tuple(data.get("checks")),
            diff_cap=_int_in_range(data.get("diff_cap", 0), 0, 0, 10_000_000),
        )
    return presets


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def save_repo_preset(config: TuiConfig, repo_name: str, preset: RepoPreset) -> TuiConfig:
    """Persist `preset` for `repo_name` into `config.config_path` and return the updated `TuiConfig`."""
    updated = dict(config.projects)
    updated[repo_name] = preset
    new_config = TuiConfig(
        config_path=config.config_path,
        root_dirs=config.root_dirs,
        scan_depth=config.scan_depth,
        fetch_timeout_s=config.fetch_timeout_s,
        output_dir=config.output_dir,
        defaults=config.defaults,
        projects=updated,
    )
    _write_atomic(config.config_path, _serialize_config(new_config))
    return new_config


def _serialize_config(config: TuiConfig) -> str:
    """Format `TuiConfig` back into clean TOML text."""
    roots_lines = [f"    {{ label = {json.dumps(r.label)}, path = {json.dumps(r.path)} }}," for r in config.root_dirs]
    if all(r.label == (Path(r.path).name or r.path) for r in config.root_dirs):
        roots_lines = [f"    {json.dumps(r.path)}," for r in config.root_dirs]
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
        f"output_dir = {json.dumps(config.output_dir)}",
        "",
        "[defaults]",
        f"profile = {json.dumps(d.profile)}",
        f"fail_on = {json.dumps(d.fail_on)}",
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
    rows = ["", f"[projects.{json.dumps(name)}]"]
    if preset.base:
        rows.append(f"base = {json.dumps(preset.base)}")
    if preset.profile:
        rows.append(f"profile = {json.dumps(preset.profile)}")
    if preset.fail_on:
        rows.append(f"fail_on = {json.dumps(preset.fail_on)}")
    if preset.comment is not None:
        rows.append(f"comment = {'true' if preset.comment else 'false'}")
    if preset.handoff is not None:
        rows.append(f"handoff = {'true' if preset.handoff else 'false'}")
    if preset.verify is not None:
        rows.append(f"verify = {'true' if preset.verify else 'false'}")
    rows.append(f"path = {json.dumps(list(preset.path))}")
    rows.append(f"path_exclude = {json.dumps(list(preset.path_exclude))}")
    if preset.checks:
        rows.append(f"checks = {json.dumps(list(preset.checks))}")
    if preset.diff_cap > 0:
        rows.append(f"diff_cap = {preset.diff_cap}")
    return rows


def _write_atomic(path: Path, text: str) -> None:
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
