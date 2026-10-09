"""Security: what the change makes exploitable or unsafe — a model's reading, where a pattern cannot decide."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Security(Guideline):
    """A rule names a shape it knows, as `secrets` and ruff's bandit codes do; this asks what the change lets an
    attacker do, which no shape names."""

    id = "security"
    title = "security defects and unsafe patterns"
    role = "You judge the change for security defects and unsafe patterns."
    thinking_budget = 4096
