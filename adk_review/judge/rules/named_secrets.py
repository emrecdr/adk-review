"""A value assigned to a name that says secret — a password, a token, an API key — with no vendor's shape.

The value decides as much as the name. A person chooses a password, which may be any words; a machine makes a token,
a key or a secret, one run of characters with a digit among them. A value that names a secret is a label, a field's
or a variable's name, and code where a literal would stand is no literal. Measured over the source of twelve published
projects — click, jinja2, httpx, anyio, attrs, fastapi, docker, langchain-core, huggingface-hub, jsonschema, ADK and
LiteLLM — the name alone flagged 374 lines as blockers, and none was a credential: a lexer's `TOKEN_COMMENT:
"comment"`, a price per token, `API_KEY = "apiKey"`, an argument passed on, a documentation's `hf_...`, a minified
bundle's code between two strings. Weighed so, 4 are left, each a value that looks made: a key, two hashes and a
token. Of 37 credentials written as projects write them, in code, JSON, YAML, `.env` and shell, none is let go that the
name alone found, and one more is found: every named value on a line is weighed, not the first alone. A scheme before
a token is no part of it, and only an unquoted value can be code: of 8 more credentials in formats those lack, 5 are
found where none was, and the twelve projects' 4 are the same 4.
"""

from __future__ import annotations

import re

from ..rule_helpers import (
    SECRET_WORDS,
    Rule,
    is_placeholder,
    is_test,
    named_at,
    named_values,
    vendor_shape,
    without_examples,
)

#: The endings of a name that is about a secret, never one: where it lives, what kind it is, how it is named or
#: checked. Measured: 177 of 714 lines flagged in 2.1 million lines of published library code, none of them a
#: credential, carried one of these — `token_url`, `secret_name`, `password_field`, `TOKEN_TYPE`.
ABOUT = (
    "_arn", "_endpoint", "_env", "_expiry", "_field", "_file", "_format", "_header", "_help", "_id", "_kind",
    "_label", "_length", "_limit", "_name", "_path", "_prefix", "_ref", "_regex", "_seconds", "_type", "_uri", "_url",
)  # fmt: skip
#: A name whose value a person chooses: a password, which may be any words. A token, a key or a secret a machine makes.
_CHOSEN = re.compile(r"(?i)passw(?:or)?d|pwd")
#: A value's words, `apiKey`, `api_key` and `API-KEY` alike: runs of capitals, of letters, of digits.
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
#: A word a name says secret with, among a value's words joined by `_`: a value that holds one names a secret.
_SECRET_WORD = re.compile(r"(?:^|_)(?:" + SECRET_WORDS + r")(?:_|$)")
#: What sets a value's words apart, as a label's are: `my_secret_key`, `Show password`.
_SEPARATOR = re.compile(r"[\s_.\-/]")
#: Code where a literal would stand: a chain of attributes, `raw_credential.oauth2.client_secret`, or a name passed on
#: as an argument, `aws_session_token=s3_aws_session_token,`; unquoted alone, as a quoted value is a literal.
_CODE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+[,)\]]*|[A-Za-z_]\w*[,)\]]+")
#: A number, a price per token or a count: no credential a machine makes. One way to split a run of digits: measured
#: by review, `\d+\.?\d*` had two and tried each on a failed match, 20,000 digits and a letter taking 6.9 s.
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
#: Marks of code, which no key's alphabet holds — base64's, hex's, a UUID's, Django's symbols: what a minified bundle
#: spans between two of its strings, `+(0,S.parse)(e))}finally{m(!1)}`.
_CODE_MARK = re.compile(r"[{};]")
#: The scheme an authorization header names before its token, `Bearer `: the token after it, one run to the value's
#: end, is what is weighed. Before prose, `"Token is invalid"`, the word is a label's.
_SCHEME = re.compile(r"(?i)\A(?:bearer|basic|token)\s+(?=\S+$)")
#: An environment's file, which a folder named `test` holds as config, never as a test's fixture: `.env`, `db.env`,
#: `.env.test`. Read by review: any file in such a folder was a test's, and a password in `deploy/test/db.env` passed.
_ENV_FILE = re.compile(r"(?:^|/)(?:[^/]*\.env|\.env(?:\.[^/]*)?)$")
#: The shortest credential a machine makes: shorter is a documentation's example, `sk-1234`.
MADE_MIN_CHARS = 8
#: The widest line a person writes: wider is generated, a minified bundle or data, where a name says nothing of who
#: chose its value, so every value there is weighed as a machine's.
WRITTEN_MAX_CHARS = 1_000


class NamedSecrets(Rule):
    """A password or a token written into the code is in its history for good, as a vendor's key is. A name about a
    secret — where one lives, its type, its field — holds none; a placeholder is none; and a test's fixture, a user
    made with a password of the test's own, in any language, is a test's: each left alone, while a vendor's key is
    `secrets`' to find in a test as anywhere. Measured: 4 of 6 test fixtures were flagged as blockers. Read by review:
    any file in a folder named `test` read as a test's, so a password in `deploy/test/db.env`, an environment's
    config, passed; an env file is read wherever it lies."""

    id = "named-secrets"
    reads_tests = True  # an env file in a test's folder, which `check` reads, and no test's own fixture
    severity = "blocker"
    title = "a value assigned to a name that says secret"
    fix = "Read it from the environment or a secret store, remove it from history, and rotate it."
    bad = (
        'password = "correct horse battery staple"',
        ("settings.yaml", "api_key: 4f8a1c2e9b7d6035"),
        '{"token_type": "bearer", "access_token": "9f8e7d6c5b4a3f2e"}',
    )
    good = (
        'token_url = "https://auth.example.com/token"',
        'password_field = "password-input"',
        'password = os.environ["PASSWORD"]',
        'api_key = "your-api-key-here"',
        'PASSWORD = "changeme"',
        ("tests/test_login.py", 'user = User(password="testpassword")'),
        "tokenizer = build()",
        'API_KEY = "apiKey"',
        "refresh_token=auth_credential.oauth2.refresh_token,",
        ("config.yaml", "input_cost_per_token: 0.00000015"),
        'api_key="hf_..."',
    )

    def check(self, path: str, line: str) -> bool:
        if is_test(path) and not _ENV_FILE.search(path):  # a test's own fixture, in any language; an env file is config
            return False
        # The generic shape first, which nearly every line lacks; then the line with its examples blanked, which must
        # hold no vendor's shape: a vendor's is `secrets`' finding, one credential, one finding. An example is blanked
        # as `[REDACTED]`, which no shape takes for a value, so what is left of one is no value either: measured by
        # review, blanked as a space, `" /application"` was flagged as a password. Then every named value on the line.
        if named_at(line) is None:
            return False
        text = without_examples(line)
        if vendor_shape(text) is not None:
            return False
        written = len(line) <= WRITTEN_MAX_CHARS
        return any(_credential(name, value, quoted, written) for name, value, quoted in named_values(text))


def _credential(name: str, value: str, quoted: bool, written: bool) -> bool:
    """Whether `value`, given to `name`, can be the credential the name says: never a name about one, a placeholder,
    code, or a label that names a secret and holds none; a password a person wrote, whatever its words; else what a
    machine makes, one run of eight characters or more, a digit among them, no number and no mark of code."""
    if name.lower().endswith(ABOUT) or is_placeholder(value) or (not quoted and _CODE.fullmatch(value)):
        return False
    made = _SCHEME.sub("", value)
    digit = any(char.isdigit() for char in made)
    if _SECRET_WORD.search("_".join(_WORDS.findall(made)).lower()) and (not digit or _SEPARATOR.search(made)):
        return False  # `"OPENAI_API_KEY"`, `"my_secret_key"`, `"Show password"`: a label, where `"password123"` is not
    if written and _CHOSEN.search(name):
        return True
    return (
        digit
        and len(made) >= MADE_MIN_CHARS
        and not any(char.isspace() for char in made)
        and not _NUMBER.fullmatch(made)
        and not _CODE_MARK.search(made)
    )
