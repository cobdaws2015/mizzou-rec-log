#!/usr/bin/env python3
"""Fold rows saved from other copies of data/ into the current data/.

Why this exists: the Mac and the GitHub Actions cron both append to the same
monthly CSV. When both land within minutes of each other, a git rebase sees the
same lines edited two ways and stops for a human, and an unattended collector
has no human. Rows are append-only facts keyed on (location_id, published_at),
so the right merge is simply "every row either side has, once". This script
does that merge itself, so git never has to.

Usage: merge_data.py SAVED_DIR [SAVED_DIR ...]
Each SAVED_DIR holds copies of YYYY-MM.csv files and optionally last_seen.json.
The current data/ (normally the remote's version) is kept as-is in its order;
rows only the saved copies have are appended after it.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from collect import DATA, FIELDS, STATE

# Lines git writes into a file it could not merge. A saved copy may carry them
# if a collector committed mid-conflict, so they are dropped, and the real rows
# on both sides of them are kept.
MARKERS = ("<<<<<<<", "=======", ">>>>>>>", "|||||||")


def read_rows(path: Path) -> list[dict]:
    """Every well-formed data row in a CSV, skipping headers and conflict junk."""
    lines = [
        ln for ln in path.read_text().splitlines()
        if ln and not ln.startswith(MARKERS) and not ln.startswith("published_at,")
    ]
    rows = []
    for rec in csv.reader(lines):
        if len(rec) == len(FIELDS):  # a torn or garbled line has the wrong width
            rows.append(dict(zip(FIELDS, rec)))
    return rows


def key(row: dict) -> tuple[str, str]:
    return (row["location_id"], row["published_at"])


def merge_csv(month_file: str, sources: list[Path]) -> int:
    target = DATA / month_file
    current = read_rows(target) if target.exists() else []
    seen = {key(r) for r in current}
    extra = []
    for src in sources:
        for row in read_rows(src):
            if key(row) not in seen:
                seen.add(key(row))
                extra.append(row)
    if not extra and target.exists():
        return 0
    # Rewrite the whole file clean, which also scrubs any junk lines it held.
    with target.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(current + extra)
    return len(extra)


def merge_state(sources: list[Path]) -> None:
    """last_seen.json is "newest reading per room": take the later of each.

    Timestamps share one ISO format, so plain string order is time order.
    """
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for src in sources:
        try:
            other = json.loads(src.read_text())
        except (OSError, ValueError):
            continue  # a conflicted state file is not worth failing over
        for loc, ts in other.items():
            if ts > state.get(loc, ""):
                state[loc] = ts
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    dirs = [Path(a) for a in argv]
    if not dirs:
        print(__doc__, file=sys.stderr)
        return 2
    DATA.mkdir(exist_ok=True)
    months = sorted({p.name for d in dirs for p in d.glob("????-??.csv")})
    added = 0
    for month in months:
        added += merge_csv(month, [d / month for d in dirs if (d / month).exists()])
    merge_state([d / STATE.name for d in dirs if (d / STATE.name).exists()])
    print(f"merged {added} row(s) the current data/ did not have")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
