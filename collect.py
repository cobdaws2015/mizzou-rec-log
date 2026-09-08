#!/usr/bin/env python3
"""Record one sample of MizzouRec facility occupancy.

The Rec's public "Facility Usage" page embeds a Connect2Concepts widget, which
reads an unauthenticated JSON endpoint. That endpoint only ever holds the
current number; nobody publishes the history. This script is the history.

Each record carries LastUpdatedDateAndTime, which is when the Rec actually took
the count, not when we asked. We key on that, so polling faster than the Rec
publishes costs nothing and polling slower loses nothing but resolution. A run
that finds no fresh counts writes no rows and the workflow skips the commit.

Rows land in data/YYYY-MM.csv (monthly files keep any single blob small, which
keeps the git pack small when the file is rewritten on every append).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Key is lifted from the widget <script src> on mizzourec.missouri.edu, which is
# public and unauthenticated. It identifies the Rec's account, not a person.
API = (
    "https://goboardapi.azurewebsites.net/api/FacilityCount/GetCountsByAccount"
    "?AccountAPIKey=2f852228-dca5-4073-bc3b-cf378f4d0e31"
)

LOCAL_TZ = ZoneInfo("America/Chicago")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATE = DATA / "last_seen.json"

FIELDS = [
    "published_at",  # Rec's own timestamp for the count, naive local time
    "weekday",       # 0=Monday, derived once here so readers never re-parse
    "hour",
    "minute",
    "location_id",
    "location",
    "facility",
    "count",
    "capacity",
    "pct",
    "is_closed",
    "collected_at",  # when this script ran, UTC
]


def fetch() -> list[dict]:
    req = urllib.request.Request(API, headers={"User-Agent": "mizzou-rec-log"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def main() -> int:
    try:
        records = fetch()
    except Exception as exc:  # noqa: BLE001 - a bad poll is not worth failing CI
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 0

    if not isinstance(records, list) or not records:
        print("no records returned", file=sys.stderr)
        return 0

    state = load_state()
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fresh: list[dict] = []

    for r in records:
        published = (r.get("LastUpdatedDateAndTime") or "").strip()
        loc_id = str(r.get("LocationId"))
        if not published or not loc_id:
            continue
        # Same reading as last time means the Rec has not re-counted this room.
        if state.get(loc_id) == published:
            continue

        try:
            ts = datetime.fromisoformat(published)
        except ValueError:
            continue

        capacity = r.get("TotalCapacity") or 0
        count = r.get("LastCount") or 0
        # The API's own PercetageCapacity field is often 0 even when LastCount
        # is not, so compute it rather than trusting it. (Typo is theirs.)
        pct = round(100 * count / capacity, 1) if capacity else ""

        fresh.append(
            {
                "published_at": published,
                "weekday": ts.weekday(),
                "hour": ts.hour,
                "minute": ts.minute,
                "location_id": loc_id,
                "location": r.get("LocationName", ""),
                "facility": r.get("FacilityName", ""),
                "count": count,
                "capacity": capacity,
                "pct": pct,
                "is_closed": bool(r.get("IsClosed")),
                "collected_at": collected_at,
            }
        )
        state[loc_id] = published

    if not fresh:
        print("no new counts")
        return 0

    DATA.mkdir(exist_ok=True)
    # Bucket by the month the Rec published in, so a row recorded either side of
    # midnight on the 1st still files under the month it belongs to.
    by_month: dict[str, list[dict]] = {}
    for row in fresh:
        by_month.setdefault(row["published_at"][:7], []).append(row)

    for month, rows in by_month.items():
        path = DATA / f"{month}.csv"
        new_file = not path.exists()
        with path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)

    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(fresh)} new rows across {len(by_month)} file(s)")

    # Let the workflow decide whether to commit without re-running git status.
    if step_out := os.environ.get("GITHUB_OUTPUT"):
        with open(step_out, "a") as fh:
            fh.write("changed=true\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
