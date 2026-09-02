"""Build per-command tables: one table per command name, one column per
argument. Option columns are named after the option (-name, -period, ...);
positional arguments get arg1, arg2, ...; value-less flags hold "Y".
Repeated options (e.g. -group) are joined with "; " in one cell.
"""

from __future__ import annotations

from collections import OrderedDict

from .model import DetailRecord

CONTEXT_COLUMNS = [
    "arguments_expanded",
    "expand_status",
    "active",
    "condition_chain",
    "condition_chain_expanded",
    "loop_context",
    "proc",
    "raw",
]


def build_command_tables(
    records: list[DetailRecord], order: list[str] | None = None
) -> "OrderedDict[str, dict]":
    """Return {command: {"columns": [...], "rows": [[...], ...]}}.

    `order` (e.g. summary order) fixes the table sequence; unknown names are
    appended in first-seen order.
    """
    groups: OrderedDict[str, list[DetailRecord]] = OrderedDict()
    for r in records:
        groups.setdefault(r.command, []).append(r)
    if order:
        ordered = [n for n in order if n in groups]
        ordered += [n for n in groups if n not in ordered]
    else:
        ordered = list(groups)

    # a "file" column appears only when records span more than one file
    # (e.g. via source following or multiple inputs)
    multi = len({r.file for r in records}) > 1

    tables: OrderedDict[str, dict] = OrderedDict()
    for name in ordered:
        recs = groups[name]
        opt_cols: list[str] = []
        max_pos = 0
        for r in recs:
            pos = 0
            for opt, _ in r.arg_items:
                if opt is None:
                    pos += 1
                elif opt not in opt_cols:
                    opt_cols.append(opt)
            max_pos = max(max_pos, pos)
        pos_cols = [f"arg{i}" for i in range(1, max_pos + 1)]
        lead_cols = ["file", "line"] if multi else ["line"]
        columns = lead_cols + opt_cols + pos_cols + CONTEXT_COLUMNS

        rows = []
        for r in recs:
            opts: dict[str, list[str]] = {}
            positionals: list[str] = []
            for opt, value in r.arg_items:
                if opt is None:
                    positionals.append(value)
                else:
                    opts.setdefault(opt, []).append(value)
            row: list = [r.file, r.line] if multi else [r.line]
            row += ["; ".join(opts.get(c, [])) for c in opt_cols]
            row += [
                positionals[i] if i < len(positionals) else "" for i in range(max_pos)
            ]
            row += [getattr(r, c) for c in CONTEXT_COLUMNS]
            rows.append(row)
        tables[name] = {"columns": columns, "rows": rows}
    return tables
