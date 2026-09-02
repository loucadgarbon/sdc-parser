from sdc_parser.analyzer import Analyzer
from sdc_parser.tables import build_command_tables


def analyze(main_path, **kwargs):
    a = Analyzer(**kwargs)
    a.analyze_file(str(main_path), main_path.read_text(encoding="utf-8"))
    return a


def test_basic_follow_and_shared_env(tmp_path):
    sub = tmp_path / "sub.tcl"
    sub.write_text("set FROM_SUB 7\ncreate_clock -period $TOP\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("set TOP 10\nsource sub.tcl\nputs $FROM_SUB\n", encoding="utf-8")
    a = analyze(main)
    # sourced commands are recorded with their own file/line
    cc = [r for r in a.records if r.command == "create_clock"][0]
    assert cc.file == str(sub)
    assert cc.line == 2
    assert cc.arguments_expanded == "-period 10"  # parent env visible in sub
    assert a.records[-1].arguments_expanded == "7"  # sub env visible in parent
    assert len(a.files) == 2


def test_relative_path_from_sourcing_dir(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "inner.tcl").write_text("puts inner\n", encoding="utf-8")
    (lib / "mid.tcl").write_text("source inner.tcl\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("source lib/mid.tcl\n", encoding="utf-8")
    a = analyze(main)
    assert any(r.command == "puts" and r.arguments == "inner" for r in a.records)


def test_var_in_source_path(tmp_path):
    (tmp_path / "cfg.tcl").write_text("puts loaded\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("set NAME cfg\nsource $NAME.tcl\n", encoding="utf-8")
    a = analyze(main)
    assert any(r.command == "puts" for r in a.records)


def test_missing_file_warns_keeps_record(tmp_path):
    main = tmp_path / "main.tcl"
    main.write_text("source nope.tcl\nputs after\n", encoding="utf-8")
    a = analyze(main)
    assert any("cannot read sourced file" in w for w in a.warnings)
    assert [r.command for r in a.records] == ["source", "puts"]


def test_unresolvable_path_warns(tmp_path):
    main = tmp_path / "main.tcl"
    main.write_text("source $UNKNOWN_DIR/x.tcl\n", encoding="utf-8")
    a = analyze(main)
    assert any("cannot resolve source path" in w for w in a.warnings)


def test_cycle_detection(tmp_path):
    (tmp_path / "a.tcl").write_text("source b.tcl\n", encoding="utf-8")
    (tmp_path / "b.tcl").write_text("source a.tcl\nputs b\n", encoding="utf-8")
    a = analyze(tmp_path / "a.tcl")
    assert any("source cycle" in w for w in a.warnings)
    assert any(r.command == "puts" for r in a.records)  # b still analyzed


def test_no_follow_source(tmp_path):
    (tmp_path / "sub.tcl").write_text("puts sub\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("source sub.tcl\n", encoding="utf-8")
    a = analyze(main, follow_source=False)
    assert [r.command for r in a.records] == ["source"]
    assert not a.warnings


def test_source_in_dead_branch_not_followed(tmp_path):
    (tmp_path / "sub.tcl").write_text("puts sub\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("if {0} {\n  source sub.tcl\n}\n", encoding="utf-8")
    a = analyze(main)
    assert not any(r.command == "puts" for r in a.records)


def test_source_in_unknown_branch_followed_as_unknown(tmp_path):
    (tmp_path / "sub.tcl").write_text("puts sub\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("if {$u} {\n  source sub.tcl\n}\n", encoding="utf-8")
    a = analyze(main)
    puts = [r for r in a.records if r.command == "puts"][0]
    assert puts.active == "unknown"
    assert puts.condition_chain == "if {$u}"


def test_file_column_only_when_multi(tmp_path):
    (tmp_path / "sub.tcl").write_text("puts sub\n", encoding="utf-8")
    main = tmp_path / "main.tcl"
    main.write_text("source sub.tcl\nputs main\n", encoding="utf-8")
    a = analyze(main)
    tables = build_command_tables(a.records)
    assert tables["puts"]["columns"][:2] == ["file", "line"]

    single = tmp_path / "single.tcl"
    single.write_text("puts only\n", encoding="utf-8")
    b = analyze(single)
    assert build_command_tables(b.records)["puts"]["columns"][0] == "line"
