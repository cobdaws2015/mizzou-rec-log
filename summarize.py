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
from datetime import date, datetime, timedelta, timezone
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


def parse_ts(raw: str) -> datetime | None:
    """Parse the Rec's published_at, which carries ragged fractional seconds.

    Values like "2026-09-08T14:30:43.14" have a 2-digit fraction that older
    fromisoformat() rejects, so the fraction is dropped before parsing. These
    are naive America/Chicago times; gap arithmetic across a DST boundary is
    off by an hour twice a year, which does not matter at this resolution.
    """
    raw = (raw or "").strip().split(".")[0]
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def read_timestamps() -> list[datetime]:
    """Every distinct moment the Rec published a count, closed rooms included.

    Deliberately not read_rows(): that one drops closed rooms and blank pcts
    because they would skew the medians. Here a closed room is evidence, since
    it proves the counters were alive and talking at that moment.
    """
    seen: set[str] = set()
    for path in sorted(DATA.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].csv")):
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("published_at"):
                    seen.add(r["published_at"])
    return sorted(filter(None, (parse_ts(t) for t in seen)))


# A weekday-by-hour cell needs weeks to fill: 7 days x 17 hours is 119 buckets
# and the Rec republishes about once an hour, so every cell sits at n=1 or n=2
# for over a month. Pooling the hour shape across Mon-Fri and scaling it by a
# per-day factor spends the same samples on 17 buckets plus 7 scalars instead,
# which is answerable today. Weekends keep their own shape because the Rec opens
# late and the curve is a different animal, not a scaled weekday.
def build_profiles(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for loc in FEATURED:
        mine = [r for r in rows if r["location"] == loc]
        if not mine:
            continue

        def bucket(sel) -> dict[str, dict]:
            acc: dict[int, list[float]] = defaultdict(list)
            for r in mine:
                if sel(r):
                    acc[r["_hour"]].append(r["_pct"])
            return {
                str(h): {"pct": round(statistics.median(v)), "n": len(v)}
                for h, v in sorted(acc.items())
            }

        # Factors are computed from staffed hours only. Including 5am, when
        # every day reads near zero, would flatten the differences between days.
        staffed = [r["_pct"] for r in mine if r["_hour"] >= 7]
        base = statistics.median(staffed) if staffed else 0
        factors: dict[str, dict] = {}
        for wd in range(7):
            v = [r["_pct"] for r in mine if r["_weekday"] == wd and r["_hour"] >= 7]
            if v and base:
                factors[DAYS[wd]] = {
                    "factor": round(statistics.median(v) / base, 2),
                    "n": len(v),
                }

        out[loc] = {
            "capacity": next((int(r["capacity"]) for r in mine if r.get("capacity")), 0),
            "weekday_hours": bucket(lambda r: r["_weekday"] < 5),
            "weekend_hours": bucket(lambda r: r["_weekday"] >= 5),
            "day_factor": factors,
        }
    return out


def read_home_games() -> dict[str, str]:
    """Map a date to the opponent Mizzou hosted that day, if any.

    A home game appears to shut the Rec for the whole day, and a shut Rec is
    indistinguishable from a broken scraper without this. Away games are in the
    file too but are not returned, since the Rec stays open for those.
    """
    path = DATA / "home_games.json"
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        g["date"]: g.get("opponent", "?")
        for g in blob.get("games", [])
        if g.get("home") and g.get("date")
    }


def find_coverage(stamps: list[datetime], home_games: dict[str, str] | None = None) -> dict:
    """Describe the holes in the record so nothing downstream averages over one.

    A closed Rec does not announce itself. The counters simply stop publishing,
    the collector sees nothing new and writes no rows, so a closure and a broken
    scraper leave an identical hole. Overnight that hole is 9-13 hours and
    expected; a whole dark calendar day is not. 2026-09-19 is the worked
    example: the Rec shut for the Troy home game and the day vanished.
    """
    if not stamps:
        return {"days_observed": 0, "days_spanned": 0, "missing_days": [],
                "explained_days": {}, "unexplained_days": [],
                "next_home_games": [], "longest_gaps": [],
                "window_starts": None, "probe_days": []}

    # Collection ran as a couple of one-off probes in July before it went
    # continuous on 2026-09-07. Counting the seven dead weeks between them as
    # "missing days" would bury the holes that actually mean something, so the
    # window starts after the last multi-week break and the strays are named
    # separately rather than silently dropped.
    start = 0
    for i in range(len(stamps) - 1, 0, -1):
        if (stamps[i] - stamps[i - 1]).total_seconds() > 7 * 86400:
            start = i
            break
    probes = sorted({d.date().isoformat() for d in stamps[:start]})
    stamps = stamps[start:]

    observed = {d.date() for d in stamps}
    first, last = min(observed), max(observed)
    span = (last - first).days + 1

    home_games = home_games or {}
    missing = []
    explained = {}
    for i in range(span):
        day = first + timedelta(days=i)
        if day not in observed:
            iso = day.isoformat()
            missing.append(iso)
            if iso in home_games:
                explained[iso] = f"home game vs {home_games[iso]}"

    # No threshold on purpose. Weekends and overnights make any fixed cutoff
    # either noisy or blind, so hand over the biggest few and let a human look.
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        hours = (b - a).total_seconds() / 3600
        dark = {(a.date() + timedelta(days=n)).isoformat() for n in range(1, (b.date() - a.date()).days)}
        gaps.append({
            "from": a.isoformat(timespec="minutes"),
            "to": b.isoformat(timespec="minutes"),
            "hours": round(hours, 1),
            "dark_days": sorted(dark & set(missing)),
        })
    gaps.sort(key=lambda g: -g["hours"])

    return {
        "days_observed": len(observed),
        "days_spanned": span,
        "window_starts": first.isoformat(),
        "probe_days": probes,
        "missing_days": missing,
        "explained_days": explained,
        "unexplained_days": [d for d in missing if d not in explained],
        "next_home_games": sorted(d for d in home_games if d > last.isoformat()),
        "longest_gaps": gaps[:5],
    }


def build(rows: list[dict], coverage: dict | None = None) -> dict:
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
        "coverage": coverage or {},
        "profiles": build_profiles(rows),
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
    coverage = find_coverage(read_timestamps(), read_home_games())
    summary = build(rows, coverage)
    DATA.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"summary: {summary['samples']} samples, {summary['days_observed']} days")

    for day in coverage["missing_days"]:
        why = coverage["explained_days"].get(day, "unexplained")
        print(f"  no data at all on {day} ({why})")
    upcoming = coverage["next_home_games"]
    if upcoming:
        nxt = upcoming[0]
        print(f"  next home game {nxt} vs {read_home_games()[nxt]} - expect a dark day")
    if coverage["longest_gaps"]:
        worst = coverage["longest_gaps"][0]
        print(f"  longest silence: {worst['hours']}h, {worst['from']} -> {worst['to']}")

    if args.show:
        print_grid(summary, args.show)


if __name__ == "__main__":
    main()
