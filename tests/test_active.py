from sdc_parser.analyzer import Analyzer


def analyze(script, params=None):
    a = Analyzer()
    if params is not None:
        if isinstance(params, str):
            params = [params]
        for i, p in enumerate(params):
            a.load_params_file(f"<params{i}>", p)
    a.analyze_file("<test>", script)
    return a


def actives(a, command):
    return [(r.line, r.active) for r in a.records if r.command == command]


def test_params_drive_if_elseif_else():
    a = analyze(
        'if {$MODE == "func"} {\n'
        "  puts A\n"
        '} elseif {$MODE == "scan"} {\n'
        "  puts B\n"
        "} else {\n"
        "  puts C\n"
        "}\n"
        "if {$PERIOD > 5} { puts D }\n",
        params="set MODE func\ndefine PERIOD 10.0\n",
    )
    assert actives(a, "puts") == [
        (2, "yes"),
        (4, "no"),
        (6, "no"),
        (8, "yes"),
    ]
    # header row inherits the parent (top-level) state
    assert actives(a, "if") == [(1, "yes"), (8, "yes")]


def test_nested_if_and():
    a = analyze(
        "set A 1\nset B 0\n"
        "if {$A} {\n"
        "  if {$B} { puts X } else { puts Y }\n"
        "}\n"
        "if {$U} {\n"
        "  if {1} { puts Z }\n"
        "}\n"
    )
    assert actives(a, "puts") == [(4, "no"), (4, "yes"), (7, "unknown")]


def test_unknown_prior_makes_else_no():
    a = analyze(
        "if {$u} { puts A } elseif {1} { puts B } else { puts C }\n"
    )
    assert actives(a, "puts") == [(1, "unknown"), (1, "unknown"), (1, "no")]


def test_switch_exact_and_default():
    script = (
        "switch -exact -- $M {\n"
        "  a { puts A }\n"
        "  b { puts B }\n"
        "  default { puts D }\n"
        "}\n"
    )
    a = analyze(script, params="set M a\n")
    assert actives(a, "puts") == [(2, "yes"), (3, "no"), (4, "no")]
    a = analyze(script, params="set M zzz\n")
    assert actives(a, "puts") == [(2, "no"), (3, "no"), (4, "yes")]
    a = analyze(script)  # unknown subject
    assert actives(a, "puts") == [(2, "unknown"), (3, "unknown"), (4, "unknown")]


def test_switch_glob_and_regexp():
    a = analyze(
        'switch $M {\n  f* { puts A }\n  s* { puts B }\n}\n', params="set M func\n"
    )
    assert actives(a, "puts") == [(2, "yes"), (3, "no")]
    a = analyze(
        "switch -regexp -- $M {\n  ^f { puts A }\n}\n", params="set M func\n"
    )
    assert actives(a, "puts") == [(2, "unknown")]


def test_switch_fallthrough():
    script = (
        "switch -exact -- $M {\n"
        "  a -\n"
        "  b { puts AB }\n"
        "  c { puts C }\n"
        "}\n"
    )
    a = analyze(script, params="set M a\n")
    assert actives(a, "puts") == [(3, "yes"), (4, "no")]
    a = analyze(script, params="set M c\n")
    assert actives(a, "puts") == [(3, "no"), (4, "yes")]


def test_dead_branch_does_not_clobber_env():
    a = analyze(
        "set X 5\nif {0} {\n  set X 7\n  unset X\n}\nputs $X\n"
    )
    assert a.env.get("X") == ("5", False)
    last = a.records[-1]
    assert last.arguments_expanded == "5"
    assert [r.active for r in a.records if r.line == 3] == ["no"]


def test_dead_foreach_and_catch_do_not_invalidate():
    a = analyze(
        "set v keep\nset e keep\n"
        "if {0} {\n  foreach v {a b} { puts $v }\n  catch { bad } e\n}\n"
    )
    assert a.env.get("v") == ("keep", False)
    assert a.env.get("e") == ("keep", False)


def test_active_branch_binds_non_conditional():
    a = analyze("set A 1\nif {$A == 1} {\n  set X 5\n}\nputs $X\n")
    assert a.env.get("X") == ("5", False)
    assert a.records[-1].expand_status == "full"


def test_unknown_branch_stays_conditional():
    a = analyze("if {$m} {\n  set x 5\n}\nputs $x\n")
    assert a.env.get("x") == ("5", True)
    assert a.records[-1].expand_status == "partial"


def test_while_zero_body_inactive():
    a = analyze("while {0} { puts dead }\nwhile {$u} { puts maybe }\n")
    assert actives(a, "puts") == [(1, "no"), (2, "yes")]


def test_proc_body_inherits_and_params_unknown():
    a = analyze("proc f {x} {\n  if {$x > 0} { puts P }\n  puts Q\n}\n")
    assert actives(a, "puts") == [(2, "unknown"), (3, "yes")]


def test_conditional_binding_never_decides_branch():
    a = analyze("if {$u} {\n  set F 1\n}\nif {$F == 1} { puts P }\n")
    assert actives(a, "puts") == [(4, "unknown")]


def test_params_override_order():
    a = analyze(
        "if {$A == 2} { puts P }\n",
        params=["set A 1\n", "set A 2\n"],
    )
    assert actives(a, "puts") == [(1, "yes")]
    # script-internal set overrides params
    a = analyze(
        "set A 3\nif {$A == 3} { puts P }\n",
        params="set A 1\n",
    )
    assert actives(a, "puts") == [(2, "yes")]


def test_params_file_ignores_other_commands_with_warning():
    a = Analyzer()
    a.load_params_file("<p>", "set A 1\ncreate_clock -name x\n")
    assert a.env.get("A") == ("1", False)
    assert any("ignored" in w for w in a.warnings)


def test_params_reference_earlier_params():
    a = analyze("if {$B == 5} { puts P }\n", params="set A 5\nset B $A\n")
    assert actives(a, "puts") == [(1, "yes")]
