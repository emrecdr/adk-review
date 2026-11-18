"""Interactive Textual front-end shell (`adk-review-tui`) for `adk-review`.

All TUI configuration (`adk_review/tui/.tui.toml`), state (`adk_review/tui/.state/`), and UI modules live in this
subpackage. `textual` is an optional dependency (`[project.optional-dependencies] tui`), so CI/CD installs of
`adk-review` never pull UI packages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.verdict import EXIT_CLEAN, EXIT_USAGE
from .config import DEFAULT_CONFIG_PATH, ensure_tui_config


def build_tui_parser() -> argparse.ArgumentParser:
    """Command-line flags for `adk-review-tui`."""
    parser = argparse.ArgumentParser(
        prog="adk-review-tui",
        description="Interactive Textual TUI launcher for adk-review.",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=str(DEFAULT_CONFIG_PATH),
        help="path to the TUI config file (default: adk_review/tui/.tui.toml)",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="ensure adk_review/tui/.tui.toml exists, print its path, and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the `adk-review-tui` application."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_tui_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    try:
        ensured = ensure_tui_config(config_path)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"tui: {exc}\n")
        return EXIT_USAGE
    if args.init_config:
        sys.stdout.write(f"{ensured}\n")
        return EXIT_CLEAN
    try:
        from .app import AdkReviewTuiApp
    except ModuleNotFoundError:
        sys.stderr.write('tui: textual is not installed. Run `uv sync --extra tui` or `uv tool install ".[tui]"`\n')
        return EXIT_USAGE
    app = AdkReviewTuiApp(config_path=ensured)
    result = app.run()
    return int(result) if isinstance(result, int) else EXIT_CLEAN


def run() -> None:
    """Console script entry point for `adk-review-tui`."""
    raise SystemExit(main())
