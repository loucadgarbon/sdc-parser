import pytest

from sdc_parser.eval_expr import eval_expr, glob_match, t_and, t_not, t_or


@pytest.mark.parametrize(
    "expr,expected",
    [
        # numeric
        ("10.0 > 5", True),
        ("3 < 2", False),
        ("0x10 == 16", True),
        ("7 % 3 == 1", True),
        ("(1+2)*3 == 9", True),
        ("-5 < 0", True),
        ("1/0", None),
        ("7 / 2 == 3", True),  # Tcl integer division floors
        # string vs numeric comparison
        ('func == "func"', True),
        ("1 == 1.0", True),
        ("1 eq 1.0", False),
        ('"abc" < "abd"', True),
        ("{abc} eq {abc}", True),
        ("a ne b", True),
        # tri-state short circuit
        ("$u && 0", False),
        ("$u && 1", None),
        ("$u || 1", True),
        ("$u || 0", None),
        ("0 && $u", False),
        ("1 || $u", True),
        ("!$u", None),
        ("!0", True),
        # unknown constructs degrade, never crash
        ("$x > 1", None),
        ("[info exists x]", None),
        ("1 ? 2 : 3", None),
        ("1 << 2", None),
        ("banana", None),
        ('""', None),
        ("", None),
        ("$arr(idx) == 1", None),
        ("(1", None),
        # boolean literals
        ("yes", True),
        ("off", False),
        ("2", True),
        ('"true" && 1', True),
    ],
)
def test_eval_expr(expr, expected):
    assert eval_expr(expr) is expected


def test_eval_value():
    from sdc_parser.eval_expr import eval_value

    assert eval_value("2 + 3") == "5"
    assert eval_value("{2 + 3}") == "5"  # braced expr operand
    assert eval_value("10.0 / 4") == "2.5"
    assert eval_value("0x10") == "16"
    assert eval_value("{abc}") == "abc"
    assert eval_value("$u + 1") is None
    assert eval_value("1 << 2") is None
    assert eval_value("") is None


def test_expr_bracket_inside_condition():
    assert eval_expr("[expr {2 + 3}] > 4") is True
    assert eval_expr("[expr {2 + 3}] > 9") is False
    assert eval_expr("[expr {$u + 3}] > 4") is None
    assert eval_expr("[llength $l] > 4") is None


def test_tri_state_tables():
    T, F, U = True, False, None
    assert [t_and(a, b) for a in (T, F, U) for b in (T, F, U)] == [
        T, F, U, F, F, F, U, F, U
    ]
    assert [t_or(a, b) for a in (T, F, U) for b in (T, F, U)] == [
        T, T, T, T, F, U, T, U, U
    ]
    assert [t_not(a) for a in (T, F, U)] == [F, T, U]


@pytest.mark.parametrize(
    "subject,pattern,nocase,expected",
    [
        ("func", "f*", False, True),
        ("func", "f?nc", False, True),
        ("func", "[a-g]unc", False, True),
        ("func", "scan", False, False),
        ("FUNC", "f*", True, True),
        ("FUNC", "f*", False, False),
        ("a", "\\a", False, None),  # fnmatch/Tcl divergence -> unknown
        ("a", "[!b]", False, None),
    ],
)
def test_glob_match(subject, pattern, nocase, expected):
    assert glob_match(subject, pattern, nocase) is expected
