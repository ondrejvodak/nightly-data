#!/usr/bin/env python3
"""One-off: the daily history (history.py) for the days before it existed,
rebuilt from this repo's own nightly commits. Each date's last commit gives
that day's prices: TCGplayer from catalog/prices.json where it exists (from 25
Oct 2026), else data/history_usd.json as it stood (a printing's last point
before that day, which the old format only repeated when the price moved or a
week passed); Cardmarket from catalog/prices.json only (25 Oct onward). A date
with no commit (26 Oct: tcgcsv unchanged, nothing ran) gets no line.

Run once from the repo root, before the first nightly run of history.py:
    python3 scripts/backfill_daily.py
"""
import json, os, subprocess, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import history


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def at(rev, path):
    try:
        return json.loads(git("show", f"{rev}:{path}"))
    except (subprocess.CalledProcessError, ValueError):
        return None


def main():
    last = {}
    for line in git("log", "--reverse", "--format=%H %ad", "--date=short").splitlines():
        rev, d = line.split()
        last[d] = rev
    for d, rev in sorted(last.items()):
        prices = at(rev, "catalog/prices.json")
        if prices is None:
            old = (at(rev, "data/history_usd.json") or {}).get("usd", {})
            day = date.fromisoformat(d).toordinal() - date(1970, 1, 1).toordinal()
            usd = {}
            for pid, flat in old.items():
                pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2) if flat[i] <= day]
                if pts and day - pts[-1][0] < 7:
                    usd[pid] = pts[-1][1] / 100
            prices = {"usd": usd, "eur": {}}
        rows = history.row(prices)
        if rows:
            n = history.append_day(d, rows)
            print(d, rev[:7], len(rows), "printings,", n, "sets")


if __name__ == "__main__":
    main()
