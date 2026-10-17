"""What the environment says of a review, read and nothing built: the arm, each role's model name, the fallback
behind it, and where Copilot's token is kept. Apart from `config.py`, which builds the models and asks readiness
with ADK and google-genai imported: a review of rules alone keys its cache by the fallback's name, and the login
writes the token, and neither should pay the engine for one variable. Measured: a rules-only review with `--cache`
took 0.42 s against 0.18 s without, 0.2 s of it this import; the login started in 0.32 s against 0.05 s.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULTS = {"gemini": "gemini-2.5-flash", "copilot": "gpt-4.1"}


def provider() -> str:
    """The arm `REVIEW_PROVIDER` names, validated once for every function below.

    Validated here and nowhere else: three functions read the variable, and
    when each read it for itself a typo in the central `.env` surfaced as a
    `ValueError` from whichever ran first — at import, before argparse.
    """
    arm = os.environ.get("REVIEW_PROVIDER", "").strip().lower() or "copilot"
    if arm not in DEFAULTS:
        raise ValueError(f"REVIEW_PROVIDER must be one of {sorted(DEFAULTS)}, not {arm!r}")
    return arm


def model_var(role: str = "") -> str:
    """The variable naming a role's model: its own `REVIEW_<ROLE>_MODEL` when set, else everyone's `REVIEW_MODEL`:
    the verifier's role is `verifier`, so `REVIEW_VERIFIER_MODEL` gives `--verify` a model of its own."""
    own = f"REVIEW_{role.upper()}_MODEL"
    return own if role and os.environ.get(own, "").strip() else "REVIEW_MODEL"


def model_name(role: str = "") -> str:
    """The model a role runs on: what its variable names, else the arm's default.

    The model is chosen per agent, so a role may run on a stricter or a cheaper one; the verifier does.
    """
    return os.environ.get(model_var(role), "").strip() or DEFAULTS[provider()]


def fallback_name() -> str:
    """The model `REVIEW_FALLBACK_MODEL` names, to stand behind every role's model; blank means none."""
    return os.environ.get("REVIEW_FALLBACK_MODEL", "").strip()


def copilot_token() -> Path:
    """Where LiteLLM caches the Copilot device-flow token: one spelling of the path, which `config.py` asks for
    readiness and the login, `login.py` at the package root, writes to."""
    return copilot_dir() / os.environ.get("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")


def copilot_dir() -> Path:
    """LiteLLM's Copilot folder, where the login keeps its token and LiteLLM the API key it is issued."""
    return Path(os.environ.get("GITHUB_COPILOT_TOKEN_DIR", "~/.config/litellm/github_copilot")).expanduser()
