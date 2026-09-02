# 設計與架構

本文件說明 `sdc-parser`（CLI 名 `tclscan`）的模組分工、資料流與關鍵設計決策。
語意層面的規則（define 覆寫、active 判定等）獨立寫在 [semantics.zh-TW.md](semantics.zh-TW.md)。

## 資料流總覽

```
輸入 .tcl/.sdc ──> parser.py ──> analyzer.py ──> DetailRecord 列表
                  (字元級掃描)   (控制流走訪，用 expand.py / eval_expr.py)
                                        │
                                        ├─> tables.py（per-command 拆欄）
                                        │       ├─> export_csv.py
                                        │       └─> export_xlsx.py
                                        ├─> export_json.py
                                        └─> diff.py（兩個記錄集比較）
                  cli.py（typer + rich）串起以上所有步驟
```

## 模組分工

### `parser.py` — Stage 1：字元級 TCL 掃描

把腳本切成 Command（由 Word 組成），**不**解讀控制流。處理 brace/quote/
bracket 巢狀、`\` 續行、`;` 指令分隔、`;#` 與行首 `#` 註解。行號在所有巢狀
中持續追蹤，讓控制結構的 body 之後能以正確的行偏移重新解析。

**為什麼手寫字元級掃描而不是 regex**：TCL 的 brace/quote/bracket 可以任意
巢狀，regex 無法正確配對（這是文法遞迴問題）；行號也必須跨續行與巢狀正確，
regex 做不到。手寫掃描器讓每個字元只看一次，行為完全可控、可測。

### `analyzer.py` — Stage 2：控制流走訪（最大的檔案）

拿 parser 的 Command 流，辨識 `if`/`elseif`/`else`、`foreach`/`for`/`while`、
`proc`、`switch`（含 fallthrough）、`catch`、`source`，遞迴走進 brace body，
同時維護三個 stack：條件（conds）、迴圈（loops）、proc。每個指令實例輸出一筆
`DetailRecord`（見 `model.py`），帶上：

- `condition_chain` / `condition_chain_expanded`：巢狀條件鏈（原文與展開後）
- `loop_context`：所在迴圈與其值列表
- `proc`：所在 proc 名稱
- `active`：三態生效判定（yes / no / unknown），由 `eval_expr.py` 對展開後的
  條件求值、再依 elseif/else 的「前面分支必須為 false」規則合成
- `arguments` / `arguments_expanded` / `expand_status`

也負責 `--unroll`（foreach 靜態列表展開，逐迭代綁定迴圈變數）與
`--follow-source`（遞迴分析 `source` 的檔案）。

### `expand.py` — 變數環境與 `$var` 代換

Best-effort 靜態環境：只追蹤靜態可知的字面值，追蹤 `set`/`append`/
`lappend`/`incr`。條件/迴圈/proc 內建立的綁定標為 conditional，任何用到它的
展開最多只能算 `partial`。每次 bind/invalidate 都記 origin 與統計，供
Variables 報告使用。`[bracket]` 代換在這裡一律不求值（整-word 的
`[expr {...}]` 由 analyzer 處理）。

**環境 gating 規則**：`active=False` 的死分支完全不動環境；確定生效的分支
（頂層或可證明為真）綁定為非 conditional；判不出的分支綁定為 conditional。
這保證環境永遠不會被不會執行的程式碼污染。

### `eval_expr.py` — 三態 expr 求值器

輸入是 `$var` 展開後的條件字串，輸出 True / False / None（unknown）。支援
子集之外的任何構造——殘留 `$`、`[bracket]`、三元、shift、語法錯——一律降級為
unknown，**絕不丟例外、絕不猜**。另提供 `eval_value` 計算整-word
`[expr {...}]` 的值（所以 `set x [expr {1+2}]` 會綁 `"3"`）。

**為什麼不用 Python `eval()`**：(1) 注入風險——條件字串來自被分析的腳本；
(2) 語意錯——TCL 的字串比較、`eq`/`ne`、glob 規則與 Python 不同。手寫求值器
讓「不確定」有明確的表達（tri-state），這是整個 active 判定的基礎。

`switch` 的 glob 比對用 `fnmatch.fnmatchcase`；pattern 含 `\` 或 `[!` 時
降級為 None（fnmatch 語意與 TCL 不完全相容的部分不硬翻）。

### `tables.py` — per-command 拆欄

把 `DetailRecord` 依指令名分組成 `{command: {columns, rows}}`：選項名當欄名、
位置參數 `argN`、無值旗標存 `Y`、重複選項同格以 `"; "` 串接，之後接固定的
上下文欄（`arguments_expanded` … `raw`）。只有記錄橫跨多個檔案時才加
`file` 欄，單檔輸出保持乾淨。

### 輸出層

- `export_csv.py`：`utf-8-sig`（Excel 直開）。summary / variables / diff /
  per-command 各一檔。
- `export_xlsx.py`：openpyxl 單一 workbook；`active=no` 灰斜體、
  `active=unknown` 黃底，讓人一眼掃出死程式碼與不確定處。
- `export_json.py`：`schema_version: 1`，**確定性輸出**（無 timestamp），
  同一輸入永遠產生 byte-identical 的檔案，可直接進版控或 diff。
- `diff.py`：識別鍵 = `(command, 未展開 arguments)`；差異的 payload 是
  active 與展開結果。用未展開參數當鍵，同一行程式碼在兩組參數下才會對得上
  （「changed」而非一刪一增）。

### `cli.py` — typer + rich

組合所有步驟；選項全表與 exit codes 見 [README](../README.md)。錯誤處理
原則：檔案/語法錯 exit 1、用法錯 exit 2、`--fail-on-unknown` exit 3。

## 關鍵設計決策一覽

| 決策 | 原因 |
|---|---|
| 手寫字元級 parser，不用 regex | TCL 文法遞迴，regex 無法正確配對巢狀；行號要求精確 |
| 手寫 tri-state expr 求值器，不用 `eval()` | 注入風險 + Python/TCL 語意差異；「不確定」需要一等公民的表達 |
| 判不出來一律 `unknown`，絕不猜 | 錯的 yes/no 比 unknown 有害；`--fail-on-unknown` 讓 CI 能強制全部可判定 |
| 死分支不動變數環境 | 否則死程式碼的 `set` 會污染後續展開與判定 |
| define 是初始值、可被腳本 `set` 覆寫 | 對齊 TCL 實際執行語意（先有環境、腳本照跑） |
| `foreach` 預設不綁定變數 | 一個變數多個值，任選其一都是錯的；`--unroll` 才逐迭代真綁定 |
| JSON 無 timestamp | 輸出確定性，能 diff、能進版控、測試好寫 |
| diff 鍵用未展開 arguments | 讓「同一行、不同參數」對得上而不是 removed+added |

## 測試

`tests/` 依模組分檔（test_parser / analyzer / expand / eval_expr / active /
source / unroll / variables / e2e），共 160 個測試；e2e 用 typer 的
`CliRunner`。fixture `tests/fixtures/sample.tcl` 的行號被測試精確引用，
**改動 fixture 必須同步改測試**。

```
python -m pytest -q
```
