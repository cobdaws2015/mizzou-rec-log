#!/bin/bash
# Publish whatever is in data/ without ever asking git to merge it.
#
# Used by both collectors (run-local.sh on the Mac, collect.yml on GitHub).
# The old way was commit, push, and on rejection `git pull --rebase`. When both
# collectors had appended to the same CSV, that rebase stopped on a conflict
# and waited for a human: one failed CI run on 2026-11-19, and the Mac repo sat
# frozen mid-rebase from 2026-11-22, committing conflict markers locally and
# pushing nothing.
#
# The new way: copy data/ aside, line up with the remote's data/, fold the
# copied rows back in with merge_data.py, rebuild the summary, commit, push.
# If someone else pushed in between, do it again. Git only ever fast-forwards.
#
# Usage: sync.sh "commit message"     (PY=... picks the python, default python3)

set -uo pipefail

MSG="${1:?commit message required}"
PY="${PY:-python3}"
BRANCH=main
cd "$(dirname "$0")" || exit 1

# Leftover from the old pull-rebase loop: get out of it before doing anything.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  git rebase --abort 2>/dev/null || git rebase --quit
fi
if [ "$(git symbolic-ref -q --short HEAD)" != "$BRANCH" ]; then
  echo "sync: not on $BRANCH, refusing to touch history" >&2
  exit 1
fi

for attempt in 1 2 3 4 5; do
  git fetch --quiet origin "$BRANCH" || { echo "sync: fetch failed" >&2; exit 1; }

  # Unpushed commits get folded into one fresh commit below, which is only safe
  # if they are pure data. A code commit stays put and this run stops.
  # (git log, not a three-dot diff: GitHub's checkout is shallow and may lack
  # the merge base a diff would need.)
  if [ -n "$(git log --format= --name-only "origin/$BRANCH..HEAD" -- . ':!data/')" ]; then
    echo "sync: unpushed non-data commits, push those by hand first" >&2
    exit 1
  fi

  SAVE="$(mktemp -d)"
  cp data/*.csv data/last_seen.json "$SAVE"/ 2>/dev/null

  # Move the branch to the remote tip and take the remote's data/. --mixed
  # leaves every non-data file in the working tree untouched, so uncommitted
  # code edits on the Mac survive this.
  git reset --quiet --mixed "origin/$BRANCH"
  git checkout "origin/$BRANCH" -- data/

  "$PY" merge_data.py "$SAVE"
  rc=$?
  rm -rf "$SAVE"
  [ $rc -eq 0 ] || { echo "sync: merge failed" >&2; exit 1; }

  # summary.json stamps its build time, so rebuilding it is always a "change".
  # Only rebuild when the rows themselves differ from the remote's.
  if git diff --quiet -- data/ ':!data/summary.json' \
     && [ -z "$(git ls-files --others --exclude-standard data/)" ]; then
    echo "sync: remote already has everything"
    exit 0
  fi
  "$PY" summarize.py >/dev/null || { echo "sync: summarize failed" >&2; exit 1; }

  git add data/
  if git diff --cached --quiet; then
    echo "sync: remote already has everything"
    exit 0
  fi
  git commit --quiet -m "$MSG"
  if git push --quiet origin "HEAD:$BRANCH"; then
    echo "sync: pushed on attempt $attempt"
    exit 0
  fi
  sleep $((attempt * 3))  # someone pushed in between; line up again
done

echo "sync: push rejected five times, data is committed locally only" >&2
exit 1
