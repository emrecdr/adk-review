"""Tests: whether what the change adds is tested at all, and whether its tests are honest."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Tests(Guideline):
    """Measured live on Copilot: asked only whether the tests were honest, the guidelines named the planted untested
    function in 2 of 5 runs; asked first whether what the change adds is tested at all, in 5 of 5."""

    id = "tests"
    title = "untested changes, and tests that are not honest"
    role = (
        "You judge the tests: whether what the change adds is tested at all, and whether the tests are honest: "
        "real behaviour, realistic inputs, no over-mocking."
    )
    thinking_budget = 4096
