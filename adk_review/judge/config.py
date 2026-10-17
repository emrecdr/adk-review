"""Which model, on which arm, and what a request to it looks like: temperature, a deadline, retries, on both arms.

The only file in the reviewer that names a provider. GitHub Copilot is the arm
when none is named; like everything that is not Gemini it goes through
LiteLLM. Gemini, which ADK speaks natively, is a plain model name. Both are
the same `LlmAgent` to the rest of the code; `REVIEW_PROVIDER` is the one
switch, read from the environment at each call: the command loads `.env`
first, and `adk web` before it imports this module.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import truststore
from google.adk.models import FallbackModel
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.registry import LLMRegistry
from google.adk.planners import BasePlanner, BuiltInPlanner
from google.adk.utils.env_utils import is_enterprise_mode_enabled
from google.auth.exceptions import GoogleAuthError
from google.genai import types

# Certificates are verified through the operating system's own store, as a browser does, before any client is
# built. Measured on a company Windows machine: its proxy re-signed HTTPS with a root the machine trusted and
# Python's bundled list did not, so the Copilot login failed, and every token refresh would have. `SSL_CERT_FILE`
# still names a bundle where one is set.
truststore.inject_into_ssl()

DEFAULTS = {"gemini": "gemini-2.5-flash", "copilot": "gpt-4.1"}
#: The Copilot arm's provider, as LiteLLM names it.
COPILOT = "github_copilot"


#: A bearer token in a log line. LiteLLM's debug dump masks a request's headers but prints its body whole, and the
#: Copilot arm carries its token in the body's `extra_headers`.
_BEARER = re.compile(r"(Bearer\s+)[^\s'\",}]+")


class _HideBearer(logging.Filter):
    """Every LiteLLM log line with its bearer token hidden. Measured with a fake token: `adk web -v`, which writes its
    log where any account on the machine can read it, printed the Copilot token whole."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = _BEARER.sub(r"\1[REDACTED]", record.getMessage()), ()
        return True


_HIDE_BEARER = _HideBearer()
#: Attempts per model call on a transient failure — a brief rate limit, a 5xx: the original plus two, a few seconds
#: apart. A call past its deadline is not retried on the Gemini arm (genai's retry test leaves aiohttp's timeout out),
#: and a quota window outlasts them (Gemini's free tier asked for 39 s) and is named instead.
RETRY_ATTEMPTS = 3
#: Seconds one model call may take; a hung provider call would otherwise hang the run for ever.
REQUEST_TIMEOUT_S = 180.0
#: Seconds one run may take, every guideline or every verifier: a provider answering nothing would otherwise hold it for
#: each call's deadline times its attempts, and on the Copilot arm that measured seven attempts at 180 s each.
RUN_DEADLINE_S = 600.0
#: Model calls in flight at once — the guidelines, or the verifiers: what a free-tier quota tolerates.
CONCURRENCY = 3


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


def serving() -> str:
    """What a run's model calls go to, for the report and the cache's key: the model, its arm, and where the
    environment sends the arm past its own default — Vertex AI for Gemini, a GitHub Enterprise tenant's endpoint for
    Copilot. Every entry point lets the shell's variables win over `.env` — ADK restores what was set before it loads
    the file — so a stale export picked the arm or the model unseen."""
    arm = provider()
    if arm == "gemini":
        # Read by ADK's own reader: `GOOGLE_GENAI_USE_ENTERPRISE` where it is set, else `GOOGLE_GENAI_USE_VERTEXAI`,
        # which 2.9.2 deprecates. Measured before: with only the new one set, the report named no Vertex AI for calls
        # that went there.
        where = "Vertex AI" if is_enterprise_mode_enabled() else ""
    else:
        host = urlsplit(base := _copilot_api()).hostname or base
        where = "" if host.endswith(".githubcopilot.com") else host  # github.com's own, an individual's or a business's
    return f"{model_name()} on {arm}" + (f" through {where}" if where else "")


def _copilot_api() -> str:
    """Where the Copilot arm's calls go, in LiteLLM 1.103.1's own order (`github_copilot/chat/transformation.py`): the
    endpoint its cached API key was issued for, else `GITHUB_COPILOT_API_BASE`. Traced in its source by review: read
    from the variable alone, the report could name a host the calls never reached, and named none for a tenant whose
    key named its own. Of that file, which holds the key, only `endpoints.api` is read, and nothing else of it kept."""
    issued = _copilot_dir() / os.environ.get("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    try:
        endpoint = json.loads(issued.read_text(encoding="utf-8"))["endpoints"]["api"]
    except (OSError, ValueError, KeyError, TypeError):  # none issued yet, or of another shape: LiteLLM reads on too
        endpoint = ""
    return str(endpoint or os.environ.get("GITHUB_COPILOT_API_BASE", "")).strip()


def fallback_name() -> str:
    """The model `REVIEW_FALLBACK_MODEL` names, to stand behind every role's model; blank means none."""
    return os.environ.get("REVIEW_FALLBACK_MODEL", "").strip()


def served(roles: tuple[str, ...]) -> str:
    """`serving()`, with each role whose model is not everyone's named beside it: `gpt-4.1 on copilot (verifier:
    gpt-5)`."""
    own = [f"{role}: {model_name(role)}" for role in roles if model_name(role) != model_name()]
    return serving() + (f" ({'; '.join(own)})" if own else "")


def build_model(*, role: str = "") -> str | BaseLlm:
    """The model for `REVIEW_PROVIDER`: a name ADK resolves natively on Gemini, a LiteLLM model otherwise — and, when
    `REVIEW_FALLBACK_MODEL` names one, ADK's `FallbackModel` over both.

    A fallback moves a call to the next model on a 429 or a 5xx, each model
    tried once, the request's own retries spent first. Seen live on Gemini's
    free tier: the quota is per model, twenty requests a day, and a run past
    it was ten named 429s where a second model would have answered. A model
    the key does not serve is a 404, not a fallback: the plugin names it.
    """
    names = [model_name(role)] + ([fallback] if (fallback := fallback_name()) else [])
    if provider() == "gemini":
        return names[0] if len(names) == 1 else FallbackModel(models=names)
    litellm = _litellm()
    from google.adk.models.lite_llm import LiteLlm

    litellm.suppress_debug_info = True  # else a red "Give Feedback" banner on stdout with every error
    # The instruction as Copilot's system turn. Measured by review: LiteLLM sent it as an assistant turn unless told
    # not to, so the fences and the reminder after them reached Copilot as words it had said itself; LiteLLM's own
    # note says Copilot now takes a system turn for every model it serves.
    litellm.disable_copilot_system_to_assistant = True

    # Copilot authenticates with a cached device-flow token, not a key: `uv run python scripts/copilot_login.py`, once.
    logging.getLogger("LiteLLM").addFilter(_HIDE_BEARER)  # once: a filter already there is not added again
    models = [LiteLlm(model=f"{COPILOT}/{name}") for name in names]
    return models[0] if len(models) == 1 else FallbackModel(models=models)


def _litellm() -> ModuleType:
    """LiteLLM, imported where the Copilot arm needs it and nowhere else. At the top of the command it cost about a
    second of every start, `--help` included, and on its own import it read `.env` unless told it runs in production,
    as ADK tells it, and fetched a price list unless `LITELLM_LOCAL_MODEL_COST_MAP` is set."""
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
    import litellm

    return litellm


def guideline_planner(thinking_budget: int) -> BasePlanner | None:
    """A thinking budget where the provider has one; nothing where it does not.

    `BuiltInPlanner` is configuration only: it sets the budget on the request
    and adds no prompt text, and only Gemini honours it. ADK's alternative for
    other models is `PlanReActPlanner` — prompt engineering that runs anywhere —
    but it collides with `output_schema`, so the reviewer keeps the schema and
    states plainly that the reasoning lever is absent on Copilot.
    """
    if provider() == "gemini":
        return BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget))
    return None


def request_config() -> types.GenerateContentConfig:
    """What every judging call carries, on both arms: temperature zero, a deadline, and retries.

    A severity is a decision, and a demo that gives a different verdict on
    every run teaches the wrong lesson. The deadline and the retries are one
    number each, on the request, and both arms read them there: genai takes
    `HttpOptions.timeout` in milliseconds and retries the request `attempts`
    times on the status codes it deems transient, with backoff; ADK's LiteLLM
    wrapper converts the timeout to LiteLLM's seconds and forwards the attempts
    as `num_retries` (`lite_llm.py`, 2.9.2). Measured against a server that never
    answered, LiteLLM hands those to the OpenAI SDK as its retries and retries
    around it too: three became seven. So the Copilot arm asks for one, and the
    SDK's own two retries make its three. A call past its deadline or its
    retries is a model error like any other, which `ReportProviderErrors` turns
    into that guideline's own answer.
    """
    attempts = RETRY_ATTEMPTS if provider() == "gemini" else 1
    return types.GenerateContentConfig(
        temperature=0.0,
        http_options=types.HttpOptions(
            timeout=int(REQUEST_TIMEOUT_S * 1000),
            retry_options=types.HttpRetryOptions(attempts=attempts, initial_delay=1.0),
        ),
    )


def copilot_token() -> Path:
    """Where LiteLLM caches the Copilot device-flow token.

    The same two variables and defaults `scripts/copilot_login.py` reads; a
    copy rather than an import: the reviewer imports nothing from `scripts/`.
    """
    return _copilot_dir() / os.environ.get("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")


def _copilot_dir() -> Path:
    """LiteLLM's Copilot folder, where the login keeps its token and LiteLLM the API key it is issued."""
    return Path(os.environ.get("GITHUB_COPILOT_TOKEN_DIR", "~/.config/litellm/github_copilot")).expanduser()


def require_ready(*, roles: tuple[str, ...] = ()) -> str | None:
    """Why the chosen arm cannot run, or None. Called by the driver before anything is spent, never at import.

    The guidelines' model is always checked; `roles` names the agents with a model
    of their own the run will build — the verifier's under `--verify` — so a
    model name no class claims is a sentence here, not a traceback from inside
    the run.
    """
    try:
        arm = provider()
    except ValueError as exc:
        return str(exc)
    named = [(model_var(role), model_name(role)) for role in ("", *roles)]
    if fallback := fallback_name():
        named.append(("REVIEW_FALLBACK_MODEL", fallback))
    if arm == "copilot":
        return _copilot_refused(named)
    return _gemini_refused() or _not_gemini(named)


def _copilot_refused(named: list[tuple[str, str]]) -> str | None:
    """Why the Copilot arm cannot run with the models `named`, each by the variable that names it, or None."""
    if not ((token := copilot_token()).is_file() and token.stat().st_size):  # an empty file is no token
        return (
            f"REVIEW_PROVIDER=copilot needs a cached token at {token}; "
            f"run `uv run python scripts/copilot_login.py` once"
        )
    # LiteLLM takes any model name and fails at the call, which is that guideline's own answer; but a model it
    # knows cannot judge at temperature zero it refuses before any call, so that is said here, as LiteLLM says it.
    # The arm's own default is not asked: LiteLLM took two seconds to import, which every dry run and every usage
    # error then waited out, and a test holds the default to LiteLLM's answer.
    for var, name in named:
        if name != DEFAULTS["copilot"] and (why := _refuses_temperature_zero(name)) is not None:
            return f"{var}={name} cannot judge at temperature 0, at which every review runs: {why}"
    return None


def _not_gemini(named: list[tuple[str, str]]) -> str | None:
    """Why a model `named` on the Gemini arm is one ADK would send elsewhere, or no class claims, or None."""
    for var, name in named:
        try:
            model = LLMRegistry.resolve(name)
        except ValueError as exc:  # the arm that resolves names up front, so it can say so up front
            return f"{var}: {exc}"
        if not issubclass(model, Gemini):
            # Measured by review: `gpt-4.1`, Copilot's default left in `.env`, was ready here, and ADK sends `gpt-*`
            # to OpenAI's API. ADK resolves a name to the class that will serve it: on this arm, a Gemini model's.
            return f"{var}={name} is no Gemini model: ADK sends it to {model.__name__}, another provider's class"
    return None


def _refuses_temperature_zero(name: str) -> str | None:
    """Why LiteLLM will not send Copilot's `name` the temperature every judging agent runs at, or None; it decides
    offline, before any call. Measured by review: it refused `gpt-5`, `gpt-5-mini`, `o3-mini` and `o4-mini`, so a
    review on one failed every guideline, exit 3, where readiness had said nothing."""
    litellm = _litellm()
    from litellm.utils import get_optional_params

    try:
        get_optional_params(model=name, custom_llm_provider=COPILOT, temperature=request_config().temperature)
    except litellm.UnsupportedParamsError as exc:
        return said(exc).split(". ")[0]
    return None


#: LiteLLM's wrapping of a provider's error body: its own class again, then the provider's, as Copilot's comes —
#: `RateLimitError: Github_copilotException - Sorry, …`, or a JSON body, `Github_copilotException - {"error": {…}}`.
_WRAPPED = re.compile(r"(?:\w+Error: )?\w+Exception - (.*)", re.DOTALL)


def said(error: Exception) -> str:
    """A provider's error in its own words: genai's `.message`, the sentence its `str` wraps in JSON; LiteLLM's with
    the class it puts before them gone and the provider's body unwrapped, a JSON body read as its code and the
    sentence it holds. Measured by review on Copilot: a JSON body read raw, and live, a rate limit read
    "RateLimitError: 429: RateLimitError: Github_copilotException - Sorry, …"."""
    message = str(getattr(error, "message", None) or error).removeprefix(f"litellm.{type(error).__name__}: ")
    wrapped = _WRAPPED.fullmatch(message)
    if wrapped is None:
        return message
    try:
        body = json.loads(wrapped[1])["error"]
    except (ValueError, KeyError, TypeError):
        return wrapped[1]
    if not isinstance(body, dict) or not body.get("message"):
        return wrapped[1]
    kind = body.get("code") or body.get("type")
    return f"{kind}: {body['message']}" if kind else str(body["message"])


def _gemini_refused() -> str | None:
    """Why google-genai will not build the Gemini arm's client as ADK builds it for each call, or None.

    Asked of the SDK, which reads a key, a project, a region and Application
    Default Credentials its own way. Measured by review: a copy of those rules
    here refused, twice, what the SDK accepts — Vertex AI by a project, then
    by the project the credentials name, as on Cloud Run and GKE. With neither
    a key nor a project set, the SDK asks the credentials, which can run gcloud
    or ask a metadata server; with one, nothing is read until the first call,
    which alone tells whether the credentials sign it.
    """
    try:
        Gemini(model=model_name()).api_client.close()  # built for what building it refuses, and let go
    except (ValueError, GoogleAuthError) as exc:
        if not is_enterprise_mode_enabled():
            return (
                "REVIEW_PROVIDER=gemini needs GOOGLE_API_KEY, set in the shell or in .env at the checkout's root, or "
                "Vertex AI and its project"
            )
        return (
            "REVIEW_PROVIDER=gemini through Vertex AI finds no project: set GOOGLE_CLOUD_PROJECT, or sign in with "
            f"Application Default Credentials that name one (`gcloud auth application-default login`) ({exc})"
        )
    return None
