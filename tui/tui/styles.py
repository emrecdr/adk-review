"""Stylesheet definitions for `adk-review-tui`."""

from __future__ import annotations


def literal(text: str) -> str:
    """`text` for a widget that takes a string alone and reads it as markup, a section's title: each `[` escaped, so
    every character is drawn as it is. Measured against Textual's parser over fourteen strings: its own `escape` drew
    a backslash in `[my[service]]`, and this form drew each as written."""
    return text.replace("[", r"\[")


APP_CSS = """
#config-line {
    height: auto;
    padding: 0 1;
    color: $text-muted;
}
Screen {
    layout: vertical;
}
#panel-dock {
    height: 1;
    layout: horizontal;
    padding: 0 1;
    background: $panel;
}
.dock-chip {
    width: auto;
    min-width: 0;
    height: 1;
    margin: 0 2 0 0;
}
.docked {
    display: none;
}
#chain-row {
    height: auto;
    max-height: 14;
    layout: horizontal;
}
.chain-box {
    width: 1fr;
    height: 14;
    border: solid $primary;
    padding: 0 1;
}
.chain-box > Contents {
    height: 1fr;
    padding: 0;
}
.chain-box OptionList {
    height: 1fr;
}
.hidden {
    display: none;
}
.chain-title {
    text-style: bold;
    color: $accent;
}
.filter-input {
    height: 1;
    margin-bottom: 1;
    border: none;
}
#status-banner {
    height: 2;
    padding: 0 1;
    background: $surface;
    color: $text;
    border-bottom: solid $primary;
}
#mid-row {
    height: auto;
    max-height: 15;
    layout: horizontal;
}
#paths-pane {
    width: 3fr;
    height: 15;
    border: solid $primary;
    padding: 0 1;
}
#profile-pane {
    width: 3fr;
    height: 15;
    border: solid $primary;
    padding: 0 1;
}
#output-options-pane {
    width: 4fr;
    height: 15;
    border: solid $primary;
    padding: 0 1;
}
#paths-pane > Contents, #profile-pane > Contents, #output-options-pane > Contents {
    height: 1fr;
    padding: 0;
}
#profile-pane VerticalScroll, #output-options-pane VerticalScroll {
    height: 1fr;
}
.budget-row {
    height: auto;
    margin-top: 1;
    layout: horizontal;
}
#check-input {
    width: 2fr;
    margin-right: 1;
}
#diff-cap-input {
    width: 1fr;
}
.path-lists-row {
    height: 1fr;
    layout: horizontal;
}
.path-col {
    width: 1fr;
    height: 100%;
    padding-right: 1;
}
.opt-row {
    height: auto;
    min-height: 2;
    margin-bottom: 1;
    layout: horizontal;
    align-vertical: middle;
}
.opt-switch {
    width: auto;
}
.opt-desc {
    width: 1fr;
    padding-left: 1;
    color: $text-muted;
}
#selection-summary {
    height: 1;
    padding: 0 1;
    background: $panel;
    color: $text;
    text-style: bold;
}
#cmd-bar {
    height: 4;
    border: solid $accent;
    padding: 0 1;
    layout: horizontal;
    align-vertical: middle;
}
#cli-preview {
    width: 1fr;
    color: $success;
}
.action-btn {
    margin-left: 1;
}
#output-tabs {
    height: 1fr;
}
#findings-toolbar {
    height: 3;
    padding: 0 1;
    layout: horizontal;
    align-vertical: middle;
}
#finding-sev-filter, #finding-tier-filter {
    width: 18;
    margin-right: 1;
}
#finding-search {
    width: 1fr;
    margin-right: 1;
}
#pr-bar {
    height: 3;
    padding: 0 1;
    layout: horizontal;
    align-vertical: middle;
}
#pr-url {
    width: 2fr;
    margin-right: 1;
}
#open-pr-list {
    height: auto;
    max-height: 5;  /* the buttons stay on a screen of 24 rows */
}

#btn-open-pr {
    width: 1fr;
    height: 1;
    min-height: 1;
    border: none;
    margin: 0;
}
#pr-status {
    width: 1fr;
    height: auto;
    max-height: 3;
}
#findings-table {
    height: 2fr;
    min-height: 4;
}
#finding-detail-scroll {
    height: 1fr;
    min-height: 3;
    border-top: solid $primary;
    padding: 0 1;
}
ModalScreen {
    align: center middle;
}
ModalScreen > Vertical {
    max-width: 100%;  /* each dialog, at its own width where the screen has room: macOS Terminal opens at 80 columns */
}
#modal-dialog {
    width: 72;
    height: auto;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
}
#explain-dialog {
    width: 84;
    height: 80%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
}
#explain-scroll {
    height: 1fr;
}
#comment-dialog {
    width: 100;
    height: 85%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
}
#comment-target {
    text-style: bold;
    margin-bottom: 1;
}
#comment-replaced {
    max-height: 14;
    color: $text-muted;
    border-left: solid $primary;
    padding: 0 1;
    margin-bottom: 1;
}
#comment-text {
    height: 1fr;
    min-height: 6;
}
#modal-buttons {
    height: 3;
    margin-top: 1;
    align-horizontal: right;
}
"""
