"""Efficiency: work the change adds that does no good — done twice, waited on in turn, or kept alive past its use."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Efficiency(Guideline):
    """Ruff names a blocking call in async code and a list built by hand; whether work is wasted where it runs, and
    how often it runs there, takes a reader."""

    id = "efficiency"
    title = "work the change wastes"
    role = (
        "You judge efficiency: work the change adds that does no good — the same value computed twice, a file, query "
        "or call repeated where one would do, independent I/O awaited one after another, slow work added at import or "
        "on a path every request takes, and a long-lived callback or cache entry built from a closure that keeps a "
        "large scope alive. Name the cheaper form where the cost is real: code that runs once wastes nothing worth a "
        "finding."
    )
    thinking_budget = 2048
