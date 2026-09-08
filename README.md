# mizzou-rec-log

A running record of how busy MizzouRec is.

The Rec publishes a live headcount for each room on its
[Facility Usage](https://mizzourec.missouri.edu/facility-usage/) page. That page
shows only the current number, and the vendor endpoint behind it keeps no public
history, so "when is the weight room actually empty" has no answer you can look
up. This repo samples that endpoint every ten minutes and keeps what it finds.

## What is here

| Path | What it is |
|---|---|
| `collect.py` | One sample. Appends any newly published counts to `data/YYYY-MM.csv`. |
| `summarize.py` | Collapses every sample into `data/summary.json`, a weekday-by-hour median per room. |
| `data/last_seen.json` | Last timestamp seen per room, so re-polling the same count writes nothing. |
| `.github/workflows/collect.yml` | The cron. Runs on GitHub's runners, commits only when there is something new. |

## Reading it

```
python3 summarize.py --print "Strength Training"
```

Prints a grid of hours against weekdays, plus the five quietest windows.

## Notes on the data

The endpoint returns `LastUpdatedDateAndTime`, the moment the Rec took the
count. Rows are keyed on that, not on when the script ran, so the record has the
Rec's real resolution regardless of polling rate. Some rooms republish every few
minutes and some only hourly.

Rooms flagged `IsClosed` are still recorded but excluded from the summary. A
closed room reads zero, which is true and also meaningless as a measure of how
busy it gets.

Capacities are the Rec's own numbers and do change between semesters, so a
percentage is only comparable within a term.

Nothing here identifies anybody. The counts are aggregate, they are already
public, and no authentication is involved in reading them.

Runs on stdlib Python only, no dependencies, but it needs a Python built
against OpenSSL 3 (3.11 or newer). macOS system Python 3.9 ships with an old
LibreSSL that the endpoint's TLS refuses.
