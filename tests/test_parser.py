import pytest

from sdc_parser.model import ParseError
from sdc_parser.parser import normalize_source, parse_script


def test_simple_words():
    cmds = parse_script("set x 5")
    assert len(cmds) == 1
    assert [w.text for w in cmds[0].words] == ["set", "x", "5"]
    assert all(w.kind == "bare" for w in cmds[0].words)
    assert cmds[0].name == "set"
    assert cmds[0].line == 1


def test_brace_and_quote_words():
    cmds = parse_script('set x {a b}\nputs "hi there"')
    assert cmds[0].words[2].kind == "brace"
    assert cmds[0].words[2].text == "a b"
    assert cmds[1].words[1].kind == "quote"
    assert cmds[1].words[1].text == "hi there"


def test_semicolon_separates_commands():
    cmds = parse_script("set a 1; set b 2")
    assert [c.name for c in cmds] == ["set", "set"]


def test_semicolon_inside_braces_and_quotes_is_data():
    cmds = parse_script('set x {a;b}\nset y "a;b"')
    assert len(cmds) == 2
    assert cmds[0].words[2].text == "a;b"
    assert cmds[1].words[2].text == "a;b"


def test_hash_mid_word_is_data():
    cmds = parse_script("set x a#b")
    assert len(cmds) == 1
    assert cmds[0].words[2].text == "a#b"


def test_comment_line_and_trailing_comment():
    cmds = parse_script("# full comment\nset z 1 ;# note")
    assert len(cmds) == 1
    assert cmds[0].line == 2
    assert len(cmds[0].words) == 3


def test_comment_backslash_continuation():
    cmds = parse_script("# comment \\\n still comment\nset a 1")
    assert len(cmds) == 1
    assert cmds[0].name == "set"
    assert cmds[0].line == 3


def test_backslash_continuation_joins_command():
    cmds = parse_script("cmd a \\\n    b\nnext")
    assert len(cmds) == 2
    assert [w.text for w in cmds[0].words] == ["cmd", "a", "b"]
    assert cmds[0].line == 1
    assert cmds[1].line == 3


def test_nested_brackets_stay_in_one_word():
    cmds = parse_script("set v [lindex [gets $fid] 0]")
    assert len(cmds) == 1
    assert cmds[0].words[2].text == "[lindex [gets $fid] 0]"


def test_multiline_brace_line_tracking():
    cmds = parse_script("set x {\n  a\n  b\n}\nnext")
    assert cmds[0].line == 1
    assert cmds[1].line == 5


def test_brace_word_records_start_line():
    cmds = parse_script("if {$a} {\n    body_cmd\n}")
    body = cmds[0].words[2]
    assert body.kind == "brace"
    assert body.line == 1
    inner = parse_script(body.text, line_offset=body.line)
    assert inner[0].name == "body_cmd"
    assert inner[0].line == 2


def test_quote_in_brace_and_brace_in_quote():
    cmds = parse_script('set a {say "hi"}\nset b "brace { here"')
    assert cmds[0].words[2].text == 'say "hi"'
    assert cmds[1].words[2].text == "brace { here"


def test_expand_prefix():
    cmds = parse_script("cmd {*}$args")
    w = cmds[0].words[1]
    assert w.expand_prefix
    assert w.text == "$args"
    assert w.raw == "{*}$args"


def test_unbalanced_brace_raises_with_line():
    with pytest.raises(ParseError) as exc:
        parse_script("set a 1\nset x {oops\n")
    assert exc.value.line == 2


def test_unbalanced_bracket_raises():
    with pytest.raises(ParseError):
        parse_script("set x [foo\n")


def test_tolerant_mode_collects_warnings():
    warnings = []
    cmds = parse_script('set x "unclosed\nset y 2', tolerant=True, warnings=warnings)
    assert warnings
    assert any(c.name == "set" and c.words[1].text == "y" for c in cmds)


def test_normalize_crlf():
    assert normalize_source("a\r\nb\rc") == "a\nb\nc"


def test_empty_and_comment_only():
    assert parse_script("") == []
    assert parse_script("# just a comment\n") == []
    assert parse_script("\n\n  \n") == []
