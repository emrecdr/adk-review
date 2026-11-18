"""What the environment says of a review, read and nothing built: the arm, each role's model name, the fallback
behind it, and where Copilot's token is kept. Apart from `config.py`, which builds the models and asks readiness
with ADK and google-genai imported: a review of rules alone keys its cache by the fallback's name, and the login
writes the token, and neither should pay the engine for one variable. Measured: a rules-only review with `--cache`
took 0.42 s against 0.18 s without, 0.2 s of it this import; the login started in 0.32 s against 0.05 s.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.guidelines import Models

DEFAULTS = {"gemini": "gemini-2.5-flash", "copilot": "gpt-4.1"}
#: An address a registry is given as: over TLS, a host, its port where it has one, and a path under it. No user's
#: name, which a lookalike host hides behind, no query and no space.
_REGISTRY = re.compile(r"https://[A-Za-z0-9._-]+(?::\d+)?(?:/[^\s?#@]*)?")
#: A domain of the operator's own, `*.example.com`: the domain and every host under it, two names at least, so no
#: setting holds a whole top-level domain. Asked by the owner: a company serves its packages from several hosts.
_DOMAIN = re.compile(r"\*\.((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*[a-z0-9])", re.IGNORECASE)


def provider() -> str:
    """The arm `REVIEW_PROVIDER` names, validated once for every function below.

    Validated here and nowhere else: three functions read the variable, and
    when each read it for itself a typo in the central `.env` surfaced as a
    `ValueError` from whichever ran first — at import, before argparse.
    """
    arm = os.environ.get("REVIEW_PROVIDER", "").strip().lower() or "copilot"
    if arm not in DEFAULTS:
        raise ValueError(f"REVIEW_PROVIDER must be one of {sorted(DEFAULTS)}, not {arm!r}. Set it in .env or the shell")
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


def model_name(role: str = "", preset: Models | None = None) -> str:
    """The model a role runs on. The model is chosen per agent, so a role may run on a stricter or a cheaper one."""
    return model_pick(role, preset)[1]


def fallback_name() -> str:
    """The model `REVIEW_FALLBACK_MODEL` names, to stand behind every role's model; blank means none."""
    return os.environ.get("REVIEW_FALLBACK_MODEL", "").strip()


def registries() -> tuple[str, ...]:
    """The operator's own package registries, `REVIEW_REGISTRIES`, a comma between two, each known to
    `lockfile-sources` for every lockfile it reads: an address over TLS, by its scheme, host and port and the path
    under it, or a domain, `*.example.com`, the domain and every host under it over TLS, its case folded. Set in the
    shell or the central `.env`, never in a file of the repository: read by review, a company's host stood in the
    rule's own source, in a public repository. `ValueError` for one that is neither: a registry misspelt is said,
    never one more or one fewer taken in silence, and a bare host, which could mean either, is one."""
    given = [one.strip() for one in os.environ.get("REVIEW_REGISTRIES", "").split(",")]
    known = []
    for one in filter(None, given):
        if _REGISTRY.fullmatch(one):
            known.append(one)
        elif domain := _DOMAIN.fullmatch(one):
            known.append(f"*.{domain[1].lower()}")
        else:
            raise ValueError(
                f"REVIEW_REGISTRIES names {one!r}, which is no https address and no domain. Give each as "
                "https://host:port/, or *.example.com for a domain and every host under it, with a comma between two"
            )
    return tuple(known)


def copilot_token() -> Path:
    """Where LiteLLM caches the Copilot device-flow token: one spelling of the path, which `config.py` asks for
    readiness and the login, `login.py` at the package root, writes to."""
    return copilot_dir() / (os.environ.get("GITHUB_COPILOT_ACCESS_TOKEN_FILE") or "access-token")


def copilot_key() -> Path:
    """Where LiteLLM caches the API key it buys with the token: one spelling, for readiness and the login."""
    return copilot_dir() / (os.environ.get("GITHUB_COPILOT_API_KEY_FILE") or "api-key.json")


def copilot_dir() -> Path:
    """LiteLLM's Copilot folder, where the login keeps its token and LiteLLM the API key it is issued. A blank value
    reads as unset, as a blank `REVIEW_*` does: read by an audit, one read as the current folder had a review ask for
    a login and the login say it was already done."""
    return Path(os.environ.get("GITHUB_COPILOT_TOKEN_DIR") or "~/.config/litellm/github_copilot").expanduser()


def pin_copilot_dir() -> None:
    """The Copilot folder and its files written for LiteLLM, which reads them as written, as this module reads them:
    `GITHUB_COPILOT_TOKEN_DIR` with its `~` expanded, and a blank file name dropped. Read by review and by an audit:
    `~/tokens` read ready, and the login wrote under the home folder, where LiteLLM looked in a folder named `~` and
    started a device login of its own."""
    if "GITHUB_COPILOT_TOKEN_DIR" in os.environ:
        os.environ["GITHUB_COPILOT_TOKEN_DIR"] = str(copilot_dir())
    for name in ("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "GITHUB_COPILOT_API_KEY_FILE"):
        if os.environ.get(name) == "":
            del os.environ[name]
