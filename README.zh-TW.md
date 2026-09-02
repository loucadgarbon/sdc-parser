# sdc-parser (`tclscan`)（中文版）

TCL/SDC 腳本靜態分析器：**不執行腳本**，直接解析 TCL 原始碼，把每個指令實例
輸出成 per-command 表格（CSV / Excel / JSON）——每種指令一個 sheet/CSV、每個參數
一欄，附巢狀 if/迴圈/proc 上下文、變數展開，以及該指令「是否真的會生效」的
三態判定（`active` = yes / no / unknown）。

典型用途：稽核被 `if {$MODE == ...}`、迴圈與變數間接包住的 SDC 時序約束
（`create_clock`、`set_false_path`…），看在某組參數下哪些約束會生效。

> English version: [README.md](README.md)。設計文件：[docs/design.zh-TW.md](docs/design.zh-TW.md)；
> 語意規格：[docs/semantics.zh-TW.md](docs/semantics.zh-TW.md)。

## 功能

- **真正的 TCL 解析**：手寫字元級掃描（brace/quote/bracket、`\` 續行、`;#` 註解），
  行號跨任意巢狀正確。不是 regex 湊合。
- **控制流走訪**：`if`/`elseif`/`else`、`foreach`/`for`/`while`、`proc`、
  `switch`（含 fallthrough）、`catch`、`source` 跟隨。
- **變數展開**：追蹤 `set`/`append`/`lappend`/`incr`；每列附
  `arguments_expanded` 與 `expand_status`（full / partial / none）。
- **分支生效判定**：手寫三態 `expr` 求值器判斷分支是否成立；
  `active` = `yes` / `no` / `unknown`。死分支完全不動變數環境。
- **參數化**：用 `--params file.tcl` 或 `-D NAME=VALUE` 給初始值；腳本內的
  `set` 可以覆寫（define 是「初始值」，見語意規格）。
- **迴圈展開**：`--unroll` 把列表為靜態已知的 `foreach` 展開成每迭代一組列，
  迴圈變數真代入。
- **Diff 模式**：比較兩個檔案、或同一檔案在兩組參數下的差異
  （added / removed / changed）。
- **三種輸出**：CSV 一組、一個帶樣式的 `.xlsx`、以及確定性 JSON
  （`schema_version: 1`，無 timestamp，可直接 diff）。

## 安裝

需要 Python ≥ 3.10。在 repo 根目錄：

```
pip install -e ".[dev]"
```

本專案的參考環境（只有 uv、無系統 Python、公司 TLS proxy）：

```
uv pip install --native-tls -e ".[dev]"
```

裝完會有 `tclscan` 指令（venv 下為 `.venv\Scripts\tclscan`）。

## 快速上手

```
tclscan tests/fixtures/sample.tcl -o demo --out-dir out --format all
```

終端會印 Summary 表與「Unresolved variables」提示表，並寫出：

```
out/demo_summary.csv        # 每種指令一列：count、active 統計、行號、signature、conditions
out/demo_variables.csv      # 變數報告：值、是否 conditional、set 次數、未解析使用處
out/demo_create_clock.csv   # 每種指令一個 CSV …
out/demo_set_false_path.csv
out/demo.xlsx               # 上述全部合成一個 workbook（帶樣式）
out/demo.json               # 機器可讀文件
```

per-command CSV 的欄位：每個選項一欄（選項名即欄名）、位置參數為 `argN`，
之後是上下文欄：

```
line,-name,-period,arg1,arguments_expanded,expand_status,active,condition_chain,condition_chain_expanded,loop_context,proc,raw
6,clk,$PERIOD,[get_ports clk],-name clk -period 10.0 [get_ports clk],full,yes,,,,,create_clock -name clk -period $PERIOD [get_ports clk]
```

無值旗標存成 `Y`；重複選項（如 `-group`）在同一格用 `"; "` 串接。只有列
橫跨多個檔案時（多輸入或 `source` 跟隨）才會多一個 `file` 欄。

用 define 決定分支：

```
tclscan constraints.tcl -D MODE=scan --filter-active yes -f xlsx
```

同一腳本、兩組參數的 diff：

```
tclscan constraints.tcl -D MODE=func --diff constraints.tcl --diff-define MODE=scan
```

## CLI 參考

```
tclscan [OPTIONS] FILES...
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `-o, --output NAME` | 第一個輸入的檔名主幹 | 輸出檔基底名稱 |
| `--out-dir DIR` | `.` | 輸出目錄（不存在會建立） |
| `-f, --format csv\|xlsx\|json\|both\|all` | `both` | `both` = csv+xlsx，`all` = csv+xlsx+json |
| `--params FILE` | — | 內容為 `set NAME VALUE` / `define NAME VALUE` 的 TCL 檔，作為初始變數值（可重複，依序套用） |
| `-D, --define NAME=VALUE` | — | 行內 define；只給 `NAME` 等同 `=1`。在 `--params` 之後套用（可重複） |
| `--filter-active LIST` | — | 只輸出這些 active 狀態的列，如 `yes` 或 `yes,unknown` |
| `--commands GLOBS` | — | 只輸出符合逗號分隔 glob 的指令，如 `create_*,set_*` |
| `--exclude GLOBS` | — | 排除符合 glob 的指令，如 `puts,set` |
| `--diff FILE` | — | 把 FILE 當 B 側分析，回報 added/removed/changed |
| `--diff-params FILE` | — | B 側的 params 檔；只要給了任一 `--diff-params`/`--diff-define`，就**整組取代** A 側的參數 |
| `--diff-define NAME=VALUE` | — | B 側的行內 define |
| `--table / --no-table` | `--table` | 在終端印 Summary（與 diff）表 |
| `--unroll` | 關 | 展開列表靜態已知的 `foreach`，每迭代一組列 |
| `--max-unroll N` | `100` | `--unroll` 的單迴圈迭代上限 |
| `--follow-source / --no-follow-source` | 開 | 遞迴分析 TCL `source` 載入的檔案 |
| `--tolerant` | 關 | 解析錯誤時警告並跳到下一行，而非直接失敗 |
| `--encoding ENC` | `utf-8-sig` | 輸入檔編碼 |
| `-q, --quiet` | 關 | 只印錯誤 |
| `-v, --verbose` | 關 | 印 per-file 與綁定細節 |
| `--fail-on-unknown` | 關 | 有任何列 `active=unknown` 時以 exit 3 結束 |
| `--version` | — | 印版本後離開 |

### Exit codes

| 代碼 | 意義 |
|---|---|
| 0 | 成功 |
| 1 | 檔案或語法錯誤 |
| 2 | 用法錯誤 |
| 3 | `--fail-on-unknown` 觸發（有分支無法判定） |

## 輸出格式

- **CSV**：`utf-8-sig`（Excel 直開不亂碼）。`<base>_summary.csv`、
  `<base>_variables.csv`、每種指令一個 `<base>_<command>.csv`，
  用 `--diff` 時另有 `<base>_diff.csv`。
- **XLSX**：單一 workbook：Summary、Variables、（有 diff 時）Diff，
  之後每種指令一個 sheet。`active=no` 的列灰色斜體；`active=unknown` 黃底。
- **JSON**：`<base>.json`，`schema_version: 1`，確定性輸出（無 timestamp）。
  頂層 key：`tclscan`（版本）、`schema_version`、`files`、`params`（最終變數
  快照）、`summary`、`commands`（per-command 記錄，`options` / `positionals`
  已拆開）、`variables`、`unresolved`、（有 diff 時）`diff`。

## 語意一段話

define/params 是「初始值」：腳本頂層的 `set` 會覆寫它。`foreach` 變數預設
不綁定（值列表顯示在 `loop_context`；`--unroll` 才逐迭代真綁定）。
`elseif`/`else` 只有在前面所有分支都可證明為 false 時才 `active=yes`；
判不出來的一律降級為 `unknown`，絕不猜。死分支（`active=no`）不動變數環境。
Diff 的識別鍵是 `(command, 未展開 arguments)`。完整規則與理由見
[docs/semantics.zh-TW.md](docs/semantics.zh-TW.md)。

## 開發

```
python -m pytest -q        # 160 個測試
```

模組管線：`parser.py`（字元級掃描）→ `analyzer.py`（控制流走訪）→
`expand.py` / `eval_expr.py`（環境 + 三態 expr）→ `tables.py` →
`export_csv.py` / `export_xlsx.py` / `export_json.py` / `diff.py` → `cli.py`。
架構細節見 [docs/design.zh-TW.md](docs/design.zh-TW.md)。
