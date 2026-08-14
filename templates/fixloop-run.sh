#!/bin/bash
# Headless fixloop triage pass (REQ-020, DEC-011 / ADR-0006).
#
# Runs `pi -p` with a read-only tool allowlist and hands its output to the
# host-side writer. The model never writes: an allowlist can say which tools,
# not which paths, so `write` and `edit` would let an unattended run modify
# skills and source directly. Removing `bash` alone does not close that.
#
# Every exit path writes one RUN line. A scheduled job that fails silently is
# indistinguishable from one that never fired, and the liveness line is the
# only thing that tells them apart.
#
# Environment overrides (all optional, and what the tests drive):
#   PI_BIN            pi executable, default `pi`
#   PSI_BIN           miner executable, default `pi-self-improvement`
#   PSI_OUTPUT_ROOT   output root, default ~/.pi-self-improvement
#   FIXLOOP_LOG_DIR   liveness log directory, default ~/Library/Logs
#   FIXLOOP_FUSE      wall-clock fuse in seconds, default 900
#   FIXLOOP_PROMPT    prompt file, default alongside this script

set -uo pipefail

PI_BIN="${PI_BIN:-pi}"
PSI_BIN="${PSI_BIN:-pi-self-improvement}"
PSI_OUTPUT_ROOT="${PSI_OUTPUT_ROOT:-$HOME/.pi-self-improvement}"
FIXLOOP_LOG_DIR="${FIXLOOP_LOG_DIR:-$HOME/Library/Logs}"
FIXLOOP_FUSE="${FIXLOOP_FUSE:-900}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXLOOP_PROMPT="${FIXLOOP_PROMPT:-$SCRIPT_DIR/fixloop-prompt.md}"

LOG_FILE="$FIXLOOP_LOG_DIR/pi-self-improvement-fixloop.log"
STATUS="unknown"
DETAIL=""

# The liveness line is written from a trap so that it survives every exit path,
# including the fuse killing us and `set -u` tripping on a typo.
write_run_line() {
  mkdir -p "$FIXLOOP_LOG_DIR" 2>/dev/null || true
  printf 'RUN %s fixloop status=%s %s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$STATUS" "$DETAIL" >>"$LOG_FILE" 2>/dev/null || true
}
trap write_run_line EXIT

QUEUE_INPUT="$PSI_OUTPUT_ROOT/review-packets"
if [ ! -d "$QUEUE_INPUT" ]; then
  STATUS="empty"
  DETAIL="reason=no-packets root=$PSI_OUTPUT_ROOT"
  exit 0
fi

LATEST_PACKET="$(ls -t "$QUEUE_INPUT"/*.md 2>/dev/null | head -1)"
if [ -z "$LATEST_PACKET" ]; then
  STATUS="empty"
  DETAIL="reason=no-packets root=$PSI_OUTPUT_ROOT"
  exit 0
fi

TRIAGE_OUT="$(mktemp -t fixloop-triage)"
cleanup() {
  rm -f "$TRIAGE_OUT"
}
trap 'cleanup; write_run_line' EXIT

# `--tools read,grep,find,ls` is the safety boundary, not a performance choice.
# Adding `write`, `edit` or `bash` here defeats ADR-0006 entirely.
"$PI_BIN" -p "$(cat "$FIXLOOP_PROMPT")

Review packet: $LATEST_PACKET" \
  --tools read,grep,find,ls \
  >"$TRIAGE_OUT" 2>/dev/null &
PI_PID=$!

# Wall-clock fuse. pi has no --max-turns, and GNU coreutils `timeout` is not on
# a stock macOS, so the fuse is a background timer that signals the run.
#
# It counts in one-second steps and exits as soon as pi is gone, rather than
# sleeping the full duration: killing the subshell does not kill a `sleep` it
# spawned, so a single long sleep outlives the run, holds the inherited stdout
# open, and leaves a stray process per scheduled fire.
(
  waited=0
  while [ "$waited" -lt "$FIXLOOP_FUSE" ]; do
    sleep 1
    kill -0 "$PI_PID" 2>/dev/null || exit 0
    waited=$((waited + 1))
  done
  kill -TERM "$PI_PID" 2>/dev/null
) >/dev/null 2>&1 &
FUSE_PID=$!

wait "$PI_PID"
PI_STATUS=$?
kill -TERM "$FUSE_PID" 2>/dev/null
wait "$FUSE_PID" 2>/dev/null

if [ "$PI_STATUS" -ge 128 ]; then
  STATUS="fused"
  DETAIL="after=${FIXLOOP_FUSE}s packet=$(basename "$LATEST_PACKET")"
  exit 0
fi

if [ "$PI_STATUS" -ne 0 ]; then
  STATUS="error"
  DETAIL="pi_exit=$PI_STATUS packet=$(basename "$LATEST_PACKET")"
  exit 0
fi

# The model produced triage text; only this deterministic writer touches disk.
if "$PSI_BIN" --write-queue "$TRIAGE_OUT" --output-root "$PSI_OUTPUT_ROOT" >/dev/null 2>&1; then
  STATUS="ok"
  DETAIL="packet=$(basename "$LATEST_PACKET")"
else
  STATUS="write-failed"
  DETAIL="packet=$(basename "$LATEST_PACKET")"
fi

exit 0
