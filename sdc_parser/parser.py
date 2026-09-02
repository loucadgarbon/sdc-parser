"""Stage 1: character-level TCL script parser.

Splits a script into Commands made of Words without interpreting control
flow. Line numbers are tracked through braces, quotes, brackets and
backslash-newline continuations, so control-structure bodies can later be
re-parsed with the correct line offset.
"""

from __future__ import annotations

from .model import Command, ParseError, Word

_SEPARATORS = " \t\n;"


def normalize_source(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_script(
    text: str,
    line_offset: int = 1,
    file: str = "<string>",
    tolerant: bool = False,
    warnings: list[str] | None = None,
) -> list[Command]:
    """Parse a TCL script (assumed to use \\n newlines) into commands."""
    sc = _Scanner(text, line_offset, file)
    commands: list[Command] = []
    while not sc.at_end():
        sc.skip_separators()
        if sc.at_end():
            break
        if sc.peek() == "#":
            sc.skip_comment()
            continue
        cmd_pos, cmd_line = sc.pos, sc.line
        try:
            cmd = sc.parse_command()
        except ParseError as exc:
            if not tolerant:
                raise
            if warnings is not None:
                warnings.append(str(exc))
            # resume from the failing command's own line, not from wherever
            # the scanner stopped (an unbalanced quote scans to EOF)
            sc.pos, sc.line = cmd_pos, cmd_line
            sc.skip_to_next_line()
            continue
        if cmd is not None:
            commands.append(cmd)
    return commands


class _Scanner:
    def __init__(self, text: str, line: int, file: str):
        self.s = text
        self.n = len(text)
        self.pos = 0
        self.line = line
        self.file = file

    def at_end(self) -> bool:
        return self.pos >= self.n

    def peek(self) -> str:
        return self.s[self.pos]

    def _error(self, msg: str, line: int | None = None):
        raise ParseError(self.file, self.line if line is None else line, msg)

    def _backslash_newline(self) -> bool:
        return (
            self.s[self.pos] == "\\"
            and self.pos + 1 < self.n
            and self.s[self.pos + 1] == "\n"
        )

    def skip_separators(self):
        while self.pos < self.n:
            c = self.s[self.pos]
            if c == "\n":
                self.line += 1
                self.pos += 1
            elif c in " \t;":
                self.pos += 1
            elif self._backslash_newline():
                self.line += 1
                self.pos += 2
            else:
                return

    def skip_inline_space(self):
        while self.pos < self.n:
            c = self.s[self.pos]
            if c in " \t":
                self.pos += 1
            elif self._backslash_newline():
                self.line += 1
                self.pos += 2
            else:
                return

    def skip_comment(self):
        # pos is at '#' (start of a command); a trailing backslash continues
        # the comment onto the next line (Tcl rule).
        while self.pos < self.n:
            if self._backslash_newline():
                self.line += 1
                self.pos += 2
            elif self.s[self.pos] == "\n":
                return  # newline left for skip_separators
            else:
                self.pos += 1

    def skip_to_next_line(self):
        while self.pos < self.n:
            c = self.s[self.pos]
            self.pos += 1
            if c == "\n":
                self.line += 1
                return

    def parse_command(self) -> Command | None:
        start = self.pos
        words: list[Word] = []
        while True:
            self.skip_inline_space()
            if self.at_end() or self.peek() in "\n;":
                break
            words.append(self.parse_word())
        if not words:
            return None
        raw = self.s[start : self.pos].strip()
        return Command(words=words, line=words[0].line, raw=raw)

    def parse_word(self) -> Word:
        c = self.peek()
        if c == "{":
            if (
                self.s.startswith("{*}", self.pos)
                and self.pos + 3 < self.n
                and self.s[self.pos + 3] not in _SEPARATORS
            ):
                line = self.line
                self.pos += 3
                inner = self.parse_word()
                return Word(
                    text=inner.text,
                    raw="{*}" + inner.raw,
                    kind=inner.kind,
                    line=line,
                    expand_prefix=True,
                )
            return self.parse_brace_word()
        if c == '"':
            return self.parse_quote_word()
        return self.parse_bare_word()

    def parse_brace_word(self) -> Word:
        start = self.pos
        start_line = self.line
        self.pos += 1  # opening '{'
        depth = 1
        while True:
            if self.at_end():
                self._error("unbalanced '{'", start_line)
            c = self.s[self.pos]
            if c == "\\" and self.pos + 1 < self.n:
                if self.s[self.pos + 1] == "\n":
                    self.line += 1
                self.pos += 2
            elif c == "{":
                depth += 1
                self.pos += 1
            elif c == "}":
                depth -= 1
                self.pos += 1
                if depth == 0:
                    break
            elif c == "\n":
                self.line += 1
                self.pos += 1
            else:
                self.pos += 1
        raw = self.s[start : self.pos]
        return Word(text=raw[1:-1], raw=raw, kind="brace", line=start_line)

    def parse_quote_word(self) -> Word:
        start = self.pos
        start_line = self.line
        self.pos += 1  # opening '"'
        while True:
            if self.at_end():
                self._error("unbalanced '\"'", start_line)
            c = self.s[self.pos]
            if c == "\\" and self.pos + 1 < self.n:
                if self.s[self.pos + 1] == "\n":
                    self.line += 1
                self.pos += 2
            elif c == '"':
                self.pos += 1
                break
            elif c == "[":
                self.scan_bracket()
            elif c == "\n":
                self.line += 1
                self.pos += 1
            else:
                self.pos += 1
        raw = self.s[start : self.pos]
        return Word(text=raw[1:-1], raw=raw, kind="quote", line=start_line)

    def parse_bare_word(self) -> Word:
        start = self.pos
        start_line = self.line
        while self.pos < self.n:
            c = self.s[self.pos]
            if c in _SEPARATORS:
                break
            if c == "\\":
                if self._backslash_newline():
                    break  # acts as a space: terminates the word
                self.pos += 2 if self.pos + 1 < self.n else 1
            elif c == "[":
                self.scan_bracket()
            else:
                self.pos += 1
        raw = self.s[start : self.pos]
        return Word(text=raw, raw=raw, kind="bare", line=start_line)

    def scan_bracket(self):
        start_line = self.line
        self.pos += 1  # '['
        depth = 1
        while True:
            if self.at_end():
                self._error("unbalanced '['", start_line)
            c = self.s[self.pos]
            if c == "\\" and self.pos + 1 < self.n:
                if self.s[self.pos + 1] == "\n":
                    self.line += 1
                self.pos += 2
            elif c == "[":
                depth += 1
                self.pos += 1
            elif c == "]":
                depth -= 1
                self.pos += 1
                if depth == 0:
                    return
            elif c == "{":
                self.skip_braces()
            elif c == '"':
                self.skip_quote()
            elif c == "\n":
                self.line += 1
                self.pos += 1
            else:
                self.pos += 1

    def skip_braces(self):
        # pos is at '{'
        start_line = self.line
        depth = 0
        while True:
            if self.at_end():
                self._error("unbalanced '{'", start_line)
            c = self.s[self.pos]
            if c == "\\" and self.pos + 1 < self.n:
                if self.s[self.pos + 1] == "\n":
                    self.line += 1
                self.pos += 2
            elif c == "{":
                depth += 1
                self.pos += 1
            elif c == "}":
                depth -= 1
                self.pos += 1
                if depth == 0:
                    return
            elif c == "\n":
                self.line += 1
                self.pos += 1
            else:
                self.pos += 1

    def skip_quote(self):
        # pos is at '"'
        start_line = self.line
        self.pos += 1
        while True:
            if self.at_end():
                self._error("unbalanced '\"'", start_line)
            c = self.s[self.pos]
            if c == "\\" and self.pos + 1 < self.n:
                if self.s[self.pos + 1] == "\n":
                    self.line += 1
                self.pos += 2
            elif c == '"':
                self.pos += 1
                return
            elif c == "[":
                self.scan_bracket()
            elif c == "\n":
                self.line += 1
                self.pos += 1
            else:
                self.pos += 1
