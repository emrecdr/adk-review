"""The loader's entry point: `adk web .` and `adk run` look here for `app` and `root_agent`.

The chat-shaped App is built here and only here, because only the loader
wants it: the driver builds the command's shape itself, the checks node and the guidelines, after the readiness
check, and nothing but the loader imports this file. Measured with the build
in `judge/graph.py` instead, where every import of the package reached it:
each start built five agents it would not run, the Copilot arm loaded ADK's
LiteLLM wrapper for them — 150 ms before `--help` printed — and a provider
misspelled in the shell was a traceback at import, exit 1, in place of
`require_ready`'s sentence and exit 2.
"""

from .core.profiles import DEFAULT
from .judge.catalog import CONFIG, catalog, load_config, select
from .judge.graph import build_app, gathering

# Chat runs the `default` profile and holds findings to `[review] fail_on`, as a review given no --profile and no
# --fail-on does: in chat no flag chooses another.
config = load_config(CONFIG)
policy = config["review"]
checks = select(catalog(), config["profiles"], DEFAULT)
app = build_app(
    chat=True,
    checks=checks,
    fail_on=policy["fail_on"],
    gathered=gathering(checks, policy["diff_cap"], policy["skip"]),
    ceiling=policy["max_tokens"] or None,  # the config's ceiling, as the command's default is
)
root_agent = app.root_agent
