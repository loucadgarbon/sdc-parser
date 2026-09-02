from sdc_parser.analyzer import Analyzer, _split_tcl_list


def analyze(script, unroll=True, **kwargs):
    a = Analyzer(unroll=unroll, **kwargs)
    a.analyze_file("s.tcl", script)
    return a


def recs(a, command):
    return [r for r in a.records if r.command == command]


def test_split_tcl_list():
    assert _split_tcl_list("a b c") == ["a", "b", "c"]
    assert _split_tcl_list("a {b c} d") == ["a", "b c", "d"]
    assert _split_tcl_list('a "b c"') == ["a", "b c"]
    assert _split_tcl_list("") == []
    assert _split_tcl_list("a \\{ b") is None
    assert _split_tcl_list("{unclosed") is None


def test_simple_unroll():
    a = analyze("foreach clk {a b c} {\n  create_clock $clk\n}\n")
    rows = recs(a, "create_clock")
    assert [r.arguments_expanded for r in rows] == ["a", "b", "c"]
    assert all(r.expand_status == "full" for r in rows)
    assert rows[0].loop_context == "foreach clk {a b c} [clk=a]"
    assert rows[2].loop_context == "foreach clk {a b c} [clk=c]"


def test_unroll_off_by_default():
    a = analyze("foreach clk {a b} {\n  create_clock $clk\n}\n", unroll=False)
    rows = recs(a, "create_clock")
    assert len(rows) == 1
    assert rows[0].arguments_expanded == "$clk"


def test_multi_var_chunking_and_padding():
    a = analyze("foreach {x y} {1 2 3} {\n  puts \"$x $y\"\n}\n")
    rows = recs(a, "puts")
    assert [r.arguments_expanded for r in rows] == ['"1 2"', '"3 "']


def test_parallel_lists():
    a = analyze("foreach a {1 2} b {x y} {\n  puts $a$b\n}\n")
    rows = recs(a, "puts")
    assert [r.arguments_expanded for r in rows] == ["1x", "2y"]


def test_incr_accumulates_and_last_binding_persists():
    a = analyze("set n 0\nforeach v {a b c} {\n  incr n\n}\nputs $n$v\n")
    assert a.env.get("n") == ("3", False)
    assert a.env.get("v") == ("c", False)
    assert a.records[-1].arguments_expanded == "3c"


def test_nested_unroll():
    a = analyze(
        "foreach a {1 2} {\n  foreach b {x y} {\n    puts $a$b\n  }\n}\n"
    )
    rows = recs(a, "puts")
    assert [r.arguments_expanded for r in rows] == ["1x", "1y", "2x", "2y"]


def test_cap_exceeded_falls_back():
    values = " ".join(str(i) for i in range(5))
    a = analyze(
        f"foreach v {{{values}}} {{\n  puts $v\n}}\n", unroll=True, max_unroll=3
    )
    rows = recs(a, "puts")
    assert len(rows) == 1  # whole-loop fallback, never half-unrolled
    assert any("unroll limit" in w for w in a.warnings)


def test_unresolvable_list_falls_back():
    a = analyze("foreach v $mystery {\n  puts $v\n}\n")
    rows = recs(a, "puts")
    assert len(rows) == 1
    assert rows[0].arguments_expanded == "$v"


def test_braced_elements():
    a = analyze("foreach p {{a b} c} {\n  puts $p\n}\n")
    rows = recs(a, "puts")
    assert [r.arguments_expanded for r in rows] == ["a b", "c"]


def test_empty_list_no_body_rows():
    a = analyze("set v keep\nforeach v {} {\n  puts $v\n}\nputs end\n")
    assert not any(r.arguments == "$v" for r in recs(a, "puts"))
    assert a.env.get("v") == ("keep", False)


def test_unroll_in_dead_branch_skipped():
    a = analyze("if {0} {\n  foreach v {a b} {\n    puts $v\n  }\n}\n")
    assert not recs(a, "puts") or all(r.active == "no" for r in recs(a, "puts"))
