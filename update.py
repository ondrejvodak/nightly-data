#!/usr/bin/env python3
"""Nightly card price snapshot.

Reads tcgcsv.com category 68 once a day (one sync, checked against
last-updated.txt, 100 ms between requests, own User-Agent) and optcgapi.com's
card lists, joins products to printing ids by card number and same-day market
price (data/map.json), and appends each printing's market price to
data/history_usd.json: printing id -> flat [day, cents, ...], days since
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
STATE = os.path.join(DATA, "state.json")

TCGCSV = "https://tcgcsv.com/tcgplayer/68"
LAST_UPDATED = "https://tcgcsv.com/last-updated.txt"
OPTCG = ["https://optcgapi.com/api/allSetCards/", "https://optcgapi.com/api/allSTCards/"]
UA = "nightly-data/1.0"

DAILY_WINDOW, KEEP_DAYS, WEEK = 30, 3 * 365, 7
MATCH_TOLERANCE = 0.03  # 3%, and never more than 2 cents off for cheap cards


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

def close(a, b):
    return abs(a - b) <= max(0.02, MATCH_TOLERANCE * max(a, b))


def learn_map(mapping, products, prices, ours):
    """products: productId -> number; prices: productId -> market; ours:
    printing id -> (number, market). Adds productId -> printing id for every
    unmapped printing whose price matches exactly one unmapped product of its
    number, and that product matches no other printing."""
    taken_products = set(mapping)
    taken_printings = set(mapping.values())
    by_number = {}
    for prod, num in products.items():
        if prod not in taken_products and prod in prices:
            by_number.setdefault(num, []).append(prod)
    ours_by_number = {}
    for q, (n2, p2) in ours.items():
        ours_by_number.setdefault(n2, []).append((q, p2))
    added = 0
    for pid, (num, price) in ours.items():
        if pid in taken_printings:
            continue
        hits = [p for p in by_number.get(num, []) if close(prices[p], price)]
        if len(hits) != 1:
            continue
        rivals = [q for q, p2 in ours_by_number[num]
                  if q != pid and q not in taken_printings and close(prices[hits[0]], p2)]
        if rivals:
            continue
        mapping[hits[0]] = pid
        taken_printings.add(pid)
        by_number[num].remove(hits[0])
        added += 1
    return added


def main(force=False):
    os.makedirs(DATA, exist_ok=True)
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
            price = r.get("marketPrice") or r.get("lowPrice")
            if price and price > 0:
                prices[str(r["productId"])] = price
    save(os.path.join(DATA, "products.json"), products_cache)

    ours = {}
    for url in OPTCG:
        for row in get(url):
            pid = row.get("card_image_id") or row.get("card_set_id")
            price = row.get("market_price") or row.get("inventory_price")
            if pid and row.get("card_set_id") and price and price > 0:
                ours.setdefault(pid, (row["card_set_id"], price))

    mapping = load(MAP, {})
    added = learn_map(mapping, products, prices, ours)
    save(MAP, mapping)

    history = load(HISTORY, {"v": 1, "usd": {}})
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
          f"printings {len(ours)}, mapped {len(mapping)} (+{added}), recorded {recorded}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
