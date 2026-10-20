"""What the environment says of a review, read and nothing built: the arm, each role's model name, the fallback
behind it, and where Copilot's token is kept. Apart from `config.py`, which builds the models and asks readiness
with ADK and google-genai imported: a review of rules alone keys its cache by the fallback's name, and the login
writes the token, and neither should pay the engine for one variable. Measured: a rules-only review with `--cache`
took 0.42 s against 0.18 s without, 0.2 s of it this import; the login started in 0.32 s against 0.05 s.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core.guidelines import Models

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


def model_pick(role: str = "", preset: Models | None = None) -> tuple[str, str]:
    """What names a role's model, and the model, the most specific choice first: the role's own
    `REVIEW_<ROLE>_MODEL`; a guideline's own `models` for the arm, from its file; everyone's `REVIEW_MODEL`; the arm's
    default. The verifier's role is `verifier`, a guideline's its id: `REVIEW_SECURITY_MODEL`."""
    arm = provider()
    own = f"REVIEW_{role.upper().replace('-', '_')}_MODEL"
    if role and (name := os.environ.get(own, "").strip()):
        return own, name
    if preset is not None and (name := getattr(preset, arm, "").strip()):
        return f"guideline.{role} models.{arm}", name
    return "REVIEW_MODEL", os.environ.get("REVIEW_MODEL", "").strip() or DEFAULTS[arm]


def model_var(role: str = "", preset: Models | None = None) -> str:
    """What names a role's model: its variable, or a guideline's file."""
    return model_pick(role, preset)[0]


def model_name(role: str = "", preset: Models | None = None) -> str:
    """The model a role runs on. The model is chosen per agent, so a role may run on a stricter or a cheaper one."""
    return model_pick(role, preset)[1]


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
