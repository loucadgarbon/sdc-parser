# 語意規格

`tclscan` 是靜態分析器：不執行腳本，所有語意都是「對 TCL 執行語意的靜態近似」。
本文件是這些近似規則的權威定義。原則只有一條：**判不出來就說 unknown，絕不猜**。

## 1. 變數與參數

### 1.1 define / params 是「初始值」

`--params file.tcl`（依序）與 `-D NAME=VALUE`（在 params 之後）建立初始變數
環境。腳本內頂層的 `set` **會覆寫**這些值——與真正執行 TCL 時一致：環境先
存在，腳本照跑。

推論：若腳本自己 `set MODE func`，對它下 `-D MODE=scan` 不會改變分支結果
（`set` 在 `-D` 之後執行）。要鎖定 define 不被覆寫是曾討論過但未實作的
`--freeze-defines`。

- `-D NAME`（無 `=`）等同 `NAME=1`。
- params 檔接受 `set NAME VALUE` 與 `define NAME VALUE` 兩種寫法。

### 1.2 追蹤的指令

`set`、`append`、`lappend`、`incr` 會更新環境；`unset` 與 `gets` 會使
目標變數失效（invalidate）。在迴圈內、或值無法靜態解析時，寫入也降級為
失效而非猜值。

### 1.3 條件綁定（conditional）

- 頂層、或位於**可證明生效**分支內的綁定：非 conditional。
- 位於 unknown 分支、迴圈、proc 內的綁定：conditional。用到 conditional
  值的展開，`expand_status` 最多 `partial`。
- **死分支（`active=no`）內的綁定完全不進環境。**

### 1.4 `[expr {...}]` 求值

`set x [expr {1+2}]` 中整-word 的 `[expr {...}]` 若能靜態求值，`x` 綁定其
結果（此例 `"3"`）。求不出（含未知變數、不支援的構造）則 `x` 失效。
其他 `[bracket]` 一律不求值。

## 2. `active` 三態判定

每筆記錄的 `active` ∈ {`yes`, `no`, `unknown`}：

- `yes`：所在的每一層條件都可證明為真（或在頂層）。
- `no`：任一層條件可證明為假。
- `unknown`：其餘所有情況。

### 2.1 `if` / `elseif` / `else`

`elseif`/`else` 分支要 `active=yes`，必須**前面所有分支都可證明為 false**
且自身條件（若有）可證明為 true。前面任何一個分支是 unknown，後面的分支
最多只能是 unknown。

### 2.2 條件求值

條件先做 `$var` 展開，再交給三態求值器。支援子集之外的構造（殘留 `$`、
`[bracket]`、三元、shift、語法錯誤）一律得 None → unknown。比較遵循 TCL
語意（數值優先、字串比較、`eq`/`ne`）。

### 2.3 `switch`

- `-exact`：字串相等比較。
- `-glob`：以 `fnmatch.fnmatchcase` 近似；pattern 含 `\` 或 `[!` 時該分支
  降級 unknown（fnmatch 與 TCL glob 語意不完全相同的部分不硬翻）。
- fallthrough（body 為 `-`）：正確接到下一個有 body 的分支。
- `default`：所有前面分支都可證明不中時為 yes，否則 unknown/no 依前述規則。

### 2.4 迴圈與 `catch`

`foreach`/`for`/`while` body、`catch` body 內的指令：active 依外層條件計，
迴圈本身不使 active 降級（body 至少概念上可能執行；`while {0}` 這類可證明
不執行的情況判 no）。

## 3. 迴圈

### 3.1 `foreach`（預設，不 unroll）

迴圈變數**不綁定**（一個變數對多個值，任選其一都錯），且會使該名稱先前的
綁定**失效**（避免外層同名變數的舊值滲入 body）。值列表顯示在
`loop_context`，body 內用到迴圈變數的展開為 partial/none。

### 3.2 `--unroll`

列表為靜態已知的 `foreach` 展開為每迭代一組記錄，迴圈變數**逐迭代真綁定**，
且這些記錄不算 in_loop（`loop_context` 不再標示該層迴圈）。上限
`--max-unroll`（預設 100）。`break`/`continue` 目前**忽略**（過近似）：
break 之後的迭代仍會產出，這是已知限制。

### 3.3 `for` / `while`

不展開。`for {set i 0}` 的初始 `set` 依 1.3 的 conditional 規則處理。

## 4. `source` 跟隨

`--follow-source`（預設開）時，`source path` 若路徑可靜態解析，遞迴分析該
檔，記錄的 `file` 欄位為實際來源檔。解析不出的 `source` 記警告。
`--no-follow-source` 關閉。

## 5. Diff

- 識別鍵 = `(command, 未展開 arguments)`。同一行程式碼在兩組參數下鍵相同，
  差異（active、展開結果）呈現為 `changed`，而非 removed+added。
- `--diff-params` / `--diff-define` **只要給了任一個**，就整組取代 A 側的
  params+defines（不是疊加）。都不給時 B 側沿用 A 側參數。

## 6. 輸出層語意

- 無值旗標（如 `-asynchronous`）在表格中存 `Y`。
- 重複選項（如多個 `-group`）同格以 `"; "` 串接；JSON 中為列表。
- `file` 欄只在記錄橫跨多檔時出現。
- `expand_status`：`full`（全部展開成功）/ `partial`（有 conditional 或
  部分未知）/ `none`。
- JSON 確定性：無 timestamp，同輸入同輸出（`schema_version: 1`）。
- Exit codes：0 成功；1 檔案/語法錯；2 用法錯；3 `--fail-on-unknown` 且存在
  `active=unknown` 的列。

## 7. 已知限制（刻意的近似）

- unroll 忽略 `break`/`continue`。
- proc 不做呼叫端內聯：proc body 記錄一次，`proc` 欄標示所屬。
- `upvar`/`uplevel`/`eval`/`namespace` 等動態構造不追蹤也不特別處理
  （它們對環境的影響不會被看見）；只有 `unset`/`gets` 明確使變數失效。
- `while`/`for` 不展開、不判迭代次數（除條件可證明恆假）。
