"""What a review is, as types the code can read — and the scale a severity sits on.

`evidence` is a required field: a finding that cannot point at the diff is
worth nothing, so the model cannot omit it. `output_schema` on a guideline turns
`Review` into the response schema the provider is asked for, and ADK puts the
parsed dict — not text — into session state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .text import CODE_MAX_CHARS, SUGGESTION_MAX_CHARS, TITLE_MAX_CHARS, WHY_MAX_CHARS, cut_at

Severity = Literal["blocker", "major", "minor"]
#: The scale, low to high: `decide` and `dedupe` key on it, and the command offers it for `--fail-on`.
RANK = {"minor": 1, "major": 2, "blocker": 3}


def at_or_above(severity: str, bar: str) -> bool:
    """Whether a finding of `severity` sits at or above `bar`: the one test the verdict, the reports and chat's bar
    read. Spelled apart before, chat and the command once disagreed on a major finding."""
    return RANK[severity] >= RANK[bar]


class Finding(BaseModel):
    file: str = Field(description="Repository-relative path of the file the finding is about.")
    line: int | None = Field(default=None, description="Line number in the new file, if it can be named.")
    severity: Severity = Field(description="blocker: must not merge; major: fix before merge; minor: worth fixing.")
    title: str = Field(description="One line naming the problem.")
    evidence: str = Field(description="The exact diff lines this finding is about, quoted verbatim.")
    why: str = Field(
        default="",
        description="For a blocker or a major: why it matters, the failure it causes and when. Empty for a minor.",
    )
    suggestion: str = Field(description="What to change, concretely.")
    code: str = Field(
        default="",
        description="For a blocker or a major that code fixes: the corrected lines, as the file should read them.",
    )

    @field_validator("title")
    @classmethod
    def _title_cut(cls, title: str) -> str:
        """A model's title, cut at `TITLE_MAX_CHARS` with the cut named: the schema the model is asked for is the same,
        so a long one is cut, never refused as a reply that does not fit."""
        return cut_at(title, TITLE_MAX_CHARS)

    def by(self, check: str) -> Finding:
        """This finding titled by the check that made it, `name: title`, as a rule's is, and cut as a model's title
        is: read by review, a guideline's finding named no check, and a name added after the cut ran past it."""
        return self.model_copy(update={"title": cut_at(f"{check}: {self.title}", TITLE_MAX_CHARS)})

    @field_validator("suggestion")
    @classmethod
    def _suggestion_cut(cls, suggestion: str) -> str:
        """A model's change, cut at `SUGGESTION_MAX_CHARS` as the title is."""
        return cut_at(suggestion, SUGGESTION_MAX_CHARS)

    @field_validator("why")
    @classmethod
    def _why_cut(cls, why: str) -> str:
        """A model's reason, cut at `WHY_MAX_CHARS` as the title is."""
        return cut_at(why, WHY_MAX_CHARS)

    @field_validator("code")
    @classmethod
    def _code_cut(cls, code: str) -> str:
        """A model's corrected code, cut at `CODE_MAX_CHARS` between its lines."""
        return cut_at(code, CODE_MAX_CHARS, sep="\n")


class Review(BaseModel):
    """A guideline's answer: its findings, and nothing no code reads. The list is required, even when empty, so an
    answer that names none is refused as a reply that does not fit, never read as a review that found nothing."""

    findings: list[Finding]


class Checked(BaseModel):
    """A check's answer, as a `Review` is a guideline's: what it found and, when it could not look at everything,
    why."""

    findings: list[Finding] = Field(default_factory=list)
    could_not_look: str | None = None
    #: What was already in the file before the change, not caused by it: never decides the verdict.
    old: list[Finding] = Field(default_factory=list)
