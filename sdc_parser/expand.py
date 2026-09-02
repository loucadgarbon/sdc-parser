"""Best-effort static variable environment and $var substitution.

Only statically-known literal values are tracked; [bracket] substitution is
never evaluated here (whole-word [expr ...] is handled by the analyzer).
Bindings created inside a condition/loop/proc are marked conditional, capping
the expand status of anything that uses them at "partial". Each bind and
invalidate records origin/stats so a Variables report can be produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_VAR_RE = re.compile(
    r"\$(?:\{(?P<braced>[^}]+)\}|(?P<name>[A-Za-z0-9_]+(?:::[A-Za-z0-9_]+)*))"
)


@dataclass
class ExpandInfo:
    total: int = 0
    resolved: int = 0
    used_conditional: bool = False
    unresolved: set[str] = field(default_factory=set)

    @property
    def status(self) -> str:
        if self.total == 0:
            return ""
        if self.resolved == 0:
            return "none"
        if self.resolved < self.total or self.used_conditional:
            return "partial"
        return "full"


class VarEnv:
    def __init__(self):
        self._vars: dict[str, tuple[str, bool]] = {}
        # per-name write statistics (bind and invalidate both count as writes)
        self.stats: dict[str, dict] = {}

    def _touch(self, name: str, conditional: bool, origin: str | None):
        st = self.stats.setdefault(
            name,
            {
                "set_count": 0,
                "first_set": None,
                "last_set": None,
                "conditional_ever": False,
            },
        )
        st["set_count"] += 1
        if st["first_set"] is None:
            st["first_set"] = origin
        st["last_set"] = origin
        st["conditional_ever"] = st["conditional_ever"] or conditional

    def bind(self, name: str, value: str, conditional: bool, origin: str | None = None):
        self._vars[name] = (value, bool(conditional))
        self._touch(name, bool(conditional), origin)

    def invalidate(self, name: str, origin: str | None = None):
        existed = self._vars.pop(name, None) is not None
        if existed or origin is not None:
            self._touch(name, True, origin)

    def get(self, name: str) -> tuple[str, bool] | None:
        return self._vars.get(name)

    def snapshot(self) -> dict[str, dict]:
        return {
            name: {"value": value, "conditional": conditional}
            for name, (value, conditional) in sorted(self._vars.items())
        }

    def expand(self, text: str) -> tuple[str, ExpandInfo]:
        info = ExpandInfo()

        def repl(m: re.Match) -> str:
            info.total += 1
            name = m.group("braced") or m.group("name")
            end = m.end()
            # $arr(idx): recognized but arrays are untracked
            if m.group("name") and end < len(text) and text[end] == "(":
                info.unresolved.add(name)
                return m.group(0)
            entry = self._vars.get(name)
            if entry is None:
                info.unresolved.add(name)
                return m.group(0)
            value, conditional = entry
            info.resolved += 1
            if conditional:
                info.used_conditional = True
            return value

        return _VAR_RE.sub(repl, text), info
