"""The reviewer's evidence and its grounding, its secrets and plugins, and what its report lets through."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    BASE,
    CANNED_REVIEW,
    HEAD,
    LONG_LINE,
    FakeModel,
    judge_module,
    load_agent,
    model_check,
    run_review,
)

#: Text every guideline's instruction carries, the heading before the change: a rule on it reaches them all.
EVERY_GUIDELINE = "## The change"
#: The planted branch's file with the shell call, as a finding names it.
CHARGE = "src/payments/charge.py"


class TestDecision:
    def test_a_quote_grounds_only_as_whole_lines_of_its_own_files_diff_in_order(self, demo_repo: Path) -> None:
        """Measured with the rule before it, a substring of the whole
        rendered block: every quote marked False here was grounded — the block's framing, git's own headers, one
        word, part of a line, a span running into the next file, the right line filed under the wrong file — except
        the two marked as carried over, which it dropped too: order and adjacency survive the change. A kept
        finding is named at the line its quote starts, never the model's: measured live on Copilot, the models named
        a wrong line for 4 of 5 findings, `charge.py:9` for a quote of lines 27 and 28."""
        rule, collect, schema = _grounding_of()
        files = {f["path"]: f for f in collect.collect_evidence(str(demo_repo), BASE, HEAD)["files"]}
        quotable = {path: f["quotable"] for path, f in files.items()}
        lines = [line for hunk in quotable[CHARGE] for _number, line in hunk if line[1:].strip()]
        hunk = next(line for line in files[CHARGE]["diff"].splitlines() if line.startswith("@@"))
        command = "    command = f\"lp -d {printer} <<< '{format_amount(charge.amount_cents, charge.currency)}'\""
        shell = "    return subprocess.run(command, shell=True, check=False).returncode"
        cases = {
            (CHARGE, f"+{shell}"): True,
            (CHARGE, shell.strip()): True,
            (CHARGE, f"+{command}\n+{shell}"): True,
            (CHARGE, f"{command}\n\n{shell}"): True,  # a blank line between quoted lines is no line
            (CHARGE, f"{command.strip()} {shell.strip()}"): True,  # measured on Gemini: the line breaks lost
            (CHARGE, f"{command.strip()}{shell.strip()}"): True,  # and joined by nothing at all
            (CHARGE, f"{shell}\n{command}"): False,  # carried over: both whole lines, out of order
            (CHARGE, f"{lines[0]}\n{lines[2]}"): False,  # carried over: both whole lines, one skipped between
            (CHARGE, "subprocess.run(command, shell=True, check=False)"): False,  # part of a line
            (CHARGE, "import"): False,
            (CHARGE, f"### {CHARGE}"): False,
            (CHARGE, "```diff"): False,
            (CHARGE, CHARGE): False,
            (CHARGE, f"diff --git a/{CHARGE} b/{CHARGE}"): False,
            (CHARGE, hunk): False,
            (CHARGE, f"{lines[-1]}\n```\n\n### src/payments/config.py"): False,  # running into the next file
            ("src/payments/refund.py", shell): False,  # the right line, filed under a file it is not in
        }
        findings = [
            schema.Finding(file=file, line=9, severity="major", title=str(n), evidence=quote, suggestion="s")
            for n, (file, quote) in enumerate(cases)
        ]
        kept, _dropped = rule.drop_ungrounded(schema.Review(findings=findings, summary="s"), quotable)
        grounded = {int(f.title) for f in kept}
        wrong = [case for n, (case, want) in enumerate(cases.items()) if (n in grounded) != want]
        assert wrong == [], wrong
        assert {f.title: f.line for f in kept if f.title in ("0", "2")} == {"0": 28, "2": 27}


class TestPlugins:
    @pytest.mark.asyncio
    async def test_a_tool_result_is_scrubbed_before_the_model_sees_it(self) -> None:
        """The chat's intake agent calls tools too. Measured by coverage: this hook, and the scrubbing of a tool's
        nested result, ran in no test."""
        plugin = importlib.import_module(judge_module("plugins")).RedactSecretsPlugin()
        result = {
            "status": "success",
            "diff": 'token = "abcdefghijklmnop"',
            "files": [{"note": "AKIAIOSFODNN7EXAMPLE"}],
        }
        out = await plugin.after_tool_callback(
            tool=SimpleNamespace(name="show_diff"), tool_args={}, tool_context=SimpleNamespace(state={}), result=result
        )
        assert out is not None and "abcdefghijklmnop" not in out["diff"] and "AKIA" not in out["files"][0]["note"]
        assert plugin.redacted == 2

    @pytest.mark.asyncio
    async def test_the_outgoing_request_is_scrubbed_too(self) -> None:
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        plugin = importlib.import_module(judge_module("plugins")).RedactSecretsPlugin()
        request = LlmRequest(contents=[types.UserContent("key AKIAIOSFODNN7EXAMPLE")])
        assert await plugin.before_model_callback(callback_context=None, llm_request=request) is None
        assert "AKIAIOSFODNN7EXAMPLE" not in request.contents[0].parts[0].text

    @pytest.mark.asyncio
    async def test_an_instruction_that_is_no_string_is_refused_never_sent(self) -> None:
        """ADK 2.9.2 writes the system instruction as a string; a `Content` once passed the scrubber unread."""
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        plugin = importlib.import_module(judge_module("plugins")).RedactSecretsPlugin()
        instruction = types.Content(parts=[types.Part(text="key AKIAIOSFODNN7EXAMPLE")])
        request = LlmRequest(config=types.GenerateContentConfig(system_instruction=instruction))
        refused = await plugin.before_model_callback(callback_context=None, llm_request=request)
        assert refused is not None and refused.error_code == "REDACTION_FAILED", refused
        assert "Content" in refused.error_message


class TestALaneThatFails:
    """A hole is named, never silent: the report says which guideline failed and why, and a hole never approves."""

    def test_a_broken_scrubber_refuses_the_model_call_and_the_report_says_so(
        self, cli, demo_repo: Path, monkeypatch, capsys
    ) -> None:
        """Fail-closed, through the real engine: if scrubbing raises, nothing unscrubbed is sent, the guideline
        yields ADK's error event instead of an answer, and the report carries the code and the reason. Measured by
        coverage: this refusal ran in no test."""
        plugins = importlib.import_module(judge_module("plugins"))  # the copy the command just loaded

        def broken(text: str) -> tuple[str, int]:  # noqa: ARG001 -- the scrubber's signature
            raise RuntimeError("regex exploded")

        monkeypatch.setattr(plugins, "scrub", broken)
        code = run_review(cli, demo_repo)
        out = capsys.readouterr().out
        assert code == 3, out
        heading = f"### {model_check('security')} — FAILED"
        assert f"{heading}: REDACTION_FAILED: refusing to call the model: regex exploded" in out


class TestTheReport:
    def test_a_blocker_no_quote_bears_out_is_named_for_a_person(
        self, cli, demo_repo: Path, fake_provider, capsys
    ) -> None:
        """A blocker dropped for its quote is a hole, exit 3, and the report says why in a sentence a person can act
        on. Measured by coverage: `decide` was tested on both sides, and that sentence rendered in no test."""
        invented = {**CANNED_REVIEW["findings"][0], "severity": "blocker", "evidence": "nothing like this is in it"}
        fake_provider.answers_when(EVERY_GUIDELINE, {**CANNED_REVIEW, "findings": [invented]})
        assert run_review(cli, demo_repo) == 3
        out = capsys.readouterr().out
        assert "at or above `--fail-on blocker`: not approved until a person reads them" in out, out


def test_the_gate_runs_before_any_model(cli, demo_repo: Path, monkeypatch) -> None:
    """Evidence and code's checks first, then the models: every rule runs as a node of the graph before the
    guidelines, so the order is held at the first model call."""
    order: list[str] = []
    where = sys.modules["adk_review.judge.checks"]  # the module that calls each rule
    real_check, real_model = where.run_rule, FakeModel.generate_content_async

    def checked(*args, **kwargs):
        order.append("code")
        return real_check(*args, **kwargs)

    async def generate_content_async(self, *args, **kwargs):
        order.append("model")
        async for response in real_model(self, *args, **kwargs):
            yield response

    monkeypatch.setattr(where, "run_rule", checked)
    monkeypatch.setattr(FakeModel, "generate_content_async", generate_content_async)
    run_review(cli, demo_repo)
    first = order.index("model")
    assert first and set(order[:first]) == {"code"} and set(order[first:]) == {"model"}, order


def test_evidence_is_cut_where_a_person_reads_it(cli, long_line_repo: Path, capsys) -> None:
    """A gate's evidence is the whole added line. Measured before: a key on a minified line of 200,000 characters
    printed all of it, a report no terminal or chat holds. Cut in the report, never before: folding compares the
    whole quote."""
    cli.main([str(long_line_repo), "--base", BASE, "--head", LONG_LINE])
    out = capsys.readouterr().out
    assert "hard-coded credential" in out, out[:2000]
    assert max(len(line) for line in out.splitlines()) < 1_000, "no line of the report floods the terminal"
    assert "[… cut at 400 characters]" in out, "and the cut is named"


#: Credential shapes the gate finds and the redaction hides, one line each as the gate reads them.
SECRETS = {
    "AWS key id": 'KEY = "AKIAIOSFODNN7EXAMPLE"',
    "AWS secret, as code": 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
    "AWS secret, as YAML": "aws_secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "a prefixed name": 'DB_PASSWORD = "hunter2hunter2"',
    "a suffixed name": 'SECRET_KEY = "django-insecure-abc123def456"',
    "a snake-case name": 'client_secret = "s3cr3tvalue99"',
    "a JSON key": '"password": "correct horse battery"',
    "a .env line": "DB_PASSWORD=hunter2hunter2",
    "a YAML line": "password: s3cretPassw0rd",
    "a short password": 'password = "hunter2"',
    "GitHub": "t = 'ghp_" + "a" * 36 + "'",
    "GitLab": "t = 'glpat-" + "a" * 20 + "'",
    "Slack token": "t = 'xoxb-" + "1234567890-" * 2 + "abcdefghijklmnopqrstuvwx'",
    "Slack webhook": "u = 'https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24 + "'",
    "Stripe": "k = 'sk_live_" + "a" * 24 + "'",
    "Google API key": "k = 'AIza" + "a" * 35 + "'",
    "JWT": "t = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'",
    "a JWT after a hyphen": "c = 'session-eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N'",
    "URL credentials": "u = 'postgres://app:s3cretpass@localhost:5432/app'",
    "Azure account key": "c = 'DefaultEndpointsProtocol=https;AccountName=x;AccountKey=" + "a" * 86 + "=='",
    "private key": "-----BEGIN RSA PRIVATE KEY-----",
    "a keyless private key": "-----BEGIN PRIVATE KEY-----",
}
#: Code that names a secret without holding one: each was once a blocker, a build failed for reading a variable.
NOT_SECRETS = {
    "an environment lookup": 'password = os.environ["DB_PASSWORD"]',
    "a call": "token = get_token(request)",
    "a setting": "secret = settings.SECRET_NAME_FOR_LOOKUP",
    "a template": 'password = "${DB_PASSWORD}"',
    "a longer word": 'tokenizer = "bert-base-uncased"',
    "an annotation": "password: str = field(default='')",
}


def _secrets_of():
    """The scrub, and the built-in rules that find a credential: a vendor's shape, and a named value."""
    from adk_review.core.rules import run_rule
    from adk_review.core.secrets import scrub
    from adk_review.judge.rules.named_secrets import NamedSecrets
    from adk_review.judge.rules.secrets import Secrets

    def secret_findings(files: list[dict]) -> list:  # a vendor's shape, and a named value: one finding a line
        return [found for rule in (Secrets(), NamedSecrets()) for found in run_rule(rule, files, None).findings]

    return scrub, secret_findings


class TestWhatTheSecretsGateKnows:
    """Measured before: 6 of these 20 shapes found, the rest reaching the models and the report, and
    three of the lines below each a hard-coded-credential blocker — a build failed for reading a variable."""

    @pytest.mark.parametrize("shape", list(SECRETS))
    def test_a_credential_is_found_once_and_hidden(self, shape: str) -> None:
        scrub, secret_findings = _secrets_of()
        line = SECRETS[shape]
        found = secret_findings([{"path": "x.py", "added": [(1, line)]}])
        assert len(found) == 1 and found[0].severity == "blocker", found
        hidden, count = scrub(line)
        assert count >= 1 and "[REDACTED" in hidden, hidden

    def test_a_url_password_holding_an_at_sign_is_hidden_whole(self) -> None:
        """Measured by review: the password ended at its first `@`, so `P@ssw0rd` was hidden as `P`, and the rest of
        it reached the model."""
        scrub, secret_findings = _secrets_of()
        line = "u = 'mssql://sa:" + "P@ssw0rd" + "@sqlserver.corp.acme.io/db'"
        hidden, count = scrub(line)
        assert count == 1 and "ssw0rd" not in hidden and "@sqlserver.corp.acme.io/db" in hidden, hidden
        assert len(secret_findings([{"path": "x.py", "added": [(1, line)]}])) == 1

    @pytest.mark.parametrize("shape", list(NOT_SECRETS))
    def test_code_that_names_a_secret_is_not_one(self, shape: str) -> None:
        scrub, secret_findings = _secrets_of()
        line = NOT_SECRETS[shape]
        assert secret_findings([{"path": "x.py", "added": [(1, line)]}]) == []
        assert scrub(line) == (line, 0)

    def test_a_private_key_is_hidden_whole(self) -> None:
        """Measured before: the header was hidden and every line of the key's body reached the lanes."""
        scrub, _gate = _secrets_of()
        key = ["+-----BEGIN RSA PRIVATE KEY-----", "+MIIEowIBAAKCAQEA7yn3bRHQ5", "+-----END RSA PRIVATE KEY-----"]
        block = "\n".join([*key, "+x = 1"])
        hidden, _count = scrub(block)
        assert "MIIEow" not in hidden and hidden.endswith("+x = 1"), hidden

    def test_a_key_header_where_no_rule_reads_hides_nothing_after_it(self) -> None:
        """Measured before: a key's header in a file's name or on an unchanged line, and a footer further on, hid
        everything between them from every model call, and the review approved. A block's body holds key-like lines
        only now, so the change between them is sent."""
        scrub, _gate = _secrets_of()
        in_a_name = (
            "### src/-----BEGIN PRIVATE KEY-----.py\n```diff\n+os.system(cmd)\n```\n+# -----END PRIVATE KEY-----"
        )
        on_a_context_line = " -----BEGIN PRIVATE KEY-----\n+os.system(cmd)\n+-----END PRIVATE KEY-----"
        for text in (in_a_name, on_a_context_line):
            assert "+os.system(cmd)" in scrub(text)[0], text

    def test_a_long_hostile_line_is_read_in_linear_time(self) -> None:
        """Measured before: one added line of 40,000 characters held a shape for up to 9.7 s, four times as long for
        each doubling, and no deadline stops a rule. Every run a shape scans is bounded now: 200,000 characters of
        each input below took milliseconds. Measured by a later review: a key's header with 25 key lines and no footer,
        as a fixture cut short has it, took over 45 s, four times as long for each two lines, and 8,000 headers on one
        line 5.7 s."""
        scrub, secret_findings = _secrets_of()
        size = 200_000
        hostile = [
            "\n" * size,
            ("pwd=\n" + "\n" * 99) * (size // 104),
            "eyJaaaaaaaa-" * (size // 12),
            "token_" * (size // 6),
            "a://b:" * (size // 6),
            "-----BEGIN PRIVATE KEY-----\n" * (size // 28),
            "-----BEGIN PRIVATE KEY-----" * (size // 27),
            "+-----BEGIN PRIVATE KEY-----\n" + "+MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAo\n" * 400 + "+x = 1\n",
        ]
        for text in hostile:
            started = time.perf_counter()
            scrub(text)
            secret_findings([{"path": "x.py", "added": [(1, text)]}])
            assert time.perf_counter() - started < 2, repr(text[:24])

    def test_the_cut_never_falls_inside_a_credential(self) -> None:
        """Measured before: a file's diff was cut at its cap, then scrubbed, and half a credential matches no shape:
        30 of an AWS secret's 40 characters reached every lane, and four lines of a key's body. The cut moves to before
        a credential it would split, and still names where it fell."""
        scrub, _gate = _secrets_of()
        from adk_review.collect.git import DIFF_CAP_CHARS, capped

        key = '+aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
        block = "+-----BEGIN RSA PRIVATE KEY-----\n" + "+MIIEowIBAAKCAQEA7yn3bRHQ5abcdefghij\n" * 8
        block += "+-----END RSA PRIVATE KEY-----\n"
        for secret in (key, block):
            before = "+x = 1\n" * ((DIFF_CAP_CHARS - 20) // 7)
            shown, cut = capped(before + secret + "+y = 2\n" * 50)
            sent = scrub(shown)[0]
            assert cut and "wJalrXUtnFEMI" not in sent and "MIIEow" not in sent, sent[-200:]
            named = int(shown.rsplit("[… cut at ", 1)[1].split(" ")[0].replace(",", ""))
            assert len(before) <= named < DIFF_CAP_CHARS, "cut before the credential, and named where"

    def test_a_value_on_the_line_after_its_name_is_hidden(self) -> None:
        """YAML may put a value on the line after its name. The redaction reads whole text, so it hides that one too,
        on an added line or a diff's context line alike."""
        scrub, _gate = _secrets_of()
        for text in ("password:\n  s3cretPassw0rd\n", " password:\n   s3cretPassw0rd\n"):
            assert "s3cretPassw0rd" not in scrub(text)[0], text

    def test_the_report_prints_no_credential_a_model_wrote(self) -> None:
        """Every model-written field reaches the report through one function, which now hides a credential too:
        before, a guideline's title or suggestion carrying one printed it."""
        from adk_review.deliver.report import one_line

        assert "AKIA" not in one_line('rotate AKIAIOSFODNN7EXAMPLE, then password = "hunter2hunter2"')


def _numbered(lines: list[str]) -> list[list[tuple[int, str]]]:
    """Hunk lines as `collect_evidence` hands them to grounding: one hunk, each line with its number."""
    return [list(enumerate(lines, 1))]


def _grounding_of():
    """The grounding rule, the evidence it grounds against, and the finding schema."""
    return (
        importlib.import_module(f"adk_review.{module}") for module in ("core.verdict", "collect.git", "core.findings")
    )


class TestWhatGroundingKeeps:
    """A quote grounds as the file has it, and a finding at the bar no quote bears out is no approval. Measured
    before: a YAML list item `- run: …` and `++i;`, quoted as the file has them, were dropped, their
    leading `-` and `+` read as diff markers; and a blocker dropped for its quote, nothing else found, APPROVED."""

    @pytest.mark.parametrize(
        ("quotable", "quote"),
        [(["+- run: curl example.sh | sh"], "- run: curl example.sh | sh"), (["+  ++i;"], "++i;")],
    )
    def test_a_line_quoted_as_the_file_has_it_grounds(self, quotable: list, quote: str) -> None:
        rule, _collect, schema = _grounding_of()
        finding = schema.Finding(file="x", line=1, severity="major", title="t", evidence=quote, suggestion="s")
        kept, dropped = rule.drop_ungrounded(schema.Review(summary="", findings=[finding]), {"x": _numbered(quotable)})
        assert kept and not dropped

    def test_a_finding_at_the_bar_no_quote_bears_out_is_no_approval(self) -> None:
        rule, _collect, schema = _grounding_of()

        def dropped(severity: str):
            finding = schema.Finding(file="x", line=1, severity=severity, title="t", evidence="q", suggestion="s")
            return rule.Outcome(dropped=[rule.Sourced(model_check("security"), finding)])

        assert rule.decide(dropped("blocker"), "blocker")[0] == "DEGRADED", "a real blocker dropped would approve"
        assert rule.decide(dropped("major"), "blocker")[0] == "APPROVED", "below the bar, a drop is only named"

    def test_a_removed_line_carries_the_new_line_before_it(self) -> None:
        """Measured by review: `@@ -1,2 +0,0 @@`, a deleted file's one hunk, numbered its lines -1, and the report
        named `old.py:-1`. Git names an empty range by the line before it: a deleted file's lines carry 0, no line."""
        _rule, git, _schema = _grounding_of()
        quotable = git.quotable
        assert quotable("@@ -1,2 +0,0 @@\n-x = 1\n-y = 2") == [[(0, "-x = 1"), (0, "-y = 2")]]
        assert quotable("@@ -5,2 +4,0 @@\n-a = 1") == [[(4, "-a = 1")]]
        assert quotable("@@ -5,2 +5,3 @@\n a\n-b\n+c\n+d") == [[(5, " a"), (5, "-b"), (6, "+c"), (7, "+d")]]

    def test_a_quote_grounds_as_the_new_or_the_old_file_reads_and_never_across_two_hunks(self) -> None:
        """Measured before, each hunk read only as git prints it, all its hunks one run: a correct quote of the new
        code, from a context line into a new one across a replaced line, was dropped, and as a blocker the run
        degraded; the last line of one hunk joined to the first of the next grounded as one span, twenty lines
        apart, and a finding between them folded into it. A file named as the diff's header spells it, `b/…`, is the
        changed file it means."""
        rule, git, schema = _grounding_of()
        quotable = git.quotable
        diff = (
            "@@ -1,4 +1,4 @@\n def total(price, quantity):\n-    amount = price * quantity\n"
            "+    amount = eval(price) * quantity\n     return amount\n"
            "@@ -20,2 +20,3 @@\n # filler\n+def label(order):\n     pass"
        )
        hunks = {"orders.py": quotable(diff)}
        assert [len(hunk) for hunk in hunks["orders.py"]] == [4, 3], "two hunks, apart"

        def placed(quote: str, file: str = "orders.py"):
            finding = schema.Finding(
                file=file, line=None, severity="blocker", title="t", evidence=quote, suggestion="s"
            )
            return rule.where_quoted(finding, hunks)

        assert placed("def total(price, quantity):\n    amount = eval(price) * quantity\n    return amount") == (1, 3)
        assert placed("def total(price, quantity):\n    amount = price * quantity") == (1, 1), "the old file's"
        assert placed(
            "def total(price, quantity):\n-    amount = price * quantity\n+    amount = eval(price) * quantity"
        )
        assert placed("    return amount\n# filler") is None, "never across two hunks"
        assert placed("    amount = eval(price) * quantity", file="b/orders.py") == (2, 2)
        finding = schema.Finding(
            file="b/orders.py", line=9, severity="major", title="t", evidence="def label(order):", suggestion="s"
        )
        kept, _dropped = rule.drop_ungrounded(schema.Review(summary="", findings=[finding]), hunks)
        assert [(f.file, f.line) for f in kept] == [("orders.py", 21)]

    def test_where_redaction_joined_lines_no_line_is_named_rather_than_a_wrong_one(self) -> None:
        """The lane saw a private key's block as one redacted line, so its quote still grounds, but a seen line no
        longer counts one line of the file."""
        rule, _collect, schema = _grounding_of()
        block = ["+-----BEGIN RSA PRIVATE KEY-----", "+MIIEowIBAAKCAQEA", "+-----END RSA PRIVATE KEY-----", "+x = 1"]
        finding = schema.Finding(file="k.py", line=2, severity="major", title="t", evidence="x = 1", suggestion="s")
        kept, dropped = rule.drop_ungrounded(schema.Review(summary="", findings=[finding]), {"k.py": _numbered(block)})
        assert [f.line for f in kept] == [None] and not dropped


def test_a_short_quote_elsewhere_in_the_file_is_no_duplicate() -> None:
    """Measured by review: folded by text alone, a minor quoting `return None` at line 2 held a blocker whose quote
    ends in another `return None` at line 6, and the report showed the minor's title as a blocker, the shell
    injection gone. Findings the diff places fold only when one's lines hold the other's."""
    verdict, _collect, schema = _grounding_of()
    lines = [
        "+def early(x):",
        "+    return None",
        "+",
        "+def run(request):",
        '+    subprocess.run(request.args["c"], shell=True)',
    ]
    lines.append("+    return None")
    quotable = {"a.py": _numbered(lines)}
    minor = schema.Finding(file="a.py", severity="minor", title="early return", evidence="return None", suggestion="s")
    quote = "\n".join(lines[3:])
    blocker = schema.Finding(file="a.py", severity="blocker", title="shell injection", evidence=quote, suggestion="s")
    found = [(model_check("complexity"), minor), (model_check("security"), blocker)]
    kept, _folded = verdict.dedupe([verdict.Sourced(key, f, verdict.where_quoted(f, quotable)) for key, f in found])
    assert sorted((s.finding.title, s.finding.severity) for s in kept) == [
        ("early return", "minor"),
        ("shell injection", "blocker"),
    ]


def test_a_quote_the_diff_holds_twice_is_placed_nearest_the_line_the_lane_named() -> None:
    """Measured by review: `return None` stood at lines 2 and 22, a model named line 22 and was placed at 2, the
    first; a finding on line 2's function then folded into it, one finding shown at the wrong function. The diff
    decides where a quote can stand; the model's line only picks among those places, the first where it named none
    and on a tie."""
    verdict, _collect, schema = _grounding_of()
    lines = ["+def first():", "+    return None", *(f"+x{n} = {n}" for n in range(18))]
    lines += ["+def second():", "+    return None"]
    quotable = {"m.py": _numbered(lines)}

    def said(line: int | None):
        return schema.Finding(
            file="m.py", line=line, severity="major", title="t", evidence="return None", suggestion="s"
        )

    placed = [verdict.where_quoted(said(line), quotable) for line in (22, 2, 15, 12, None)]
    assert placed == [(22, 22), (2, 2), (22, 22), (2, 2), (2, 2)]
    found = [(model_check("security"), said(22)), (model_check("tests"), said(2))]
    kept, _folded = verdict.dedupe([verdict.Sourced(key, f, verdict.where_quoted(f, quotable)) for key, f in found])
    assert len(kept) == 2


def test_the_tests_lane_is_asked_what_the_change_leaves_untested() -> None:
    """Measured live on Copilot: asked only whether the tests were honest, the models named the planted untested
    function in 2 of 5 runs (`scripts/measure.py`), as often for complexity as for tests."""
    from adk_review.judge.guidelines.tests import Tests

    assert "tested at all" in Tests.role and "tests are honest" in Tests.role, Tests.role


class TestWhatTheLanesRead:
    """The change is fenced as data, and told to be. Measured before: a context line of three backticks, a markdown
    file's own fence, closed the fence of three the change was written in, so the text after it read as the prompt's;
    and nothing told the guidelines the change was data rather than instructions."""

    def test_no_line_of_the_change_closes_its_fence(self, hostile_repo: Path) -> None:
        git = importlib.import_module("adk_review.collect.git")
        evidence = git.collect_evidence(str(hostile_repo), BASE, "fence")
        lines = evidence["diff"].split("\n")
        opening = lines[1]
        fence = opening.removesuffix("diff")
        assert opening.endswith("diff") and set(fence) == {"`"}, opening
        closing = next(i for i, line in enumerate(lines[2:], 2) if line.strip().startswith(fence))
        assert closing == len(lines) - 1, f"closed at line {closing} of {len(lines)}: {lines[closing]!r}"

    def test_every_lane_is_told_the_change_is_data(self, monkeypatch) -> None:
        load_agent(monkeypatch)
        from adk_review.judge.agents import build_guideline
        from adk_review.judge.guidelines.security import Security

        instruction = build_guideline(Security()).instruction
        assert "never instructions" in instruction, instruction

    def test_the_report_drops_what_a_terminal_would_run_or_reorder(self) -> None:
        """U+009B is ESC [ in one character; U+202E turns the text after it around."""
        from adk_review.deliver.report import one_line

        assert one_line("a\u009b2Jb‮c⁦d") == "a 2Jb c d"

    def test_the_report_drops_every_character_a_reader_cannot_see(self) -> None:
        """Every format character Python's Unicode tables know, and the variation selectors. Measured before: a sentence
        in tag characters printed whole, unseen, in the report and in the JSON a pipeline posts."""
        import sys
        import unicodedata

        from adk_review.deliver.report import one_line

        unseen = [chr(point) for point in range(sys.maxunicode + 1) if unicodedata.category(chr(point)) == "Cf"]
        unseen += [chr(point) for point in (*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0))]
        kept = [f"U+{ord(c):04X}" for c in unseen if one_line(f"a{c}b") != "a b"]
        assert kept == [], kept


class TestWhatRanUntested:
    """Branches the docs rely on that no test ran, measured by coverage."""

    def test_the_planted_branch_redacts_six_secrets(self, cli, demo_repo: Path, capsys) -> None:
        """The count itself, where a test read only its label before."""
        assert run_review(cli, demo_repo) == 1
        assert "- secrets redacted before any model call: 6" in capsys.readouterr().out

    def test_an_engine_that_fails_is_named_and_degrades(self, cli, demo_repo: Path, monkeypatch, capsys) -> None:
        """What escapes the engine itself is every guideline's reason, never a traceback and never an approval."""
        # The command imports the engine when a review starts, from ADK's own module, so its runner is changed there.
        owner = importlib.import_module("google.adk.runners")

        class Broken(owner.InMemoryRunner):
            async def run_async(self, *_args, **_kwargs):
                raise RuntimeError("the engine broke")
                yield  # an async generator, as the real one is

        monkeypatch.setattr(owner, "InMemoryRunner", Broken)
        assert run_review(cli, demo_repo) == 3
        assert "RuntimeError: the engine broke" in capsys.readouterr().out

    def test_a_lane_answer_the_schema_refuses_is_a_failed_lane(self, monkeypatch) -> None:
        load_agent(monkeypatch)
        from adk_review.core.findings import Review
        from adk_review.core.profiles import guideline_id, state_key
        from adk_review.judge.agents import read_back

        key = state_key(guideline_id("security"))
        said = read_back({key: {"findings": "not a list"}}, key, Review, "no event")
        assert isinstance(said, str) and said, said

    def test_the_package_answers_its_agent_lazily_and_nothing_else(self, monkeypatch) -> None:
        load_agent(monkeypatch)
        import adk_review

        assert adk_review.agent.root_agent.name == "adk_review"
        with pytest.raises(AttributeError):
            adk_review.nothing_here  # noqa: B018
