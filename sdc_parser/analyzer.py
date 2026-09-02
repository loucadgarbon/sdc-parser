"""Stage 2: context walk over parsed commands.

Recognizes if/elseif/else, foreach/for/while, proc, switch and catch,
recursing into brace bodies while maintaining condition/loop/proc stacks,
and emits one DetailRecord per command instance.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .eval_expr import (
    active_label,
    eval_expr,
    eval_value,
    glob_match,
    t_and,
    t_not,
    t_or,
)
from .expand import VarEnv
from .model import Command, DetailRecord, Word
from .parser import normalize_source, parse_script

LOOP_COMMANDS = {"foreach", "for", "while"}

_NAME_RE = re.compile(r"^[A-Za-z0-9_:]+$")
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class _Ctx:
    file: str
    conds: list[tuple[str, str]] = field(default_factory=list)
    loops: list[str] = field(default_factory=list)
    procs: list[str] = field(default_factory=list)
    # tri-state branch activeness: True=yes, False=no, None=unknown
    active: bool | None = True
    # depth of loops whose bodies may run 0..N times (unrolled loops excluded)
    real_loop_depth: int = 0


def _collapse(text: str) -> str:
    text = text.replace("\\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _display_word(w: Word, abbrev: bool = False) -> str:
    if w.kind == "brace":
        body = "{...}" if abbrev else "{" + _collapse(w.text) + "}"
        return ("{*}" if w.expand_prefix else "") + body
    return _collapse(w.raw)


def _is_name(w: Word) -> bool:
    return w.kind == "bare" and bool(_NAME_RE.match(w.text))


_EXPR_INNER_RE = re.compile(r"expr\s+(.*)$", re.S)


def _split_tcl_list(text: str) -> list[str] | None:
    """Split a TCL list into elements, honoring {} and "" grouping.
    Returns None on backslashes or unbalanced groups (fall back, don't guess)."""
    if "\\" in text:
        return None
    items = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "{":
            depth = 1
            j = i + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                return None
            items.append(text[i + 1 : j - 1])
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            if j >= n:
                return None
            items.append(text[i + 1 : j])
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            items.append(text[i:j])
            i = j
    return items


def _whole_expr_bracket(text: str) -> str | None:
    """Return the operand text when `text` is exactly one `[expr ...]`."""
    if not (text.startswith("[") and text.endswith("]")):
        return None
    depth = 0
    for idx, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and idx != len(text) - 1:
                return None
    if depth != 0:
        return None
    m = _EXPR_INNER_RE.match(text[1:-1].strip())
    return m.group(1) if m else None


def _is_option(w: Word) -> bool:
    t = w.text
    return w.kind == "bare" and t.startswith("-") and len(t) > 1 and not _NUM_RE.match(t)


def _signature(cmd: Command) -> str:
    parts = [cmd.name]
    for w in cmd.args:
        parts.append(w.text if _is_option(w) else "<arg>")
    return " ".join(parts)


def _arg_items(cmd: Command, displays: list[str]) -> list[tuple[str | None, str]]:
    """Pair -option names with their values; value-less flags get "Y"."""
    items: list[tuple[str | None, str]] = []
    args = cmd.args
    i = 0
    while i < len(args):
        w = args[i]
        if _is_option(w):
            if i + 1 < len(args) and not _is_option(args[i + 1]):
                items.append((w.text, displays[i + 1]))
                i += 2
            else:
                items.append((w.text, "Y"))
                i += 1
        else:
            items.append((None, displays[i]))
            i += 1
    return items


def _body_indices(cmd: Command) -> set[int]:
    """Indices (into cmd.args) of script-body words, abbreviated in display."""
    name = cmd.name
    args = cmd.args
    idx: set[int] = set()
    if name == "if":
        i = 0
        first = True
        while i < len(args):
            t = args[i].text
            if first or t == "elseif":
                if not first:
                    i += 1
                i += 1  # condition
                if i < len(args) and args[i].text == "then":
                    i += 1
                if i < len(args):
                    idx.add(i)
                    i += 1
                first = False
            elif t == "else":
                i += 1
                if i < len(args):
                    idx.add(i)
                    i += 1
            else:  # implicit else
                idx.add(i)
                i += 1
    elif name in ("foreach", "while"):
        if args:
            idx.add(len(args) - 1)
    elif name == "for":
        for j in (0, 2, 3):
            if j < len(args):
                idx.add(j)
    elif name == "proc":
        if len(args) >= 3:
            idx.add(2)
    elif name == "catch":
        if args:
            idx.add(0)
    elif name == "switch":
        if args:
            idx.add(len(args) - 1)
    return idx


class Analyzer:
    def __init__(
        self,
        tolerant: bool = False,
        follow_source: bool = True,
        unroll: bool = False,
        max_unroll: int = 100,
        encoding: str = "utf-8-sig",
    ):
        self.tolerant = tolerant
        self.follow_source = follow_source
        self.unroll = unroll
        self.max_unroll = max_unroll
        self.encoding = encoding
        self.records: list[DetailRecord] = []
        self.env = VarEnv()
        self.warnings: list[str] = []
        self.files: list[str] = []
        # unresolved $name uses: name -> ["file:line", ...]
        self.unresolved: dict[str, list[str]] = {}
        self._source_stack: list[str] = []
        self._unroll_budget = 1000

    def _note(self, info, file: str, line: int):
        for name in info.unresolved:
            uses = self.unresolved.setdefault(name, [])
            loc = f"{file}:{line}"
            if loc not in uses:
                uses.append(loc)

    def analyze_file(self, path: str, text: str):
        text = normalize_source(text)
        cmds = parse_script(
            text, 1, file=path, tolerant=self.tolerant, warnings=self.warnings
        )
        if path not in self.files:
            self.files.append(path)
        self._source_stack.append(os.path.normcase(os.path.abspath(path)))
        try:
            self._walk(cmds, _Ctx(file=path))
        finally:
            self._source_stack.pop()

    def load_params_file(self, path: str, text: str):
        """Load a TCL parameter file of `set NAME VALUE` / `define NAME VALUE`
        commands as initial, non-conditional variable bindings. Emits no
        detail records; script-internal `set` executed later overrides."""
        cmds = parse_script(
            normalize_source(text),
            1,
            file=path,
            tolerant=self.tolerant,
            warnings=self.warnings,
        )
        for cmd in cmds:
            if (
                cmd.name in ("set", "define")
                and len(cmd.args) == 2
                and _is_name(cmd.args[0])
            ):
                value, ok, _ = self._resolve_value(cmd.args[1])
                origin = f"{path}:{cmd.line}"
                if ok:
                    self.env.bind(cmd.args[0].text, value, False, origin=origin)
                else:
                    self.env.invalidate(cmd.args[0].text, origin=origin)
            else:
                self.warnings.append(
                    f"{path}:{cmd.line}: ignored non-set/define command "
                    f"'{cmd.name}' in params file"
                )

    # -- walk ------------------------------------------------------------

    def _walk(self, commands: list[Command], ctx: _Ctx):
        for cmd in commands:
            self._emit(cmd, ctx)
            name = cmd.name
            if name == "if":
                self._handle_if(cmd, ctx)
            elif name in LOOP_COMMANDS:
                self._handle_loop(cmd, ctx)
            elif name == "proc":
                self._handle_proc(cmd, ctx)
            elif name == "switch":
                self._handle_switch(cmd, ctx)
            elif name == "catch":
                self._handle_catch(cmd, ctx)
            elif name == "source":
                self._handle_source(cmd, ctx)
            else:
                self._update_env(cmd, ctx)

    def _parse_body(self, body: Word, ctx: _Ctx) -> list[Command]:
        return parse_script(
            body.text,
            body.line,
            file=ctx.file,
            tolerant=self.tolerant,
            warnings=self.warnings,
        )

    def _emit(self, cmd: Command, ctx: _Ctx):
        bodies = _body_indices(cmd)
        parts = []
        for i, w in enumerate(cmd.args):
            abbrev = i in bodies or (w.kind == "brace" and "\n" in w.text)
            parts.append(_display_word(w, abbrev))
        arguments = " ".join(parts)
        expanded, info = self.env.expand(arguments)
        self._note(info, ctx.file, cmd.line)
        chain_orig = " > ".join(c[0] for c in ctx.conds)
        chain_exp = " > ".join(c[1] for c in ctx.conds)
        self.records.append(
            DetailRecord(
                active=active_label(ctx.active),
                file=ctx.file,
                line=cmd.line,
                command=cmd.name,
                arguments=arguments,
                arguments_expanded=expanded,
                expand_status=info.status,
                condition_chain=chain_orig,
                condition_chain_expanded=chain_exp,
                loop_context=" > ".join(ctx.loops),
                proc=" > ".join(ctx.procs),
                raw=cmd.raw,
                signature=_signature(cmd),
                arg_items=_arg_items(cmd, parts),
            )
        )

    # -- control structures ---------------------------------------------

    def _eval_cond(self, text: str, ctx: _Ctx, line: int) -> bool | None:
        expanded, info = self.env.expand(text.replace("\\\n", " "))
        self._note(info, ctx.file, line)
        if info.used_conditional:
            # a conditionally-bound value cannot soundly decide a branch
            return None
        return eval_expr(expanded)

    def _walk_body(self, body: Word, ctx: _Ctx, active: bool | None):
        if body.kind != "brace":
            return
        saved = ctx.active
        ctx.active = active
        try:
            self._walk(self._parse_body(body, ctx), ctx)
        finally:
            ctx.active = saved

    def _recurse_cond(self, body: Word, ctx: _Ctx, label: str, active: bool | None):
        if body.kind != "brace":
            return
        expanded, _ = self.env.expand(label)
        ctx.conds.append((label, expanded))
        try:
            self._walk_body(body, ctx, active)
        finally:
            ctx.conds.pop()

    def _handle_if(self, cmd: Command, ctx: _Ctx):
        args = cmd.args
        parent = ctx.active
        prior: bool | None = False  # tri-state "some earlier branch taken"
        i = 0
        first = True
        while i < len(args):
            t = args[i].text
            if first or t == "elseif":
                if not first:
                    i += 1
                if i >= len(args):
                    return
                cond = args[i]
                i += 1
                if i < len(args) and args[i].text == "then":
                    i += 1
                if i >= len(args):
                    return
                kw = "if" if first else "elseif"
                # evaluate before recursing: the body may rebind variables
                c = self._eval_cond(cond.text, ctx, cond.line)
                self._recurse_cond(
                    args[i],
                    ctx,
                    f"{kw} {{{_collapse(cond.text)}}}",
                    active=t_and(parent, t_and(t_not(prior), c)),
                )
                prior = t_or(prior, c)
                i += 1
                first = False
            elif t == "else":
                i += 1
                if i < len(args):
                    self._recurse_cond(
                        args[i], ctx, "else", active=t_and(parent, t_not(prior))
                    )
                    i += 1
            else:  # implicit else: if {c} {b1} {b2}
                self._recurse_cond(
                    args[i], ctx, "else", active=t_and(parent, t_not(prior))
                )
                i += 1

    def _handle_loop(self, cmd: Command, ctx: _Ctx):
        args = cmd.args
        if not args:
            return
        name = cmd.name
        if name == "foreach" and self._try_unroll_foreach(cmd, ctx):
            return
        parent = ctx.active
        if name == "for" and len(args) >= 4:
            header_words = args[:3]
        else:
            header_words = args[:-1]
        header = name + " " + " ".join(_display_word(w) for w in header_words)
        if name == "foreach" and parent is not False:
            # loop variables are deliberately left unbound; the loop-context
            # column already shows the full value list
            for j in range(0, max(len(args) - 1, 0), 2):
                for var in args[j].text.split():
                    if _NAME_RE.match(var):
                        self.env.invalidate(var, origin=f"{ctx.file}:{cmd.line}")
        ctx.loops.append(header)
        try:
            if name == "for" and len(args) >= 4:
                self._walk_body(args[0], ctx, parent)  # init runs exactly once
                c = self._eval_cond(args[1].text, ctx, args[1].line)
                body_active = False if c is False else parent
                ctx.real_loop_depth += 1
                try:
                    self._walk_body(args[2], ctx, body_active)  # next
                    self._walk_body(args[3], ctx, body_active)
                finally:
                    ctx.real_loop_depth -= 1
            elif name == "while":
                c = (
                    self._eval_cond(args[0].text, ctx, args[0].line)
                    if len(args) >= 2
                    else None
                )
                body_active = False if c is False else parent
                ctx.real_loop_depth += 1
                try:
                    self._walk_body(args[-1], ctx, body_active)
                finally:
                    ctx.real_loop_depth -= 1
            else:  # foreach: body reached whenever the loop is
                ctx.real_loop_depth += 1
                try:
                    self._walk_body(args[-1], ctx, parent)
                finally:
                    ctx.real_loop_depth -= 1
        finally:
            ctx.loops.pop()

    def _try_unroll_foreach(self, cmd: Command, ctx: _Ctx) -> bool:
        """Unroll a foreach with statically-known lists: walk the body once
        per iteration with the loop vars bound. Returns False to fall back.
        Note: break/continue are ignored (records over-approximate)."""
        if not self.unroll or ctx.active is False:
            return False
        args = cmd.args
        if len(args) < 3 or len(args) % 2 == 0 or args[-1].kind != "brace":
            return False
        pairs: list[tuple[list[str], list[str]]] = []
        for j in range(0, len(args) - 1, 2):
            varnames = args[j].text.split()
            if not varnames or not all(_NAME_RE.match(v) for v in varnames):
                return False
            value, ok, used_cond = self._resolve_value(args[j + 1], ctx)
            if not ok or used_cond:
                return False
            values = _split_tcl_list(value)
            if values is None:
                return False
            pairs.append((varnames, values))
        iters = max(
            -(-len(values) // len(varnames)) for varnames, values in pairs
        )
        if iters == 0:
            return True  # empty list: body never runs, vars untouched
        if iters > self.max_unroll or iters > self._unroll_budget:
            self.warnings.append(
                f"{ctx.file}:{cmd.line}: foreach with {iters} iterations exceeds "
                f"the unroll limit; not unrolled"
            )
            return False
        self._unroll_budget -= iters
        header = "foreach " + " ".join(_display_word(w) for w in args[:-1])
        conditional = bool(ctx.procs) or (ctx.active is not True)
        origin = f"{ctx.file}:{cmd.line}"
        body = args[-1]
        for k in range(iters):
            assigns = []
            for varnames, values in pairs:
                for vi, var in enumerate(varnames):
                    idx = k * len(varnames) + vi
                    val = values[idx] if idx < len(values) else ""
                    self.env.bind(var, val, conditional, origin=origin)
                    assigns.append(f"{var}={val[:40]}")
            ctx.loops.append(f"{header} [{' '.join(assigns)}]")
            try:
                self._walk_body(body, ctx, ctx.active)
            finally:
                ctx.loops.pop()
        # per Tcl semantics the last iteration's bindings remain visible
        return True

    def _handle_proc(self, cmd: Command, ctx: _Ctx):
        args = cmd.args
        if len(args) < 3 or args[2].kind != "brace":
            return
        ctx.procs.append(args[0].text)
        try:
            # proc bodies run only when called; activeness is inherited and
            # the proc column carries the caller signal
            self._walk_body(args[2], ctx, ctx.active)
        finally:
            ctx.procs.pop()

    def _handle_switch(self, cmd: Command, ctx: _Ctx):
        args = cmd.args
        i = 0
        mode = "glob"  # Tcl default
        nocase = False
        while i < len(args) and args[i].kind == "bare" and args[i].text.startswith("-"):
            t = args[i].text
            if t in ("-exact", "-glob", "-regexp"):
                mode = t[1:]
            elif t == "-nocase":
                nocase = True
            i += 1
            if t == "--":
                break
        if i >= len(args):
            return
        subject_word = args[i]
        subject = _display_word(subject_word)
        subj_val, subj_ok, subj_cond = self._resolve_value(subject_word, ctx)
        subj_known = subj_ok and not subj_cond
        rest = args[i + 1 :]
        if len(rest) == 1 and rest[0].kind == "brace":
            pair_cmds = parse_script(
                rest[0].text,
                rest[0].line,
                file=ctx.file,
                tolerant=True,
                warnings=self.warnings,
            )
            flat = [w for c in pair_cmds for w in c.words]
        else:
            flat = rest

        parent = ctx.active
        prior: bool | None = False  # some earlier arm taken
        pending: bool | None = False  # matches of preceding "-" fallthrough arms
        for j in range(0, len(flat) - 1, 2):
            pat, body = flat[j], flat[j + 1]
            is_default = pat.text == "default"
            if is_default or not subj_known:
                match: bool | None = None
            elif mode == "exact":
                match = (
                    subj_val.lower() == pat.text.lower()
                    if nocase
                    else subj_val == pat.text
                )
            elif mode == "glob":
                match = glob_match(subj_val, pat.text, nocase)
            else:  # -regexp: not statically evaluated
                match = None
            if body.kind == "bare" and body.text == "-":
                # fallthrough: this pattern's match belongs to the next body
                if not is_default:
                    pending = t_or(pending, match)
                else:
                    pending = None if prior is None else t_not(prior)
                continue
            if body.kind != "brace":
                if not is_default:
                    prior = t_or(prior, t_or(pending, match))
                pending = False
                continue
            if is_default:
                own = t_or(pending, t_not(prior))
                label = f"switch {subject} default"
                self._recurse_cond(body, ctx, label, active=t_and(parent, own))
            else:
                own = t_or(pending, match)
                label = f"switch {subject} == {pat.text}"
                self._recurse_cond(
                    body, ctx, label, active=t_and(parent, t_and(t_not(prior), own))
                )
                prior = t_or(prior, own)
            pending = False

    def _handle_catch(self, cmd: Command, ctx: _Ctx):
        args = cmd.args
        if args and args[0].kind == "brace":
            self._walk_body(args[0], ctx, ctx.active)
        if len(args) >= 2 and _is_name(args[1]) and ctx.active is not False:
            self.env.invalidate(args[1].text, origin=f"{ctx.file}:{cmd.line}")

    def _handle_source(self, cmd: Command, ctx: _Ctx):
        """Recursively analyze a sourced file, sharing env and context.
        Dead branches (active is False) are not followed, mirroring env gating."""
        if not self.follow_source or ctx.active is False or not cmd.args:
            return
        w = cmd.args[-1]  # tolerate `source -encoding enc path`
        value, ok, used_cond = self._resolve_value(w, ctx)
        loc = f"{ctx.file}:{cmd.line}"
        if not ok:
            self.warnings.append(
                f"{loc}: cannot resolve source path '{_display_word(w)}'; not followed"
            )
            return
        if used_cond:
            self.warnings.append(
                f"{loc}: source path '{value}' resolved from a conditional binding"
            )
        p = Path(value)
        if not p.is_absolute():
            p = Path(ctx.file).parent / p
        key = os.path.normcase(os.path.abspath(str(p)))
        if key in self._source_stack:
            self.warnings.append(f"{loc}: source cycle for '{p}'; not followed")
            return
        try:
            text = p.read_text(encoding=self.encoding, errors="replace")
        except OSError as exc:
            self.warnings.append(f"{loc}: cannot read sourced file: {exc}")
            return
        display = str(p)
        if display not in self.files:
            self.files.append(display)
        sub = parse_script(
            normalize_source(text),
            1,
            file=display,
            tolerant=self.tolerant,
            warnings=self.warnings,
        )
        self._source_stack.append(key)
        # share the parent's stacks/env so conditions, loops and bindings flow
        # through the sourced content
        child = _Ctx(
            file=display,
            conds=ctx.conds,
            loops=ctx.loops,
            procs=ctx.procs,
            active=ctx.active,
            real_loop_depth=ctx.real_loop_depth,
        )
        try:
            self._walk(sub, child)
        finally:
            self._source_stack.pop()

    # -- variable environment --------------------------------------------

    def _resolve_value(self, w: Word, ctx: _Ctx | None = None) -> tuple[str, bool, bool]:
        """Return (value, resolvable, used_conditional_binding)."""
        if w.kind == "brace":
            text = w.text
            used_cond = False
        else:
            text, info = self.env.expand(w.text)
            used_cond = info.used_conditional
            if ctx is not None:
                self._note(info, ctx.file, w.line)
        if "$" in text or "[" in text:
            inner = _whole_expr_bracket(text)
            if inner is not None:
                value = eval_value(inner)
                if value is not None:
                    return value, True, used_cond
            return text, False, used_cond
        return text, True, used_cond

    def _update_env(self, cmd: Command, ctx: _Ctx):
        if ctx.active is False:
            # dead branches must neither bind nor invalidate
            return
        name = cmd.name
        args = cmd.args
        # inside a proven-taken branch, conds alone no longer force
        # conditional; unrolled loops don't count as loops (each iteration's
        # body runs exactly once)
        conditional = bool(ctx.real_loop_depth or ctx.procs) or (
            ctx.active is not True
        )
        in_loop = ctx.real_loop_depth > 0
        origin = f"{ctx.file}:{cmd.line}"

        if name == "set" and len(args) == 2 and _is_name(args[0]):
            var = args[0].text
            value, ok, used_cond = self._resolve_value(args[1], ctx)
            if ok:
                self.env.bind(var, value, conditional or used_cond, origin=origin)
            else:
                self.env.invalidate(var, origin=origin)
        elif name == "unset":
            for w in args:
                if _is_name(w):
                    self.env.invalidate(w.text, origin=origin)
        elif name == "incr" and args and _is_name(args[0]):
            var = args[0].text
            entry = self.env.get(var)
            step = 1
            step_ok = True
            if len(args) >= 2:
                try:
                    step = int(args[1].text)
                except ValueError:
                    step_ok = False
            if in_loop or entry is None or not step_ok:
                self.env.invalidate(var, origin=origin)
            else:
                value, was_cond = entry
                try:
                    self.env.bind(
                        var, str(int(value) + step), conditional or was_cond,
                        origin=origin,
                    )
                except ValueError:
                    self.env.invalidate(var, origin=origin)
        elif name in ("append", "lappend") and args and _is_name(args[0]):
            var = args[0].text
            if in_loop:
                self.env.invalidate(var, origin=origin)
                return
            entry = self.env.get(var)
            base, was_cond = entry if entry else ("", False)
            pieces = []
            ok_all = True
            used_cond = False
            for w in args[1:]:
                value, ok, uc = self._resolve_value(w, ctx)
                ok_all &= ok
                used_cond |= uc
                pieces.append(value)
            if not ok_all:
                self.env.invalidate(var, origin=origin)
                return
            if name == "append":
                new_value = base + "".join(pieces)
            else:
                new_value = " ".join(([base] if base else []) + pieces)
            self.env.bind(
                var, new_value, conditional or was_cond or used_cond, origin=origin
            )
        elif name == "gets" and len(args) == 2 and _is_name(args[1]):
            self.env.invalidate(args[1].text, origin=origin)


    # -- variables report -------------------------------------------------

    def variables_report(self) -> list[dict]:
        """One row per variable ever written or referenced-unresolved.
        Sorted so the variables worth defining (-D/--params) come first."""
        rows = []
        for name in set(self.env.stats) | set(self.unresolved):
            entry = self.env.get(name)
            st = self.env.stats.get(name, {})
            uses = self.unresolved.get(name, [])
            lines = ", ".join(uses[:20])
            if len(uses) > 20:
                lines += f", +{len(uses) - 20} more"
            rows.append(
                {
                    "name": name,
                    "value": entry[0] if entry else "",
                    "conditional": "yes" if entry and entry[1] else "",
                    "set_count": st.get("set_count", 0),
                    "first_set": st.get("first_set") or "",
                    "last_set": st.get("last_set") or "",
                    "unresolved_uses": len(uses),
                    "unresolved_lines": lines,
                }
            )
        rows.sort(key=lambda r: (-r["unresolved_uses"], r["name"]))
        return rows


# -- summary -------------------------------------------------------------


def summarize(records: list[DetailRecord]) -> list[dict]:
    groups: dict[str, dict] = {}
    multi = len({r.file for r in records}) > 1
    for r in records:
        g = groups.setdefault(
            r.command,
            {
                "count": 0,
                "lines": [],
                "sigs": [],
                "conds": [],
                "active": {"yes": 0, "no": 0, "unknown": 0},
            },
        )
        g["count"] += 1
        g["active"][r.active or "unknown"] += 1
        g["lines"].append(f"{r.file}:{r.line}" if multi else str(r.line))
        if r.signature not in g["sigs"]:
            g["sigs"].append(r.signature)
        cond = r.condition_chain or "(top)"
        if cond not in g["conds"]:
            g["conds"].append(cond)
    rows = []
    for name in sorted(groups, key=lambda n: (-groups[n]["count"], n)):
        g = groups[name]
        rows.append(
            {
                "command": name,
                "count": g["count"],
                "active": " / ".join(
                    f"{n} {k}" for k, n in g["active"].items() if n
                ),
                "lines": ", ".join(g["lines"]),
                "signatures": " | ".join(g["sigs"]),
                "conditions": "\n".join(g["conds"]),
            }
        )
    return rows
