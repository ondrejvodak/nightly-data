#!/usr/bin/env python3
"""Nightly card price snapshot.

Reads tcgcsv.com category 68 once a day (one sync, checked against
last-updated.txt, 100 ms between requests, own User-Agent), joins products
to printing ids by the catalog's own pairs (catalog/tcg_products.json: each
printing's own TCGplayer product, checked by picture; copied to
data/map.json), and appends each printing's market price (what copies sold
for; a product nobody has bought has no point) to data/history_usd.json: printing id -> flat [day, cents, ...], days since
1970-01-01 UTC. A point only when the price moved or a week passed; older
than 30 days thinned to weekly; older than 3 years dropped.

Standard library only. Run: python3 update.py [--force]
"""
import gzip, json, os, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MAP = os.path.join(DATA, "map.json")
HISTORY = os.path.join(DATA, "history_usd.json")
HISTORY_EUR = os.path.join(DATA, "history_eur.json")
CATALOG_PRICES = os.path.join(HERE, "catalog", "prices.json")
CATALOG_MAP = os.path.join(HERE, "catalog", "tcg_products.json")
STATE = os.path.join(DATA, "state.json")

TCGCSV = "https://tcgcsv.com/tcgplayer/68"
LAST_UPDATED = "https://tcgcsv.com/last-updated.txt"
UA = "nightly-data/1.0"

DAILY_WINDOW, KEEP_DAYS, WEEK = 30, 3 * 365, 7


def get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    time.sleep(0.1)
    return json.loads(raw) if as_json else raw.decode()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def today_day():
    return int(time.time() // 86400)


# MARK: history

def thin(flat, today):
    if len(flat) <= 2:
        return flat
    out, last_kept = [], None
    n = len(flat)
    for i in range(0, n - 1, 2):
        day, newest = flat[i], i == n - 2
        if today - day > KEEP_DAYS and not newest:
            continue
        if today - day > DAILY_WINDOW and not newest and last_kept is not None and day - last_kept < WEEK:
            continue
        out += [day, flat[i + 1]]
        last_kept = day
    return out


def append(series, pid, price, today):
    cents = int(round(price * 100))
    flat = series.get(pid, [])
    if len(flat) >= 2 and flat[-2] == today:
        flat[-1] = cents
    elif len(flat) >= 2 and flat[-1] == cents and today - flat[-2] < WEEK:
        pass
    else:
        flat += [today, cents]
    series[pid] = thin(flat, today)


# MARK: joining TCGplayer products to printing ids

def catalog_map():
    """productId -> printing id, from the catalog build (empty if missing)."""
    groups = load(CATALOG_MAP, {}).get("groups", {})
    return {prod: pid for products in groups.values() for prod, pid in products.items()}


def drop_misfiled(history, old, new):
    """Until 25 Oct 2026 products were joined to printings by same-day price,
    which sometimes picked another version. A printing whose product the
    catalog now names differently loses the points recorded under the old
    one: they were another card's prices."""
    was = {pid: prod for prod, pid in old.items()}
    now = {pid: prod for prod, pid in new.items()}
    dropped = [pid for pid, prod in was.items() if pid in now and now[pid] != prod]
    for pid in dropped:
        history.pop(pid, None)
    return len(dropped)


def record_eur():
    """Today's Cardmarket trend per printing, from the catalog build. A
    price guide already recorded (same createdAt) adds nothing."""
    prices = load(CATALOG_PRICES, {})
    eur, created = prices.get("eur") or {}, prices.get("cardmarket")
    history = load(HISTORY_EUR, {"v": 1, "eur": {}})
    if not eur or (created and history.get("cardmarket") == created):
        print("Cardmarket guide unchanged or missing - no EUR points")
        return
    today = today_day()
    for pid, row in eur.items():
        if row and row[0]:
            append(history["eur"], pid, row[0], today)
    history["v"] = 1
    history["source"] = "Cardmarket trend price (price guide)"
    history["cardmarket"] = created
    history["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save(HISTORY_EUR, history)
    print(f"Cardmarket: recorded {len(eur)} printings")


def main(force=False):
    os.makedirs(DATA, exist_ok=True)
    record_eur()
    state = load(STATE, {})
    stamp = get(LAST_UPDATED, as_json=False).strip()
    if not force and state.get("tcgcsv") == stamp:
        print("tcgcsv unchanged since", stamp, "- nothing to do")
        return

    groups = get(f"{TCGCSV}/groups")["results"]
    products_cache = load(os.path.join(DATA, "products.json"), {})
    products, prices = {}, {}
    for g in groups:
        gid = str(g["groupId"])
        cached = products_cache.get(gid)
        if not cached or cached.get("modifiedOn") != g.get("modifiedOn"):
            numbers = {}
            for p in get(f"{TCGCSV}/{gid}/products")["results"]:
                num = next((e["value"] for e in p.get("extendedData", []) if e.get("name") == "Number"), None)
                if num:
                    numbers[str(p["productId"])] = num
            cached = {"modifiedOn": g.get("modifiedOn"), "numbers": numbers}
            products_cache[gid] = cached
        products.update(cached["numbers"])
        for r in get(f"{TCGCSV}/{gid}/prices")["results"]:
            price = r.get("marketPrice")
            if price and price > 0 and (str(r["productId"]) not in prices or r.get("subTypeName") == "Normal"):
                prices[str(r["productId"])] = price
    save(os.path.join(DATA, "products.json"), products_cache)

    mapping = catalog_map()
    if not mapping:
        print("no catalog/tcg_products.json - nothing to join")
        sys.exit(1)
    history = load(HISTORY, {"v": 1, "usd": {}})
    dropped = drop_misfiled(history["usd"], load(MAP, {}), mapping)
    save(MAP, mapping)
    today = today_day()
    recorded = 0
    for prod, pid in mapping.items():
        if prod in prices:
            append(history["usd"], pid, prices[prod], today)
            recorded += 1
    history["v"] = 1
    history["source"] = "TCGplayer market price via tcgcsv.com"
    history["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save(HISTORY, history)

    state["tcgcsv"] = stamp
    save(STATE, state)
    print(f"groups {len(groups)}, products {len(products)}, priced {len(prices)}, "
          f"mapped {len(mapping)}, recorded {recorded}, series dropped as misfiled {dropped}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
