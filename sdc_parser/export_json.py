"""JSON export: one machine-readable file with summary, per-command records,
variables and unresolved references. Deterministic (no timestamp) so outputs
can be diffed."""

from __future__ import annotations

import json

from .model import DetailRecord

SCHEMA_VERSION = 1


def _record_dict(r: DetailRecord) -> dict:
    options: dict[str, list[str]] = {}
    positionals: list[str] = []
    for opt, value in r.arg_items:
        if opt is None:
            positionals.append(value)
        else:
            options.setdefault(opt, []).append(value)
    return {
        "file": r.file,
        "line": r.line,
        "arguments": r.arguments,
        "arguments_expanded": r.arguments_expanded,
        "expand_status": r.expand_status,
        "active": r.active,
        "condition_chain": r.condition_chain,
        "condition_chain_expanded": r.condition_chain_expanded,
        "loop_context": r.loop_context,
        "proc": r.proc,
        "raw": r.raw,
        "options": options,
        "positionals": positionals,
    }


def build_json_document(
    *,
    records: list[DetailRecord],
    variables_rows: list[dict],
    env,
    files: list[str],
    unresolved: dict[str, list[str]],
    version: str,
    order: list[str] | None = None,
    diff: dict | None = None,
) -> dict:
    commands: dict[str, list[dict]] = {}
    summary: dict[str, dict] = {}
    for r in records:
        commands.setdefault(r.command, []).append(_record_dict(r))
        s = summary.setdefault(
            r.command,
            {
                "command": r.command,
                "count": 0,
                "active": {"yes": 0, "no": 0, "unknown": 0},
                "lines": [],
                "signatures": [],
                "conditions": [],
            },
        )
        s["count"] += 1
        s["active"][r.active or "unknown"] += 1
        s["lines"].append({"file": r.file, "line": r.line})
        if r.signature not in s["signatures"]:
            s["signatures"].append(r.signature)
        cond = r.condition_chain or "(top)"
        if cond not in s["conditions"]:
            s["conditions"].append(cond)

    names = order or list(summary)
    names = [n for n in names if n in summary] + [
        n for n in summary if n not in names
    ]
    variables = [
        {**row, "unresolved_lines": unresolved.get(row["name"], [])}
        for row in variables_rows
    ]
    doc = {
        "tclscan": version,
        "schema_version": SCHEMA_VERSION,
        "files": files,
        "params": env.snapshot(),
        "summary": [summary[n] for n in names],
        "commands": {n: commands[n] for n in names},
        "variables": variables,
        "unresolved": unresolved,
    }
    if diff is not None:
        doc["diff"] = diff
    return doc


def write_json(path: str, **kwargs):
    doc = build_json_document(**kwargs)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
