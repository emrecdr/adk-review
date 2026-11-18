.PHONY: help sync install web demo demo-repo demo-fix review copilot-login test lint typecheck format ci

REPO ?=  # blank: make demo-repo builds into the temp dir; the command assumes no repository and asks for one
BASE ?= main
HEAD ?= feature/payments
# Where `make demo` builds the demo repository: the script's own default, adk-demo-repo in the temp folder. Asked with
# --no-project here and below: a plain `uv run`, read on every make, synced the environment and rewrote a stale
# uv.lock, so `make sync`'s --locked never saw it stale. Read by an audit, and reproduced.
ifndef DEMO
DEMO := $(shell uv run --no-project python -c "import os, tempfile; print(os.path.join(tempfile.gettempdir(), 'adk-demo-repo'))")
endif
# The lock, as constraints `make install` hands `uv tool install`, which reads no lock of its own.
LOCKED := $(shell uv run --no-project python -c "import os, tempfile; print(os.path.join(tempfile.gettempdir(), 'adk-review-locked.txt'))")

help:
	@echo "make sync                                    - install (uv)"
	@echo "make install                                 - adk-review, adk-review-push, adk-review-commit and adk-review-login on PATH, every dependency at the lock's version"
	@echo "make web                                     - adk web .  (the chat, in a browser, sessions in memory)"
	@echo "make demo                                    - build the demo repository and review its branch with the demo profile: no model, exit 1 is the point"
	@echo "make demo-repo                               - build the throwaway repository with its planted defects"
	@echo "make demo-fix                                - commit the two fixes on the demo branch, then make review: exit 0"
	@echo "make review REPO=<path> [BASE= HEAD= ARGS=]  - review a branch (HEAD defaults to feature/payments, the demo's); ARGS='--profile rules-only' calls no model"
	@echo "make copilot-login                           - one-time GitHub device-flow login for the copilot arm"
	@echo "make test / lint / typecheck / format / ci"

sync:
	uv sync --locked --extra tui

# Measured: `uv tool install .` alone took a LiteLLM three days old or less, where the lock holds an older one; and,
# reading no [tool.uv] of the project, it took the build backend's newest release: --exclude-newer keeps the wait.
install:
	uv export --frozen --no-hashes --no-dev --no-emit-project --quiet --output-file "$(LOCKED)"
	uv tool install --reinstall --exclude-newer P3D --constraints "$(LOCKED)" .

# Sessions in memory: by default ADK keeps each in .adk/session.db, which holds the diffs chat reviewed, a
# credential among them unredacted, the redaction being the model's alone. Read by an audit, and reproduced.
web:
	uv run adk web --no_use_local_storage .

demo:
	uv run python scripts/make_demo_repo.py "$(DEMO)"
	-uv run adk-review "$(DEMO)" --head feature/payments --base main --profile demo

demo-repo:
	uv run python scripts/make_demo_repo.py $(if $(REPO),"$(REPO)")

demo-fix:
	uv run python scripts/make_demo_repo.py $(if $(REPO),"$(REPO)") --fix

review:
	uv run adk-review $(if $(REPO),"$(REPO)") $(if $(BASE),--base $(BASE)) --head $(HEAD) $(ARGS)

copilot-login:
	uv run adk-review-login $(ARGS)

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run pyrefly check

format:
	uv run ruff check --fix . && uv run ruff format .

ci: lint typecheck test
