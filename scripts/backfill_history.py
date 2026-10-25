#!/usr/bin/env python3
"""One-off backfill of data/history_usd.json from tcgcsv's daily price archive.

tcgcsv published one archive a day from 2024-02-08:
    https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z
which extracts to YYYY-MM-DD/<categoryId>/<groupId>/prices (the same JSON as
the live /tcgplayer/<cat>/<group>/prices). Only category 68 is read.

Status 25 Oct 2026: the archive answers HTTP 403 ("temporarily removed due to
rising server costs"). The script stops at the first such answer and changes
nothing. If the archive returns, or the archives are obtained another way and
extracted locally, run it again (--from-dir reads extracted day folders).

Polite by design: own User-Agent, one download at a time, a pause between
downloads, each archive fetched once (kept in --cache). Sampled, not daily:
weekly for the last 12 months, every 4 weeks before that, back to 2024-02-08.

Products map to printing ids through data/map.json (the same map update.py
learns). Points merge into the existing file in its exact format (printing id
-> flat [day, cents, ...], sorted); the recorded point wins on a shared day,
and update.py's rules apply: an unchanged price within a week adds no point,
older than 30 days thins to weekly, older than 3 years goes.

Run:  python3 scripts/backfill_history.py [--dry-run] [--cache DIR] [--from-dir DIR]
Needs `7zz`, `7z` or `7za` on PATH (brew install sevenzip), or py7zr importable.
"""
import argparse, glob, json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import update  # noqa: E402  (thin, load, save, constants)

ARCHIVE = "https://tcgcsv.com/archive/tcgplayer/prices-{d}.ppmd.7z"
UA = "nightly-data-backfill/1.0"
CATEGORY = "68"
FIRST = date(2024, 2, 8)
PAUSE = 5.0  # seconds between downloads


class ArchiveGone(Exception):
    pass


def sample_dates(today):
    """Weekly back to a year ago, then every 4 weeks back to FIRST; oldest first."""
    out, d = [], today - timedelta(days=1)
    year_ago = today - timedelta(days=365)
    while d >= FIRST:
        out.append(d)
        d -= timedelta(days=7 if d > year_ago else 28)
    return sorted(out)


def download(d, cache):
    path = os.path.join(cache, f"prices-{d}.ppmd.7z")
    if os.path.exists(path):
        return path, False
    req = urllib.request.Request(ARCHIVE.format(d=d), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        body = e.read(400).decode(errors="replace")
        if e.code == 403:
            raise ArchiveGone(f"{d}: HTTP 403 - {body.strip()[:200]}")
        if e.code == 404:
            return None, True
        raise
    os.replace(path + ".part", path)
    return path, True


def extract(archive, out):
    """Only <day>/68/... out of one day's archive."""
    tool = next((t for t in ("7zz", "7z", "7za") if shutil.which(t)), None)
    if tool:
        subprocess.run([tool, "x", "-y", f"-o{out}", archive, f"*/{CATEGORY}/*", "-r"],
                       check=True, stdout=subprocess.DEVNULL)
        return
    try:
        import py7zr
    except ImportError:
        sys.exit("No 7z extractor: brew install sevenzip, or pip install py7zr")
    with py7zr.SevenZipFile(archive) as z:
        names = [n for n in z.getnames() if n.split("/")[1:2] == [CATEGORY]]
        z.extract(path=out, targets=names)


def read_day(folder):
    """productId -> price for one extracted day, the way update.py reads the
    live file (market price, else low; the last subtype row wins)."""
    prices = {}
    for path in sorted(glob.glob(os.path.join(folder, CATEGORY, "*", "prices"))):
        try:
            with open(path) as f:
                rows = json.load(f).get("results", [])
        except (OSError, ValueError):
            continue
        for r in rows:
            price = r.get("marketPrice") or r.get("lowPrice")
            if price and price > 0:
                prices[str(r["productId"])] = price
    return prices


def merge(series, backfill, today):
    """backfill: printing id -> {day: cents}. The recorded point wins a day."""
    touched = 0
    for pid, days in backfill.items():
        mine = series.get(pid, [])
        by_day = dict(days)
        for i in range(0, len(mine) - 1, 2):
            by_day[mine[i]] = mine[i + 1]
        flat = []
        for day in sorted(by_day):
            cents = by_day[day]
            # update.append's rule: unchanged and within a week says nothing new.
            if len(flat) >= 2 and flat[-1] == cents and day - flat[-2] < update.WEEK:
                continue
            flat += [day, cents]
        flat = update.thin(flat, today)
        if flat != mine:
            series[pid] = flat
            touched += 1
    return touched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, don't write data/")
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "tcgcsv-archive"))
    ap.add_argument("--from-dir", help="already-extracted YYYY-MM-DD folders; no downloads")
    args = ap.parse_args()

    mapping = update.load(update.MAP, {})
    if not mapping:
        sys.exit("data/map.json is empty: run update.py first")
    today = update.today_day()
    epoch = date(1970, 1, 1)
    backfill = {}

    def take(d, prices):
        day = (d - epoch).days
        n = 0
        for prod, pid in mapping.items():
            if prod in prices:
                backfill.setdefault(pid, {})[day] = int(round(prices[prod] * 100))
                n += 1
        print(f"{d}: {len(prices)} priced, {n} mapped")

    if args.from_dir:
        for folder in sorted(glob.glob(os.path.join(args.from_dir, "20??-??-??"))):
            take(date.fromisoformat(os.path.basename(folder)), read_day(folder))
    else:
        os.makedirs(args.cache, exist_ok=True)
        for d in sample_dates(date.today()):
            try:
                archive, fetched = download(d, args.cache)
            except ArchiveGone as e:
                sys.exit(f"tcgcsv archive unavailable, nothing changed.\n{e}")
            if archive:
                with tempfile.TemporaryDirectory() as tmp:
                    extract(archive, tmp)
                    take(d, read_day(os.path.join(tmp, str(d))))
            else:
                print(f"{d}: no archive")
            if fetched:
                time.sleep(PAUSE)

    history = update.load(update.HISTORY, {"v": 1, "usd": {}})
    touched = merge(history["usd"], backfill, today)
    weeks = sum(1 for flat in history["usd"].values() if len(flat) // 2 >= 14)
    print(f"printings backfilled {len(backfill)}, changed {touched}, with 14+ points {weeks}")
    if not args.dry_run:
        update.save(update.HISTORY, history)


if __name__ == "__main__":
    main()
