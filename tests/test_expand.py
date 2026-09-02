from sdc_parser.analyzer import Analyzer
from sdc_parser.expand import VarEnv


def analyze(text):
    a = Analyzer()
    a.analyze_file("<test>", text)
    return a


def test_expand_basic_forms():
    env = VarEnv()
    env.bind("x", "10", conditional=False)
    result, info = env.expand("period $x and ${x} end")
    assert result == "period 10 and 10 end"
    assert info.status == "full"


def test_expand_unknown_is_none():
    env = VarEnv()
    result, info = env.expand("$missing")
    assert result == "$missing"
    assert info.status == "none"


def test_expand_partial():
    env = VarEnv()
    env.bind("a", "1", conditional=False)
    result, info = env.expand("$a $b")
    assert result == "1 $b"
    assert info.status == "partial"


def test_conditional_binding_caps_at_partial():
    env = VarEnv()
    env.bind("a", "1", conditional=True)
    _, info = env.expand("$a")
    assert info.status == "partial"


def test_array_reference_untouched():
    env = VarEnv()
    env.bind("arr", "zzz", conditional=False)
    result, info = env.expand("$arr(1)")
    assert result == "$arr(1)"
    assert info.status == "none"


def test_no_dollar_blank_status():
    env = VarEnv()
    _, info = env.expand("plain text")
    assert info.status == ""


def test_set_literal_binds():
    a = analyze("set x 5\nputs $x")
    assert a.env.get("x") == ("5", False)
    assert a.records[-1].arguments_expanded == "5"


def test_set_from_bracket_invalidates():
    a = analyze("set x 5\nset x [llength $l]")
    assert a.env.get("x") is None


def test_set_from_evaluable_expr_binds():
    a = analyze("set a 5\nset v [expr {$a + 1}]\nputs $v")
    assert a.env.get("v") == ("6", False)
    assert a.records[-1].arguments_expanded == "6"


def test_expr_unsupported_op_still_invalidates():
    a = analyze("set bits 8\nset top [expr {1 << ($bits - 1)}]")
    assert a.env.get("top") is None


def test_expr_partial_word_not_evaluated():
    a = analyze("set x pre[expr {1 + 2}]post")
    assert a.env.get("x") is None


def test_set_chained_variable():
    a = analyze("set a hello\nset b $a")
    assert a.env.get("b") == ("hello", False)


def test_set_inside_if_is_conditional():
    a = analyze("if {$m} {\n  set x 5\n}\nputs $x")
    assert a.env.get("x") == ("5", True)
    assert a.records[-1].expand_status == "partial"


def test_incr_outside_loop():
    a = analyze("set i 1\nincr i\nincr i 3")
    assert a.env.get("i") == ("5", False)


def test_incr_inside_loop_invalidates():
    a = analyze("set i 0\nwhile {$i < 3} {\n  incr i\n}")
    assert a.env.get("i") is None


def test_append_and_lappend():
    a = analyze("set s ab\nappend s cd\nset l one\nlappend l two three")
    assert a.env.get("s") == ("abcd", False)
    assert a.env.get("l") == ("one two three", False)


def test_foreach_var_not_bound():
    a = analyze("set clk stale\nforeach clk {a b} {\n  puts $clk\n}")
    assert a.env.get("clk") is None
    inner = [r for r in a.records if r.command == "puts"][0]
    assert inner.arguments_expanded == "$clk"


def test_unset_and_gets_invalidate():
    a = analyze("set x 1\nunset x")
    assert a.env.get("x") is None
    a2 = analyze("set line 1\ngets $fh line")
    assert a2.env.get("line") is None
