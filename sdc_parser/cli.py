"""Command-line entry point: tclscan (typer + rich)."""

from __future__ import annotations

import fnmatch
import re
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .analyzer import Analyzer, summarize
from .diff import diff_records, diff_rows, entries_json
from .export_csv import (
    write_command_csvs,
    write_diff_csv,
    write_summary_csv,
    write_variables_csv,
)
from .export_json import write_json
from .export_xlsx import write_xlsx
from .model import DetailRecord, ParseError
from .tables import build_command_tables

app = typer.Typer(add_completion=False)

_NAME_RE = re.compile(r"^[A-Za-z0-9_:]+$")
_ACTIVE_VALUES = {"yes", "no", "unknown"}
_ACTIVE_COLORS = {"yes": "green", "no": "red", "unknown": "yellow"}

stdout = Console()
stderr = Console(stderr=True)


class Format(str, Enum):
    csv = "csv"
    xlsx = "xlsx"
    json = "json"
    both = "both"  # csv + xlsx
    all = "all"  # csv + xlsx + json

    @property
    def wants_csv(self) -> bool:
        return self in (Format.csv, Format.both, Format.all)

    @property
    def wants_xlsx(self) -> bool:
        return self in (Format.xlsx, Format.both, Format.all)

    @property
    def wants_json(self) -> bool:
        return self in (Format.json, Format.all)


def _version_callback(value: bool):
    if value:
        stdout.print(f"tclscan {__version__}")
        raise typer.Exit()


def _parse_defines(defines: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for item in defines:
        name, _, value = item.partition("=")
        if not _NAME_RE.match(name):
            raise typer.BadParameter(
                f"invalid define name {name!r} (expected NAME=VALUE or NAME)",
                param_hint="--define",
            )
        pairs.append((name, value if "=" in item else "1"))
    return pairs


def _parse_active_filter(spec: str | None) -> set[str] | None:
    if spec is None:
        return None
    values = {v.strip() for v in spec.split(",") if v.strip()}
    bad = values - _ACTIVE_VALUES
    if bad or not values:
        raise typer.BadParameter(
            f"expected comma-separated values from yes/no/unknown, got {spec!r}",
            param_hint="--filter-active",
        )
    return values


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _filter_records(
    records: list[DetailRecord],
    active_filter: set[str] | None,
    commands: str | None,
    exclude: str | None,
) -> list[DetailRecord]:
    if active_filter is not None:
        records = [r for r in records if r.active in active_filter]
    if commands:
        patterns = [p.strip() for p in commands.split(",") if p.strip()]
        records = [r for r in records if _matches_any(r.command, patterns)]
    if exclude:
        patterns = [p.strip() for p in exclude.split(",") if p.strip()]
        records = [r for r in records if not _matches_any(r.command, patterns)]
    return records


def _colorize_active(active: str) -> str:
    parts = []
    for part in active.split(" / "):
        color = next(
            (c for word, c in _ACTIVE_COLORS.items() if part.endswith(word)), None
        )
        parts.append(f"[{color}]{part}[/{color}]" if color else part)
    return " / ".join(parts)


def _print_summary_table(summary_rows: list[dict]):
    table = Table(title="Summary")
    table.add_column("command", style="bold")
    table.add_column("count", justify="right")
    table.add_column("active")
    for row in summary_rows:
        table.add_row(row["command"], str(row["count"]), _colorize_active(row["active"]))
    stdout.print(table)


_DIFF_STYLES = {"added": ("green", "+"), "removed": ("red", "-"), "changed": ("yellow", "~")}


def _print_diff(entries, other: str, show_table: bool):
    if not entries:
        stdout.print(f"diff vs {other}: no differences")
        return
    counts = {c: sum(1 for e in entries if e.change == c) for c in _DIFF_STYLES}
    stdout.print(
        f"diff vs {other}: [green]{counts['added']} added[/green], "
        f"[red]{counts['removed']} removed[/red], "
        f"[yellow]{counts['changed']} changed[/yellow]"
    )
    if not show_table:
        return
    table = Table(title="Diff")
    table.add_column("")
    table.add_column("command", style="bold")
    table.add_column("arguments")
    table.add_column("line")
    table.add_column("active")
    for e in entries:
        color, sign = _DIFF_STYLES[e.change]
        line_a = str(e.a.line) if e.a else ""
        line_b = str(e.b.line) if e.b else ""
        active_a = e.a.active if e.a else ""
        active_b = e.b.active if e.b else ""
        args = e.key_args if len(e.key_args) <= 50 else e.key_args[:47] + "..."
        table.add_row(
            f"[{color}]{sign}[/{color}]",
            f"[{color}]{e.command}[/{color}]",
            args,
            line_a if line_a == line_b else f"{line_a}->{line_b}".strip("->"),
            active_a if active_a == active_b else f"{active_a}->{active_b}".strip("->"),
        )
    stdout.print(table)


def _print_unresolved_table(variables_rows: list[dict]):
    unresolved = [r for r in variables_rows if r["unresolved_uses"]]
    if not unresolved:
        return
    table = Table(title="Unresolved variables (define with -D or --params)")
    table.add_column("name", style="bold red")
    table.add_column("uses", justify="right")
    table.add_column("lines")
    for row in unresolved[:10]:
        table.add_row(row["name"], str(row["unresolved_uses"]), row["unresolved_lines"])
    if len(unresolved) > 10:
        table.caption = f"+{len(unresolved) - 10} more"
    stdout.print(table)


@app.command()
def scan(
    files: list[Path] = typer.Argument(..., help="Input .tcl/.sdc files"),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Output base name (default: first input's stem)"
    ),
    out_dir: Path = typer.Option(
        Path("."), "--out-dir", help="Output directory (created if missing)"
    ),
    format: Format = typer.Option(Format.both, "--format", "-f"),
    params: list[Path] = typer.Option(
        [],
        "--params",
        help="TCL file of 'set NAME VALUE' / 'define NAME VALUE' commands loaded "
        "as initial variable values (repeatable, applied in order)",
    ),
    define: list[str] = typer.Option(
        [],
        "--define",
        "-D",
        help="Define a variable inline: NAME=VALUE, or bare NAME (=1). "
        "Applied after --params files (repeatable)",
    ),
    filter_active: str | None = typer.Option(
        None,
        "--filter-active",
        help="Only export rows with these active states, e.g. 'yes' or 'yes,unknown'",
    ),
    commands: str | None = typer.Option(
        None,
        "--commands",
        help="Only export commands matching these comma-separated globs, "
        "e.g. 'create_*,set_*'",
    ),
    exclude: str | None = typer.Option(
        None, "--exclude", help="Exclude commands matching these globs, e.g. 'puts,set'"
    ),
    diff: Path | None = typer.Option(
        None,
        "--diff",
        help="Compare against this file (analyzed as side B) and report "
        "added/removed/changed commands",
    ),
    diff_params: list[Path] = typer.Option(
        [],
        "--diff-params",
        help="Params files for the --diff side; when any --diff-params/"
        "--diff-define is given they REPLACE the main side's params",
    ),
    diff_define: list[str] = typer.Option(
        [], "--diff-define", help="Inline defines for the --diff side"
    ),
    table: bool = typer.Option(
        True, "--table/--no-table", help="Print a summary table to the terminal"
    ),
    unroll: bool = typer.Option(
        False,
        "--unroll",
        help="Unroll foreach loops with statically-known lists into one row "
        "set per iteration (loop vars substituted)",
    ),
    max_unroll: int = typer.Option(
        100, "--max-unroll", help="Per-loop iteration cap for --unroll"
    ),
    follow_source: bool = typer.Option(
        True,
        "--follow-source/--no-follow-source",
        help="Recursively analyze files loaded via the TCL 'source' command",
    ),
    tolerant: bool = typer.Option(
        False,
        "--tolerant",
        help="Warn and skip to the next line on parse errors instead of failing",
    ),
    encoding: str = typer.Option("utf-8-sig", "--encoding"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print errors"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-file and binding details"
    ),
    fail_on_unknown: bool = typer.Option(
        False,
        "--fail-on-unknown",
        help="Exit with code 3 if any row has active=unknown (params file does "
        "not decide every branch)",
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True
    ),
):
    """Statically analyze TCL/SDC scripts and export a per-command-kind table
    set (one sheet/CSV per command, one column per argument) plus a summary,
    with variable expansion and branch-activeness evaluation."""
    defines = _parse_defines(define)
    active_filter = _parse_active_filter(filter_active)

    analyzer = Analyzer(
        tolerant=tolerant,
        follow_source=follow_source,
        unroll=unroll,
        max_unroll=max_unroll,
        encoding=encoding,
    )
    try:
        for p in params:
            analyzer.load_params_file(
                str(p), p.read_text(encoding=encoding, errors="replace")
            )
        for name, value in defines:
            analyzer.env.bind(name, value, conditional=False, origin="-D")
        if verbose and not quiet and (params or defines):
            stdout.print(
                f"loaded {len(params)} params file(s), {len(defines)} define(s)"
            )
        for f in files:
            before = len(analyzer.records)
            analyzer.analyze_file(str(f), f.read_text(encoding=encoding, errors="replace"))
            if verbose and not quiet:
                stdout.print(f"{f}: {len(analyzer.records) - before} commands")
    except OSError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1)
    except ParseError as exc:
        stderr.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1)

    if not quiet:
        for w in analyzer.warnings:
            stderr.print(f"[yellow]warning:[/yellow] {w}")

    unknown_rows = [r for r in analyzer.records if r.active == "unknown"]

    records = _filter_records(analyzer.records, active_filter, commands, exclude)
    summary_rows = summarize(records)
    order = [r["command"] for r in summary_rows]
    tables = build_command_tables(records, order=order)
    variables_rows = analyzer.variables_report()

    diff_entries = None
    if diff is not None:
        analyzer_b = Analyzer(
            tolerant=tolerant,
            follow_source=follow_source,
            unroll=unroll,
            max_unroll=max_unroll,
            encoding=encoding,
        )
        override = bool(diff_params or diff_define)
        b_param_files = diff_params if override else params
        b_defines = _parse_defines(diff_define) if override else defines
        try:
            for p in b_param_files:
                analyzer_b.load_params_file(
                    str(p), p.read_text(encoding=encoding, errors="replace")
                )
            for name, value in b_defines:
                analyzer_b.env.bind(name, value, conditional=False, origin="-D")
            analyzer_b.analyze_file(
                str(diff), diff.read_text(encoding=encoding, errors="replace")
            )
        except (OSError, ParseError) as exc:
            stderr.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(1)
        records_b = _filter_records(analyzer_b.records, active_filter, commands, exclude)
        diff_entries = diff_records(records, records_b)

    out_dir.mkdir(parents=True, exist_ok=True)
    base = str(out_dir / (output or files[0].stem))
    written = []
    if format.wants_csv:
        summary_path = f"{base}_summary.csv"
        write_summary_csv(summary_path, summary_rows)
        written.append(summary_path)
        variables_path = f"{base}_variables.csv"
        write_variables_csv(variables_path, variables_rows)
        written.append(variables_path)
        if diff_entries is not None:
            columns, rows = diff_rows(diff_entries)
            diff_path = f"{base}_diff.csv"
            write_diff_csv(diff_path, columns, rows)
            written.append(diff_path)
        written += write_command_csvs(base, tables)
    if format.wants_xlsx:
        xlsx_path = f"{base}.xlsx"
        write_xlsx(
            xlsx_path,
            tables,
            summary_rows,
            variables_rows,
            diff_table=diff_rows(diff_entries) if diff_entries is not None else None,
        )
        written.append(xlsx_path)
    if format.wants_json:
        json_path = f"{base}.json"
        write_json(
            json_path,
            records=records,
            variables_rows=variables_rows,
            env=analyzer.env,
            files=analyzer.files,
            unresolved=analyzer.unresolved,
            version=__version__,
            order=order,
            diff=(
                {"other": str(diff), "entries": entries_json(diff_entries)}
                if diff_entries is not None
                else None
            ),
        )
        written.append(json_path)

    if not quiet:
        stdout.print(
            f"parsed {len(files)} file(s): {len(records)} commands, "
            f"{len(summary_rows)} distinct command names"
        )
        if table:
            _print_summary_table(summary_rows)
            _print_unresolved_table(variables_rows)
        if diff_entries is not None:
            _print_diff(diff_entries, str(diff), show_table=table)
        for p in written:
            stdout.print(f"wrote {p}")

    if fail_on_unknown and unknown_rows:
        locations = ", ".join(
            f"{r.command}@{r.line}" for r in unknown_rows[:5]
        )
        more = "" if len(unknown_rows) <= 5 else f" (+{len(unknown_rows) - 5} more)"
        stderr.print(
            f"[red]fail-on-unknown:[/red] {len(unknown_rows)} row(s) with "
            f"active=unknown: {locations}{more}"
        )
        raise typer.Exit(3)


def main():
    app()


if __name__ == "__main__":
    main()
