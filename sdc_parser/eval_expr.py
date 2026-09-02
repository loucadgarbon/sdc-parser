"""Tri-state static evaluator for TCL expr condition strings.

Input is the condition text AFTER $var expansion. Results are True / False /
None (unknown). Any construct outside the supported subset — leftover $refs,
[bracket] substitutions, ternary, shifts, bad syntax — degrades to unknown,
never to an exception.

Hand-rolled on purpose: Python eval() would be an injection vector and gets
TCL semantics wrong anyway (numeric-first comparison, eq/ne, barewords).
"""

from __future__ import annotations

import fnmatch
import re

UNKNOWN = object()

_TRUE_WORDS = {"true", "yes", "on"}
_FALSE_WORDS = {"false", "no", "off"}


class _Unsupported(Exception):
    pass


# -- tri-state logic ------------------------------------------------------


def t_and(a: bool | None, b: bool | None) -> bool | None:
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def t_or(a: bool | None, b: bool | None) -> bool | None:
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def t_not(a: bool | None) -> bool | None:
    return None if a is None else not a


def active_label(a: bool | None) -> str:
    return {True: "yes", False: "no", None: "unknown"}[a]


# -- glob matching for switch ---------------------------------------------


def glob_match(subject: str, pattern: str, nocase: bool = False) -> bool | None:
    # bail out where fnmatch and Tcl `string match` semantics diverge
    if "\\" in pattern or "[!" in pattern:
        return None
    if nocase:
        subject, pattern = subject.lower(), pattern.lower()
    return fnmatch.fnmatchcase(subject, pattern)


# -- tokenizer ------------------------------------------------------------
# tokens: (kind, value, lexeme); kinds: NUM, STR, WORD, UNK, OP

_VARNAME = re.compile(r"[A-Za-z0-9_:]+")
_NUMBER = re.compile(
    r"0[xX][0-9A-Fa-f]+"
    r"|\d+\.\d*(?:[eE][+-]?\d+)?"
    r"|\.\d+(?:[eE][+-]?\d+)?"
    r"|\d+(?:[eE][+-]?\d+)?"
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.:]*")


_EXPR_RE = re.compile(r"\s*expr\s+(.*)$", re.S)
_MAX_EXPR_DEPTH = 5


def _strip_outer_braces(text: str) -> str:
    """Strip one brace layer when the whole text is a single {...} group."""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return text
    depth = 0
    for idx, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[1:-1] if idx == len(text) - 1 else text
    return text


def _tokenize(s: str, depth: int = 0) -> list[tuple]:
    tokens = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        two = s[i : i + 2]
        if two in ("==", "!=", "<=", ">=", "&&", "||"):
            tokens.append(("OP", two, two))
            i += 2
        elif c in "<>!+-*/%()":
            tokens.append(("OP", c, c))
            i += 1
        elif c == '"':
            j = i + 1
            out = []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    out.append({"n": "\n", "t": "\t"}.get(s[j + 1], s[j + 1]))
                    j += 2
                else:
                    out.append(s[j])
                    j += 1
            if j >= n:
                raise _Unsupported("unterminated quote")
            text = "".join(out)
            tokens.append(("STR", text, text))
            i = j + 1
        elif c == "{":
            depth = 1
            j = i + 1
            while j < n and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                raise _Unsupported("unbalanced brace")
            text = s[i + 1 : j - 1]
            tokens.append(("STR", text, text))
            i = j
        elif c == "$":
            j = i + 1
            if j < n and s[j] == "{":
                k = s.find("}", j)
                if k < 0:
                    raise _Unsupported("unbalanced ${")
                j = k + 1
            else:
                m = _VARNAME.match(s, j)
                if not m:
                    raise _Unsupported("bad $ reference")
                j = m.end()
                if j < n and s[j] == "(":
                    k = s.find(")", j)
                    if k < 0:
                        raise _Unsupported("unbalanced paren")
                    j = k + 1
            tokens.append(("UNK", UNKNOWN, s[i:j]))
            i = j
        elif c == "[":
            bdepth = 1
            j = i + 1
            while j < n and bdepth:
                if s[j] == "[":
                    bdepth += 1
                elif s[j] == "]":
                    bdepth -= 1
                j += 1
            if bdepth:
                raise _Unsupported("unbalanced bracket")
            inner = s[i + 1 : j - 1]
            token = ("UNK", UNKNOWN, s[i:j])
            m = _EXPR_RE.match(inner)
            if m and depth < _MAX_EXPR_DEPTH:
                sub = eval_value(m.group(1), _depth=depth + 1)
                if sub is not None:
                    num = _as_num((sub, sub))
                    token = ("NUM", num, sub) if num is not None else ("STR", sub, sub)
            tokens.append(token)
            i = j
        elif c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            lex = _NUMBER.match(s, i).group(0)
            if lex.lower().startswith("0x"):
                val: int | float = int(lex, 16)
            elif any(ch in lex for ch in ".eE"):
                val = float(lex)
            else:
                val = int(lex)
            tokens.append(("NUM", val, lex))
            i += len(lex)
        elif _IDENTIFIER.match(s, i):
            lex = _IDENTIFIER.match(s, i).group(0)
            tokens.append(("WORD", lex, lex))
            i += len(lex)
        else:
            raise _Unsupported(f"unsupported char {c!r}")
    return tokens


# -- value helpers --------------------------------------------------------
# operands are UNKNOWN or (python value, source text)


def _num_text(n) -> str:
    return repr(n) if isinstance(n, float) else str(n)


def _bool_val(t: bool | None):
    if t is None:
        return UNKNOWN
    return (1, "1") if t else (0, "0")


def _as_num(v):
    if v is UNKNOWN:
        return None
    val, _text = v
    if isinstance(val, (int, float)):
        return val
    s = val.strip()
    for parse in (lambda x: int(x, 0), int, float):
        try:
            return parse(s)
        except ValueError:
            continue
    return None


def _truth(v) -> bool | None:
    if v is UNKNOWN:
        return None
    num = _as_num(v)
    if num is not None:
        return num != 0
    s = v[1].strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None


def _compare_eq(left, right, op):
    if left is UNKNOWN or right is UNKNOWN:
        return UNKNOWN
    if op in ("eq", "ne"):
        res = left[1] == right[1]
        return _bool_val(res if op == "eq" else not res)
    ln, rn = _as_num(left), _as_num(right)
    res = (ln == rn) if (ln is not None and rn is not None) else (left[1] == right[1])
    return _bool_val(res if op == "==" else not res)


def _compare_rel(left, right, op):
    if left is UNKNOWN or right is UNKNOWN:
        return UNKNOWN
    ln, rn = _as_num(left), _as_num(right)
    if ln is not None and rn is not None:
        a, b = ln, rn
    else:
        a, b = left[1], right[1]
    if op == "<":
        return _bool_val(a < b)
    if op == "<=":
        return _bool_val(a <= b)
    if op == ">":
        return _bool_val(a > b)
    return _bool_val(a >= b)


def _arith(left, right, op):
    ln, rn = _as_num(left), _as_num(right)
    if ln is None or rn is None:
        return UNKNOWN
    if op == "+":
        res = ln + rn
    elif op == "-":
        res = ln - rn
    elif op == "*":
        res = ln * rn
    elif op == "/":
        if rn == 0:
            return UNKNOWN
        # Tcl 8.5+ integer division floors
        res = ln // rn if isinstance(ln, int) and isinstance(rn, int) else ln / rn
    else:  # %
        if rn == 0:
            return UNKNOWN
        res = ln % rn
    return (res, _num_text(res))


# -- recursive-descent parser --------------------------------------------
# expr := or; or := and ("||" and)*; and := eq ("&&" eq)*;
# eq := rel (("=="|"!="|"eq"|"ne") rel)*; rel := add (("<"|"<="|">"|">=") add)*;
# add := mul (("+"|"-") mul)*; mul := unary (("*"|"/"|"%") unary)*;
# unary := ("!"|"-"|"+") unary | primary;
# primary := NUM | STR | WORD | UNK | "(" expr ")"


class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def _peek_op(self, *ops):
        if self.i < len(self.toks):
            kind, _val, lex = self.toks[self.i]
            if kind == "OP" and lex in ops:
                return lex
            if kind == "WORD" and lex in ("eq", "ne") and lex in ops:
                return lex
        return None

    def _next(self):
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def parse(self):
        value = self.parse_or()
        if self.i != len(self.toks):
            raise _Unsupported("trailing tokens")
        return value

    def parse_or(self):
        left = self.parse_and()
        while self._peek_op("||"):
            self._next()
            right = self.parse_and()
            left = _bool_val(t_or(_truth(left), _truth(right)))
        return left

    def parse_and(self):
        left = self.parse_equality()
        while self._peek_op("&&"):
            self._next()
            right = self.parse_equality()
            left = _bool_val(t_and(_truth(left), _truth(right)))
        return left

    def parse_equality(self):
        left = self.parse_rel()
        while True:
            op = self._peek_op("==", "!=", "eq", "ne")
            if not op:
                return left
            self._next()
            left = _compare_eq(left, self.parse_rel(), op)

    def parse_rel(self):
        left = self.parse_add()
        while True:
            op = self._peek_op("<", "<=", ">", ">=")
            if not op:
                return left
            self._next()
            left = _compare_rel(left, self.parse_add(), op)

    def parse_add(self):
        left = self.parse_mul()
        while True:
            op = self._peek_op("+", "-")
            if not op:
                return left
            self._next()
            left = _arith(left, self.parse_mul(), op)

    def parse_mul(self):
        left = self.parse_unary()
        while True:
            op = self._peek_op("*", "/", "%")
            if not op:
                return left
            self._next()
            left = _arith(left, self.parse_unary(), op)

    def parse_unary(self):
        op = self._peek_op("!", "-", "+")
        if op:
            self._next()
            value = self.parse_unary()
            if op == "!":
                return _bool_val(t_not(_truth(value)))
            num = _as_num(value)
            if num is None:
                return UNKNOWN
            num = -num if op == "-" else num
            return (num, _num_text(num))
        return self.parse_primary()

    def parse_primary(self):
        if self.i >= len(self.toks):
            raise _Unsupported("unexpected end of expression")
        kind, value, lex = self._next()
        if kind == "OP":
            if lex == "(":
                inner = self.parse_or()
                if not self._peek_op(")"):
                    raise _Unsupported("missing )")
                self._next()
                return inner
            raise _Unsupported(f"unexpected operator {lex}")
        if kind == "UNK":
            return UNKNOWN
        return (value, lex)


def eval_expr(text: str) -> bool | None:
    """Evaluate an expanded condition string to True/False/None(unknown)."""
    try:
        tokens = _tokenize(text.replace("\\\n", " "))
        if not tokens:
            return None
        return _truth(_Parser(tokens).parse())
    except Exception:
        return None


def eval_value(text: str, _depth: int = 0) -> str | None:
    """Evaluate an expanded expression to its string VALUE (not truthiness).

    Used for `[expr {...}]` command substitution. Returns None when the
    expression contains anything unresolvable.
    """
    try:
        tokens = _tokenize(_strip_outer_braces(text).replace("\\\n", " "), _depth)
        if not tokens:
            return None
        result = _Parser(tokens).parse()
        if result is UNKNOWN:
            return None
        value, lexeme = result
        if isinstance(value, (int, float)):
            return _num_text(value)
        return value
    except Exception:
        return None
