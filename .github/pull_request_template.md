## What changed, and what was learned

## Checks

- [ ] `uv run ruff check . && uv run ruff format --check .` and `uv run pytest -q` pass locally
- [ ] `GUIDE.md` and `README.md` say what the code does: a new flag, check, JSON field or limit is in the guide
- [ ] Imports point one way, `core` → `collect` → `judge` → `deliver`, and a check is one file of its own
- [ ] Works on both arms, Copilot and Gemini: nothing outside `adk_review/judge/config.py` names a provider
- [ ] No date and no count of the repository's state added to the docs; nothing reads or prints `.env`
