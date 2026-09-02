"""Data model for parsed TCL commands and analysis output rows."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    """One word of a TCL command.

    text: inner text (brace/quote content without the delimiters).
    raw: verbatim source slice including delimiters and any {*} prefix.
    kind: "bare" | "brace" | "quote".
    line: line number where the word starts (1-based).
    """

    text: str
    raw: str
    kind: str
    line: int
    expand_prefix: bool = False


@dataclass
class Command:
    words: list[Word]
    line: int
    raw: str

    @property
    def name(self) -> str:
        return self.words[0].text

    @property
    def args(self) -> list[Word]:
        return self.words[1:]


class ParseError(Exception):
    def __init__(self, file: str, line: int, msg: str):
        self.file = file
        self.line = line
        self.msg = msg
        super().__init__(f"{file}:{line}: {msg}")


@dataclass
class DetailRecord:
    file: str
    line: int
    command: str
    arguments: str
    arguments_expanded: str
    expand_status: str
    condition_chain: str
    condition_chain_expanded: str
    loop_context: str
    proc: str
    raw: str
    # branch-activeness: "yes" / "no" / "unknown"
    active: str = ""
    # not exported to the detail sheet; consumed by summarize()
    signature: str = ""
    # (option name | None for positional, display value); "Y" for value-less flags
    arg_items: list[tuple[str | None, str]] = field(default_factory=list)
