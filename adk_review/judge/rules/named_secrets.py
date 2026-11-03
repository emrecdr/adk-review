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

Not read, as known limits: a docker-compose file's list item, `- DB_PASSWORD=...`, a Dockerfile's `ENV` line continued
with a backslash, and a value in triple quotes or split over lines, as a parenthesized concatenation writes it, which
the redaction misses too: measured, none of the 2.3 million lines of library code this project installs held a
credential so. Read by review, three rounds running: each widening of the shape this rule shares with the redaction
before every model call moved what it missed and what it flagged, for every format at once.
"""

from __future__ import annotations

import re

from ..rule_helpers import (
    Rule,
    is_credential,
    named_at,
    named_values,
    vendor_shape,
    without_examples,
)

#: An environment's file, which a folder named `test` holds as config, never as a test's fixture: `.env`, `db.env`,
#: `.env.test`. Read by review: any file in such a folder was a test's, and a password in `deploy/test/db.env` passed.
_ENV_FILE = re.compile(r"(?:^|/)(?:[^/]*\.env|\.env(?:\.[^/]*)?|\.envrc)$")
#: A test suite's own folder, by each language's layout. At any depth: `tests/`, Jest's `__tests__/`, Go's `testdata/`.
#: At the top, as a package keeps its suite: `test/`, RSpec's `spec/`, Swift's `Tests/`, Xcode's `MyAppTests/`, .NET's
#: `MyApp.Tests/`. Maven's `src/test/`, a Gradle source set's `src/androidTest/`. A `test/` folder below the top names
#: an environment as often, `deploy/test/`, and is read. Read by the fourth review: any folder named `test` was a suite,
#: and a password in `deploy/test/db.js` passed.
_SUITE = re.compile(
    r"(?:^|/)(?:tests|__tests__|testdata)/|^(?:test|spec|\w*Tests|[\w.]+\.Tests)/|(?:^|/)src/(?:test|[a-z]\w*Test)/"
)
#: A test file by its own name, in the languages that name one: `test_login.py`, `conftest.py`, `login_test.go`,
#: `login.test.ts`, `login.spec.js`, `login_spec.rb`. Each name is bound to its language: `pod_spec.py` is production
#: code, as the third review found.
_TEST_NAME = re.compile(
    r"(?:^|/)(?:test_\w*\.pyi?|conftest\.py)$|_test\.(?:pyi?|go|rb|exs|dart|rs)$|\.(?:test|spec)\.[cm]?[jt]sx?$"
    r"|_spec\.rb$"
)
#: The widest line a person writes: wider is generated, a minified bundle or data, where a name says nothing of who
#: chose its value, so every value there is weighed as a machine's.
WRITTEN_MAX_CHARS = 1_000


def _tests_own(path: str) -> bool:
    """Whether `path` is a test's own, its password a fixture's: a file under a suite's folder, or named as a test.
    Never an env file: an environment's config even in a folder named `test`."""
    return not _ENV_FILE.search(path) and bool(_SUITE.search(path) or _TEST_NAME.search(path))


class NamedSecrets(Rule):
    """A password or a token written into the code is in its history for good, as a vendor's key is. A name about a
    secret — where one lives, its type, its field — holds none; a placeholder is none; and a test's fixture, a user
    made with a password of the test's own, in any language, is a test's: each left alone, while a vendor's key is
    `secrets`' to find in a test as anywhere. Measured: 4 of 6 test fixtures were flagged as blockers. Read by review:
    any file in a folder named `test` read as a test's, so a password in `deploy/test/db.env`, an environment's
    config, passed; a test's code and a fixture under `tests` are left alone, an env file read wherever it lies."""

    id = "named-secrets"
    reads_tests = True  # all but a test's own code and its suite's fixtures, which `check` passes over
    reads_every_kind = True  # a password leaks from a script or a page as from code
    severity = "blocker"
    title = "a value assigned to a name that says secret"
    fix = "Read it from the environment or a secret store, remove it from history, and rotate it."
    why = "A value in the code is in every clone and in the history. Anyone who reads either can use it."
    bad = (
        'password = "correct horse battery staple"',
        ("settings.yaml", "api_key: 4f8a1c2e9b7d6035"),
        '{"token_type": "bearer", "access_token": "9f8e7d6c5b4a3f2e"}',
        ("Dockerfile", "ENV DB_PASSWORD=Sup3rS3cret-db9"),
    )
    good = (
        'token_url = "https://auth.example.com/token"',
        'password_field = "password-input"',
        'password = os.environ["PASSWORD"]',
        ("Dockerfile", "ENV API_KEY=${API_KEY}"),
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
        if _tests_own(path):
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
        return any(is_credential(name, value, quoted, written) for name, value, quoted in named_values(text))
