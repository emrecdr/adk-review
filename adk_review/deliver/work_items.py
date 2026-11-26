"""The work items a pull request links, as Azure DevOps answers its work items call, which the terminal front end writes
for `--work-items FILE`, and as the hand-off quotes them for a coding agent: what the change is for, people's words it
checks the change against and never obeys. The reviewer reads the file alone and makes no call: the front end
fetches, or a pipeline's own step, with the fields `FIELDS` names. Their rich text is HTML, read here as text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

#: The most work items the hand-off quotes, in the file's order, the rest counted.
WORK_ITEMS_SHOWN = 10
#: The longest work item the hand-off quotes, its title and its text together, the rest cut and the cut named.
WORK_ITEM_MAX_CHARS = 6_000
#: Each field the hand-off quotes, by its reference name, after the title, under the label it is quoted with. A
#: field a work item's process does not hold, or holds empty, is left out of Azure DevOps' answer.
TEXTS = (
    ("System.Description", "Description"),
    ("Microsoft.VSTS.Common.AcceptanceCriteria", "Acceptance criteria"),
    ("Microsoft.VSTS.TCM.ReproSteps", "Repro steps"),
)
#: The fields a fetch asks for: what the hand-off quotes, and no other.
FIELDS = ("System.Id", "System.WorkItemType", "System.State", "System.Title", *(name for name, _ in TEXTS))


@dataclass(frozen=True)
class WorkItem:
    """One work item: its number, kind, state and title, and each text field it holds as text, by its label."""

    number: int
    kind: str
    state: str
    title: str
    texts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class WorkItems:
    """The work items the file holds, and how many it names that Azure DevOps could not give, deleted or out of
    reach, which its answer holds as `null`."""

    items: tuple[WorkItem, ...] = ()
    unread: int = 0


#: No work item read: what a review without `--work-items` quotes.
NO_WORK_ITEMS = WorkItems()


def read_work_items(path: str) -> WorkItems:
    """The work items the file at `path` holds, as Azure DevOps answered, or a sentence for anything else, before the
    review runs."""
    try:
        return work_items_of(json.loads(Path(path).expanduser().read_text(encoding="utf-8")))
    except RecursionError:
        raise ValueError(f"--work-items {path}: nested deeper than Azure DevOps answers. {_NAME_IT}") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"--work-items {path}: {getattr(exc, 'strerror', None) or exc}. {_NAME_IT}") from None


#: What a refusal of the file asks for.
_NAME_IT = "Name the file the front end wrote, or leave the flag out"


def work_items_of(data: object) -> WorkItems:
    """The work items in Azure DevOps' answer to its work items call, or a `ValueError` saying what is wrong with it."""
    if not isinstance(data, dict) or not isinstance(data.get("value"), list):
        raise ValueError("no list of work items under `value`")
    items = tuple(_item(raw) for raw in data["value"] if raw is not None)
    return WorkItems(items, sum(raw is None for raw in data["value"]))


def _item(raw: object) -> WorkItem:
    """One work item as Azure DevOps answers it, or a `ValueError` saying what is wrong with it."""
    if not isinstance(raw, dict) or type(raw.get("id")) is not int or not isinstance(raw.get("fields"), dict):
        raise ValueError("a work item with no number or no fields")
    fields = raw["fields"]
    kind, state, title = (_said(fields.get(name)) for name in ("System.WorkItemType", "System.State", "System.Title"))
    texts = tuple((label, text) for name, label in TEXTS if (text := as_text(_said(fields.get(name)))))
    return WorkItem(raw["id"], kind, state, title, texts)


def _said(value: object) -> str:
    return value if isinstance(value, str) else ""


def as_text(html: str) -> str:
    """Rich text as a person reads it: each block on a line of its own, a table's cells apart, a list item after a
    dash, each entity read, the spaces in a line made one, no blank lines but one between blocks, and a tag HTML has
    not, `Result<Order>`, kept as written, a script's and a style's among them: read by an audit and by review, one
    hid every word after it, and a security story names one. A field with no HTML in it, as Azure DevOps keeps one
    written in Markdown, is quoted as written: read as HTML, its spaces and its `<...>` were lost."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    if not reader.html:
        return "\n".join(line.rstrip() for line in html.splitlines()).strip("\n")
    if STYLESHEET.search(html):  # rich text, where a tag typed is kept escaped: a paired one is pasted markup
        reader = _Reader()
        reader.feed(STYLESHEET.sub("", html))
        reader.close()
    lines = (" ".join(line.split()) for line in "".join(reader.said).splitlines())
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip("\n")


#: A stylesheet paired with its end tag, as Word and Outlook paste one into rich text: dropped there, never content.
#: Read by an outside audit: one came before the first acceptance criterion and spent the text's room on CSS. An
#: unclosed `<style>` stays the text's own, as a security story names one.
STYLESHEET = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)

#: The tags HTML has, which rich text is written in: any other, `<Order>` in `Result<Order>`, is the text's own.
HTML_TAGS = frozenset(
    [
        "a",
        "abbr",
        "article",
        "aside",
        "b",
        "big",
        "blockquote",
        "body",
        "br",
        "caption",
        "center",
        "cite",
        "code",
        "col",
        "colgroup",
        "dd",
        "del",
        "details",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "font",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hr",
        "html",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "li",
        "main",
        "mark",
        "nav",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "samp",
        "section",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "time",
        "title",
        "tr",
        "tt",
        "u",
        "ul",
        "var",
        "wbr",
    ]
)


class _Reader(HTMLParser):
    """The text of rich text, its blocks apart, and whether it held HTML at all."""

    BLOCKS = frozenset(
        [
            "article",
            "blockquote",
            "br",
            "caption",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "figure",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "tfoot",
            "thead",
            "tr",
            "ul",
        ]
    )
    CELLS = frozenset({"td", "th"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.said: list[str] = []
        self.html = False
        self._item = False  # a list item's dash written, its text not yet: a paragraph inside it stays on its line

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002 -- the parser's own
        if tag not in HTML_TAGS:
            self.said.append(self.get_starttag_text() or "")
            return
        self.html = True
        if tag == "br":  # the one line break that is always one: `<div><br></div>` is an empty line
            self.said.append("\n")
        elif tag == "li":
            self._line_ends()
            self.said.append("- ")
            self._item = True
        elif tag in self.CELLS:
            self.said.append(" | ")
        elif tag in self.BLOCKS and not self._item:
            self._line_ends()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)  # `<br/>` is one line break, never a start and an end

    def handle_endtag(self, tag: str) -> None:
        if tag not in HTML_TAGS:
            self.said.append(f"</{tag}>")
        elif tag in self.BLOCKS and tag != "br":
            self._line_ends()

    def handle_data(self, data: str) -> None:
        self.said.append(data)
        self._item = self._item and not data.strip()

    def _line_ends(self) -> None:
        """The line written so far ended, where one is open: a block's start and its end each end a line, and read by
        review, adding a break at both put a blank line between every two rows, terms and paragraphs."""
        if next((chunk[-1] for chunk in reversed(self.said) if chunk), "\n") != "\n":
            self.said.append("\n")
