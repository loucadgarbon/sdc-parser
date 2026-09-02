# sdc-parser (`tclscan`)

Static TCL/SDC script analyzer. It parses TCL scripts **without executing them** and
exports every command instance into per-command tables (CSV / Excel / JSON):
one sheet or CSV per command name, one column per argument, plus nested
if/loop/proc context, variable expansion, and a tri-state judgment of whether
each command is actually reached (`active` = yes / no / unknown).

Typical use case: auditing SDC timing constraints (`create_clock`,
`set_false_path`, ...) that are wrapped in `if {$MODE == ...}` branches,
loops, and variable indirection — and seeing which constraints take effect
under a given parameter set.

> 中文版說明請見 [README.zh-TW.md](README.zh-TW.md)。設計與語意規格（中文）：
> [docs/design.zh-TW.md](docs/design.zh-TW.md)、[docs/semantics.zh-TW.md](docs/semantics.zh-TW.md)。

## Features

- **Real TCL parsing** — hand-written character-level scanner (braces, quotes,
  brackets, `\`-continuations, `;# `comments), correct line numbers through
  arbitrary nesting. No regex heuristics.
- **Control-flow walk** — `if`/`elseif`/`else`, `foreach`/`for`/`while`,
  `proc`, `switch` (with fallthrough), `catch`, and `source` following.
- **Variable expansion** — tracks `set`/`append`/`lappend`/`incr`; every row
  gets `arguments_expanded` and an `expand_status` (full / partial / none).
- **Branch activeness** — a tri-state static `expr` evaluator decides whether
  each branch is taken: `active` = `yes`, `no`, or `unknown`. Dead branches
  never pollute the variable environment.
- **Parameterization** — feed initial values via `--params file.tcl` and/or
  `-D NAME=VALUE`; script-level `set` may override them (defines are *initial
  values*, see semantics doc).
- **Loop unrolling** — `--unroll` expands `foreach` loops over statically-known
  lists into one row set per iteration with the loop variable substituted.
- **Diff mode** — compare two files, or the same file under two parameter
  sets, and report added / removed / changed commands.
- **Three output formats** — CSV set, one styled `.xlsx` workbook, and a
  deterministic JSON document (`schema_version: 1`, no timestamps, diffable).

## Installation

Requires Python ≥ 3.10. From the repo root:

```
pip install -e ".[dev]"
```

On a machine with only [uv](https://docs.astral.sh/uv/) and a corporate TLS
proxy (this project's reference environment):

```
uv pip install --native-tls -e ".[dev]"
```

This installs the `tclscan` console command (in a venv: `.venv\Scripts\tclscan`).

## Quickstart

```
tclscan tests/fixtures/sample.tcl -o demo --out-dir out --format all
```

prints a summary table plus an "Unresolved variables" hint table, and writes:

```
out/demo_summary.csv        # one row per command name: count, active tally, lines, signatures, conditions
out/demo_variables.csv      # variables report: value, conditional?, set_count, unresolved uses
out/demo_create_clock.csv   # one CSV per command name ...
out/demo_set_false_path.csv
out/demo.xlsx               # all of the above as one workbook (styled)
out/demo.json               # machine-readable document
```

A per-command CSV has one column per option (option name = column name),
`argN` columns for positionals, then the context columns:

```
line,-name,-period,arg1,arguments_expanded,expand_status,active,condition_chain,condition_chain_expanded,loop_context,proc,raw
6,clk,$PERIOD,[get_ports clk],-name clk -period 10.0 [get_ports clk],full,yes,,,,,create_clock -name clk -period $PERIOD [get_ports clk]
```

Value-less flags are stored as `Y`; repeated options (e.g. `-group`) are
joined with `"; "` in one cell. A `file` column is added only when rows span
more than one file (multiple inputs or `source` following).

Deciding branches with a define:

```
tclscan constraints.tcl -D MODE=scan --filter-active yes -f xlsx
```

Diffing two parameter sets of the same script:

```
tclscan constraints.tcl -D MODE=func --diff constraints.tcl --diff-define MODE=scan
```

## CLI reference

```
tclscan [OPTIONS] FILES...
```

| Option | Default | Description |
|---|---|---|
| `-o, --output NAME` | first input's stem | Output base name |
| `--out-dir DIR` | `.` | Output directory (created if missing) |
| `-f, --format csv\|xlsx\|json\|both\|all` | `both` | `both` = csv+xlsx, `all` = csv+xlsx+json |
| `--params FILE` | — | TCL file of `set NAME VALUE` / `define NAME VALUE` loaded as initial values (repeatable, applied in order) |
| `-D, --define NAME=VALUE` | — | Inline define; bare `NAME` means `=1`. Applied after `--params` (repeatable) |
| `--filter-active LIST` | — | Only export rows with these active states, e.g. `yes` or `yes,unknown` |
| `--commands GLOBS` | — | Only export commands matching comma-separated globs, e.g. `create_*,set_*` |
| `--exclude GLOBS` | — | Exclude commands matching globs, e.g. `puts,set` |
| `--diff FILE` | — | Analyze FILE as side B and report added/removed/changed commands |
| `--diff-params FILE` | — | Params files for the diff side; any `--diff-params`/`--diff-define` **replaces** the main side's params for side B |
| `--diff-define NAME=VALUE` | — | Inline defines for the diff side |
| `--table / --no-table` | `--table` | Print summary (and diff) tables to the terminal |
| `--unroll` | off | Unroll `foreach` over statically-known lists, one row set per iteration |
| `--max-unroll N` | `100` | Per-loop iteration cap for `--unroll` |
| `--follow-source / --no-follow-source` | on | Recursively analyze files loaded via TCL `source` |
| `--tolerant` | off | Warn and skip to the next line on parse errors instead of failing |
| `--encoding ENC` | `utf-8-sig` | Input file encoding |
| `-q, --quiet` | off | Only print errors |
| `-v, --verbose` | off | Per-file and binding details |
| `--fail-on-unknown` | off | Exit 3 if any row has `active=unknown` |
| `--version` | — | Print version and exit |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | File or parse error |
| 2 | Usage error |
| 3 | `--fail-on-unknown` triggered (some branch could not be decided) |

## Output formats

- **CSV** — `utf-8-sig` (Excel-friendly). `<base>_summary.csv`,
  `<base>_variables.csv`, `<base>_<command>.csv` per command, and
  `<base>_diff.csv` when `--diff` is used.
- **XLSX** — one workbook: Summary sheet, Variables sheet, Diff sheet (if
  any), then one sheet per command. Rows with `active=no` are gray italic;
  `active=unknown` rows have a yellow background.
- **JSON** — `<base>.json`, `schema_version: 1`, deterministic (no
  timestamp). Top-level keys: `tclscan` (version), `schema_version`, `files`,
  `params` (final variable snapshot), `summary`, `commands` (per-command
  record lists with `options` / `positionals` split out), `variables`,
  `unresolved`, and `diff` (when `--diff` is used).

## Semantics in one paragraph

Defines/params are *initial values*: a plain top-level `set` in the script
overrides them. `foreach` variables are not bound by default (the value list
is shown in `loop_context`; `--unroll` performs real per-iteration binding).
`elseif`/`else` is `active=yes` only when every preceding branch is provably
false; anything undecidable degrades to `unknown`, never to a wrong answer.
Dead branches (`active=no`) do not touch the variable environment. Diff
identity is `(command, unexpanded arguments)`. Full rules with rationale:
[docs/semantics.zh-TW.md](docs/semantics.zh-TW.md) (Chinese).

## Development

```
python -m pytest -q        # 160 tests
```

Module layout: `parser.py` (char-level scan) → `analyzer.py` (control-flow
walk) → `expand.py` / `eval_expr.py` (env + tri-state expr) → `tables.py` →
`export_csv.py` / `export_xlsx.py` / `export_json.py` / `diff.py` → `cli.py`.
Architecture notes: [docs/design.zh-TW.md](docs/design.zh-TW.md) (Chinese).
