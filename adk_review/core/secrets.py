"""Known credential shapes: scrubbed from everything a model sees, and the shapes the secrets rule finds.

Redaction is a plugin no profile turns off: every model call, in every review, is scrubbed with `scrub`. The rule
`judge/rules/secrets.py` is the other half, and asks `vendor_shape`. Measured on the Copilot arm: with the plugin
scrubbing every prompt, no guideline reported the hard-coded AWS key, because no guideline ever saw it. A secret
must therefore be found by code, from the diff itself, before the guidelines run — and that is why the guidelines
judge evidence rather than hunt for it. Shape-based: the rule reads added lines one at a time, so a private key is
found by its header, and the redaction, reading whole text, hides its block. Measured before this table: 6 of 20
common shapes found, and `token = get_token(request)` a hard-coded-credential blocker; a value is a secret when it is
quoted, or when a `.env` or YAML line holds it with a digit, never when code names, reads or calls it. What only
shows a credential's shape, an example, `without_examples` blanks for the rules; redaction still hides it.
"""

from __future__ import annotations

import re
from typing import Any

#: The words a secret goes by: what the name of a named value holds, and what a value holds that only names a secret,
#: `"OPENAI_API_KEY"` or `"Show password"`.
SECRET_WORDS = r"api[_-]?key|secret|token|passw(?:or)?d|pwd"
#: A run each spelling of a word above holds, casefolded, none with the `i` a shape also reads in U+0130 and U+0131:
#: a line holding none is not searched, `named-secrets` over 1.61 million lines taking 2.9 s where it took 5.6.
_WORD_CORES = ("key", "secret", "token", "passw", "pwd")
#: (kind, pattern, replacement).
#: The name a secret goes by, prefixed and suffixed as code spells it (`DB_PASSWORD`, `client_secret`, `SECRET_KEY`),
#: never inside a longer word: `tokenizer` names none.
_NAMED = r"(?:" + SECRET_WORDS + r")(?:[_.-][\w.-]{0,80})?"
#: Not a value but a stand-in for one: a template, a placeholder, or what an earlier shape already hid.
_STAND_IN = r"(?![$%{<]|\[REDACTED)"
#: Every run a shape scans is bounded: a name to 80 characters past its keyword, a scheme to 40, a token's segment to
#: 2,000. Measured with open runs: 9 s over one added line of 40,000 characters, four times as long for each doubling,
#: and no deadline stops a rule. A key block's body holds key-like lines only — a diff's marker, then base64, an armour
#: header, or nothing — so a header where no rule reads, a file's name or an unchanged line, cannot reach a footer
#: further on: measured with any text allowed between them, the change in between was hidden from every model call.
_KEY_HEADER = r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
_KEY_FOOTER = r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
_KEY_LINE = r"[+\- ]?[ \t]*(?:[A-Za-z0-9+/=]+|(?:Proc-Type|DEK-Info|Version|Comment|Hash|Charset): [^\n]*)?[ \t]*\r?"
#: A URL's scheme and user, and its password: up to the last `@` before the host, since a password may hold one,
#: `P@ssw0rd`. Measured before, the password ended at its first `@`: the rule read `P` as a placeholder, and redaction
#: sent the rest of the password to the model.
_URL_LEAD = r"\b([a-z][a-z0-9+.-]{0,40}://[^\s:/@\"']+:)"
_URL_PASSWORD = r"[^\s/\"']+"
#: The shapes a vendor's credential takes, or a URL's or a key file's: what the secrets rule finds.
_VENDOR = (
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws_access_key_id]"),
    (
        "aws_secret_access_key",
        re.compile(r"(?i)(aws_secret_access_key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9/+=]{40}"),
        r"\1[REDACTED:aws_secret_access_key]",
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{80,})\b"),
        "[REDACTED:github_token]",
    ),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "[REDACTED:gitlab_token]"),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack_token]"),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9_/]+"), "[REDACTED:slack_webhook]"),
    ("stripe_key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}"), "[REDACTED:stripe_key]"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[REDACTED:google_api_key]"),
    # Measured by review: a key of each vendor below, held by a name that says no secret, was found by no rule and
    # reached the model unredacted. Each is known by its vendor's own prefix: measured over 7.6 million lines of this
    # repository and its installed packages, the OpenAI, Anthropic and LiteLLM SDKs among them, none matched.
    ("anthropic_api_key", re.compile(r"\bsk-ant-[a-z]{2,10}\d{2}-[A-Za-z0-9_-]{80,}"), "[REDACTED:anthropic_api_key]"),
    (
        "openai_api_key",
        re.compile(r"\bsk-(?:(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}|[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b)"),
        "[REDACTED:openai_api_key]",
    ),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{34}\b"), "[REDACTED:huggingface_token]"),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "[REDACTED:npm_token]"),
    ("pypi_token", re.compile(r"\bpypi-AgE[A-Za-z0-9_-]{50,}"), "[REDACTED:pypi_token]"),
    # Its last segment may end in `-`, after which `\b` finds no edge before a quote.
    (
        "sendgrid_api_key",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![\w-])"),
        "[REDACTED:sendgrid_api_key]",
    ),
    # As each vendor documents its prefix: Shopify's access tokens and shared secret, 32 hex digits on; Stripe's
    # webhook signing secret, of more than one length; Tailscale's keys, a kind, an id and the secret. Measured over
    # the same 7.6 million lines, none matched.
    ("shopify_token", re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b"), "[REDACTED:shopify_token]"),
    ("stripe_webhook_secret", re.compile(r"\bwhsec_[A-Za-z0-9]{32,}"), "[REDACTED:stripe_webhook_secret]"),
    (
        "tailscale_key",
        re.compile(r"\btskey-(?:auth|api|client|scim|webhook)-[A-Za-z0-9]{6,}-[A-Za-z0-9]{16,}"),
        "[REDACTED:tailscale_key]",
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,2000}\.eyJ[A-Za-z0-9_-]{8,2000}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    (
        "url_credentials",
        re.compile(r"(?i)" + _URL_LEAD + _STAND_IN + _URL_PASSWORD + r"(@)"),
        r"\1[REDACTED]\2",
    ),
    ("azure_account_key", re.compile(r"(?i)(AccountKey=)" + _STAND_IN + r"[A-Za-z0-9+/=]{20,}"), r"\1[REDACTED]"),
    (
        # The whole block where the text holds it, its header where a line is all there is. Each key line is matched
        # once, atomic, and no run reads past another header: measured by review, a header with 25 key lines and no
        # footer took over 45 s to scrub, each line matched two ways or three, and 8,000 headers on one line 5.7 s.
        "private_key",
        re.compile(
            _KEY_HEADER
            + r"(?:(?:(?!-----BEGIN )[^\n])*?"
            + _KEY_FOOTER
            + r"|(?:(?!-----BEGIN )[^\n])*\n(?:(?>"
            + _KEY_LINE
            + r"\n)){0,400}?[^\n]*?"
            + _KEY_FOOTER
            + r")?"
        ),
        "[REDACTED:private_key]",
    ),
)
#: A value a name alone says is secret, with no vendor's shape: what `named-secrets` weighs.
_GENERIC = (
    (
        "generic_secret",  # a named value in quotes: code, JSON, YAML
        re.compile(r"(?i)(" + _NAMED + r"[\"']?\s*[:=]\s*)([\"'])" + _STAND_IN + r"([^\"'\n]{6,})\2"),
        r"\1\2[REDACTED]\2",
    ),
    (
        "generic_secret",  # a named value unquoted, a `.env` or YAML line, holding a digit: code names, reads, calls
        re.compile(
            r"(?im)^([+ -]?[^\S\n]*(?:export[^\S\n]+)?[\w.-]{0,80}?"
            + _NAMED
            + r"\s*[:=]\s*)(?=[^\s#]*\d)"
            + _STAND_IN
            + r"([^\s\"'#(\[][^\s\"'#()\[\]{}]{7,})\s*$"
        ),
        r"\1[REDACTED]",
    ),
)
#: Every shape, a vendor's first: what redaction scrubs, and what a cut never falls inside.
_PATTERNS = (*_VENDOR, *_GENERIC)
#: The vendors' shapes that open on a word's edge and a prefix of their own, read first as one pattern: a text none of
#: them is in holds none, and the other shapes alone read it. Measured by review: each took a search of every added
#: line and of every diff past its cap, `vendor_shape` over a million lines 2.22 s and `spans` over 35.7 million
#: characters 5.44 s, read first as one 1.04 s and 2.91 s, every answer the same. Only a shape that reads the same
#: inside an alternation joins: no flag, no group referred back to.
_PREFIXED = tuple(
    shape
    for shape in _VENDOR
    if shape[1].pattern.startswith(r"\b")
    and shape[1].flags == re.UNICODE
    and not re.search(r"\\[1-9]|\(\?P[<=]", shape[1].pattern)
)
_ANY_PREFIXED = re.compile("|".join(f"(?:{pattern.pattern})" for _kind, pattern, _ in _PREFIXED))
_VENDOR_REST = tuple(shape for shape in _VENDOR if shape not in _PREFIXED)
_PATTERNS_REST = (*_VENDOR_REST, *_GENERIC)


def scrub(text: str) -> tuple[str, int]:
    """`text` with every known secret shape replaced, and how many were. A prefixed shape none of which is in the text
    is passed over until a shape rewrites it, since a rewrite can make one: `[REDACTED…]` is the word's edge a token
    glued to the value before it opens on. Measured by review: nine prefixed shapes more took scrubbing a request at
    the block cap from 36 ms to 54, every request of every review, each on the event loop; passed over, 23."""
    total = 0
    unprefixed = not _ANY_PREFIXED.search(text)
    for shape in _PATTERNS:
        if unprefixed and shape in _PREFIXED:
            continue
        text, count = shape[1].subn(shape[2], text)
        total += count
        unprefixed = unprefixed and not count
    return text, total


#: How far past a cut a credential crossing it can run: past twice an 8,192-bit key's block, about 6,600 characters
#: with a diff's markers. A cut searches no further, so it costs its own length and never the text's: measured by
#: review, a 2 MB diff of padding shaped like a token took 3.6 s to cut at 16,000.
CREDENTIAL_REACH = 16_384


def spans(text: str) -> list[tuple[int, int]]:
    """Where each known secret shape sits in `text`, as `(start, end)`: what a cut must never fall inside, since half a
    credential no longer matches its shape and would be sent unredacted."""
    table = _PATTERNS if _ANY_PREFIXED.search(text) else _PATTERNS_REST
    return [match.span() for _kind, pattern, _replacement in table for match in pattern.finditer(text)]


def scrub_value(value: Any) -> tuple[Any, int]:
    """`scrub` applied to every string inside a tool result, however nested."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        out, total = {}, 0
        for key, item in value.items():
            out[key], count = scrub_value(item)
            total += count
        return out, total
    if isinstance(value, list):
        out, total = [], 0
        for item in value:
            scrubbed, count = scrub_value(item)
            out.append(scrubbed)
            total += count
        return out, total
    return value, 0


def shape_of(text: str) -> str | None:
    """The kind of the first known secret shape `text` holds, or None. A vendor's shape comes first, so a line holding
    one and a named value is the vendor's: `generic_secret` is a named value alone, what `named-secrets` weighs."""
    table = _PATTERNS if _ANY_PREFIXED.search(text) else _PATTERNS_REST
    return next((kind for kind, pattern, _ in table if pattern.search(text)), None)


def vendor_shape(text: str) -> str | None:
    """The kind of the first vendor's shape `text` holds, or None: what the secrets rule asks of an added line. A
    named value's shapes are never searched, nearly every line holds no vendor's, and they cost the most."""
    table = _VENDOR if _ANY_PREFIXED.search(text) else _VENDOR_REST
    return next((kind for kind, pattern, _ in table if pattern.search(text)), None)


def _may_name(text: str) -> bool:
    """Whether `text` can hold a value of the generic shape: whether it holds one of `_WORD_CORES`, casefolded."""
    folded = text.casefold()
    return any(core in folded for core in _WORD_CORES)


def named_at(text: str) -> tuple[str, str, bool] | None:
    """The first value of the generic shape in `text` as `(name, value, quoted)`, None where it holds none:
    `("password", "hunter2hunter2", True)` for `db_password = "hunter2hunter2"`; a `.env` line's name is whole."""
    if not _may_name(text):
        return None
    found = [match for _kind, pattern, _ in _GENERIC if (match := pattern.search(text))]
    first = min(found, key=lambda match: match.start(), default=None)
    return None if first is None else _named(first)


def named_values(text: str) -> list[tuple[str, str, bool]]:
    """Every value of the generic shape in `text`, as `named_at` gives one, in the order they stand: what a rule that
    weighs the name and the value asks; redaction weighs neither, and hides them all. Every one, not the first:
    measured, `{"token_type": "bearer", "access_token": …}` was let go for its first name, a token's type."""
    if not _may_name(text):
        return []
    found = sorted((match for _kind, pattern, _ in _GENERIC for match in pattern.finditer(text)), key=re.Match.start)
    return [_named(match) for match in found]


#: A name as code, a `.env` line or YAML spells it, past a diff's marker and an `export`.
_NAME = re.compile(r"[+ -]?\s*(?:export\s+)?([\w.-]+)")


def _named(found: re.Match[str]) -> tuple[str, str, bool]:
    """A generic shape's match as `(name, value, quoted)`: the first shape is the quoted one."""
    name = _NAME.match(found.group(1))
    return (name.group(1) if name else found.group(1)), found.group(found.lastindex or 0), found.re is _GENERIC[0][1]


#: A value that only stands for a secret: a run of one character, `****` or `...` among them; the word that names its
#: field, `password` or `secret`; a word saying it is to be replaced; one holding the stand-in for what is hidden or
#: blanked, `[REDACTED]`; or a key that documentation cut short or masked, `hf_...`, `sk-1****cdef`, `6xxxxxxx`.
#: Measured: 11 of 17 such values were flagged as blockers by name, `"changeme"` and `"REPLACE_ME"` among them; the
#: field's own word, five times in ADK's source, was never a credential: an enum's `PASSWORD = "password"`, a
#: docstring's `client_secret="secret"`, a comment; nor were the cut and the masked keys of four projects' docstrings.
_PLACEHOLDER = re.compile(
    r"(?i)(.)\1*|change[-_ ]?me|replace[-_ ]?me|your[-_ ].*|.*[-_ ]here|example.*|dummy.*|placeholder.*|redacted"
    r"|pass(?:word|wd)?|pwd|secret|token|key|.*\[REDACTED\].*|.*(?:\.\.\.|…)|.*(?:[*•]{3}|xxxx).*"
)
#: A password that only masks one, at any host: `****`, `xxxx`, `...`.
_MASK = re.compile(r"\*+|x+|X+|\.+|…+|•+")
#: The names RFC 2606 keeps for documentation and tests, each with its dot: a credential to one opens nothing. A host
#: named `test` alone is a compose service's, and a real password to it opens one.
_EXAMPLE_HOST = re.compile(r"(?i)(?:[\w-]+\.)*example\.(?:com|net|org)|(?:[\w-]+\.)+(?:example|test|invalid)")
#: A host a machine names for itself: no dot in its name, `localhost` or a compose service, a loopback address, or
#: Docker's name for the host it runs on. A placeholder password to one stands for a developer's own.
_LOCAL_HOST = re.compile(r"(?i)[\w-]*|127(?:\.\d{1,3}){3}|0\.0\.0\.0|host\.docker\.internal")
#: A URL's credential as the url shape reads it, its password and its host apart.
_URL_CREDENTIAL = re.compile(r"(?i)" + _URL_LEAD + _STAND_IN + "(" + _URL_PASSWORD + r")@\[?([\w.-]*)")
_HOST_AFTER = re.compile(r"\[?([\w.-]*)")
#: A private key's header with an ellipsis where the key would be: a form's hint, or a docstring's.
_KEY_HINT = re.compile(_KEY_HEADER + r"(?:\\n|\s)*(?:\.\.\.|…)")
#: What an example is blanked with: the stand-in every shape skips, as it skips what redaction already hid, so no
#: shape and no named value is read from what is left of one.
_BLANK = "[REDACTED]"


def is_placeholder(value: str) -> bool:
    """Whether `value` only stands for a secret, `"changeme"` or `"****"`, and holds none."""
    return _PLACEHOLDER.fullmatch(value.strip()) is not None


def without_examples(text: str) -> str:
    """`text` with each credential that only shows a credential's shape blanked as `[REDACTED]`: a URL's to a
    documentation host, or with a mask for its password, or with a placeholder password to a host a machine names for
    itself, `localhost` or `127.0.0.1`; and a private key's header with an ellipsis for the key. Another host with such
    a password is a default credential, and a vendor's key in a URL is a key whatever its host: both stay. The rules
    weigh what is left, so a real key beside an example is still found: measured before, a line holding an example was
    let go whole. Redaction never calls this: it hides every shape, examples too."""
    return _KEY_HINT.sub(_BLANK, _URL_CREDENTIAL.sub(_unless_example, text))


def _unless_example(found: re.Match[str]) -> str:
    """A URL's credential blanked where it only shows one's shape, kept where it may open something. A vendor's shape
    comes before the URL's in the table, so a key as its user or its password is named first. A password may hold an
    `@`, and the host after the last one can be a query's: blanked only where it is an example read either way, to
    its first `@` or its last. Measured by review: a token in a URL to `api.example.com` was blanked with the URL; and
    read to the last `@` alone, `S3cret@api.acme.io?to=ops@example.com` was an example, and stopped at `?` or `#`,
    `Password@123#@sql.acme.io` was one: each a real credential that went unfound."""
    if vendor_shape(found.group(0)) != "url_credentials":
        return found.group(0)
    password, host = found.group(2), found.group(3)
    readings = [(password, host)]
    first, at, rest = password.partition("@")
    if at and (after := _HOST_AFTER.match(rest)):  # it matches any text: only `at` decides
        readings.append((first, after.group(1)))
    return _BLANK if all(_example(word, name) for word, name in readings) else found.group(0)


def _example(password: str, host: str) -> bool:
    """Whether a URL's credential opens nothing: to a documentation host, with a mask for a password, or with a
    placeholder password to a host a machine names for itself."""
    if _EXAMPLE_HOST.fullmatch(host) or _MASK.fullmatch(password):
        return True
    return _LOCAL_HOST.fullmatch(host) is not None and is_placeholder(password)
