---
applyTo: "tests/**"
---

# Tests

- `conftest.py` has the plumbing: a `FakeModel` registered for `fake.*`; `load_agent(monkeypatch)`, which imports
  `adk_review.agent` afresh with `REVIEW_MODEL=fake` so its real `build_model()`, graph, callbacks and plugins run, on
  the Gemini arm, where the fake registers, unless a test names another arm and model (`load_command` loads the command
  that way: the Copilot arm runs offline with ADK's LiteLLM client replaced, and LiteLLM's own routing only where a test
  replaces the network below it, as `test_a_copilot_call_carrying_a_tool_reaches_copilot` does); the `cli` fixture, the
  command's module loaded that way; `demo_repo`, a real repository built by `make_demo_repo.build`, and `hostile_repo`,
  a branch per way a change once hid from the review; and `fake_provider.raises_when` / `answers_when` / `calls_when`,
  which make the provider fail, answer or call a tool on a text in the system instruction; `confirms`, chat's intake
  confirming a scope with its tool; `Chat`, one conversation over several turns, under a `run_config` where a test names
  one, its last turn's events in `events`; and `asked`, each request as it was sent. A test is named as the sentence it
  proves (`test_the_gate_runs_before_any_model`), and an async one carries `@pytest.mark.asyncio`, since
  `asyncio_mode = "strict"`. A test still running after `faulthandler_timeout` prints every thread's stack and ends the
  run, so a hang names itself.
- A test may run the developer's own git in folders it made (`S607` is ignored for `tests/*`); the reviewer under
  test must still find git by its full path.
- A test asserts one claim a reader can check and prefers the public functions; a helper several tests need goes
  into `conftest.py`, never into the package.
