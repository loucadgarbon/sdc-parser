"""CSV export: one <base>_<command>.csv per command plus <base>_summary.csv
(all utf-8-sig)."""

from __future__ import annotations

import csv
import re

SUMMARY_COLUMNS = ["command", "count", "active", "lines", "signatures", "conditions"]

VARIABLES_COLUMNS = [
    "name",
    "value",
    "conditional",
    "set_count",
    "first_set",
    "last_set",
    "unresolved_uses",
    "unresolved_lines",
]


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.+-]", "_", name)
    return cleaned or "_"


def _write_rows(path: str, columns: list, rows: list[list]):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


def write_command_csvs(base: str, tables: dict) -> list[str]:
    written = []
    used: set[str] = set()
    for command, table in tables.items():
        stem = sanitize_filename(command)
        candidate = stem
        n = 2
        while candidate.lower() in used:
            candidate = f"{stem}_{n}"
            n += 1
        used.add(candidate.lower())
        path = f"{base}_{candidate}.csv"
        _write_rows(path, table["columns"], table["rows"])
        written.append(path)
    return written


def write_summary_csv(path: str, rows: list[dict]):
    _write_rows(path, SUMMARY_COLUMNS, [[row[c] for c in SUMMARY_COLUMNS] for row in rows])


def write_variables_csv(path: str, rows: list[dict]):
    _write_rows(
        path, VARIABLES_COLUMNS, [[row[c] for c in VARIABLES_COLUMNS] for row in rows]
    )


def write_diff_csv(path: str, columns: list[str], rows: list[list]):
    _write_rows(path, columns, rows)
