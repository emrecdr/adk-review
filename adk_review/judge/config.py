"""Which model, on which arm, and what a request to it looks like: temperature, a deadline, retries, on both arms.

The only file in the reviewer that names a provider. GitHub Copilot is the arm
when none is named; like everything that is not Gemini it goes through
LiteLLM. Gemini, which ADK speaks natively, is a plain model name. Both are
the same `LlmAgent` to the rest of the code; `REVIEW_PROVIDER` is the one
switch, read from the environment at each call: the command loads `.env`
first, and `adk web` before it imports this module.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
from collections.abc import AsyncGenerator, Mapping
from typing import Any
from urllib.parse import urlsplit

import truststore
from google.adk.models import FallbackModel
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.registry import LLMRegistry
from google.adk.utils.env_utils import is_enterprise_mode_enabled
from google.auth.exceptions import GoogleAuthError
from google.genai import types

from ..core.guidelines import Models
from .settings import DEFAULTS, copilot_dir, copilot_token, fallback_name, model_name, model_pick, provider

# Certificates are verified through the operating system's own store, as a browser does, before any client is
# built. Measured on a company Windows machine: its proxy re-signed HTTPS with a root the machine trusted and
# Python's bundled list did not, so the Copilot login failed, and every token refresh would have. `SSL_CERT_FILE`
# still names a bundle where one is set.
truststore.inject_into_ssl()

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


def serving() -> str:
    """What a run's model calls go to, for the report and the cache's key: the model, its arm, and where the
    environment sends the arm past its own default — Vertex AI for Gemini, a GitHub Enterprise tenant's endpoint for
    Copilot. Every entry point lets the shell's variables win over `.env` — ADK restores what was set before it loads
    the file — so a stale export picked the arm or the model unseen."""
    arm = provider()
    if arm == "gemini":
        # Read by ADK's own reader: `GOOGLE_GENAI_USE_ENTERPRISE` where it is set, else `GOOGLE_GENAI_USE_VERTEXAI`,
        # which ADK deprecates. Measured before: with only the new one set, the report named no Vertex AI for calls
        # that went there.
        where = "Vertex AI" if is_enterprise_mode_enabled() else ""
    else:
        host = urlsplit(base := _copilot_api()).hostname or base
        where = "" if host.endswith(".githubcopilot.com") else host  # github.com's own, an individual's or a business's
    return f"{model_name()} on {arm}" + (f" through {where}" if where else "")


def _copilot_api() -> str:
    """Where the Copilot arm's calls go, in LiteLLM's own order (`github_copilot/chat/transformation.py`, 1.103.1,
    1.105.1 and 1.106.0 alike): the endpoint its cached API key was issued for, else `GITHUB_COPILOT_API_BASE`. Traced
    in its source by review: read from the variable alone, the report could name a host the calls never reached, and
    named none for a tenant whose key named its own. Of that file, which holds the key, only `endpoints.api` is read,
    and nothing else of it kept."""
    issued = copilot_dir() / os.environ.get("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    try:
        endpoint = json.loads(issued.read_text(encoding="utf-8"))["endpoints"]["api"]
    except (OSError, ValueError, KeyError, TypeError):  # none issued yet, or of another shape: LiteLLM reads on too
        endpoint = ""
    return str(endpoint or os.environ.get("GITHUB_COPILOT_API_BASE", "")).strip()


def served(roles: tuple[str, ...], presets: Mapping[str, Models] | None = None) -> str:
    """`serving()`, with each role whose model is not everyone's named beside it: `gpt-4.1 on copilot (verifier:
    gpt-5)`. `presets` holds each guideline's own `models`, by its id."""
    chosen = presets or {}
    own = [f"{role}: {name}" for role in roles if (name := model_name(role, chosen.get(role))) != model_name()]
    return serving() + (f" ({'; '.join(own)})" if own else "")


def build_model(*, role: str = "", preset: Models | None = None) -> str | BaseLlm:
    """The model for `REVIEW_PROVIDER`: a name ADK resolves natively on Gemini, a LiteLLM model otherwise — and, when
    `REVIEW_FALLBACK_MODEL` names one, ADK's `FallbackModel` over both.

    A fallback moves a call to the next model on a 429, a 5xx or a timeout,
    each model tried once, the request's own retries spent first. Seen live on
    Gemini's free tier: the quota is per model, twenty requests a day, and a
    run past it was ten named 429s where a second model would have answered. A
    model the key does not serve is a 404, not a fallback: the plugin names it.
    ADK leaves a timeout out, since one may have been answered and paid for;
    a review's call is read, never acted on, and an operator who names a
    fallback has chosen to pay for a second answer.
    """
    names = [model_name(role, preset)] + ([fallback] if (fallback := fallback_name()) else [])
    if provider() == "gemini":
        return names[0] if len(names) == 1 else FallbackModel(models=names, retriable_status_codes=_FALL_BACK_ON)
    litellm = _litellm()

    litellm.suppress_debug_info = True  # else a red "Give Feedback" banner on stdout with every error
    # The instruction as Copilot's system turn. Measured by review: LiteLLM sent it as an assistant turn unless told
    # not to, so the fences and the reminder after them reached Copilot as words it had said itself; LiteLLM's own
    # note says Copilot now takes a system turn for every model it serves.
    litellm.disable_copilot_system_to_assistant = True

    # Copilot authenticates with a cached device-flow token, not a key: `adk-review-login`, once.
    logging.getLogger("LiteLLM").addFilter(_HIDE_BEARER)  # once: a filter already there is not added again
    # Copilot's attempts are the OpenAI SDK's own retries, spaced and waiting out a Retry-After, `RETRY_ATTEMPTS` in
    # all: the reviewer's constant, where LiteLLM's default of two retries made them before, unwritten here.
    bounded = _bounded()
    models = [bounded(model=f"{COPILOT}/{name}", max_retries=RETRY_ATTEMPTS - 1) for name in names]
    return models[0] if len(models) == 1 else FallbackModel(models=models, retriable_status_codes=_FALL_BACK_ON)


#: What a fallback model moves a call on from: ADK's codes, and a timeout.
_FALL_BACK_ON = FallbackModel.DEFAULT_STATUS_CODES | {408}


class CallTimeoutError(TimeoutError):
    """A call no answer came back to within `REQUEST_TIMEOUT_S`, the client's own retries included: a timeout, 408,
    which a fallback model moves on from."""

    status_code = 408


@functools.cache
def _bounded() -> type[BaseLlm]:
    """Copilot's model under one deadline for a call and every retry the OpenAI SDK makes of it, where each attempt
    had a deadline of its own. Measured offline against a server that never answered: each call was sent three times,
    each waiting out 180 s, nine minutes a guideline, and the fallback never asked. Built where the arm is, as LiteLLM
    is imported."""
    from google.adk.models.lite_llm import LiteLlm

    class Bounded(LiteLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[Any]:
            # The answer is gathered under the deadline and handed on after it: read by review, a deadline held across
            # the yield counted ADK's own time with an answer that came in time, and running out there cancelled
            # ADK's await, the answer lost and no timeout raised for a fallback to move on from.
            asked = super().generate_content_async(llm_request, stream)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_S):
                    answered = [response async for response in asked]
            except TimeoutError as exc:
                raise CallTimeoutError(f"no answer within {REQUEST_TIMEOUT_S:g} s, the retries included") from exc
            for response in answered:
                yield response

    return Bounded


def _litellm():  # unannotated: a checker then knows the module and its typed flags, where `ModuleType` names none
    """LiteLLM, imported where the Copilot arm needs it and nowhere else. At the top of the command it cost about a
    second of every start, `--help` included, and on its own import it read `.env` unless told it runs in production,
    as ADK tells it, and fetched a price list unless `LITELLM_LOCAL_MODEL_COST_MAP` is set: set here, where before
    `.env.example` alone set it, and an installed command reads no `.env`. It reads `GITHUB_COPILOT_TOKEN_DIR` as
    written, so it is handed the folder readiness and the login read: read by review, `~/tokens` read ready, and
    LiteLLM, finding no token under a folder named `~`, would have started its own device login mid-review."""
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # the pinned version's own price list
    if "GITHUB_COPILOT_TOKEN_DIR" in os.environ:
        os.environ["GITHUB_COPILOT_TOKEN_DIR"] = str(copilot_dir())
    import litellm

    return litellm


def _gemini_retried() -> list[int]:
    """The statuses genai retries: a timeout and each 5xx, and a 429 only through Vertex AI, whose shared quota throws
    429s that a moment's wait passes. AI Studio's names a quota spent for most of a minute or the day: measured
    offline, genai retried it after 1.3 s and 2.7 s, each retry a request more against the quota and none able to pass
    it, where unretried it ends what the review spends at once (`StopAtRateLimit`)."""
    return [408, *([429] if is_enterprise_mode_enabled() else []), 500, 502, 503, 504]


def request_config(thinking_budget: int | None = None) -> types.GenerateContentConfig:
    """What every judging call carries, on both arms: temperature zero and a deadline; on Gemini its retries, which
    Copilot's OpenAI SDK makes itself (`build_model`); and a guideline's thinking budget, where the provider has one.

    A severity is a decision, and a demo that gives a different verdict on every run
    teaches the wrong lesson. The deadline is one number, on the request, and both
    arms read it there, as genai reads its retries: genai takes
    `HttpOptions.timeout` in milliseconds and retries the request `attempts` times
    on the status codes it deems transient, with backoff; ADK's LiteLLM wrapper
    converts the timeout to LiteLLM's seconds and forwards the attempts as
    `num_retries` (`lite_llm.py`, 2.12.0). Measured against a server that never
    answered, LiteLLM hands those to the OpenAI SDK as its retries and retries
    around it too: three became seven. So the Copilot arm asks for none, and the
    SDK's own retries, `RETRY_ATTEMPTS` in all, make its three, backing off and
    waiting out the server's Retry-After: measured against a local server answering
    429, asking for one added LiteLLM's own retry twenty milliseconds after the
    SDK's last, its Retry-After unread, where asking for none spaced the same three.
    A call past its deadline or its retries is a model error like any other, which
    `ReportProviderErrors` turns into that guideline's own answer.

    The budget is configuration, not a plan: ADK's `BuiltInPlanner` only copied
    it onto the request's config, which the request carries itself, and the
    ledger reads it there to reserve the thinking a call allows. Only Gemini
    honours it; LiteLLM, the Copilot arm, never forwards it, so none is set
    there, and the reviewer states plainly that the reasoning lever is absent on
    Copilot.
    """
    gemini = provider() == "gemini"
    return types.GenerateContentConfig(
        temperature=0.0,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget) if thinking_budget and gemini else None,
        http_options=types.HttpOptions(
            timeout=int(REQUEST_TIMEOUT_S * 1000),
            retry_options=types.HttpRetryOptions(
                attempts=RETRY_ATTEMPTS, initial_delay=1.0, http_status_codes=_gemini_retried()
            )
            if gemini
            else None,
        ),
    )


def require_ready(*, roles: tuple[str, ...] = (), presets: Mapping[str, Models] | None = None) -> str | None:
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
    named = [model_pick(role, (presets or {}).get(role)) for role in ("", *roles)]
    if fallback := fallback_name():
        named.append(("REVIEW_FALLBACK_MODEL", fallback))
    if arm == "copilot":
        return _copilot_refused(named)
    return _gemini_refused() or _not_gemini(named)


def _copilot_refused(named: list[tuple[str, str]]) -> str | None:
    """Why the Copilot arm cannot run with the models `named`, each by the variable that names it, or None."""
    if not ((token := copilot_token()).is_file() and token.stat().st_size):  # an empty file is no token
        return (
            f"REVIEW_PROVIDER=copilot needs a cached token at {token}. "
            "Run `adk-review-login` once: `uv run adk-review-login` in the checkout"
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
        except ValueError as exc:
            # Said up front, as only this arm resolves names up front: ADK's first line, since the rest may advise
            # installing another provider's package, and the reviewer serves Copilot and Gemini only.
            first, _, _ = str(exc).partition("\n")
            return f"{var}: {first}"
        except ImportError:  # a `class:name` prefix whose class needs a package the reviewer does not install
            return f"{var}={name} is no Gemini model"
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
