"""Daily price history, the Riftbound way (Aethos workers/prices, README
"History"): every printing, every day, both markets, kept for good, never
thinned.

One text per set and calendar month, history/<SET>/<YYYY-MM>.txt, one line a
day, oldest first:

    2026-10-28<TAB>{"OP01-001":[0.29,0.13],"OP01-001.ja":[null,0.14],...}

[TCGplayer market price USD, Cardmarket trend EUR]; null where that market
has no price that day. A line is only ever appended: a past month never
changes, so readers keep it once fetched. history/index.json lists each set's
months and the first day each market has history:

    {"since": {"usd": "2026-10-21", "eur": "2026-10-25"}, "sets": {"OP01": ["2026-10"]}, "updated": "..."}

The set of an id: the part before the first "-" or "_" ("OP01-001_p1" ->
OP01, "DON_t525340" -> DON), sealed products ("S620144") -> SEALED, anything
else -> OTHER. The apps and the site use the same rule.

data/history_usd.json and history_eur.json (thinned, for app versions that
read those) are kept alongside by update.py.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "history")
INDEX = os.path.join(ROOT, "index.json")


def set_of(pid):
    if re.fullmatch(r"S\d+", pid):
        return "SEALED"
    head = re.split(r"[-_]", pid, 1)[0]
    return head if re.fullmatch(r"[A-Z0-9]+", head) else "OTHER"


def row(prices):
    """{id: [usd, eur]} from a catalog/prices.json."""
    usd, eur = prices.get("usd") or {}, prices.get("eur") or {}
    out = {}
    for pid in set(usd) | set(eur):
        u = usd.get(pid)
        e = (eur.get(pid) or [None])[0]
        u = round(u, 2) if u and u > 0 else None
        e = round(e, 2) if e and e > 0 else None
        if u is not None or e is not None:
            out[pid] = [u, e]
    return out


def _last_day(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.read().splitlines()
            return lines[-1][:10].decode() if lines else ""
    except OSError:
        return ""


def append_day(day, prices_by_id):
    """Appends `day` ("YYYY-MM-DD") to every set's month. A set whose month
    already ends on this day or later is left as it is (a second run the
    same day adds nothing). Returns the number of sets appended."""
    index = load_index()
    by_set = {}
    for pid, v in prices_by_id.items():
        by_set.setdefault(set_of(pid), {})[pid] = v
    month, appended = day[:7], 0
    for s, rows in sorted(by_set.items()):
        path = os.path.join(ROOT, s, month + ".txt")
        if _last_day(path) >= day:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(day + "\t" + json.dumps(rows, separators=(",", ":"), sort_keys=True) + "\n")
        months = index["sets"].setdefault(s, [])
        if month not in months:
            months.append(month)
            months.sort()
        appended += 1
    if any(v[0] is not None for v in prices_by_id.values()):
        index["since"]["usd"] = min(filter(None, [index["since"].get("usd"), day]))
    if any(v[1] is not None for v in prices_by_id.values()):
        index["since"]["eur"] = min(filter(None, [index["since"].get("eur"), day]))
    save_index(index)
    return appended


def load_index():
    try:
        with open(INDEX) as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    index.setdefault("since", {})
    index.setdefault("sets", {})
    return index


def save_index(index):
    from datetime import datetime, timezone
    index["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(ROOT, exist_ok=True)
    with open(INDEX + ".tmp", "w") as f:
        json.dump(index, f, separators=(",", ":"), sort_keys=True)
    os.replace(INDEX + ".tmp", INDEX)
