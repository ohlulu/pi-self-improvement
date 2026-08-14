#!/bin/bash
# Scheduled miner pass (REQ-021).
#
# Writes one RUN line on every exit path. A scheduled job that fails silently is
# indistinguishable from one that never fired, and on launchd the difference
# between "no findings" and "job never loaded" is otherwise invisible.
#
# The window deliberately overlaps the schedule: --since-days must exceed the
# longest gap between two successful runs, so one missed fire cannot leave a
# blind spot. See examples/*.plist for the arithmetic.
#
# Environment overrides (all optional):
#   PSI_BIN           miner executable, default `pi-self-improvement`
#   PSI_OUTPUT_ROOT   output root, default ~/.pi-self-improvement
#   PSI_SINCE_DAYS    window, default 8
#   FIXLOOP_LOG_DIR   liveness log directory, default ~/Library/Logs

set -uo pipefail

PSI_BIN="${PSI_BIN:-pi-self-improvement}"
PSI_OUTPUT_ROOT="${PSI_OUTPUT_ROOT:-$HOME/.pi-self-improvement}"
PSI_SINCE_DAYS="${PSI_SINCE_DAYS:-8}"
FIXLOOP_LOG_DIR="${FIXLOOP_LOG_DIR:-$HOME/Library/Logs}"

LOG_FILE="$FIXLOOP_LOG_DIR/pi-self-improvement-miner.log"
STATUS="unknown"
DETAIL=""

write_run_line() {
  mkdir -p "$FIXLOOP_LOG_DIR" 2>/dev/null || true
  printf 'RUN %s miner status=%s %s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$STATUS" "$DETAIL" >>"$LOG_FILE" 2>/dev/null || true
}
trap write_run_line EXIT

SCAN_OUT="$("$PSI_BIN" --since-days "$PSI_SINCE_DAYS" --output-root "$PSI_OUTPUT_ROOT" 2>&1)"
SCAN_STATUS=$?

if [ "$SCAN_STATUS" -eq 0 ]; then
  STATUS="ok"
  DETAIL="window=${PSI_SINCE_DAYS}d $(printf '%s' "$SCAN_OUT" | grep -o 'staged [0-9]* proposal(s)' | head -1)"
else
  STATUS="error"
  DETAIL="exit=$SCAN_STATUS window=${PSI_SINCE_DAYS}d"
fi

exit 0
