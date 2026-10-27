"""The rules as one node of the graph, in both shapes: code, run before any guideline.

A `FunctionNode` is a Python function the graph runs as a node beside the agents: ADK 2.x's place for a deterministic
step, where a 1.x pipeline had a tool a model had to choose to call. The command gathers the evidence before the graph
runs and hands it in; in chat the node gathers it itself, for the commits the intake's first tool confirmed, so a review
in `adk web` runs the rules the command runs. Every rule runs the same way, through `core/rules.py`'s `run_rule`, and
its answer is left in state under its check's `state_key`, as each guideline's review is left under its own: the command
reads them back, and for the chat's verdict agent the node renders them all, capped, under one key. The node writes the
blast radius the guidelines read too, in both shapes, from the evidence it holds. A rule that names an outside tool is
given its diagnostics first, gathered by its adapter, `collect/ruff.py` or `collect/pyrefly.py`, over the texts the
collector read. The rules run off the event loop, since ruff and git are processes, and the node never raises: measured,
a node that raises ends the whole run, every guideline with it. Once the rules have read it, the node lets go of what no
one reads after them — the tree, each file's text, diff, added and shown lines — before the model calls: measured, the
tree weighs about thirty times the source's bytes, and under `--all` the diffs and added lines four times the texts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from ..collect.git import gather
from ..collect.tools import ADAPTERS
from ..core.evidence import Change, Entry, Evidence, Failure
from ..core.findings import Checked
from ..core.profiles import state_key
from ..core.rules import AnyRule, FileRule, run_rule

if TYPE_CHECKING:
    import threading

    from google.adk.agents.context import Context
    from google.adk.workflow import FunctionNode

#: How chat gathers the evidence for a scope `preflight` resolved: `collect/git.py`'s `gather`, its settings bound.
type Gatherer = Callable[[dict], Evidence | Failure]
#: The node's name in the graph, in `adk web` and in a trace.
NAME = "checks"
#: How a rule that could not look ends its reason, whatever kept it from looking: one shape for every hole here.
DID_NOT_LOOK = ", so the rule did not look"


def unspent(ceiling: int | None) -> dict:
    """The spend of a review that called no model: the shape `UsageLedger.summary` in `plugins.py` gives, every count
    zero, the ceiling as asked. Spelled here, where the rules run with no engine imported, because `plugins.py`
    imports it and the driver reads this module without it; a change to one is a change to both."""
    return {"calls": 0, "attempted": 0, "tokens": 0, "cached_tokens": 0, "ceiling": ceiling, "by_agent": {}}


def _hole(why: str) -> Checked:
    """A rule's answer where it could not look, and why: one shape for every hole here."""
    return Checked(could_not_look=why + DID_NOT_LOOK)


def _diagnosed(tool: str, settings: Mapping[str, object], evidence: Change) -> dict | str:
    """What a rule's outside tool, run with the rule's settings, says of the change, by path, or why it could not
    look. An adapter that raises past its own answers is its rule's hole alone, as `run_rule` keeps a rule's own
    failure to it: measured, an `IndexError` out of ruff's answer reached the node's catch-all and every rule answered
    with it, the planted key's finding gone."""
    try:
        return ADAPTERS[tool](evidence, **settings)  # the catalog refused a tool none runs
    except Exception as exc:  # noqa: BLE001 -- the tool's failure, named for its rule
        return f"{tool} raised {type(exc).__name__}: {exc}"


def run_rules(evidence: Change, rules: Sequence[AnyRule], stop: threading.Event | None = None) -> dict[str, Checked]:
    """Every rule over what it reads, each the same way, by its check id: what it found, or why it could not look. A
    file rule that names an outside tool is given its diagnostics on each changed Python file, beside the file, and a
    tree rule on the tree. `stop`, once set, ends the run before the next rule: the rules run in a thread, which no
    cancel reaches."""
    answers: dict[str, Checked] = {}
    for rule in rules:
        if stop is not None and stop.is_set():
            break
        files: list[Entry] = evidence["files"]
        if rule.reads_every_kind:  # the project's own files `scan` leaves out: a credential rule reads them too
            files = [*files, *evidence.get("unscanned", ())]
        tree = evidence["tree"]
        if tool := rule.tool:
            found = _diagnosed(tool, rule.tool_settings, evidence)
            if isinstance(found, str):
                answers[rule.check_id] = _hole(found)
                continue
            if isinstance(rule, FileRule):
                files = [{**entry, "diagnostics": found.get(entry["path"], ())} for entry in files]
            elif tree is not None:
                tree = tree.diagnosed(found)
        answers[rule.check_id] = run_rule(rule, files, tree)
    return answers


def _gather(resolved: dict | None, gathered: Gatherer) -> Evidence | str:
    """In chat: the evidence the command would gather, for the scope the intake confirmed, or why there is none."""
    if not resolved:
        return "nothing was gathered"
    evidence = gathered(resolved)
    return evidence if evidence["status"] == "success" else evidence["error_message"]


def _release(evidence: Evidence) -> None:
    """What the rules alone read, let go of before the model calls: the tree, and each file's text, diff, added
    lines, and the lines shown of one not read whole. The command holds the same dict for the report, which reads of
    these only the added lines' numbers, kept apart as `added_at`: measured, the JSON read the lines after they were
    gone, and no finding of a real review stood on an added line. Runs after a failure too, over whatever shape the
    evidence had."""
    evidence["tree"] = None
    for entry in (*evidence.get("files", ()), *evidence.get("unscanned", ())):  # `added_at` stays, for the report
        entry["text"] = entry["added"] = entry["diff"] = entry["shown"] = None


async def _checked(
    scope: dict | None, evidence: Evidence | None, gathered: Gatherer, rules: Sequence[AnyRule], stop: threading.Event
) -> tuple[Evidence | str, dict[str, Checked]]:
    """The evidence the rules read, the command's or the one chat gathers for `scope`, and each rule's answer: a hole
    for each where nothing was gathered."""
    import asyncio

    found = evidence if evidence is not None else await asyncio.to_thread(_gather, scope, gathered)
    if isinstance(found, str):
        return found, {rule.check_id: _hole(found) for rule in rules}
    return found, await asyncio.to_thread(run_rules, found, rules, stop)


def _seen(found: Evidence | str) -> tuple[str, list[str], dict[str, dict]]:
    """What chat's later agents read of what the node gathered: the change the guidelines read, the files the review
    ignored, and each file as grounding reads it. None where nothing was gathered, so every guideline's guard skips
    and names the hole. Measured before, chat's guidelines judged the intake model's retyping of its tools' replies,
    cut at the tools' own cap and under no skip list, and a tool's error, repeated, read as a change. The command
    seeds the change at the session's start, where no event carries it: measured, a state delta carried the diff
    unredacted into `--debug-log`'s file. Chat grounds the guidelines' findings before its verdict, as the command
    does after."""
    if not isinstance(found, dict):
        return "", [], {}
    quotable = {f["path"]: {key: f[key] for key in ("quotable", "framing", "unread", "cut")} for f in found["files"]}
    return found["diff"], found.get("ignored", []), quotable


def build_checks(
    rules: Sequence[AnyRule],
    evidence: Evidence | None,
    gathered: Gatherer = gather,
) -> FunctionNode:
    """The node: each rule's answer into state under its check's key, why one could not look its answer; every answer
    rendered for the chat's verdict, and the blast radius for the guidelines. `evidence` is the command's, gathered
    before the graph, and None in chat, where the node gathers its own by `gathered`, its settings bound already.
    ADK, asyncio and the agents' keys are imported here, where a graph is built: `run_rules`, which `--try` runs
    alone, needs none of them."""
    import asyncio
    import threading

    from google.adk.workflow import FunctionNode

    from .agents import BLAST, DIFF, IGNORED, QUOTABLE, SCOPE, for_the_guidelines

    async def run(ctx: Context) -> None:
        # A stopped review stops here too: measured, a SIGTERM one second into the rules of a 2,683-file `--all`
        # ended the review 6.5 s later, the loop's close waiting on the thread for every rule still to run; told to
        # stop, the thread ends at its next rule, and the review 2.6 to 3.1 s later.
        stop = threading.Event()
        try:
            found, answers = await _checked(ctx.state.get(SCOPE), evidence, gathered, rules, stop)
        except asyncio.CancelledError:
            stop.set()
            raise
        except Exception as exc:  # noqa: BLE001 -- a node that raises ends the run, every guideline with it
            found = f"{type(exc).__name__}: {exc}"
            answers = {rule.check_id: _hole(found) for rule in rules}
        for check, answer in answers.items():
            ctx.state[state_key(check)] = answer.model_dump(mode="json")
        ctx.state[BLAST] = (
            for_the_guidelines(found["blast"], found.get("callers", "")) if isinstance(found, dict) else ""
        )
        if evidence is None:
            ctx.state[DIFF], ctx.state[IGNORED], ctx.state[QUOTABLE] = _seen(found)
        else:  # read by the rules, not held through the guidelines' model calls
            _release(evidence)

    return FunctionNode(func=run, name=NAME)
