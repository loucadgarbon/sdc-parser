"""Compare two analyzed record sets (file vs file, or same file under
different params). Identity is (command, unexpanded arguments); active and
expansion differences are the payload."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .model import DetailRecord

DIFF_COLUMNS = [
    "change",
    "command",
    "line_a",
    "line_b",
    "arguments",
    "active_a",
    "active_b",
    "expanded_a",
    "expanded_b",
    "changed_fields",
]


@dataclass
class DiffEntry:
    change: str  # "added" | "removed" | "changed"
    command: str
    key_args: str
    a: DetailRecord | None
    b: DetailRecord | None
    fields: list[str]


def _index(records: list[DetailRecord]):
    idx: OrderedDict[tuple[str, str], list[DetailRecord]] = OrderedDict()
    for r in records:
        idx.setdefault((r.command, r.arguments), []).append(r)
    return idx


def diff_records(
    a_records: list[DetailRecord], b_records: list[DetailRecord]
) -> list[DiffEntry]:
    ia, ib = _index(a_records), _index(b_records)
    keys = list(ia) + [k for k in ib if k not in ia]
    entries: list[DiffEntry] = []
    for key in keys:
        la = list(ia.get(key, []))
        lb = list(ib.get(key, []))
        # phase 1: consume pairs identical in active + expansion
        rest_a: list[DetailRecord] = []
        for ra in la:
            match = next(
                (
                    rb
                    for rb in lb
                    if rb.active == ra.active
                    and rb.arguments_expanded == ra.arguments_expanded
                ),
                None,
            )
            if match is not None:
                lb.remove(match)
            else:
                rest_a.append(ra)
        # phase 2: pair leftovers positionally as "changed"
        n = min(len(rest_a), len(lb))
        for ra, rb in zip(rest_a[:n], lb[:n]):
            fields = []
            if ra.active != rb.active:
                fields.append("active")
            if ra.arguments_expanded != rb.arguments_expanded:
                fields.append("expanded")
            entries.append(DiffEntry("changed", key[0], key[1], ra, rb, fields))
        for ra in rest_a[n:]:
            entries.append(DiffEntry("removed", key[0], key[1], ra, None, []))
        for rb in lb[n:]:
            entries.append(DiffEntry("added", key[0], key[1], None, rb, []))
    return entries


def diff_rows(entries: list[DiffEntry]) -> tuple[list[str], list[list]]:
    rows = []
    for e in entries:
        rows.append(
            [
                e.change,
                e.command,
                e.a.line if e.a else "",
                e.b.line if e.b else "",
                e.key_args,
                e.a.active if e.a else "",
                e.b.active if e.b else "",
                e.a.arguments_expanded if e.a else "",
                e.b.arguments_expanded if e.b else "",
                ", ".join(e.fields),
            ]
        )
    return DIFF_COLUMNS, rows


def entries_json(entries: list[DiffEntry]) -> list[dict]:
    def side(r: DetailRecord | None):
        if r is None:
            return None
        return {
            "file": r.file,
            "line": r.line,
            "active": r.active,
            "arguments_expanded": r.arguments_expanded,
        }

    return [
        {
            "change": e.change,
            "command": e.command,
            "arguments": e.key_args,
            "a": side(e.a),
            "b": side(e.b),
            "changed_fields": e.fields,
        }
        for e in entries
    ]
