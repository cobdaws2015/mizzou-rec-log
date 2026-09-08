#!/usr/bin/env python3
"""Collapse the raw samples into a weekday-by-hour picture.

Writes data/summary.json (small, stable shape, safe for anything downstream to
fetch) and can print the same thing as a terminal grid:

    python3 summarize.py --print "Strength Training"

Median rather than mean, because a single holiday or a closed-for-maintenance
afternoon would drag an average around and there is no reason to let it.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "summary.json"

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# The rooms worth lifting in. Everything else is still collected; this is only
# what gets promoted into the summary so downstream files stay small.
FEATURED = ["Strength Training", "Weight Room", "Jungle Annex", "Cardio Gallery"]


def read_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(DATA.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].csv")):
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                # A closed room reads 0, which is true but says nothing about
                # how busy it gets, so it would drag every median down.
                if r.get("is_closed") == "True":
                    continue
                if not r.get("pct"):
                    continue
                try:
                    r["_pct"] = float(r["pct"])
                    r["_count"] = int(r["count"])
                    r["_weekday"] = int(r["weekday"])
                    r["_hour"] = int(r["hour"])
                except (ValueError, KeyError):
                    continue
                rows.append(r)
    return rows


def build(rows: list[dict]) -> dict:
    buckets: dict[str, dict[tuple[int, int], list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    capacity: dict[str, int] = {}
    dates: set[str] = set()

    for r in rows:
        loc = r["location"]
        buckets[loc][(r["_weekday"], r["_hour"])].append(r)
        try:
            capacity[loc] = int(r["capacity"])
        except (ValueError, KeyError):
            pass
        dates.add(r["published_at"][:10])

    locations: dict[str, dict] = {}
    for loc, grid in buckets.items():
        cells: dict[str, dict[str, dict]] = {}
        for (wd, hour), samples in sorted(grid.items()):
            cells.setdefault(DAYS[wd], {})[str(hour)] = {
                "pct": round(statistics.median(s["_pct"] for s in samples)),
                "count": round(statistics.median(s["_count"] for s in samples)),
                "n": len(samples),
            }
        locations[loc] = {"capacity": capacity.get(loc, 0), "grid": cells}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "samples": len(rows),
        "days_observed": len(dates),
        "first_day": min(dates) if dates else None,
        "last_day": max(dates) if dates else None,
        "featured": [n for n in FEATURED if n in locations],
        "locations": locations,
    }


def shade(pct: int | None) -> str:
    if pct is None:
        return " . "
    if pct < 25:
        return "   "
    if pct < 45:
        return " - "
    if pct < 65:
        return " = "
    if pct < 85:
        return " # "
    return " @ "


def print_grid(summary: dict, location: str) -> None:
    loc = summary["locations"].get(location)
    if not loc:
        print(f"no data yet for {location}")
        return

    grid = loc["grid"]
    hours = sorted({int(h) for day in grid.values() for h in day})
    days = summary["days_observed"]

    print(f"\n{location}  (capacity {loc['capacity']}, {days} days observed)")
    print("       " + "".join(f"{d:^4}" for d in DAYS))
    for hour in hours:
        h12 = hour % 12 or 12
        label = f"{h12}{'a' if hour < 12 else 'p'}"
        cells = ""
        for d in DAYS:
            cell = grid.get(d, {}).get(str(hour))
            cells += f"{shade(cell['pct'] if cell else None):^4}"
        print(f"{label:>5}  {cells}")
    print("\n  blank <25%   - 25-45%   = 45-65%   # 65-85%   @ 85%+   . no data")

    # The actual answer to "when should I go", rather than a picture of it.
    flat = [
        (cell["pct"], d, h)
        for d, day in grid.items()
        for h, cell in day.items()
        if 5 <= int(h) <= 22 and cell["n"] >= 2
    ]
    if flat:
        print("\n  quietest windows:")
        for pct, d, h in sorted(flat)[:5]:
            h12 = int(h) % 12 or 12
            print(f"    {d} {h12}{'am' if int(h) < 12 else 'pm'}  {pct}% full")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", nargs="?", const=FEATURED[0])
    args = ap.parse_args()

    rows = read_rows()
    summary = build(rows)
    DATA.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"summary: {summary['samples']} samples, {summary['days_observed']} days")

    if args.show:
        print_grid(summary, args.show)


if __name__ == "__main__":
    main()
