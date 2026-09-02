from sdc_parser.analyzer import Analyzer


def analyze(script, params=None):
    a = Analyzer()
    if params:
        a.load_params_file("<params>", params)
    a.analyze_file("s.tcl", script)
    return a


def report_by_name(a):
    return {r["name"]: r for r in a.variables_report()}


def test_report_basic_fields():
    a = analyze("set A 1\nset A 2\n")
    r = report_by_name(a)["A"]
    assert r["value"] == "2"
    assert r["conditional"] == ""
    assert r["set_count"] == 2
    assert r["first_set"] == "s.tcl:1"
    assert r["last_set"] == "s.tcl:2"
    assert r["unresolved_uses"] == 0


def test_unresolved_collected_from_args_and_conditions():
    a = analyze("puts $MISSING\nif {$MISSING > 1} { puts x }\n")
    r = report_by_name(a)["MISSING"]
    assert r["set_count"] == 0
    assert r["unresolved_uses"] == 2
    assert "s.tcl:1" in r["unresolved_lines"]
    assert "s.tcl:2" in r["unresolved_lines"]


def test_sort_unresolved_first():
    a = analyze("set A 1\nputs $ZZZ\n")
    rows = a.variables_report()
    assert rows[0]["name"] == "ZZZ"


def test_params_and_conditional_origins():
    a = analyze("if {$u} {\n  set C 9\n}\n", params="set P 5\n")
    rep = report_by_name(a)
    assert rep["P"]["first_set"] == "<params>:1"
    assert rep["C"]["conditional"] == "yes"


def test_define_origin():
    a = Analyzer()
    a.env.bind("D1", "1", conditional=False, origin="-D")
    a.analyze_file("s.tcl", "puts $D1\n")
    assert report_by_name(a)["D1"]["first_set"] == "-D"
