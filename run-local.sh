#!/bin/bash
# Sample the Rec from this Mac and push the result.
#
# The GitHub Actions cron in .github/workflows/collect.yml is meant to be the
# collector. As of 2026-09-08 it has never fired on its own — nine consecutive
# scheduled slots missed with the workflow reported active — so this script is
# the one that actually keeps the record. The cron stays armed: if GitHub starts
# honouring it, the two just fill each other's gaps, because collect.py keys on
# the Rec's published timestamp and will not write a row twice.
#
# Runs under launchd every 10 minutes (com.mizzourec.collect). launchd does not
# fire while the Mac is asleep and does not backfill, so expect holes overnight
# and whenever the lid is shut. Holes are survivable; the medians just take
# longer to firm up.

set -uo pipefail

# launchd hands a job almost no PATH, so spell it out (same as daily-refresh.sh).
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# The repo is wherever this script lives, so nothing here names one machine.
REPO="$(cd "$(dirname "$0")" && pwd)"
# System python3 is 3.9 against LibreSSL 2.8.3 and the Rec's endpoint refuses
# its TLS handshake. 3.11 is built against OpenSSL 3. Override with PY=...
PY="${PY:-$HOME/.local/bin/python3.11}"
LOG="$HOME/Library/Logs/mizzou-rec-collect.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

cd "$REPO" || { log "FAILED: no repo at $REPO"; exit 1; }
[ -x "$PY" ] || { log "FAILED: no python at $PY"; exit 1; }

# Pick up code and data the Actions run landed. Fast-forward only: if both sides
# have new commits, collect anyway and let sync.sh reconcile them at the end.
git fetch --quiet origin main && git merge --ff-only --quiet origin/main 2>/dev/null \
  || log "WARN: could not fast-forward, sync.sh will reconcile"

"$PY" collect.py >> "$LOG" 2>&1

# Ask git what actually changed rather than trusting the script's own report.
# Also go ahead if an earlier tick committed rows it could not push.
if [ -z "$(git status --porcelain data/)" ] && [ -z "$(git log --oneline origin/main..HEAD)" ]; then
  exit 0
fi

# sync.sh copies data/ aside, lines up with GitHub, folds the rows back in and
# pushes, so a clash with the Actions run can no longer freeze this repo.
if PY="$PY" "$REPO/sync.sh" "data: $(date -u '+%Y-%m-%dT%H:%MZ') (mac)" >> "$LOG" 2>&1; then
  log "pushed"
  exit 0
fi
log "FAILED: sync did not push, rows are kept and retried next tick"
exit 1
