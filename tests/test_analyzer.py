import pathlib

import pytest

from sdc_parser.analyzer import Analyzer, summarize

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sample.tcl"


@pytest.fixture(scope="module")
def records():
    a = Analyzer()
    a.analyze_file(str(FIXTURE), FIXTURE.read_text(encoding="utf-8-sig"))
    return a.records


def one(records, line, command):
    matches = [r for r in records if r.line == line and r.command == command]
    assert len(matches) == 1, f"expected 1 record for {command}@{line}, got {matches}"
    return matches[0]


def test_proc_context_and_condition(records):
    r = one(records, 15, "set")
    assert r.proc == "sign_extend"
    assert r.condition_chain == "if {$v >= $top}"
    assert r.loop_context == ""


def test_if_elseif_else_chains(records):
    assert one(records, 21, "set_false_path").condition_chain == 'if {$MODE == "func"}'
    assert (
        one(records, 23, "set_false_path").condition_chain == 'elseif {$MODE == "scan"}'
    )
    assert one(records, 25, "puts").condition_chain == "else"


def test_condition_chain_expanded(records):
    r = one(records, 21, "set_false_path")
    assert r.condition_chain_expanded == 'if {func == "func"}'


def test_nested_if_chain(records):
    r = one(records, 44, "set_clock_uncertainty")
    assert r.condition_chain == 'if {$MODE == "func"} > if {$PERIOD > 5}'
    assert r.condition_chain_expanded == 'if {func == "func"} > if {10.0 > 5}'


def test_nested_loop_context(records):
    r = one(records, 31, "puts")
    assert r.loop_context == (
        "foreach clk {clk_a clk_b clk_c}"
        " > for {set i 0} {$i < 3} {incr i}"
        " > while {$i < 2}"
    )
    assert r.condition_chain == ""


def test_for_init_gets_loop_context(records):
    r = one(records, 29, "set")
    assert r.loop_context.startswith("foreach clk {clk_a clk_b clk_c} > for ")


def test_switch_conditions(records):
    assert one(records, 37, "puts").condition_chain == "switch $MODE == func"
    assert one(records, 39, "puts").condition_chain == "switch $MODE default"
    assert one(records, 37, "puts").condition_chain_expanded == "switch func == func"


def test_variable_expansion_in_arguments(records):
    r = one(records, 6, "create_clock")
    assert r.arguments == "-name clk -period $PERIOD [get_ports clk]"
    assert r.arguments_expanded == "-name clk -period 10.0 [get_ports clk]"
    assert r.expand_status == "full"


def test_continuation_command_single_row(records):
    r = one(records, 8, "set_clock_groups")
    assert r.arguments == "-asynchronous -group {clk_a clk_b} -group {clk_c}"
    assert "\\" in r.raw  # raw keeps the continuations verbatim


def test_control_command_row_abbreviates_bodies(records):
    r = one(records, 20, "if")
    assert r.arguments == (
        '{$MODE == "func"} {...} elseif {$MODE == "scan"} {...} else {...}'
    )


def test_unexpandable_stays_original(records):
    r = one(records, 31, "puts")
    # $clk / $i are loop-scoped: deliberately unexpanded
    assert r.arguments_expanded == r.arguments
    assert r.expand_status == "none"


def test_fixture_active_states(records):
    # MODE=func, PERIOD=10.0 are set at fixture top level -> branches resolve
    assert one(records, 21, "set_false_path").active == "yes"
    assert one(records, 23, "set_false_path").active == "no"
    assert one(records, 25, "puts").active == "no"
    assert one(records, 44, "set_clock_uncertainty").active == "yes"  # 10.0 > 5
    assert one(records, 37, "puts").active == "yes"  # switch func arm
    assert one(records, 38, "puts").active == "no"
    assert one(records, 39, "puts").active == "no"  # default: func matched
    assert one(records, 6, "create_clock").active == "yes"  # top level
    assert one(records, 31, "puts").active == "yes"  # loops inherit
    assert one(records, 15, "set").active == "unknown"  # proc param condition


def test_arg_items_split(records):
    r = one(records, 6, "create_clock")
    assert r.arg_items == [
        ("-name", "clk"),
        ("-period", "$PERIOD"),
        (None, "[get_ports clk]"),
    ]


def test_arg_items_flag_and_repeated_option(records):
    r = one(records, 8, "set_clock_groups")
    assert r.arg_items == [
        ("-asynchronous", "Y"),
        ("-group", "{clk_a clk_b}"),
        ("-group", "{clk_c}"),
    ]


def test_command_tables_columns(records):
    from sdc_parser.tables import build_command_tables

    tables = build_command_tables(records)
    cc = tables["create_clock"]
    assert cc["columns"][:4] == ["line", "-name", "-period", "arg1"]
    assert "file" not in cc["columns"]
    assert cc["rows"][0][:4] == [6, "clk", "$PERIOD", "[get_ports clk]"]

    scg = tables["set_clock_groups"]
    row = dict(zip(scg["columns"], scg["rows"][0]))
    assert row["-asynchronous"] == "Y"
    assert row["-group"] == "{clk_a clk_b}; {clk_c}"


def test_summary(records):
    rows = summarize(records)
    by_name = {row["command"]: row for row in rows}

    sfp = by_name["set_false_path"]
    assert sfp["count"] == 2
    assert sfp["active"] == "1 yes / 1 no"
    assert sfp["lines"] == "21, 23"
    assert sfp["signatures"] == "set_false_path -from <arg>"
    assert 'if {$MODE == "func"}' in sfp["conditions"]
    assert 'elseif {$MODE == "scan"}' in sfp["conditions"]

    cc = by_name["create_clock"]
    assert cc["conditions"] == "(top)"
    assert cc["signatures"] == "create_clock -name <arg> -period <arg> <arg>"

    scg = by_name["set_clock_groups"]
    assert scg["signatures"] == "set_clock_groups -asynchronous -group <arg> -group <arg>"

    assert by_name["puts"]["count"] == 5
