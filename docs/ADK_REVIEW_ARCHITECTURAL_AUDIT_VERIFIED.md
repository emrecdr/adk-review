# `adk-review` Audit, Verified

A finding-by-finding verification of [`ADK_REVIEW_ARCHITECTURAL_AUDIT.md`](ADK_REVIEW_ARCHITECTURAL_AUDIT.md)
(2026-10-11). Each finding and each proposed fix was checked against the code, against the installed libraries' own
source, and, where behaviour was in question, by running it. The check also turned up new findings, and the report
ends with one plan of verified changes.

- **Verified on:** 2026-10-13, working tree of `main` at `c0c2f8e` plus its uncommitted changes. The offline suite
  passes: 504 passed, 1 skipped.
- **Stack, as installed:** Python 3.13.15, google-adk 2.9.2, google-genai 2.26.0, litellm 1.103.1, pydantic 2.13.5,
  ruff 0.16.9, aiohttp 3.14.3, httpx 0.28.1.
- **How each claim was checked:**
  - ADK's, LiteLLM's and google-genai's installed source was read where a claim rests on library behaviour.
  - Offline experiments ran through the real `Runner`, `Workflow`, plugins and callbacks, with the suite's fake model
    (`tests/conftest.py`). They ran as temporary test files, deleted afterwards.
  - Schema probes ran both arms' own converters: ADK's `_to_litellm_response_format` and google-genai's `t_schema`.
  - The proposed rule regex and a corrected one were run through the project's rule harness (`try_rule`).
  - A whole-tree review was timed and profiled on a 723-file repository.
  - Pyrefly 1.3.2 and Pyright ran on a two-file reproduction.
  - External claims were checked on the web, from primary sources, cited where used.
- **What the verification did not do:**
  - No live model call was made.
  - No source file was changed: only this report was added.
  - The live measurement in §4.7 and the implementation in §6 came after it, on the branch
    `hardening-from-the-audits`.
- **Line numbers** in the project's files are the branch's as committed, so each can be opened. §3 and §4 describe
  the code as it was verified, before the fixes, and §6 says what each fix changed. A library's line numbers are its
  installed version's, above.

---

## 1. Verdict at a glance

| ID | Audit claim | Verdict | Severity: audit → verified | Why, in one line |
| :-- | :-- | :-- | :-- | :-- |
| FLOW-01 | Chat runs the whole graph on turn 1 | **Confirmed** (mechanism); the proposed fix does not build | P0 → **P1** | The verdict model call runs on every turn with no scope. Fail-closed, never approves. The audit's `"END"` route is refused by ADK 2.9.2's validator. |
| ASYNC-01 | Two `asyncio.run()` calls break connections and embedding | **Refuted** as a defect | P0 → optional hygiene | LiteLLM 1.103.1 keys its clients by event loop, and the verifiers build their own models. `review.py` is a CLI, not an embeddable API. |
| SEC-01 | Redaction count leaks across sessions | **Refuted**; the proposed fix is broken | P0 → none | Only the command reads the count, from a fresh App per process. The fix's hook signature and `session_id` do not exist in ADK 2.9.2. |
| TYP-01 | `extra="forbid"` needed for OpenAI strict mode | **Refuted**; the change is harmful | P1 → none | ADK already sends `strict: true` with `additionalProperties: false` on the Copilot arm. On Gemini it would add a field the API has answered with a 400 before. |
| TYP-02 | PEP 695 `type` aliases | **Refuted**; the change is harmful | P1 → none | It drops the severity scale's description from the schema on **both** arms, and breaks a test. |
| CONC-01 | Cache race corrupts reads | **Refuted**; the change is harmful | P1 → none | There is no shared default folder. The write is atomic, the read validates, and a write error is suppressed. `fcntl` does not exist on Windows, which CI runs. |
| ASYNC-02 | `CancelledError` skips session cleanup | **Refuted** | P1 → none | There is nothing to clean up, and cancellation must propagate. The proposed `finally` reads state only to discard it. |
| RULE-01 | No rule for unpinned actions | **Confirmed gap**; the proposed regex misses the common form | P2 → **P2** | `^\s*uses:` never matches a step's `- uses:`. A corrected rule was run through the harness. |
| RULE-02 | `shell=True` and `pickle` left to the model | **Refuted**: already implemented | P2 → none | `rule.lint`, in `default`, runs ruff's bandit family. It reports S602 and S301 as major (run). |
| LLM-01 | Add Anthropic, OpenAI and Ollama arms | Fact correct; **not recommended** | P2 → declined | It conflicts with the stated direction: Copilot and Gemini first-class, each change measured on both. |
| PERF-01 | Process-pool AST health scan | **Refuted** (measured) | P2 → none | Health took 0.30 s of a 4 s whole-tree review over 723 files and 6.3 MB. A pool costs more than it saves. |
| LLM-02 | Give verifiers only their file's diff | Cost arithmetic right; **the recommendation is refuted** | P2 → declined | It reverses a documented design decision. Less context means more refutations, and a refuted finding is dropped. A cheaper option without that risk is a candidate (§4.7). |
| DEV-01 | Type-checker search paths for `scripts/` | **Partly confirmed**; already applied | P1 → P3 | Pyrefly needs `search-path` (run). Pyright resolves sibling imports with no config (run). Both sections were already in `pyproject.toml`, then uncommitted. |

The audit gave its §5.5 no ID; LLM-02 is this report's name for it.

**New findings from the verification** (§4), each reproduced offline:

| ID | Finding | Severity |
| :-- | :-- | :-- |
| NEW-1 | Chat re-runs the whole review, about five model calls, on every turn after a scope was confirmed, a "thanks!" included | P1 |
| NEW-2 | Chat's verdict agent receives every guideline's findings **unfenced** as its user turn, beside the fenced copy in its instruction; each chat guideline receives the intake's reply | P1 (injection hardening) |
| NEW-3 | A SIGTERM that lands while Python runs inside a model call is swallowed: the review carries on, every model call still made, and ends in exit 3 instead of 143 | P2 |
| NEW-4 | The chat intake forgets every earlier turn: as a workflow node it defaults to `single_turn`, which reads the current message alone | P2 |
| NEW-5 | No test pins the schema each arm receives, which is how TYP-01 and TYP-02 would have slipped in | P2 |
| NEW-6 | The `--cache` folder is a trust boundary the guide does not name: a well-formed file in it is served as the verdict | P3 (doc) |
| NEW-7 | The verifier's prompt puts the per-finding text before the shared diff, which defeats Gemini's implicit prefix caching | Measured: the other order caches, but refuted a true finding in 2 of 2 runs. Not adopted |

---

## 2. Baseline claims (§1–§2 of the audit)

The audit's description of the system holds. Each claim was checked:

- **Layering**: `core` → `collect` → `judge` → `deliver`, held by `tests/test_layering.py`. Holds.
- **Core**: `core/`, the checks and `rule_helpers/` import no network, subprocess, `os` or `asyncio`. Holds.
- **Git**: read-only verbs and the sanitised `GIT_CONFIG_COUNT` environment. Holds.
  - On Windows a timeout kills the whole process tree with `taskkill /F /T` (`collect/git.py:132`).
  - The model chooses the branch names, and they reach git only behind `--end-of-options` (`collect/git.py:190`).
- **Grounding**: every guideline finding's quote is checked against the diff. Holds, and is now stricter: a quote must
  read in the framing each file was actually shown in.

**Corrections:**
- The suite had 504 passing tests, not 500.
- The audit's line numbers had moved: `review.py:582` to `:581`, and `:773` to `:771`. The two `asyncio.run` calls
  they name are now one `asyncio.Runner` (`review.py:778`, §6 item 8).
- The audit's `docs/` file itself failed `ruff format --check .`, the `make lint` gate: ruff 0.16 formats the Python
  blocks inside Markdown. It was formatted before it was committed, whitespace only: with every space and line break
  removed, its text is unchanged.

---

## 3. The audit's findings, one by one

### 3.1 FLOW-01: chat runs past the intake: confirmed, P1; the proposed fix does not build

**What happens.**
- Inside a `Workflow`, an `LlmAgent` with no `mode` is made `single_turn`
  (`google/adk/workflow/_llm_agent_wrapper.py:391-394`). A single-turn node answers once, and the graph moves on.
- So the intake asks for the repository, and the edge continues to `checks`.
- `checks` finds no scope and writes "nothing was gathered" as every rule's hole (`judge/checks.py:76-81`).
- Every guideline's guard skips it, so no model call is made there.
- The verdict agent then makes a model call that says nothing was reviewed.

Measured offline, one "hello" turn:

| Turn | Model calls today | Agents that ran |
| :-- | :-- | :-- |
| "hello" | 2: intake, verdict | intake, 3 guidelines (skipped), verdict |

**Severity.**
- The audit's P0 overstates it. The run is fail-closed: the verdict's instruction says "never approve what was not
  judged", and in chat no code reads that verdict for a decision.
- The cost is one wasted model call a turn: 1 of the free tier's 20 a day on Gemini.
- The other cost is a confusing second message after the intake's question.
- The audit's "DEGRADED: Nothing was gathered" is not a string the code emits. The verdict is model-written prose.

**The proposed fix.**
- It routes to `"END"`. ADK 2.9.2 has no such node. Running `Workflow(edges=[..., (router, {"awaiting_input":
  "END"})])` fails validation with `Input should be a valid dictionary or instance of Edge`.
- ADK ends a branch when no edge matches the emitted route, and logs a warning (`workflow/_graph.py:174-181`).
- The fix also checks only that a scope exists. A scope from an earlier turn passes it (NEW-1).

**Corrected fix, prototyped and run offline.** The scope a turn reviews is the one its own intake confirmed:
1. The intake's `before_agent_callback` clears `review_scope`.
2. A `FunctionNode` gate routes on whether this turn confirmed a scope.
3. `False` goes to a no-op node, so ADK logs no warning.

```python
from google.adk.events import Event
from google.adk.workflow import FunctionNode


def _forget_scope(callback_context) -> None:
    """A turn reviews the scope its own intake confirmed, or none: a scope an earlier turn left is not this one's."""
    callback_context.state[SCOPE] = None


def _scoped(ctx) -> Event:
    """On to the checks only when this turn's intake confirmed a scope; the turn ends at its question otherwise."""
    return Event(route=bool(ctx.state.get(SCOPE)))


def chat_edges(intake, rules, middle, verdict) -> list:
    intake.before_agent_callback = _forget_scope
    gate = FunctionNode(func=_scoped, name="scoped")
    asked = FunctionNode(func=lambda ctx: None, name="asked")  # False, named, so ADK logs no unmatched route
    return [("START", intake, gate), (gate, {True: rules, False: asked}), (*middle, verdict)]
```

Measured with the prototype. The fake model was scripted to call `inspect_repository` on "review it":

| Turn | Model calls today | Model calls with the fix |
| :-- | :-- | :-- |
| "hello" | 2 | **1**: intake only; the final answer is its question |
| "review it": the tool confirms a scope | 6 | 6: intake ×2, 3 guidelines, verdict |
| "thanks!" after that | 5, plus a fresh gather of the repository | **1** |

- With `False` routed to the no-op node, ADK logs nothing. Without it, ADK warns on each non-review turn: "Node
  'scoped' has conditional/DEFAULT edges but none were matched".
- **Tests to update.** The chat tests that seed `review_scope` straight into state must drive the tool instead, since
  the intake now clears a seeded scope. The prototype shows how: a fake that answers with a `FunctionCall`.

### 3.2 ASYNC-01: two event loops: refuted as a defect; optional hygiene

| Audit claim | What the stack does |
| :-- | :-- |
| The second loop "tears down connection pools" | LiteLLM 1.103.1 keys every cached client by `id(running loop)` (`litellm/caching/llm_caching_handler.py:46-56`). Its logging worker carries its queue onto a new loop (`litellm_core_utils/logging_worker.py:123-158`). google-genai 2.26.0 keeps one aiohttp session per loop. The verifiers are new `LlmAgent`s, each resolving its own model (`agents/llm_agent.py:684-691`), so no client crosses loops. A new TLS handshake costs milliseconds; a model call costs seconds. |
| `asyncio.run()` "crashes in async embeddings" | `review.py` is the command. It installs a SIGTERM handler, prints the report and returns an exit code; it was never an embeddable API. `--verify`, the only path with a second loop, is off by default (`[review] verify = false`). |

**What is real.**
- `asyncio.run` "should ideally only be called once" (Python docs, `asyncio.Runner`).
- ADK's cached `Gemini.api_client` does fail across loops when **one model instance** serves several loops: ADK issue
  #5538, fixed upstream after 2.9.2. Neither the command nor `adk web`, which runs one loop, does that.

**Optional hygiene, low value.** Run both phases in one `with asyncio.Runner() as loop:` and call `loop.run(...)`
twice. It is three lines and removes any reliance on the libraries' loop-keying. Verify it with one live `--verify`
review on each arm.

### 3.3 SEC-01: redaction count across sessions: refuted; the proposed fix is broken

**What the counter is.**
- `RedactSecretsPlugin.redacted` is read in one place: `review.py:785`, after the verifiers.
- The command builds one App per process, for one review.
- In chat nothing reads it: it reaches no state, no event and no UI. No data crosses sessions; it is an integer
  nobody reads.

**Why the proposed fix fails:**
- `after_run_callback(self, *, callback_context)`: ADK 2.9.2 calls it as `async def after_run_callback(self, *,
  invocation_context)` (`plugins/base_plugin.py:174-176`). The proposed signature is neither async nor named
  correctly, and would raise `TypeError` at the end of every run.
- `getattr(callback_context, "session_id", "default")`: ADK's `Context` has no `session_id` attribute
  (`agents/readonly_context.py`, `agents/context.py`). Every count would fall under `"default"`, the very pooling it
  meant to remove.

### 3.4 TYP-01: `extra="forbid"` for strict mode: refuted; harmful on Gemini

**The Copilot arm is already strict.**
- ADK, not LiteLLM, builds the response format. `_to_litellm_response_format` runs `_enforce_strict_openai_schema`
  (`google/adk/models/lite_llm.py:2583-2709`), which does four things:
  - sets `additionalProperties: false` on every object, `$defs` included;
  - lists every property as required;
  - strips the keys sitting beside a `$ref`;
  - sends `"strict": true`.
- Probed with `Review` as it stands: `strict=True` and `additionalProperties=False` at the top and on `Finding`.
  `extra="forbid"` changes nothing on this arm.
- OpenAI documents that a strict schema it cannot support is an **error, not a silent fallback**: "you will receive
  an error". The audit's "LiteLLM falls back to standard JSON mode" does not happen on this stack.

**The Gemini arm risks breaking.**
- ADK 2.9.2 sends `output_schema` as `response_schema` (`google/adk/models/llm_request.py:400`): the OpenAPI-subset
  schema, not `response_json_schema`.
- With `extra="forbid"`, google-genai 2.26.0 now emits `additional_properties: False` in it (probed).
- google-genai issue #1815 records the server answering 400 "Unknown name additional_properties" to exactly that. A
  maintainer wrote that "no new feature will be added to `response_schema`".
- Whether today's server still refuses it is unconfirmed. If it does, every guideline gets a 400, and every review
  on the Gemini arm ends in exit 3. Only a live call would settle it, and there is nothing to gain from making it.

**On validation it is brittle.**
- Pydantic's default ignores a key the model adds. `forbid` turns a harmless extra key into a `ValidationError`, so
  the guideline is lost as `SCHEMA_MISMATCH`, a hole.

**`frozen=True` and the `id()` it would replace.**
- `second_opinion` tracks refutations by identity on purpose (`review.py:573`: "by identity: two guidelines can file
  the same finding").
- Hashing by value would conflate equal findings.

### 3.5 TYP-02: PEP 695 aliases: refuted; harmful on both arms

Probed with `type Severity = Literal["blocker", "major", "minor"]`:

- **Pydantic 2.13** emits a named alias as `$defs` plus `$ref`. The field's description, "blocker: must not merge;
  major: fix before merge; minor: worth fixing.", becomes a sibling of the `$ref`.
- **Copilot:** ADK's strict pass strips every `$ref` sibling, so the description is **gone**.
- **Gemini:** genai's transformer inlines the enum and also drops the description.
- **Today** both arms receive the description, and it is the scale every guideline is told to use.
- **A test breaks:** `tests/test_catalog.py:182` reads `Severity.__args__`, and a `TypeAliasType` has none:
  `AttributeError`.
- **Lint gain: none.** Ruff's UP040 fires only on `X: TypeAlias = …` and `TypeAliasType(...)`, not on a plain
  assignment, so no rule asks for the change. `Lines`, `Example` and `Pushed` are annotation-only and gain nothing.

### 3.6 CONC-01: cache file locking: refuted; harmful on Windows

- **No shared default folder.** `--cache DIR` is opt-in with no default. The audit's `~/.cache/adk-review` does not
  exist in the code.
- **Readers never see half a file.** `_written` writes a temporary file and then `os.replace`s it
  (`review.py:610-621`).
- **The reader trusts nothing.** `_from_cache` returns `None` for any file that does not parse or does not fit the
  shape, and the review runs afresh (`review.py:902-909`).
- **A failed write costs only the next run.** The cache write sits under `contextlib.suppress(OSError)`
  (`review.py:842`): "a cache that cannot be written costs the next run, never this one". That also covers Windows'
  `PermissionError` when a reader holds the file open.
- **Concurrent writers are harmless.** Two writers of one key wrote a review of the same inputs, and the last one
  wins.
- **The proposed `import fcntl` is Unix-only.** At the top of `review.py` it would stop the command loading on
  Windows, which `ci.yml` and `azure-pipelines.yml` test. If locking were ever wanted, the portable tool is
  `filelock`, which uses `LockFileEx` on Windows.

### 3.7 ASYNC-02: cancellation and the session read: refuted

- `get_session` is a read of an in-memory session, not a cleanup. On cancellation nothing needs releasing.
- A cancelled run must propagate. Ctrl-C already ends as one sentence and exit 130 (`review.py:925`).
- The proposed `finally` would await a read during cancellation and then discard it, since the exception
  propagates past the `return`. If the read raised, it would mask the original exception.

### 3.8 RULE-01: unpinned actions: confirmed gap; the proposed code misses the common form

**The gap.**
- No rule in `default` reads `uses:`. `rule.steering-files` names any change under `.github/workflows/`, but it is
  minor and outside `default`.
- The project holds itself to SHA pinning (`tests/test_copilot.py`) but offers no rule for the branches it reviews.

**The rationale is current:**
- CVE-2025-30066: tj-actions/changed-files' tags were repointed to a malicious commit, and only users pinned by SHA
  were safe.
- CVE-2025-30154: reviewdog.
- Since 2025-08-15 GitHub lets an enterprise, organization or repository require full-SHA pins: "Require actions to
  be pinned to a full-length commit SHA".

**The proposed regex `^\s*uses:` cannot match a step's `- uses:`**, the form most workflows use. Run against real
shapes, the audit's pattern and a corrected one compare as follows:

| Line | Audit | Corrected |
| :-- | :-- | :-- |
| `      - uses: actions/checkout@v4` | missed | **flagged** |
| `      - uses: 'actions/setup-python@v5'` | missed | **flagged** |
| `      - uses: actions/checkout@11bd719` (short SHA; GitHub refuses short SHAs since 2021) | missed | **flagged** |
| `    uses: org/repo/.github/workflows/build.yml@main` (reusable workflow) | flagged | flagged |
| `      - uses: actions/checkout@<40 hex> # v4.2.2` | passed | passed |
| `./local`, `docker://…`, `- run: echo "uses: x@v1"` | passed | passed |

**Corrected rule, run through `try_rule`.**
- It flags workflow lines 6, 7, 12 and 14 and a composite `action.yml`'s step.
- It passes the pinned, local and Docker lines, a `run:` line and a `docs/` file.

```python
"""A workflow or an action that runs another action by a tag or a branch, not a full commit SHA."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Where GitHub reads `uses:`: a workflow, and an action's own metadata, whose composite steps run other actions.
READ = (".github/workflows/*.yml", ".github/workflows/*.yaml", "action.yml", "action.yaml")
#: A `uses:` value as a step (`- uses:`) or a job (`uses:`) writes it, quoted or not, a comment after it.
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*[\"']?([^\s\"'#@]+)@([^\s\"'#]+)")
#: A full commit SHA: GitHub refuses a shorter one.
SHA = re.compile(r"[0-9a-f]{40}")


class PinnedActions(FileRule):
    """A tag or a branch can be moved to other code after review; a full SHA cannot."""

    id = "pinned-actions"
    severity = "major"
    title = "a workflow runs an action by a tag or a branch, not a full commit SHA"
    fix = "Pin it by its 40-character commit SHA, the version in a comment after it: `@<sha> # v4.2.2`."

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        if not matches(file.path, *READ):
            return []
        found = []
        for number in sorted(file.added):
            used = USES.match(file.near(number) or "")
            if used and not used[1].startswith(("./", "docker://")) and not SHA.fullmatch(used[2]):
                found.append((number, f"{used[1]} is run by `{used[2]}`, which can be moved"))
        return found
```

**Still to add, per `.github/skills/new-rule/SKILL.md`:**
- the `bad` and `good` examples the catalog holds every rule to;
- GUIDE's check list;
- a profile decision.

**Known limits:**
- A pin to a fork's SHA, an "impostor commit", passes. Detecting one needs the network; zizmor's `impostor-commit`
  audit does it.
- A `docker://image:tag` is not held to a digest.
- A `run: |` block whose line starts `uses: x@y` would read as a step.
- Dependabot keeps both the SHA and the `# vX.Y.Z` comment current.

### 3.9 RULE-02: deterministic `shell=True` and `pickle` rules: already implemented

- `rule.lint`, in `default`, runs ruff `--isolated` with the `S` family, flake8-bandit, at severity major
  (`judge/rules/lint.py`). Running `adk-review --try danger.py --check rule.lint` reported both:
  - `S602 subprocess call with shell=True … (danger.py:6)`, major;
  - `S301 pickle … (danger.py:7)`, major.
- The rules the audit asks for are stable in ruff 0.16, not preview: S602 and S604 (`shell=True`), S605
  (`os.system`, `os.popen`), S301 (`pickle`), S302 (`marshal`), S307 (`eval`), S506 (`yaml.load`) and S202
  (`tarfile`).
- **A real residual, for the security guideline to keep covering.** S602 is silent for `shell=flag`,
  `shell=CONST` and `**kw`, and no S rule flags `asyncio.create_subprocess_shell`. A hand-written AST rule would
  share most of these blind spots. Reusing the bandit family through `lint` is the right layer.

### 3.10 LLM-01: more provider arms: not recommended

- The fact is right: `REVIEW_PROVIDER` takes `copilot` or `gemini` (`judge/config.py:77`).
- The project's direction is that **GitHub Copilot and Gemini are first-class**: every change works, is tested and is
  measured live on each.
- Each new arm multiplies that cost, and dilutes the two arms that matter: its temperature-zero check,
  `require_ready`'s readiness sentence, the offline arm test, and the live measurements.
- The model names in the audit (`claude-3-7-sonnet`, `gpt-4o`) are also dated.

### 3.11 PERF-01: process-pool health scan: refuted, measured

**Whole-tree review** (`--all --profile rules-only`) of a 723-file, 6.3 MB repository built from ADK's sources:
- **3.9–4.1 s wall time.**
- `collect_evidence`: 1.30 s.
- `measured()`: **0.30 s**. Each file is parsed once and shared, so health walks parsed trees only.

**Why a process pool loses:**
- macOS and Windows start workers with `spawn`. Starting the pool and pickling ASTs, or re-parsing in each worker,
  costs more than 0.30 s.
- `core/` is the pure layer, and a process pool there would be the first process control in it.
- With guidelines on, a review's wall time is model calls of tens of seconds.

### 3.12 LLM-02: verifiers given their file's diff alone: recommendation refuted

- The arithmetic is right. `BLOCK_CAP_CHARS` is 250,000, so ten verifiers over a 150 KB diff read about 375 k prompt
  tokens.
- The recommendation reverses a **documented** decision. `judge/verify.py`'s docstring: "Each reads the whole diff,
  not its finding's file: a guideline judged with everything in view is judged with the same, and less would refute
  for what it could not see."
- A refuted finding is **dropped**. Starving the verifier of context therefore moves blockers toward exit 0: a
  fail-open risk.
- `--verify` is opt-in, and asks only about findings a rule has not already made.
- A cost reduction without that risk is NEW-7.

### 3.13 DEV-01: type-checker search paths: partly confirmed, already applied

Run on a two-file reproduction: `scripts/labelled.py` doing `from measure import X`.

| Checker | No config | With config |
| :-- | :-- | :-- |
| Pyrefly 1.3.2 | `ERROR Cannot find module 'measure' [missing-import]` | 0 errors with `[tool.pyrefly] search-path = [".", "scripts"]` |
| Pyright | **0 errors**: its last fallback imports from the importing file's own folder | 0 errors |

- Both sections were already in the working tree's `pyproject.toml`, then uncommitted. Pyrefly reports 0 errors on
  the three scripts with them.
- The `[tool.pyright]` section is redundant. It is harmless, and can be dropped or kept for explicitness.

---

## 4. New findings from the verification

### 4.1 NEW-1: chat re-reviews on every turn after a scope (P1)

**The cause.**
- `inspect_repository` leaves the confirmed scope in session state (`judge/tools.py:53`), and nothing clears it.
- Each new message restarts the workflow at `START`. After one confirmed review, every later turn finds the scope
  again: "thanks!", "what does that mean?", anything.

**Measured offline**, a session holding a confirmed scope and the message "thanks!":
- The intake, all three guidelines and the verdict ran: **5 model calls**.
- The repository was gathered again.

**Cost.**
- On Gemini's free tier, 20 requests a day, four follow-up questions spend a day's quota.
- Each repeat review can also disagree with the last, since a model's reading varies.

**Fix:** the same gate as FLOW-01 (§3.1), measured: "thanks!" costs one call.

### 4.2 NEW-2: chat's judging agents read text outside their fences (P1, injection hardening)

**The design.** Every untrusted section a judging agent reads sits between nonce-carrying marker lines, with a
reminder after them (`judge/agents.py`). The verdict's instruction says: "The findings quote the branch's own text,
between two marker lines".

**What ADK adds in chat.**
- A `single_turn` node is handed its predecessor's output as a synthetic user turn
  (`_llm_agent_wrapper.py:305-348`), and `include_contents="none"` keeps the current turn.
- Measured offline, the chat verdict's request carries one user turn: the **JoinNode's output, raw JSON of every
  guideline's findings**. That includes `evidence`, quoted verbatim from the branch, and the model-written titles.
  None of it is fenced.
- So the fenced copy in the instruction has an unfenced twin, the attacker-controlled part included, and the verdict
  pays for its tokens twice.
- Each chat guideline likewise receives the intake's reply as "For context: … [intake] said: …". It sits between
  ADK's fixed `<<<BEGIN_QUOTED_AGENT_CONTENT>>>` markers, which, unlike the project's nonce markers, a text can
  forge.
- The command is not affected. Its guidelines' only turn is `review`, and a verifier's is `judge` (both measured).

**Tried and rejected.** A no-op node before the verdict replaces the join's JSON with the last guideline's output,
under ADK's fixed markers. Still unfenced.

**Fix, prototyped and run.** Give each chat judging agent one fixed turn. As an agent `before_model_callback` that
sets `llm_request.contents = [types.UserContent(...)]`, the verdict's request carried only that turn.

Ship it as a **plugin placed first**, chat only, for one reason: a plugin runs before the debug log and `Recorder`,
so they record exactly what is sent. An agent callback runs after them. Using the command's own word, `review`,
keeps every recorded request hash unchanged.

```python
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types


class OneTurn(BasePlugin):
    """Each judging agent reads its instruction and one fixed turn, as the command's do: in chat ADK hands the verdict
    the join's output, every guideline's findings unfenced, and each guideline the intake's reply."""

    def __init__(self, agents: frozenset[str]) -> None:
        super().__init__(name="one_turn")
        self.agents = agents

    async def before_model_callback(self, *, callback_context, llm_request) -> None:
        if callback_context.agent_name in self.agents:
            llm_request.contents = [types.UserContent("review")]
```

**The test to write first.** Run a chat review with the fake model. Assert that the verdict's and each guideline's
request contents are exactly `["review"]`, and that no finding's evidence appears outside the instruction's fences.

### 4.3 NEW-3: a SIGTERM can be swallowed, so the run never stops (P2)

**The cause.**
- `Terminated` subclasses `Exception` (`review.py:583`). A signal handler raises it in whatever Python frame is
  running.
- When that frame sits inside one of the broad handlers that keep the engine fail-closed, the handler catches it:
  ADK's node error handling, `ReportProviderErrors`, `run_seeded`, or the checks node.
- Then the run goes on: the other guidelines still call their models, and a report is written.

**Measured offline.** The fake model sent SIGTERM to its own process mid-call:
- Today: **exit 3**, with `### guideline.security — FAILED: Terminated:` in a full report.
- With `Terminated` as a `BaseException`: **exit 143**, and `terminated: the review did not finish`.

**Why it matters.**
- A CI cancel sends signals with a short grace period before `SIGKILL`.
- A run that ignores SIGTERM is killed mid-flight, which leaves behind the temporary files the handler exists to
  clean up (`review.py:951-953`).
- The window is the time the main thread spends running Python, not waiting on the network: small, but real.

**Fix: one line.** `KeyboardInterrupt` and `SystemExit` are `BaseException`s for this reason. No `except
BaseException` exists in the package to undo it.

```python
class Terminated(BaseException):  # noqa: N818 -- as KeyboardInterrupt is, and like it past every `except Exception`
    """A job stopped the run with SIGTERM: raised in the run's place, so what it holds is let go as on Ctrl-C."""
```

**The test to write first** is the experiment above: the fake sends SIGTERM inside a model call, and the run ends
143 with the sentence. It fails today with exit 3.

### 4.4 NEW-4: the chat intake has no memory between turns (P2)

**The cause.** As a node, the intake becomes `single_turn`, and `single_turn` sets `include_contents="none"` unless
the agent sets it (`_llm_agent_wrapper.py:403-404`).

**Measured offline.** On turn 2 the intake's request held one content, the new message alone.
- "the repo is /x" followed by "review feature/y" cannot work: turn 2 has no repository.
- The person must restate everything in one message.

**Options, each run:**

| Option | Result | Verdict |
| :-- | :-- | :-- |
| `include_contents="default"` | Sees the history, but each user message twice, since ADK injects the node input as a second user turn. After a review it also reads every guideline's and the verdict's output, branch quotes included, which is untrusted text in front of the agent that holds the tools. | Rejected |
| `mode="chat"` | Clean history, but the workflow **stops at the intake**: no checks, guidelines or verdict ran even after the tool confirmed a scope. A chat-mode node waits for the user. | Rejected |
| A `{review_repo?}` placeholder in the intake's instruction | Gives the intake the one fact the guard already pins, the conversation's repository, from state the tools wrote. No untrusted text. | **Recommended** |

The intake's instruction gains one sentence: "The repository this conversation confirmed, if any: `{review_repo?}`;
use it when the person names only branches."

### 4.5 NEW-5: no test pins what each arm receives as the schema (P2)

- TYP-01 and TYP-02 are both one-line changes to `core/findings.py` that pass every current test. Both silently
  change what a model is sent, on one arm or both.
- No test converts `Review` or `Judgement` through either arm's converter (`grep` finds none).

**A guard, using the probes run for this report.** For each output schema, an offline test asserts:
- the Gemini arm's `t_schema` conversion succeeds, with **no** `additional_properties`, and keeps every field's
  description;
- the Copilot arm's `_to_litellm_response_format` is `strict` and keeps every field's description;
- the schema has no `dict[...]` field, which the Gemini Developer API rejects in `response_schema`.

### 4.6 NEW-6: the cache folder is a trust boundary (P3, documentation)

- `_from_cache` serves any well-formed file in the folder as the review's verdict, `"code": 0` included, with no
  model call. That is correct for the operator's own folder.
- GUIDE's `--cache` row does not say who may write the folder.

**Proposed sentence:** "Whoever can write the folder can write a verdict: keep it the operator's own, and in CI never
restore it from a cache that untrusted jobs can write."

### 4.7 NEW-7: verifier prompt order defeats implicit caching (candidate: measure first)

**The cause.**
- Each verifier's instruction is the role, then **the finding**, then the whole change (`judge/verify.py:47-62`).
- Gemini's implicit caching is on by default for 2.5 models, with a 2,048-token minimum on 2.5 Flash. It matches
  prefixes, and Google's advice is "put large and common contents at the beginning of your prompt."
- With the finding first, N verifiers share only a few hundred characters of prefix.

**The change.** Role, then the change, then the finding, then the reminder. That makes the shared diff the cacheable
prefix, and keeps the reminder last, where it guards.

**Why unverified.**
- The verifiers run three at a time, and simultaneous requests may miss a cache still being written.
- `UsageLedger` does not record `cached_content_token_count`.

**Measure** cached tokens on a live `--verify` run with several findings, on each arm, before adopting. Copilot bills
per request, so the gain there would be latency, not cost.

**Measured on Copilot (`gpt-4.1`): not adopted.** The demo's planted branch plus one public module (`shlex.py` from
the standard library), so each verifier's prompt is about 4,300 tokens, above both arms' caching minimums. A harness
records each call's `cached_content_token_count`, which ADK fills from LiteLLM's usage. It patches the verifier in its
own process, so the checkout is never edited between the sides. Two `--verify` runs a side:

| Order | Verifier calls that read from the cache | Cached tokens on a hit | The true finding "no tests for `report.py`" |
| :-- | :-: | :-: | :-- |
| As it is: the finding, then the change | 0 of 8 | — | Held in 2 of 2 runs |
| The change, then the finding | 4 of 7 | 3,968 of about 4,300 | **Refuted in 2 of 2 runs**: "the quoted lines only show the implementation" |

- The caching works as predicted, even with three verifiers in flight at once.
- But the order changes the judgement. The branch adds `report.py` and no test for it, so the finding is true, and a
  verifier reads the whole change precisely so that an absence like this one can be judged. Refuting it is the
  failure `--verify` must not have: it exists to drop invented findings.
- The gain on Copilot is latency alone, a fraction of a second a verifier, since Copilot bills per request.
- The current order stays. A Gemini measurement would only matter for a new order, and that would first need a
  quality measurement over many runs (`scripts/labelled_set.py`), not a cache count.

---

## 5. Verified plan

Ordered by value over cost. Each item's test is written first and seen to fail.

| # | Change | Files | Test to write first | Evidence |
| :-: | :-- | :-- | :-- | :-- |
| 1 | NEW-2: one fixed turn for chat's judging agents (`OneTurn` plugin, first) | `judge/plugins.py`, `judge/graph.py` | Chat verdict and guidelines read `["review"]` only | §4.2, prototyped |
| 2 | FLOW-01 + NEW-1: the intake clears the scope; a gate continues only on this turn's scope | `judge/graph.py`, chat tests that seed a scope | "hello" and a later "thanks!" each make one model call; a confirmed scope reviews | §3.1, prototyped |
| 3 | NEW-3: `Terminated(BaseException)` | `review.py` | SIGTERM inside a model call ends 143 | §4.3, prototyped |
| 4 | RULE-01: `rule.pinned-actions`, corrected regex | `judge/rules/pinned_actions.py`, `GUIDE.md`, `config.toml` | The rule's own `bad`/`good`, including `- uses:` and a short SHA | §3.8, prototyped |
| 5 | NEW-5: pin each output schema per arm | `tests/` | Fails if a description or strictness changes, or a `dict` field appears | §3.4, §3.5, probed |
| 6 | NEW-4: the conversation's repository in the intake's instruction | `judge/graph.py` | The intake's instruction carries `review_repo` | §4.4 |
| 7 | NEW-6: one sentence on the cache's trust | `GUIDE.md` | `tests/test_docs.py` already holds GUIDE to the flags | §4.6 |
| 8 | Optional: one `asyncio.Runner` for both phases; drop the redundant `[tool.pyright]` | `review.py`, `pyproject.toml` | One live `--verify` review per arm | §3.2, §3.13 |
| 9 | Candidate: verifier prompt order, after a live caching measurement (measured: not adopted) | `judge/verify.py` | Cached tokens counted, before and after | §4.7 |

**Declined, with the reason recorded:**
- TYP-01, TYP-02, CONC-01: harmful.
- SEC-01, ASYNC-02, PERF-01: no defect.
- RULE-02: already done.
- LLM-01: direction.
- LLM-02: fail-open.

Each change in items 1–4 touches both arms' shared path. Per the project's rule, it is tested offline on the
Copilot arm too (`load_agent(monkeypatch, "copilot", "gpt-4.1")`) and measured live on each arm once.

**Open decisions carried over** (the owner's to make, not findings):
- Lockfile coverage: the skip list names 21 lockfile kinds, `rule.lockfile-sources` reads 10.
- Whether the report should name a Copilot endpoint from LiteLLM's token cache.
- Copilot reasoning models, which cannot run at temperature zero.
- Thinking budgets for the Gemini verifier and chat agents.

**Watch items** (no action today):
- Copilot's gpt-4.1 is served from Azure OpenAI, whose strict mode is stricter than OpenAI's. The schema ADK sends
  keeps pydantic's `default` keys, and live Copilot runs accept it today. The test in item 5 would show if that
  changes.
- Google's API reference now marks `response_schema` deprecated in favour of `responseFormat`. ADK chooses the
  field, so this moves with an ADK upgrade.
- ADK #5538, the cached Gemini client across event loops, is fixed upstream after 2.9.2. It matters only if one model
  instance ever serves more than one loop.

---

## 6. Implemented (2026-10-13)

Items 1–8 of §5 are implemented. Each test was written first and seen to fail for the reason named. Each fix was
mutation-checked: removing it makes its test fail again. The suite passes, 516 passed and 1 skipped, and ruff check
and format are clean.

| # | Change | Where | Tests | Proof |
| :-: | :-- | :-- | :-- | :-- |
| 1 | `OneTurn` plugin, chat only, after `RefuseUnready` and before the redaction, the log and the recorder | `judge/plugins.py`, `judge/graph.py` | `test_in_chat_each_judging_agent_reads_its_instruction_and_one_fixed_turn`, and the same on Copilot's LiteLLM path | Without the plugin both fail. Live on both arms, every guideline and the verdict were sent `['review']`. |
| 2 | The intake clears the scope at each turn's start; a `scoped` gate routes `True` to the checks and `False` to a no-op node, so ADK logs no warning | `judge/graph.py` | `test_a_turn_whose_intake_confirms_no_scope_ends_at_its_answer`, `test_a_later_turn_reviews_again_only_when_its_own_intake_confirms`, and the Copilot-arm test | Opening the gate, or not clearing the scope, makes them fail. Live on both arms: "hello" made 1 call (was 2), "thanks!" 1 (was 5). |
| 3 | `Terminated(BaseException)` | `review.py` | `test_a_job_stopped_during_a_model_call_stops_the_review`; `test_a_job_that_signals_a_waiting_review_stops_it`, added on review, sends a real SIGTERM to the installed entry point while every guideline awaits its model (POSIX only) | Exit 3 before, 143 after. 25 repeated runs, none hung. A real signal, in the guidelines' calls or the verifiers': 143 within 0.8 s, and SIGINT 130. Without `settle`'s handler the second test fails: the process dies by the signal, with no sentence and no cleanup. |
| 4 | `rule.pinned-actions`, in `default` and `default-rules`, severity major | `judge/rules/pinned_actions.py`, `config.toml`, `GUIDE.md`, `README.md` | `test_an_action_run_by_anything_but_a_full_commit_sha_is_named`, and the catalog's check of its own examples | Run over this repository's SHA-pinned workflows, it reports nothing. |
| 5 | Each answer schema pinned as each arm's converter writes it | `tests/test_wiring.py` | `test_each_arm_is_sent_each_answers_schema_as_it_is_written` | Applying TYP-02's alias or TYP-01's `extra="forbid"` makes it fail. |
| 6 | The intake is told `{review_repo?}`; the key moves to `judge/agents.py` beside the other shared keys | `judge/graph.py`, `judge/agents.py`, `judge/tools.py` | `test_the_intake_is_told_the_repository_its_conversation_confirmed` | Live on Copilot, "Review feature/payments against main once more", with no path, reviewed the confirmed repository. |
| 7 | The cache folder's trust, one sentence | `GUIDE.md` | `tests/test_docs.py` | — |
| 8 | One `asyncio.Runner` for the run and the second opinion | `review.py` | `test_the_run_and_its_second_opinion_share_one_event_loop` | Two loops before, one after. Live `--verify` on Copilot: 3 guidelines and 4 verifiers, all completed, nothing refuted, exit 1. |

**Tests that changed meaning with the gate.** A failed intake used to let the guidelines run, each saying that
nothing was gathered. Now the turn ends at the intake's own error, with no verdict on nothing. Those tests now assert
that, and the guard's own contract has a direct test:
`test_a_guideline_with_nothing_gathered_or_a_failure_before_it_says_so_and_why`.

The chat tests that seeded a scope now drive the intake's tool through the fake, using `calls_when` and `confirms`.
`Chat`, `asked` and the tool-calling fake are new in `tests/conftest.py`.

**Since then (2026-10-16).** Row 2's callback is gone. The scope is now `temp:review_scope`, a key ADK 2.9.2 keeps
for the one turn that wrote it and never saves, as `temp:failure` already was. Probed offline: a tool in the intake
writes it, the gate and the checks node read it in that turn, and the next turn starts without it. The same tests hold
it: with the key kept in the session, the "thanks!" test fails again.

**Not done:**
- **Item 9 (NEW-7).** Measured on Copilot and not adopted; §4.7 has the numbers.
- **A live `--verify` review on Gemini.** Not that day: the free tier's daily requests were spent on the chat check.
  Run on 2026-10-17 instead, on the demo branch:
  - The first run was a hole that was the provider's: one guideline met a 503 ("high demand"), and the retries and
    three verifiers passed the free tier's five requests a minute. The verifiers' calls reached Google and came back
    429, with no call failing on a closed loop.
  - Rerun after a minute: all three guidelines and 8 of the 9 verifiers completed in the one loop, the ninth a 429.
    Item 8 holds on Gemini.
  - The same review on Copilot that day: three guidelines and four verifiers, all completed, exit 1.
  - The rerun also dropped one finding as ungrounded: `guideline.tests` quoted the planted credential lines with the
    placeholder `[REDACTED:secret_access_key]` where it had been shown `[REDACTED:aws_secret_access_key]`. Grounding
    held the quote to what the model was shown; the commit before also drops it. Since then any placeholder stands
    for any other in grounding, and that quote grounds; three recorded Copilot reviews regrounded the same, finding
    for finding.
  - Three recorded Gemini reviews of the demo branch followed that day: nine calls, none refused. With the commit
    before and the placeholder change, they regrounded the same, finding for finding, since no label was renamed in
    them. In one, though, `guideline.tests` sent every quote on one line, the line breaks lost and each line's `-` and
    the fence left in, and all five of its findings dropped, a hole. Grounding now reads a marker and a fence run into
    a quote where the file's lines begin and end: all 36 findings of the three reviews ground, those five on the lines
    the same findings stood on in the other two, and the Copilot recordings reground the same.
- **The redundant `[tool.pyright]` section.** Kept: it is harmless, and the audit's author added it.

---

## 7. Sources

**Installed source, read directly:**
- `google/adk/workflow/_llm_agent_wrapper.py`, `_graph.py`
- `google/adk/models/lite_llm.py`, `models/llm_request.py`
- `google/adk/plugins/base_plugin.py`
- `google/adk/agents/llm_agent.py`, `readonly_context.py`
- `litellm/caching/llm_caching_handler.py`, `litellm_core_utils/logging_worker.py`

**OpenAI and Gemini:**
- OpenAI, structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Gemini structured output: https://ai.google.dev/gemini-api/docs/generate-content/structured-output
- google-genai issue #1815 and PR #845: https://github.com/googleapis/python-genai/issues/1815,
  https://github.com/googleapis/python-genai/pull/845
- Gemini implicit caching: https://ai.google.dev/gemini-api/docs/caching
- ADK issue #5538: https://github.com/google/adk-python/issues/5538

**GitHub Actions:**
- SHA-pinning policy: https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/
- Short-SHA removal: https://github.blog/changelog/2021-01-21-github-actions-short-sha-deprecation/
- CVE-2025-30066: https://nvd.nist.gov/vuln/detail/CVE-2025-30066
- CVE-2025-30154: https://nvd.nist.gov/vuln/detail/CVE-2025-30154
- Impostor commits: https://www.chainguard.dev/unchained/what-the-fork-imposter-commits-in-github-actions-and-ci-cd
- zizmor `unpinned-uses`: https://docs.zizmor.sh/audits/#unpinned-uses

**Tools and Python:**
- Ruff S602: https://docs.astral.sh/ruff/rules/subprocess-popen-with-shell-equals-true/
- Ruff UP040: https://docs.astral.sh/ruff/rules/non-pep695-type-alias/
- Pydantic named type aliases: https://pydantic.dev/docs/validation/latest/concepts/types/
- Python `asyncio.Runner`: https://docs.python.org/3.13/library/asyncio-runner.html
- LiteLLM loop-keyed clients: https://github.com/BerriAI/litellm/issues/7667
- `os.replace` on Windows: https://bugs.python.org/issue26362
- `filelock`: https://py-filelock.readthedocs.io/
- Pyright import resolution: https://github.com/microsoft/pyright/blob/main/docs/import-resolution.md
- Pyrefly import resolution: https://pyrefly.org/en/docs/import-resolution/
