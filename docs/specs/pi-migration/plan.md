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
- Parser 只支援 pi transcript 一種 source：`message` records 的 `user` / `assistant` / `toolResult` / `bashExecution` 四種 role，`toolResult.isError` 為 failure 主訊號；其餘 record types（`custom`、`custom_message`、`compaction`、`branch_summary` 等）不進入偵測但必須計數。Transcript 依檔案行序解析，不走 `parentId` 樹。
- Correction 偵測抽象為 cue pack（內建 `en`、`zh-Hant`），每個 pack 自帶 strong/weak cues、長度閘門與 negative guards；CJK 不依賴 `\b`。
- Staging、state、resolutions 的檔案 schema 沿用上游概念與欄位命名（deterministic proposal id、`decisions.json` handoff），review 工作流與 prompt 可直接轉用。
- Closing half v1 為單機精簡版：`FIX-QUEUE.md` + `decisions/<ID>.json`（logical ID）+ learn-loop pi skill + `pi -p` headless runner + host-side deterministic writer；不建 leader/catalog 機制。

## Decisions

### DEC-001: Pi-native rewrite, not a fork

**Choice**: 全新實作，只支援 pi；上游概念與教訓以文件對照方式移植。
**Alternatives**: (a) vendor 上游 3097 行單檔加 `parse_pi_session`——保留 detector 精度但背上 Codex/Hermes 死重與手動同步；(b) import 上游當 library——上游是 script 非 library，內部 API 無穩定性承諾。
**Rationale**: User 決策。程式碼所有權、精簡度、pi-first 設計（skill-read 偵測、扁平 tool 命名、bilingual corrections 為一等公民）優先於重用。精度回歸風險以「上游 precision guards 逐條轉為 test case」緩解（見 DEC-012）。
**Satisfies**: 全部 REQ
**Record**: [ADR-0001](../../adr/0001-pi-native-rewrite-not-a-fork.md)

### DEC-002: Python stdlib package, not TypeScript

**Choice**: Python >= 3.10、零依賴、`src/` layout package，經 pipx/uv 安裝。
**Alternatives**: TypeScript pi extension——miner 是離線 batch job，不需要 pi runtime 整合；把 20+ 條微妙的 detector regex 從 Python 語義翻到 JS 語義是純風險零收益。
**Rationale**: 上游語義以 Python regex 表達，1:1 對照重寫最能守住行為；排程由 launchd 負責，與 pi process 無耦合。
**Satisfies**: NFR（Dependencies、Runtime）
**Record**: [ADR-0002](../../adr/0002-python-stdlib-not-typescript.md)

### DEC-003: Module layout

**Choice**: `src/pi_self_improvement/` 下十一個模組：`model`（dataclasses）、`parse`（JSONL → SessionSummary）、`redact`、`cues`（language packs）、`detect`（friction detectors）、`route`、`stage`（outputs + packet）、`state`（seen/recurrence + resolutions）、`writer`（host-side queue/decision writer）、`config`、`cli`。
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
**Rationale**: 作者語料實測 20764/20764 筆 toolResult 都帶 `isError`（2026-08 量測，取樣方式見 DEC-012），flag 可信；heuristics 保留是為了 pi 格式未來 drift 時不至全盲。
**Satisfies**: REQ-005

### DEC-006: Skill detection defaults to the read-path heuristic

**Choice**: `read` tool call 的 `path` 以 `SKILL.md` 結尾 → skill invocation，名稱取 path 的父目錄名。此為規格預設；pi extension 寫入的 skill-load custom entry 由 config `skill_loaded_custom_types` 選擇性加入。
**Alternatives**: 以 `context:skill_loaded` custom entry 為預設——它直接帶 skill 名稱、精度更高（作者語料 337 筆），但那是作者個人 extension 發出的訊號、非 pi core 行為；公開工具若以它為預設，其他使用者的 skill 偵測會靜默歸零。
**Rationale**: REQ-019 已明訂預設保持 generic、個人 workflow 細節只存在於 config，這條訊號正屬後者。
**Satisfies**: REQ-009、REQ-019
**Record**: [ADR-0007](../../adr/0007-skill-detection-defaults-to-read-heuristic.md)

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

**Choice**: 結構優先——非 `message` record 不進入 user 文字偵測；內建 marker 名單只保留實測會出現在 user 訊息內文的兩項：box-drawing separator 行（連續 `─` >= 10，90 次）與 `Task:` 開頭的 subagent task seed（87 次）；config `extra_scaffold_markers` 擴充。
**Alternatives**: 保留上游 leading-XML-tag 規則與完整 marker 名單（`[Project docs index]`、`Mid-run steering`、`Cymbal suggests:`）——實測這些注入在 pi 是獨立的 `custom_message` record（docs-list 352 次、pi-cymbal-nudge 734 次），結構上本來就被排除；在 user 訊息內文各只命中 0–1 次，留著只會在使用者「談論」這些 marker 時誤判。
**Rationale**: 迴圈必須吃自己的狗食：自家 harness 的注入若不過濾，系統會把自己標成 user friction（上游 CLOSING-THE-LOOP 教訓 #8）。pi 用 record type 表達注入，所以最強的過濾器是資料模型而非 regex。
**Satisfies**: REQ-011

### DEC-010: Closing-half-lite, logical-ID decisions

**Choice**: v1 只做 `queue/FIX-QUEUE.md`、`decisions/<ID>.json`、learn-loop skill、fixloop runner、host-side writer；不移植 catalog generation、conflicts、rekey、leader collect。decision 以 logical ID 命名，machine 前綴保留給未來的 entry 層級。
**Alternatives**: (a) 完整移植上游 learnings store（1670 行）——catalog generation 與 conflict 偵測是多 writer 問題的解，單機沒有這個問題；(b) 原案的 `<machine>--<ID>` decision 檔名——fleet 到來時同一 logical incident 會分裂成每台機器各一個 outcome，正是它宣稱要避免的 migration。
**Rationale**: 單機 v1 不為不存在的問題付複雜度。前向相容的宣稱收窄為「decision 格式 fleet 相容，entry 匯總延後」。
**Satisfies**: REQ-020

### DEC-011: Read-only runner, host-side writer, wall-clock fuse

**Choice**: fixloop runner 以 `pi -p` 執行，`--tools read,grep,find,ls`（無 shell、無寫入）；模型輸出 structured triage，由 host-side deterministic writer 寫入 output root；pi 沒有 `--max-turns`，wall-clock fuse 以 shell 背景計時實作（不依賴 GNU coreutils `timeout`）；RUN liveness line 無條件寫入 `~/Library/Logs`。
**Alternatives**: (a) 原案的 `read,grep,find,ls,write,edit`——拿掉 `bash` 不限制 `write`/`edit` 的路徑，無人看管的模型仍可直接改 source 與 skills，與 REQ-001 正面衝突；(b) 信任 headless run 自然收斂——上游教訓：無 fuse 的排程 agent 是無人看管的失控成本；(c) 寫 liveness 進資料目錄——launchd 的 `/bin/bash` 可能無 TCC 權限寫入受保護路徑。
**Rationale**: 「daily pass 不能改東西」是整個安全模型的支柱，而 allowlist 只能表達「哪些工具」不能表達「哪些路徑」，所以寫入必須離開模型、交給可讀死的程式碼。
**Satisfies**: REQ-001、REQ-020、REQ-021、REQ-022
**Record**: [ADR-0006](../../adr/0006-unattended-runner-cannot-write.md)

### DEC-012: Testing strategy and dogfood gate

**Choice**: stdlib `unittest`。三類 corpus tests：redaction（secret 形狀零存活，含 command arguments）、corrections（bilingual，含已知 false positives：貼上文件中的 "use X instead"、orchestrator steering 注入、「沒錯」肯定句）、scaffold。E2E 以 synthetic fixture 跑完整 pipeline。Dogfood gate 以 structural invariants 判定：`parse_errors = 0`、`isError` 欄位覆蓋率 100%、每個 detector 至少一筆 fixture 覆蓋、root + subagent session 數總和等於檔案數。
**Alternatives**: (a) pytest——多一個 dev 依賴，違反零依賴原則的精神；(b) 以絕對數字為基準——語料持續成長，任何 count 都會漂移，無法區分回歸與「使用者這週比較忙」。
**Rationale**: 上游的 redaction corpus test 是其安全模型的執行機制，照搬；false-positive corpus 把 feasibility 調查抓到的實際誤判釘成 regression tests。
**Dated observation (2026-08)**: 以 `find ~/.pi/agent/sessions -name '*.jsonl'` 取樣得 419 檔（334 root + 85 subagent）、43236 筆可解析 record、20785 tool calls、20764 toolResults 且 `isError` 覆蓋率 100%（20094 false / 670 true）、user 訊息 1348 筆其中 90.1% 含 CJK、63 檔有分支共 88 個 branch point、`stopReason` aborted 80 / error 151、0 parse errors。此為單次快照，不作為 gate。
**Satisfies**: REQ-004、REQ-010、REQ-018、NFR

### DEC-013: Fresh-install bootstrap: no automatic backfill

**Choice**: scan 行為不因 state 存在與否而改變——首次執行（`state.json` 不存在）時 windowed scan 與平常完全相同，時間窗外的歷史 session 只能由顯式 `--all` 帶入；README install 段提供 bootstrap 指引（先 `--all --dry-run` 預覽，再決定是否實跑 backfill，`--max-sessions` 作為上限）。
**Alternatives**: 偵測到無 state 時自動掃全部歷史——首份 review packet 會被整個歷史的 proposal 灌爆，違背小批次人工核准的工作模型，且排程首次 fire 的成本不可預測。
**Rationale**: 可預測性優先：在已有 pi 歷史的機器上首次安裝，排程的第一次執行與其後每次執行行為一致；backfill 是一次性、有人看管的顯式動作。
**Satisfies**: REQ-002

### DEC-014: Line-order parse, no tree walk

**Choice**: transcript 依檔案行序解析，不沿 `parentId` 回溯 active leaf。
**Alternatives**: 走樹只挖存活鏈（pi 自己 `buildContextEntries()` 的語意）——精度較高，但 session format v1 是線性、無 `parentId`，且 pi 只在「載入」檔案時才遷移版本，miner 直接讀檔不會觸發，樹狀 parser 會在他人機器的舊 transcript 上失效。
**Rationale**: 版本無關性優先於分支精度；代價（sibling branch 錯誤關聯）由 REQ-018 的 branch-point counts 變成可量測數字。
**Satisfies**: REQ-003
**Record**: [ADR-0004](../../adr/0004-line-order-parse-not-tree-walk.md)

### DEC-015: Session model and subagent identification

**Choice**: 每一份 transcript 都是一個 session，以 `origin`（`root` / `subagent`）區分；subagent session 由巢狀路徑 `<session-id>/<hash>/run-N/session.jsonl` 判定；`--max-sessions` 只數 root sessions。
**Alternatives**: (a) subagent run 另立一個名詞——讀起來自然，但每條計數與識別規則都要並列兩種東西；(b) 以 `.pi-subagents/artifacts/` 路徑判定——那是暫存目錄、會被 prune，且不在 `~/.pi/agent/sessions/` 之下，預設 scan 根本走不到。
**Rationale**: 巢狀路徑在 pi 的 session 目錄底下，因此不受 pi-subagents 把 artifacts 從 `<repo>/.pi-subagents/` 搬到 `<repo>/.pi/subagents/` 影響。
**Satisfies**: REQ-002、REQ-012
**Record**: [ADR-0003](../../adr/0003-subagent-conversations-are-sessions.md)

### DEC-016: Target identity and normalization

**Choice**: 四條 route 的 target 依 REQ-013 的表決定；正規化為 realpath 解 symlink → git toplevel → 保留原大小寫，無法判定時 `<unknown>`。
**Alternatives**: 以原始 cwd 分組——同一專案的子目錄與 git worktree 會裂成多個 target，各自都達不到 recurrence 門檻。
**Rationale**: `route:target` 同時是 proposal id、recurrence key 與 resolution key，等同資料格式；先定死再實作，改格式等於作廢所有既存 state。
**Satisfies**: REQ-013、REQ-016、REQ-017
**Record**: [ADR-0005](../../adr/0005-target-identity-normalized-to-repo-root.md)

### DEC-017: Pipeline order and dry-run purity

**Choice**: 固定為 resolution filter → seen-key filter → grouping/staging → recurrence annotation；`fixed` 只讓 watermark 後的 evidence 成為 regression，`wontfix`/`ignored` 永久 suppress，resolve 時移除 watermark 以前的 recurrence history；`--dry-run` 不建立或修改任何 state、output、log。
**Alternatives**: 先做 seen filtering——已見過的 key 會在 resolution 之前被吞掉，regression 永遠浮不出來。
**Rationale**: dry-run 純度不是潔癖：DEC-013 把 `--all --dry-run` 寫進 README 當 backfill 預覽步驟，若預覽會寫 seen state，照做的人會讓正式 backfill 被自己完全 suppress。
**Satisfies**: REQ-016、REQ-017

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

Config keys（`~/.pi-self-improvement/config.json`）：`extra_session_roots`（REQ-002 的額外 roots，附加於預設 root 之後）、`tracked_clis`、`tracked_cli_suffix`、`cue_packs`（enable/extend）、`extra_scaffold_markers`、`extra_redaction_patterns`、`ext_family_map`、`extra_backlog_ignore`、`include_subagent_failures`、`skill_loaded_custom_types`、`detect_silent_empty`、`silent_empty_fetch_verbs`、`silent_empty_ignore`。

## Change Map

| File | Action | Change | Satisfies |
|------|--------|--------|-----------|
| `pyproject.toml` | Create | Package metadata, `pi-self-improvement` entry point, Python >= 3.10 | NFR |
| `src/pi_self_improvement/model.py` | Create | `Evidence` / `ToolCall` / `SessionSummary` dataclasses | REQ-003 |
| `src/pi_self_improvement/parse.py` | Create | Pi JSONL parser：四種 role、toolCall/toolResult pairing、timestamps、cwd、session origin、line-order traversal、record-type counts | REQ-002, REQ-003, REQ-005, REQ-012 |
| `src/pi_self_improvement/redact.py` | Create | Single redaction boundary for all transcript-derived strings, secret-shape masking, excerpt shortening, canary scan | REQ-004 |
| `src/pi_self_improvement/cues.py` | Create | Cue pack model, built-in `en` + `zh-Hant`, guard logic | REQ-010 |
| `src/pi_self_improvement/detect.py` | Create | Failure/hang/retry/silent-empty/skill/correction detectors, scaffold filter | REQ-005–REQ-011 |
| `src/pi_self_improvement/route.py` | Create | 4-route assignment, ext-family grouping, discard rules | REQ-013, REQ-014 |
| `src/pi_self_improvement/stage.py` | Create | Run metadata, proposal JSON, review packet writer | REQ-015 |
| `src/pi_self_improvement/state.py` | Create | Seen keys, recurrence history, resolutions registry, pipeline ordering, self-check counts | REQ-016–REQ-018 |
| `src/pi_self_improvement/config.py` | Create | JSON config load/validate/apply | REQ-019 |
| `src/pi_self_improvement/cli.py` | Create | argparse wiring, scan + resolutions subflows | REQ-001, REQ-015 |
| `tests/fixtures/` | Create | Synthetic pi transcript fixtures（絕不含真實 transcript 內容） | 全部 |
| `tests/test_*.py` | Create | Unit + corpus + e2e tests | 全部 |
| `skills/learn-loop/SKILL.md` | Create | Interactive execution skill, pi skill format | REQ-020 |
| `templates/fixloop-prompt.md` | Create | Headless triage prompt | REQ-020 |
| `templates/fixloop-run.sh` | Create | Runner：`pi -p` read-only allowlist, fuse, RUN liveness line | REQ-020 |
| `src/pi_self_improvement/writer.py` | Create | Host-side deterministic writer：queue/decision 檔案、output-root confinement | REQ-022 |
| `templates/miner-run.sh` | Create | Miner runner with unconditional RUN line | REQ-021 |
| `examples/*.plist` | Create | launchd schedules（miner 每週兩次 `--since-days 8`、fixloop daily） | REQ-021 |

## Verification

| Layer | What | How |
|-------|------|-----|
| Unit | Parser、每個 detector、routing、state、resolutions | `python3 -m unittest discover -s tests` |
| Corpus | Redaction 零存活、bilingual corrections 含 false-positive 回歸、scaffold | 同上（`test_redaction_corpus` / `test_corrections_corpus` / `test_scaffold_corpus`） |
| E2E | Synthetic fixture 跑完整 scan → 驗證 outputs、packet、dedup、resolutions 生效 | `test_e2e.py` |
| Dogfood | 真實語料 `--dry-run` 對照 DEC-012 的 structural invariants；首份 review packet 人工評讀 | `pi-self-improvement --all --dry-run` |

## Review Dispositions

2026-08 獨立 critic review（Codex gpt-5.6-sol，判 NEEDS-REVISION）對本三件套 findings 的處置：

| Finding | Disposition | Reason |
|---------|-------------|--------|
| P1 四條 route 的 target identity 不完整 | Accept | REQ-013 補上 normative target table 與正規化規則，見 DEC-016 |
| P1 resolution/seen/recurrence transition order 未定義；dry-run 無 mutation contract | Accept | REQ-016/REQ-017 補上固定管線順序與 dry-run 零副作用，見 DEC-017 |
| P1 parser 把 tree transcript 簡化成線性 stream | Reject（部分接受） | 線性解析為刻意選擇：v1 transcript 無 `parentId` 且 miner 讀檔不觸發版本遷移，樹狀 parser 會在他人機器失效；sibling 汙染改以 REQ-018 的 branch-point counts 量測。同 findings 內的 `bashExecution` 與 unmatched call/result 語意已接受，補入 REQ-003/REQ-007 |
| P1 closing half 無 producer/consumer contract，fleet-forward 宣稱不成立 | Accept（範圍收窄） | decision 改用 logical ID，v1 不寫 immutable entry files，DEC-010 的宣稱同步收窄 |
| P1 unattended runner 的 write/edit 與 approval gate 衝突 | Accept | allowlist 收為 `read,grep,find,ls`，寫入移交 host-side writer（新增 REQ-022、ADR-0006） |
| P1 twice-weekly `--since-days 4` 無法保證漏一次仍完整 | Accept | 改為 `--since-days 8`，AC-037 改為對「漏一次後最大成功間隔」斷言 |
| P1 redaction boundary 未涵蓋原始 command | Accept | REQ-004 改為單一 boundary 涵蓋所有 transcript-derived 字串，並加 output-root canary scan |
| P2 Verify commands 未覆蓋數個 AC；AC-036 的 grep 驗證無效 | Accept | AC-036 改為解析 argv 斷言 `--tools` 值；tasks.md 補上缺漏 AC 的自動化測試 |
| P3 Empirical baseline 缺少可重現 snapshot | Accept | 數字降為 DEC-012 的 dated observation，gate 改用 structural invariants |
