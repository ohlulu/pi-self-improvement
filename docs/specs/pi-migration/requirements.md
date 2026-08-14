---
summary: Behavioral requirements (REQ/AC) for the pi-native session-mining self-improvement loop
read_when:
  - Implementing or changing any miner, detector, routing, or staging behavior
  - Verifying implementation against the spec
  - Deciding whether a new signal belongs in scope
---

# Requirements: Pi-Native Self-Improvement Loop

## Summary

把 agent-improvement-loop 的 session-mining 自我改進迴圈以 pi-native 方式重寫：讀取 pi session transcripts、偵測可複用的摩擦訊號、staged 成需人工核准的 proposals，並提供 closing half（headless triage + interactive 執行）。本 feature 範圍為單機 v1；fleet 模式與非 pi sources（Claude Code、Codex、Hermes）不在範圍內，前者列入 roadmap，後者由上游工具持續服務。

## Requirements

### REQ-001: Approval gate

The system MUST NOT modify skills, memory files, configuration, or source code; scan 與 stage 自動化，apply 永遠需要人工核准。

- AC-001: GIVEN 一次完整 scan WHEN 執行結束 THEN 系統寫入的檔案僅存在於 output root 之下，且每個 proposal 都標記 `manual_approval_required`

### REQ-002: Session collection

WHEN a scan runs, the system SHALL discover pi session transcripts under `~/.pi/agent/sessions/**/*.jsonl` 與 config 指定的額外 roots，並以時間窗過濾。首次執行（state 尚不存在）MUST NOT 觸發自動 backfill：時間窗外的歷史 session 僅經由顯式 `--all` 進入 scan。

- AC-002: GIVEN `--since-days 1` 與一個 mtime 為 3 天前的 session WHEN scan THEN 該 session 不被解析
- AC-003: GIVEN `--max-sessions N` WHEN 過濾後 root session 數超過 N THEN 僅保留最新的 N 個 root sessions；subagent sessions 隨其 root 帶入，不佔額度
- AC-038: GIVEN 全新 output root（無 `state.json`）與 mtime 在窗外的歷史 session WHEN `--since-days N` scan THEN 該 session 不被解析；WHEN 同條件改跑 `--all` THEN 該 session 納入
- AC-039: GIVEN 一個 root session 底下有 10 個 subagent sessions 且 `--max-sessions 5` WHEN 過濾 THEN 該 root session 仍完整帶入其 subagent sessions，且其餘 4 個 root sessions 不被排擠

### REQ-003: Normalized event model

The system SHALL reduce each transcript to tool calls, failures, hangs, silent-empty results, skill invocations, and user corrections；parser MUST 認得 pi 的四種 message role（`user`、`assistant`、`toolResult`、`bashExecution`）與其餘 record types，未支援的 record type MUST 被計數而非靜默丟棄。Transcript 以檔案行序解析，MUST NOT 走 `parentId` 樹（見 ADR-0004）。Evidence 以 `path:line` reference 加短 excerpt 儲存，MUST NOT 儲存整份 transcript。

- AC-004: GIVEN 任一 staged proposal THEN 每筆 evidence 含 source、path、line、excerpt，且 excerpt 不超過設定上限（預設 360 字元）
- AC-040: GIVEN 一筆 `bashExecution` record WHEN 解析 THEN `command`、`exitCode`、`cancelled` 進入事件模型，且該筆 MUST NOT 進入 correction 偵測
- AC-041: GIVEN transcript 含 `stopReason` 為 `aborted`/`error` 的 assistant turn、無對應 result 的 toolCall、或非標準 schema 檔案 WHEN 解析 THEN 三者皆計入 self-check counts 且皆不產生 evidence

### REQ-004: Redaction

WHEN writing any transcript-derived string, the system SHALL 讓它通過單一 redaction boundary——涵蓋 command、arguments、excerpt、summary、cwd 與顯示用路徑——並 mask secret-shaped strings（email、電話、auth header、API key、雲端服務 key 形狀、private-key block、JWT、長不透明 token）。

- AC-005: GIVEN 內含各類 secret 形狀的 tool output 與 command arguments WHEN 寫入任何輸出 THEN redaction corpus test 斷言原始 secret 字串不出現在任何輸出檔案
- AC-006: GIVEN `--full` WHEN scan THEN excerpt 保留原文，且 run metadata、proposal JSON 與 review packet 三處皆標記 `local_only: true`
- AC-042: GIVEN 一次寫入完成的 output root WHEN 執行 canary scan THEN 整個 output root 之下不存在任何 secret canary 字串

### REQ-005: Failure detection

WHEN a toolResult record carries `isError: true`, the system SHALL record failure evidence attributed to the originating tool call; IF `isError` 欄位缺席, THEN the system SHALL fall back to text heuristics。

- AC-007: GIVEN 一個 `isError: true` 的 bash toolResult WHEN 解析 THEN 產生 failure evidence 且附上原始 command
- AC-008: GIVEN `isError: false` 且輸出文字含 "error" 字樣的 build log WHEN 解析 THEN 不產生 failure evidence

### REQ-006: Hang detection

WHEN a tool result text indicates a stall（timeout、killed、deadline 等 pattern）without clean completion, the system SHALL record hang evidence；command 文字本身 MUST NOT 觸發。

- AC-009: GIVEN toolResult 文字為 "Command timed out after 30 seconds" WHEN 解析 THEN 產生 hang evidence
- AC-010: GIVEN command 為 `timeout 120 foo` 且結果正常 WHEN 解析 THEN 不產生 hang evidence

### REQ-007: Tracked CLI attribution

The system SHALL attribute tool-route evidence to tracked CLIs，以 config 的明確名單為主、name suffix pattern 為輔；`bashExecution` 的非零 `exitCode` 與 `cancelled` 同樣構成 tool-route 佐證；並偵測 retry-before-success（同 session 重複呼叫 + failure/hang 佐證或同 subcommand flag 變形）。

- AC-011: GIVEN config 名單含 `foo` 且 `foo` 在 session 中失敗 WHEN routing THEN 產生 target 為 `foo` 的 tool route proposal
- AC-012: GIVEN 同一 CLI 同 subcommand 三次以上不同 flag 組合且伴隨一次 failure WHEN 偵測 THEN proposal summary 註記 retry 次數
- AC-043: GIVEN 一筆 `bashExecution` 執行 tracked CLI `foo` 且 `exitCode` 為 127 WHEN routing THEN 產生 target 為 `foo` 的 tool route evidence

### REQ-008: Silent-empty detection

WHEN a data-intending call returns a structurally empty result（`[]`、`{}`、`null`、空 stdout、`0 rows`、`No results`、`(empty)`）and no later agent step acknowledges the emptiness, the system SHALL record silent-empty evidence。

- AC-013: GIVEN `foo list --json` 回傳 `[]` 且 agent 後續步驟與最終回覆皆未提及空結果 WHEN 偵測 THEN 產生 silent-empty evidence
- AC-014: GIVEN `grep` 或 `rg` 無匹配結果 WHEN 偵測 THEN 不產生 silent-empty evidence

### REQ-009: Skill invocation detection

WHEN a read tool call targets a path ending in `SKILL.md`, the system SHALL record a skill invocation，名稱取自 skill 目錄名。此為規格預設偵測路徑；config MAY 透過 `skill_loaded_custom_types` 額外採計 pi extension 寫入的 skill-load custom entries（見 ADR-0007）。

- AC-015: GIVEN read `~/.pi/agent/skills/commit/SKILL.md` WHEN 解析 THEN 產生名為 `commit` 的 skill invocation
- AC-044: GIVEN config 未設定 `skill_loaded_custom_types` 且 transcript 含 skill-load custom entry WHEN 解析 THEN 僅 read-path heuristic 生效；WHEN 設定該 key 後 THEN 該 entry 亦產生 skill invocation

### REQ-010: Bilingual correction detection

WHEN a user message matches a correction cue pack, the system SHALL record correction evidence；strong cues 於一般長度訊息有效、weak cues 僅於短反應式訊息有效；內建 English 與 Traditional Chinese cue packs，config MAY 擴充。

- AC-016: GIVEN assistant 已回應過 WHEN user 送出短訊息「不對，我是說要用 X」 THEN 產生 correction evidence
- AC-017: GIVEN user 貼上長篇文件內含 "use X instead" 且超過 weak-cue 長度閘門 WHEN 偵測 THEN 不產生 correction
- AC-018: GIVEN 訊息「沒錯，就這樣做」 WHEN 偵測 THEN 不產生 correction（negative guard）

### REQ-011: Scaffold filtering

The system SHALL treat injected scaffolding as non-user text。結構優先：非 `message` record（含 `custom`、`custom_message`）MUST NOT 進入 user 文字偵測。內建 marker 名單僅保留實測會出現在 user 訊息內文的兩項——box-drawing separator 行（連續 `─` >= 10）與 `Task:` 開頭的 subagent task seed——config `extra_scaffold_markers` 可擴充。

- AC-019: GIVEN user 訊息含連續 10 個以上 `─` 的 separator 行 WHEN 偵測 THEN 視為 scaffold，不產生 correction
- AC-020: GIVEN docs-index 注入以 `custom_message` record 出現 WHEN 解析 THEN 因結構過濾即不進入 user 文字偵測，無需 marker 比對
- AC-045: GIVEN 一則真正由 user 輸入、內文討論 scaffold 而提及 `[Project docs index]` 的訊息 WHEN 偵測 THEN 不因該字串被誤判為 scaffold

### REQ-012: Subagent exclusion

IF a transcript is a subagent session（路徑形如 `<session-id>/<hash>/run-N/session.jsonl`）, THEN failure 與 silent-empty 訊號預設不進入 backlog route，且其注入的 prompt 不採計為 correction；config MAY 開啟採計。

- AC-021: GIVEN 位於 `sessions/<cwd-slug>/<session-id>/<hash>/run-N/session.jsonl` 的 transcript 內含 failure WHEN routing（預設 config）THEN 不產生 backlog proposal

### REQ-013: Proposal routing

The system SHALL route each proposal to exactly one of `tool`、`skill_improvement`、`memory_context`、`backlog`，每條 route 的 target 依下表決定；無法歸類的一次性訊號 SHALL 被捨棄而非 staged。

| Route | Target |
|-------|--------|
| `tool` | tracked executable basename，或 extension tool 的 `ext:<family>` |
| `skill_improvement` | skill 名稱 |
| `memory_context` | repository root |
| `backlog` | 正規化後的 executable 名稱 |

Target 正規化：realpath 解 symlink → git toplevel → 保留原大小寫；無法判定時為 `<unknown>`（見 ADR-0005）。

- AC-022: GIVEN skill invocation 且同 session 後續出現 correction WHEN routing THEN 產生 `skill_improvement` proposal
- AC-023: GIVEN correction 未關聯任何 skill WHEN routing THEN 產生依 repository root 分組的 `memory_context` proposal，且單一 session 最多貢獻 `MAX_CORRECTIONS_PER_SESSION`（預設 3）個
- AC-024: GIVEN 跨 session 重複的一般工具 failure WHEN routing THEN 產生 `backlog` proposal
- AC-046: GIVEN 同一 repo 的主目錄與其 git worktree 各自出現 correction WHEN routing THEN 兩者歸入同一個 `memory_context` target

### REQ-014: Extension-tool family grouping

WHEN recurring failures or silent-empty results accumulate for pi extension tools, the system SHALL group them per tool family as `ext:<family>` under the tool route；builtin tools MUST NOT 進入 family 分組。

- AC-025: GIVEN `jira_search_issues` 與 `jira_get_issue` 各自累積 failure WHEN routing THEN 兩者合併為 target `ext:jira`
- AC-026: GIVEN builtin tool `read` 的 failure WHEN routing THEN 不產生 `ext:read` target

### REQ-015: Staging outputs

WHEN a scan completes, the system SHALL write run metadata、per-proposal JSON、以及 human-readable review packet 到 output root（預設 `~/.pi-self-improvement/`）。

- AC-027: GIVEN 一次有 proposal 的 scan THEN `runs/<run-id>.json`、`proposals/<run-id>/*.json`、`review-packets/<run-id>.md` 皆存在
- AC-028: GIVEN recurring 與新 target 並存 WHEN 產生 packet THEN recurring proposals 排在最前

### REQ-016: State and deduplication

The system SHALL derive deterministic proposal ids from route、target 與 evidence references；pipeline 順序固定為 resolution filter → seen-key filter → grouping/staging → recurrence annotation；已見過的 key 不重複 staged（除非 `--include-seen`）；曾在先前 run 出現的 target 標註 recurring。`--dry-run` MUST NOT 建立或修改任何 state、output 或 log，僅輸出到 stdout。

- AC-029: GIVEN 同一組 evidence 再次 scan WHEN staging THEN 不產生重複 proposal
- AC-030: GIVEN target 於第二次 run 再被偵測 WHEN staging THEN proposal 註記 "also flagged in N previous run(s)"
- AC-047: GIVEN 一個已 resolve 為 `fixed` 且 watermark 後有新 evidence 的 target WHEN scan THEN 先通過 resolution filter 成為 regression，再進入 seen-key filter，不因曾見過而被吞掉
- AC-048: GIVEN `--all --dry-run` 後緊接一次 `--all` 實跑 WHEN 比較產出 THEN 實跑的 proposal 與未先跑 dry-run 時完全相同

### REQ-017: Resolutions registry

The system SHALL keep a `route:target` resolutions registry（`fixed`/`wontfix`/`ignored` + `resolved_at` watermark）；`fixed` 之後出現的新 evidence 以 regression 標註重新浮出，`wontfix` 與 `ignored` 永久 suppress；resolve 時 MUST 移除該 target 於 watermark 以前的 recurrence history；支援 `decisions.json` 批次匯入。

- AC-031: GIVEN target 已 resolve 為 `fixed` at T WHEN scan 遇到時間 ≤ T 的 evidence THEN suppressed；遇到 > T 的 evidence THEN 進入 packet 的 regression section
- AC-032: GIVEN `--resolve-from decisions.json` WHEN 匯入 THEN registry 更新且 open/deferred 項目不受影響
- AC-049: GIVEN target 已 resolve 為 `wontfix` WHEN scan 遇到 watermark 後的新 evidence THEN 仍 suppressed；GIVEN 另一 target resolve 為 `fixed` 後首次 regression WHEN 產生 packet THEN 不被標記為累積多次的 recurring

### REQ-018: Parser self-check

WHEN a scan parses transcripts but yields zero tool calls for the source, the system SHALL warn loudly（stderr、run metadata、review packet 三處）；每次 scan 並 SHALL 輸出一塊 counts：sessions（root/subagent 分列）、branch points、`aborted`/`error` turns、無對應 result 的 toolCall、以及被跳過的 record types 與非標準 schema 檔案數。

- AC-033: GIVEN 一批可解析但無 toolCall 的 fixture WHEN scan THEN 三處皆出現 parser 警告
- AC-050: GIVEN 一批含分支、abort 與 subagent session 的 fixture WHEN scan THEN counts 區塊列出各項數字，且 root 與 subagent 數量總和等於解析的 transcript 檔案數

### REQ-019: Configuration

The system SHALL load detector overrides from a JSON config（cue packs、scaffold markers、tracked CLIs、ignores、ext-family map、`skill_loaded_custom_types` 等）；預設值保持 generic，個人 workflow 細節——包含依賴個人 extension 的訊號來源——只存在於 config。

- AC-034: GIVEN config 覆蓋某 detector 預設 WHEN scan THEN 覆蓋值生效且未覆蓋值維持預設

### REQ-020: Closing half

The system SHALL provide a headless fixloop runner（以 `pi -p` 執行、tool allowlist 為 `read,grep,find,ls`、wall-clock fuse）與一個 learn-loop pi skill；runner MUST 在任何結果下寫入一行 RUN liveness log 到 `~/Library/Logs`；closing-half 的 decision 檔案以 logical ID 命名（`decisions/<ID>.json`），machine 前綴僅用於 entry 層級。

- AC-035: GIVEN 空 queue WHEN runner 執行 THEN RUN line 仍寫入
- AC-036: GIVEN runner 組出的 `pi -p` 指令 WHEN 解析其 argv THEN `--tools` 的值恰為 `read,grep,find,ls`，不含 `bash`、`write` 或 `edit`
- AC-051: GIVEN 兩台機器對同一 logical incident 各自產生 decision WHEN 檢視 `decisions/` THEN 兩者寫入同一個 `<ID>.json`，不因 machine 而分裂

### REQ-021: Scheduling

The system SHALL ship launchd examples that run the miner on an overlapping window schedule，`--since-days` 覆蓋範圍 MUST 大於「漏掉一次排程後」兩次成功執行的最大間隔；miner runner 同樣無條件寫入 RUN liveness line。

- AC-037: GIVEN 範例 plist 為每週兩次且參數為 `--since-days 8` WHEN 計算漏掉一次 fire 後的最大成功執行間隔（7 天）THEN 覆蓋窗嚴格大於該間隔

### REQ-022: Host-side proposal writer

The system SHALL write closing-half queue and decision files through a deterministic host-side writer；headless `pi -p` 的輸出僅作為 structured triage 輸入，MUST NOT 由模型直接寫檔；writer 可寫入的路徑 MUST 限於 output root（見 ADR-0006）。

- AC-052: GIVEN headless runner 回傳一份 triage 結果 WHEN host-side writer 執行 THEN 僅 output root 之下的檔案被建立或修改，且 writer 對 output root 以外的路徑一律拒絕

## Non-Functional Requirements

| Category | Requirement | Strength |
|----------|-------------|----------|
| Dependencies | Python 標準函式庫 only，零第三方依賴 | MUST |
| Runtime | Python >= 3.10 | MUST |
| Performance | 500 sessions 的 scan 於一般筆電 30 秒內完成 | SHOULD |
| Platform | macOS 為主要目標（launchd、`~/Library/Logs`）；Linux cron 可用但非驗收範圍 | SHOULD |
| Privacy | Repo 保持 public-safe：真實 dogfood 輸出、個人 detector 細節僅存在於 local config 與 output root | MUST |
