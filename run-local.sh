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

REPO="/Users/cobymaybrook/Claude Projects/mizzou-rec-log"
# System python3 is 3.9 against LibreSSL 2.8.3 and the Rec's endpoint refuses
# its TLS handshake. 3.11 is built against OpenSSL 3.
PY="/Users/cobymaybrook/.local/bin/python3.11"
LOG="/Users/cobymaybrook/Library/Logs/mizzou-rec-collect.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

cd "$REPO" || { log "FAILED: no repo at $REPO"; exit 1; }
[ -x "$PY" ] || { log "FAILED: no python at $PY"; exit 1; }

# Take whatever the Actions run (or another machine) may have landed first, so
# the push at the end is a fast-forward.
git pull --rebase --autostash --quiet || log "WARN: pull failed, continuing"

"$PY" collect.py >> "$LOG" 2>&1

# Ask git what actually changed rather than trusting the script's own report.
if [ -z "$(git status --porcelain data/)" ]; then
  exit 0
fi

"$PY" summarize.py >> "$LOG" 2>&1

git add data/
git commit -q -m "data: $(date -u '+%Y-%m-%dT%H:%MZ') (mac)"

for _ in 1 2 3; do
  if git push --quiet 2>>"$LOG"; then
    log "pushed"
    exit 0
  fi
  git pull --rebase --autostash --quiet || true
done

log "FAILED: push rejected three times, commit is local only"
exit 1
