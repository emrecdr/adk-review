.PHONY: help sync web demo-repo demo-fix review copilot-login test lint format ci

REPO ?=  # blank: make demo-repo builds into the temp dir; the command assumes no repository and asks for one
BASE ?= main
HEAD ?= feature/payments

help:
	@echo "make sync                                    - install (uv)"
	@echo "make web                                     - adk web .  (the chat, in a browser)"
	@echo "make demo-repo                               - build the throwaway repository with its planted defects"
	@echo "make demo-fix                                - commit the two fixes on the demo branch, then make review: exit 0"
	@echo "make review REPO=<path> [BASE= HEAD= ARGS=]  - review a branch; ARGS='--profile rules-only' calls no model"
	@echo "make copilot-login                           - one-time GitHub device-flow login for the copilot arm"
	@echo "make test / lint / format / ci"

sync:
	uv sync --locked

web:
	uv run adk web .

demo-repo:
	uv run python scripts/make_demo_repo.py $(REPO)

demo-fix:
	uv run python scripts/make_demo_repo.py $(REPO) --fix

review:
	uv run adk-review $(REPO) $(if $(BASE),--base $(BASE)) --head $(HEAD) $(ARGS)

copilot-login:
	uv run python scripts/copilot_login.py $(ARGS)

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff check --fix . && uv run ruff format .

ci: lint test
