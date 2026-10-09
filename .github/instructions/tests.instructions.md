---
applyTo: "tests/**"
---

# Tests

- `conftest.py` has the plumbing: a `FakeModel` registered for `fake.*`; `load_agent(monkeypatch)`, which imports
  `adk_review.agent` afresh with `REVIEW_MODEL=fake` so its real `build_model()`, graph, callbacks and plugins run;
  the `cli` fixture, the command's module loaded that way; `demo_repo`, a real repository built by
  `make_demo_repo.build`, and `hostile_repo`, a branch per way a change once hid from the review; and
  `fake_provider.raises_when` / `answers_when`, which make the provider fail or answer on a text in the system
  instruction. A test is named as the sentence it proves (`test_the_gate_runs_before_any_model`), and an async one
  carries `@pytest.mark.asyncio`, since `asyncio_mode = "strict"`.
- A test may run the developer's own git in folders it made (`S607` is ignored for `tests/*`); the reviewer under
  test must still find git by its full path.
- A test asserts one claim a reader can check and prefers the public functions; a helper several tests need goes
  into `conftest.py`, never into the package.
