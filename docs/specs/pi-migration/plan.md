---
summary: Architecture decisions (DEC), module layout, interfaces, and change map for the pi-native rewrite
read_when:
  - Implementing any task from tasks.md
  - Questioning why a design choice was made or when to overturn it
  - Adding a detector, cue pack, or output format
---

# Plan: Pi-Native Self-Improvement Loop

## Technical Approach

- 全新 Python 3.10+ stdlib-only package。移植上游 agent-improvement-loop 的**概念**（four-phase pipeline、4-route + discard、resolutions watermark、recurrence、redaction、precision guards），不搬程式碼；每條 detector 語義對照上游文件重寫並以 fixture 測試釘住。
- Parser 只支援 pi transcript 一種 source：`message` records 的 `user` / `assistant` / `toolResult` 三種 role，`toolResult.isError` 為 failure 主訊號。
- Correction 偵測抽象為 cue pack（內建 `en`、`zh-Hant`），每個 pack 自帶 strong/weak cues、長度閘門與 negative guards；CJK 不依賴 `\b`。
- Staging、state、resolutions 的檔案 schema 沿用上游概念與欄位命名（deterministic proposal id、`decisions.json` handoff），review 工作流與 prompt 可直接轉用。
- Closing half v1 為單機精簡版：`FIX-QUEUE.md` + `decisions/` 目錄 + learn-loop pi skill + `pi -p` headless runner；不建 leader/catalog 機制，但檔名格式從第一天就 fleet 前向相容。

## Decisions

### DEC-001: Pi-native rewrite, not a fork

**Choice**: 全新實作，只支援 pi；上游概念與教訓以文件對照方式移植。
**Alternatives**: (a) vendor 上游 3097 行單檔加 `parse_pi_session`——保留 detector 精度但背上 Codex/Hermes 死重與手動同步；(b) import 上游當 library——上游是 script 非 library，內部 API 無穩定性承諾。
**Rationale**: User 決策。程式碼所有權、精簡度、pi-first 設計（skill-read 偵測、扁平 tool 命名、bilingual corrections 為一等公民）優先於重用。精度回歸風險以「上游 precision guards 逐條轉為 test case」緩解（見 DEC-012）。
**Satisfies**: 全部 REQ

### DEC-002: Python stdlib package, not TypeScript

**Choice**: Python >= 3.10、零依賴、`src/` layout package，經 pipx/uv 安裝。
**Alternatives**: TypeScript pi extension——miner 是離線 batch job，不需要 pi runtime 整合；把 20+ 條微妙的 detector regex 從 Python 語義翻到 JS 語義是純風險零收益。
**Rationale**: 上游語義以 Python regex 表達，1:1 對照重寫最能守住行為；排程由 launchd 負責，與 pi process 無耦合。
**Satisfies**: NFR（Dependencies、Runtime）

### DEC-003: Module layout

**Choice**: `src/pi_self_improvement/` 下十個模組：`model`（dataclasses）、`parse`（JSONL → SessionSummary）、`redact`、`cues`（language packs）、`detect`（friction detectors）、`route`、`stage`（outputs + packet）、`state`（seen/recurrence + resolutions）、`config`、`cli`。
**Alternatives**: 上游式單檔——重寫正是模組化與可測性的機會；單檔的 global-config pattern 是上游可 import 性差的主因。
**Rationale**: 每個模組對應 pipeline 一段，fixture 測試可以逐段釘住；`cues` 獨立讓語言包成為可擴充介面。
**Satisfies**: REQ-002–REQ-019

### DEC-004: Output root `~/.pi-self-improvement/`, upstream-compatible schemas

**Choice**: 預設 output root 為 `~/.pi-self-improvement/`；`state.json`、`resolutions.json`、proposal JSON、`decisions.json` 欄位沿用上游 schema_version 1 的概念與命名。
**Alternatives**: 沿用 `~/.agent-improvement/`——與上游工具共用 state 會互相污染 seen-key 與 recurrence。
**Rationale**: 過渡期兩套工具並行互不干擾；schema 相容讓上游的 reviewer prompt 與 decisions 匯入工作流零成本轉用。
**Satisfies**: REQ-015、REQ-016、REQ-017

### DEC-005: `isError` first, text heuristics as drift safety net

**Choice**: failure 判定以 `toolResult.isError` 為準；欄位缺席才落到 text heuristics（exit-code 樣式、error 片語）。
**Alternatives**: heuristics 永遠參與——上游經驗證明會把含 "error" 字樣的成功 build log 誤判為 failure。
**Rationale**: 作者語料 717/717 筆 toolResult 都帶 `isError`，flag 可信；heuristics 保留是為了 pi 格式未來 drift 時不至全盲。
**Satisfies**: REQ-005

### DEC-006: Skill detection via read-path heuristic

**Choice**: `read` tool call 的 `path` 以 `SKILL.md` 結尾 → skill invocation，名稱取 path 的父目錄名。
**Alternatives**: 不偵測 skill（上游的 `Skill` tool 在 pi 不存在）——會直接廢掉 `skill_improvement` route。
**Rationale**: pi 的 skill 載入機制就是「read SKILL.md」；作者語料驗證此訊號密度高（單一 skill 400 sessions 內 77 次），比上游的專用 tool 更穩定。
**Satisfies**: REQ-009

### DEC-007: Extension-tool family grouping

**Choice**: builtin tool 名單（`read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`/`ask`/`todo` 等）排除後，其餘 tool 以第一個 `_` 前的 token 為 family（`jira_issue` → `jira`），config 提供 override map 處理例外；target 格式 `ext:<family>`。
**Alternatives**: 每個 tool 獨立 target——同一 extension 的失敗會被稀釋到十幾個 target，永遠到不了 recurrence 門檻。
**Rationale**: 對應上游 `mcp:<server>` 的分組語義；pi 的扁平命名慣例（`jira_*`、`confluence_*`、`cymbal_*`）讓 first-token 分組覆蓋大多數情況，例外走 config。
**Satisfies**: REQ-014

### DEC-008: Correction cue packs

**Choice**: 每個 pack 定義 strong cues、weak cues、長度閘門與 negative guards。內建兩包：

| Pack | Strong cues（一般長度有效） | Weak cues（僅短訊息） | Gates (chars) | Negative guards |
|------|------------------------------|------------------------|---------------|-----------------|
| `en` | that's wrong / not what I asked / you missed / never do / stop doing / you should have / why did you / I meant / remember this | instead / don't do / do not / should have / 行首 actually | strong 2000 / weak 400 | — |
| `zh-Hant` | 不對 / 不是這樣 / 你搞錯 / 我是說 / 我的意思是 / 為什麼你 / 你應該先 / 記住 | 應該 / 改成 / 不要 / 直接 / 重來 | strong 1000 / weak 150 | 沒錯 / 不錯 / 沒問題 / 還不錯 |

CJK cue 以 substring + guard 比對（不用 `\b`，漢字無詞界）；guard 命中則整句跳過。閘門初始值反映中文約 2 倍的資訊密度，由 corpus test 迭代校準。
**Alternatives**: 單一混語 regex——英文 `\b` 語義與中文 substring 語義混在一條 pattern 裡無法各自調精度。
**Rationale**: 作者語料 85% user 訊息含 CJK，上游英文 cue 在 400 sessions 只命中 2 次且皆為 false positive——bilingual 是這次重寫的核心價值，pack 抽象讓其他語言可後續加入。
**Satisfies**: REQ-010

### DEC-009: Pi scaffold defaults

**Choice**: 移植上游 leading-XML-tag 規則，內建 marker 名單針對 pi 生態：box-drawing separator 行（連續 `─` >= 10）、`[Project docs index]`、subagent task seed（`Task:` 開頭）、steering 注入（`Mid-run steering`）、tool 提示注入（`Cymbal suggests:`）、`reply with exactly:`；config `extra_scaffold_markers` 擴充。
**Alternatives**: 只靠 leading-tag 規則——作者語料顯示 pi 注入最常見的形態是 separator 行（400 sessions 內 88 次），不帶 XML tag。
**Rationale**: 迴圈必須吃自己的狗糧：自家 harness 的注入若不過濾，系統會把自己標成 user friction（上游 CLOSING-THE-LOOP 教訓 #8）。
**Satisfies**: REQ-011

### DEC-010: Closing-half-lite with fleet-forward file naming

**Choice**: v1 只做 `queue/FIX-QUEUE.md`、`decisions/<ID>.json`、learn-loop skill、fixloop runner；不移植 catalog generation、conflicts、rekey、leader collect。entry 與 decision 檔名從第一天採 `<machine>--<ID>` 前綴。
**Alternatives**: 完整移植上游 learnings store（1670 行）——catalog generation 與 conflict 偵測是多 writer 問題的解，單機沒有這個問題。
**Rationale**: 單機 v1 不為不存在的問題付複雜度；machine 前綴檔名讓 fleet phase 到來時只加 collector、不 migrate 資料。
**Satisfies**: REQ-020

### DEC-011: Runner fuse and no-shell allowlist

**Choice**: fixloop runner 以 `pi -p` 執行，`--tools read,grep,find,ls,write,edit`（無 `bash`）；pi 沒有 `--max-turns`，wall-clock fuse 以 shell 背景計時實作（不依賴 GNU coreutils `timeout`）；RUN liveness line 無條件寫入 `~/Library/Logs`。
**Alternatives**: 信任 headless run 自然收斂——上游教訓：無 fuse 的排程 agent 是無人看管的失控成本；寫 liveness 進資料目錄——launchd 的 `/bin/bash` 可能無 TCC 權限寫入受保護路徑，死信號正是它要防的盲區。
**Rationale**: 「daily pass 無 shell」是上游安全模型的支柱，pi 的 `--tools` allowlist 剛好原生支援；liveness 位置直接採納上游踩過的 TCC 坑。
**Satisfies**: REQ-020、REQ-021

### DEC-012: Testing strategy and benchmark baseline

**Choice**: stdlib `unittest`。三類 corpus tests：redaction（secret 形狀零存活）、corrections（bilingual，含已知 false positives：貼上文件中的 "use X instead"、orchestrator steering 注入、「沒錯」肯定句）、scaffold。E2E 以 synthetic fixture 跑完整 pipeline。真實語料 parity 基準（作者首次量測：400 sessions、20207 tool calls、272 sessions with signals、0 parse errors）記錄於此，dogfood 執行時對照。
**Alternatives**: pytest——多一個 dev 依賴，違反零依賴原則的精神。
**Rationale**: 上游的 redaction corpus test 是其安全模型的執行機制，照搬；false-positive corpus 把 feasibility 調查抓到的實際誤判釘成 regression tests。
**Satisfies**: REQ-004、REQ-010、REQ-018、NFR

### DEC-013: Fresh-install bootstrap: no automatic backfill

**Choice**: scan 行為不因 state 存在與否而改變——首次執行（`state.json` 不存在）時 windowed scan 與平常完全相同，時間窗外的歷史 session 只能由顯式 `--all` 帶入；README install 段提供 bootstrap 指引（先 `--all --dry-run` 預覽，再決定是否實跑 backfill，`--max-sessions` 作為上限）。
**Alternatives**: 偵測到無 state 時自動掃全部歷史——首份 review packet 會被整個歷史的 proposal 灌爆，違背小批次人工核准的工作模型，且排程首次 fire 的成本不可預測。
**Rationale**: 可預測性優先：在已有 pi 歷史的機器上首次安裝，排程的第一次執行與其後每次執行行為一致；backfill 是一次性、有人看管的顯式動作。
**Satisfies**: REQ-002

## Interfaces / Contracts

CLI（安裝後指令名 `pi-self-improvement`）：

```text
pi-self-improvement [--since-days N | --all] [--max-sessions N]
                    [--dry-run] [--full] [--include-seen] [--include-resolved]
                    [--home PATH] [--output-root PATH] [--config PATH]
                    [--machine NAME]
pi-self-improvement --resolve ROUTE:TARGET --decision {fixed,wontfix,ignored}
                    [--pr V] [--note T] [--by N] [--resolved-at TS]
pi-self-improvement --resolve-from decisions.json | --list-resolutions | --unresolve T
```

Config keys（`~/.pi-self-improvement/config.json`）：`tracked_clis`、`tracked_cli_suffix`、`cue_packs`（enable/extend）、`extra_scaffold_markers`、`extra_redaction_patterns`、`ext_family_map`、`extra_backlog_ignore`、`include_subagent_failures`、`detect_silent_empty`、`silent_empty_fetch_verbs`、`silent_empty_ignore`。

## Change Map

| File | Action | Change | Satisfies |
|------|--------|--------|-----------|
| `pyproject.toml` | Create | Package metadata, `pi-self-improvement` entry point, Python >= 3.10 | NFR |
| `src/pi_self_improvement/model.py` | Create | `Evidence` / `ToolCall` / `SessionSummary` dataclasses | REQ-003 |
| `src/pi_self_improvement/parse.py` | Create | Pi JSONL parser: roles, toolCall/toolResult pairing, timestamps, cwd, subagent origin | REQ-002, REQ-005, REQ-012 |
| `src/pi_self_improvement/redact.py` | Create | Secret-shape masking, excerpt shortening | REQ-004 |
| `src/pi_self_improvement/cues.py` | Create | Cue pack model, built-in `en` + `zh-Hant`, guard logic | REQ-010 |
| `src/pi_self_improvement/detect.py` | Create | Failure/hang/retry/silent-empty/skill/correction detectors, scaffold filter | REQ-005–REQ-011 |
| `src/pi_self_improvement/route.py` | Create | 4-route assignment, ext-family grouping, discard rules | REQ-013, REQ-014 |
| `src/pi_self_improvement/stage.py` | Create | Run metadata, proposal JSON, review packet writer | REQ-015 |
| `src/pi_self_improvement/state.py` | Create | Seen keys, recurrence history, resolutions registry, self-check stats | REQ-016–REQ-018 |
| `src/pi_self_improvement/config.py` | Create | JSON config load/validate/apply | REQ-019 |
| `src/pi_self_improvement/cli.py` | Create | argparse wiring, scan + resolutions subflows | REQ-001, REQ-015 |
| `tests/fixtures/` | Create | Synthetic pi transcript fixtures（絕不含真實 transcript 內容） | 全部 |
| `tests/test_*.py` | Create | Unit + corpus + e2e tests | 全部 |
| `skills/learn-loop/SKILL.md` | Create | Interactive execution skill, pi skill format | REQ-020 |
| `templates/fixloop-prompt.md` | Create | Headless triage prompt | REQ-020 |
| `templates/fixloop-run.sh` | Create | Runner: `pi -p` no-shell allowlist, fuse, RUN liveness line | REQ-020 |
| `templates/miner-run.sh` | Create | Miner runner with unconditional RUN line | REQ-021 |
| `examples/*.plist` | Create | launchd schedules（miner 每週兩次 `--since-days 4`、fixloop daily） | REQ-021 |

## Verification

| Layer | What | How |
|-------|------|-----|
| Unit | Parser、每個 detector、routing、state、resolutions | `python3 -m unittest discover -s tests` |
| Corpus | Redaction 零存活、bilingual corrections 含 false-positive 回歸、scaffold | 同上（`test_redaction_corpus` / `test_corrections_corpus` / `test_scaffold_corpus`） |
| E2E | Synthetic fixture 跑完整 scan → 驗證 outputs、packet、dedup、resolutions 生效 | `test_e2e.py` |
| Dogfood | 真實語料 `--dry-run` 對照 DEC-012 基準；首份 review packet 人工評讀 | `pi-self-improvement --all --dry-run` |
