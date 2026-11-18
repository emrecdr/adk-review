"""Interactive Textual front-end shell (`adk-review-tui`) for `adk-review`.

An application of its own on top of the reviewer, `tui/` in the reviewer's checkout, a uv workspace member with its
own project file: it imports `adk_review` as any user would, and its config and state live in the person's own
folders (`config.py`). An install of `adk-review` for a pipeline takes none of its code, command or UI package.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adk_review.core.verdict import EXIT_CLEAN, EXIT_USAGE

from .config import DEFAULT_CONFIG_PATH, ensure_tui_config, load_tui_config


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
        help=f"read the config from FILE. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="write the config where it is missing, print its path, and exit",
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
    try:  # read here first: what the file cannot mean is a sentence, where the screen would raise it with a traceback
        load_tui_config(ensured)
    except ValueError as exc:
        sys.stderr.write(f"tui: {exc}\n")
        return EXIT_USAGE
    try:
        from .app import AdkReviewTuiApp
    except ModuleNotFoundError:
        # Named as the lock installs it: `uv tool install ".[tui]"`, said before, reads no lock and no cooldown.
        sys.stderr.write("tui: textual is not installed. In the checkout, run `uv sync --locked --all-packages`.\n")
        sys.stderr.write("Install the front end as its own tool: tui/README.md shows the command\n")
        return EXIT_USAGE
    app = AdkReviewTuiApp(config_path=ensured)
    result = app.run()
    return result if isinstance(result, int) else EXIT_CLEAN


def run() -> None:
    """Console script entry point for `adk-review-tui`."""
    raise SystemExit(main())
