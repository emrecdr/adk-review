"""A credential in an added line, by a vendor's shape `core/secrets.py` knows, found before any model runs.

The same shapes hide every secret from what a model sees, in every review and whatever profile runs: that is
redaction, a plugin, and turning this rule off leaves it on. This rule is the other half: with redaction on, no
guideline ever sees a key, so a key must be found by code. It reads the added lines of each file's whole diff, not
the capped block the guidelines read, so a credential past the cap is still found. One finding a line: a line
holding a key is one finding, whatever else fits. A value assigned to a name that says secret, with no vendor's
shape, is `named-secrets`', which weighs the name.

An example is no credential, and redaction still hides it. A line is weighed with its examples blanked
(`without_examples`): a URL's credential to a documentation host, or one whose password only stands for one or
names its field at a host no dot names, and a private key's header with an ellipsis for the key. Measured on twelve
real projects: all eleven credentials this rule found in their source were examples, each a blocker; seven were such
ones, and no shape tells the four left from a key — a docstring's URL to a real domain, twice, AWS's documented key,
and a form's key header before a template. Blanked, never skipped: a real key beside an example is still found, and a
vendor's key shape whatever its value or its host, since a key cannot say it is an example.
"""

from __future__ import annotations

from ..rule_helpers import Rule, vendor_shape, without_examples


class Secrets(Rule):
    """A credential committed to a repository is in its history for good: whoever can read the history has it."""

    id = "secrets"
    severity = "blocker"
    title = "a hard-coded credential"
    fix = "Read it from the environment or a secret store, remove it from history, and rotate it."
    bad = (
        'client = Github("ghp_' + "a1B2" * 9 + '")',
        ("deploy.yaml", "url: https://hooks.slack.com/services/T0/B0/x"),
        'DATABASE_URL = "postgresql://app:' + "Xk9mQ2vR7t" + '@db.internal.acme.io/app"',
        'URL = "mssql://sa:' + "P@ssw0rd" + '@sqlserver.corp.acme.io/db"',
        ("README.md", "curl https://x-access-token:ghp_" + "a1B2" * 9 + "@api.example.com/repos"),
    )
    good = (
        "token = get_token(request)",
        'password = os.environ["PASSWORD"]',
        'password = "correct horse battery"',
        'DATABASE_URL = "postgresql://user:password@localhost:5432/app"',
        ("README.md", "Connect with https://jo:a-secret@www.example.com/path"),
        'private_key = "-----BEGIN PRIVATE KEY-----..."',
    )

    def check(self, path: str, line: str) -> bool:  # noqa: ARG002 -- a credential is one in any file
        # The shapes first, which nearly every line lacks; then the line with its examples blanked. A value a name
        # alone says is secret is `named-secrets`' to weigh.
        return vendor_shape(line) is not None and vendor_shape(without_examples(line)) is not None
