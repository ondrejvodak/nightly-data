#!/usr/bin/env python3
"""KingofOP card catalog builder.

Writes `ios/KingofOP/Resources/cards.json`: every One Piece Card Game printing
the official English card list publishes (base prints, parallels, starter
decks, promos), for the collection and the deck builder.

Sources, each for what it is the authority on:

- Card DATA from Bandai's own English card list
  (https://en.onepiece-cardgame.com/cardlist/), one page per product. It is the
  only source that prints traits slash-separated (optcgapi concatenates them),
  and it carries the block icon and the trigger as their own fields. The list
  of products is read from the landing page's selector, so a new set is picked
  up the day Bandai publishes it.
- PRICES from optcgapi.com (TCGplayer market price, USD), matched on the
  printing id ("OP01-077_p1"). Prices are a convenience, never a rule input.
- IMAGES are not bundled: each card carries a URL and the app fetches and
  caches it on demand (CardImageLoader). optcgapi's 600px JPEG where it has the
  printing, Bandai's own PNG otherwise.
- PROMO PACKS, EVENT SETS and SEALED products from TCGplayer's catalog
  (tcgcsv.com): see tcgplayer_extras.py.

Card art (c) Eiichiro Oda/Shueisha, Toei Animation, Bandai — shown for
identification only.

Run after each set release:  python3 scripts/build_card_catalog.py
With KOP_CATALOG_OUT=<folder> every catalog file (this one, cards_ja.json,
tcg_products.json, cardmarket_map.json, tcg_map.json) is read from and
written to that folder instead of the app: the nightly job's way
(github.com/ondrejvodak/nightly-data, scripts/pull_nightly_catalog.py).
"""
import datetime
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

import tcgplayer_extras

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The folder the nightly job builds into; unset, the app's own files.
CATALOG_OUT = pathlib.Path(os.environ["KOP_CATALOG_OUT"]) if os.environ.get("KOP_CATALOG_OUT") else None
OUT = CATALOG_OUT / "cards.json" if CATALOG_OUT else ROOT / "ios/KingofOP/Resources/cards.json"
# Copies the Android app ships (identical files).
COPIES = [] if CATALOG_OUT else [ROOT / "android/app/src/main/assets/cards.json"]
CACHE = pathlib.Path(__file__).resolve().parent / ".cache"

BANDAI = "https://en.onepiece-cardgame.com/cardlist/"
BANDAI_IMG = "https://en.onepiece-cardgame.com/images/cardlist/card/"
API = "https://optcgapi.com/api"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url, cache_name=None, ua=BROWSER_UA, max_age=86400):
    if cache_name:
        CACHE.mkdir(exist_ok=True)
        f = CACHE / cache_name
        if f.exists() and time.time() - f.stat().st_mtime < max_age:
            return f.read_text()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    if cache_name:
        (CACHE / cache_name).write_text(text)
    return text


def clean(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def number(fragment):
    v = clean(fragment)
    return int(v) if re.fullmatch(r"\d+", v) else None


def split_slash(fragment):
    return [p.strip() for p in clean(fragment).split("/") if p.strip() and p.strip() not in ("-", "?")]


# --- Products -------------------------------------------------------------

def series_list():
    """[(series id, code, label, kind)], oldest product first."""
    landing = fetch(BANDAI, "bandai_landing.html")
    out = []
    for sid, label in re.findall(r'<option value="(5\d{5})"[^>]*>([^<]*)', landing):
        label = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(label)))
        label = re.sub(r"\s+", " ", label).strip()
        code = re.search(r"\[([^\]]+)\]\s*$", label)
        if sid == "569901":
            code, kind = "PROMO", "promo"
        elif sid == "569801":
            code, kind = "OTHER", "other"
        else:
            code = code.group(1) if code else sid
            head = label.upper()
            kind = ("starter" if ("STARTER" in head or "ULTRA DECK" in head) else
                    "extra" if "EXTRA BOOSTER" in head else
                    "premium" if "PREMIUM BOOSTER" in head else "booster")
        out.append((sid, code, label, kind))
    # Release order: starter decks and boosters interleave in real life, but
    # the series id runs in release order within each line. Boosters first by
    # id, then the rest — the app sorts sets by `order` for display.
    return sorted(set(out), key=lambda s: s[0])


PRODUCTS = "https://en.onepiece-cardgame.com/products/"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def release_dates():
    """{code: ("2026-08-28", label)} from Bandai's products pages, which carry
    the release date the card list does not — announced products included."""
    out = {}
    for sub in ("boosters", "decks"):
        for page in range(1, 10):
            text = fetch(f"{PRODUCTS}?subcategory={sub}&page={page}", f"products_{sub}_{page}.html")
            text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
            lines = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", text)).split("\n") if l.strip()]
            found = 0
            for i, line in enumerate(lines):
                code = re.search(r"\[((?:OP|EB|ST|PRB)\d*-?(?:EB)?\d+)\]\s*$", line)
                if not code or code.group(1) in out:
                    continue
                for nxt in lines[i + 1:i + 4]:
                    d = re.fullmatch(r"([A-Z][a-z]+) (\d{1,2}), (20\d\d)", nxt)
                    if d and d.group(1) in MONTHS:
                        out[code.group(1)] = (f"{d.group(3)}-{MONTHS[d.group(1)]:02d}-{int(d.group(2)):02d}",
                                              re.sub(r"\s+", " ", line))
                        found += 1
                        break
            if not found:
                break
    return out


def add_release_dates(sets):
    """Stamp each set with its release date; return the announced products
    that have no card list yet, for the shelf's "coming" plates."""
    dates = release_dates()
    for s in sets:
        if s["code"] in dates:
            s["released"] = dates[s["code"]][0]
    have = {s["code"] for s in sets}
    today = datetime.date.today().isoformat()
    upcoming = []
    for code, (day, label) in sorted(dates.items(), key=lambda kv: kv[1][0]):
        if code in have or day <= today:
            continue
        head = label.upper()
        kind = ("starter" if ("STARTER" in head or "ULTRA DECK" in head) else
                "extra" if "EXTRA BOOSTER" in head else
                "premium" if "PREMIUM BOOSTER" in head else "booster")
        upcoming.append({"code": code, "name": title_name(re.sub(r"\s*\[[^\]]*\]\s*$", "", label)), "kind": kind, "order": 0, "released": day})
    return upcoming


SET_FIXES = {
    "ST-19": {"name": "Starter Deck 19: BLACK Smoker"},
    "ST-05": {"released": "2023-02-03"},
    "EB-05": {"name": "Extra Booster: One Piece Heroines Edition vol.2"},
}


def title_name(label):
    """'BOOSTER PACK -ROMANCE DAWN- [OP-01]' -> 'Romance Dawn'."""
    m = re.search(r"-(.+)-", label)
    name = m.group(1).strip() if m else label
    small = {"of", "the", "in", "on", "and", "a", "an", "to", "vol.2"}
    words = name.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if any(c.islower() for c in w):
            out.append(w)            # already cased by Bandai ("Anime 25th Collection")
        elif i and lw in small:
            out.append(lw)
        else:
            out.append(w[:1] + w[1:].lower())
    return " ".join(out).replace("’", "'")


# --- Cards ----------------------------------------------------------------

BLOCK = re.compile(r'<dl class="modalCol" id="([A-Za-z0-9]+-[A-Za-z0-9]+(?:_[a-z0-9]+)?)">(.*?)</dl>', re.S)
CATEGORIES = {"LEADER": "leader", "CHARACTER": "character", "EVENT": "event", "STAGE": "stage", "DON!!": "don"}


def field(body, cls):
    m = re.search(rf'<div class="{cls}">(.*?)</div>', body, re.S)
    if not m:
        return None, None
    inner = m.group(1)
    h3 = re.search(r"<h3>(.*?)</h3>", inner, re.S)
    label = clean(h3.group(1)) if h3 else ""
    value = re.sub(r"<h3>.*?</h3>", "", inner, count=1, flags=re.S)
    return label, value


def parse_card(raw_id, body, set_code):
    info = re.search(r'<div class="infoCol">(.*?)</div>', body, re.S)
    spans = [clean(s) for s in re.findall(r"<span>(.*?)</span>", info.group(1), re.S)] if info else []
    rarity = spans[1] if len(spans) > 1 else ""
    category = CATEGORIES.get(spans[2].upper(), "other") if len(spans) > 2 else "other"
    name = clean(re.search(r'<div class="cardName">(.*?)</div>', body, re.S).group(1))

    card = {"id": raw_id, "num": raw_id.split("_")[0], "name": name, "cat": category,
            "rar": rarity, "set": set_code}

    label, value = field(body, "cost")
    if value is not None:
        n = number(value)
        if n is not None:
            card["life" if label.lower().startswith("life") else "cost"] = n
    _, value = field(body, "power")
    if (n := number(value)) is not None:
        card["pow"] = n
    _, value = field(body, "counter")
    if (n := number(value)) is not None:
        card["ctr"] = n
    _, value = field(body, "color")
    card["col"] = split_slash(value)
    _, value = field(body, "block")
    if (n := number(value)) is not None:
        card["blk"] = n
    attr = re.search(r'<div class="attribute">.*?<i>(.*?)</i>', body, re.S)
    if attr and split_slash(attr.group(1)):
        card["attr"] = split_slash(attr.group(1))
    _, value = field(body, "feature")
    if value is not None and split_slash(value):
        card["tr"] = split_slash(value)
    _, value = field(body, "text")
    text = clean(value) if value is not None else ""
    if text and text != "-":
        card["text"] = text
    _, value = field(body, "trigger")
    trig = clean(value) if value is not None else ""
    if trig:
        card["trig"] = re.sub(r"^\[Trigger\]\s*", "", trig)
    return card


def bandai_cards(series):
    cards, sets = {}, []
    for order, (sid, code, label, kind) in enumerate(series):
        try:
            page = fetch(f"{BANDAI}?series={sid}", f"bandai_{sid}.html")
        except Exception as e:
            print(f"  WARN series {sid} {code}: {e}", file=sys.stderr)
            continue
        found = 0
        for raw_id, body in BLOCK.findall(page):
            if raw_id in cards:
                continue
            try:
                cards[raw_id] = parse_card(raw_id, body, code)
                found += 1
            except Exception as e:
                print(f"  WARN card {raw_id}: {e}", file=sys.stderr)
        sets.append({"code": code, "name": title_name(label) if kind not in ("promo", "other") else
                     ("Promotion Cards" if kind == "promo" else "Other Products"),
                     "kind": kind, "order": order})
        print(f"  {code:<10} {found:4d} new printings")
        time.sleep(0.25)
    return cards, sets


# The printing's variant, read from the qualifiers TCGplayer names it with
# ("Shanks (Parallel) (Manga) (Alternate Art)"): what a collector sorts by.
VARIANTS = [("Manga", "Manga"), ("SPR", "Super Parallel"), ("SP", "SP"), ("Wanted Poster", "Wanted Poster"),
            ("Full Art", "Full Art"), ("Jolly Roger Foil", "Jolly Roger Foil"), ("Pirate Foil", "Pirate Foil"),
            ("Textured Foil", "Textured Foil"), ("Box Topper", "Box Topper"), ("Alternate Art", "Alt art"),
            ("Parallel", "Alt art"), ("Reprint", "Reprint")]
variants = {}


def optcg_prices():
    """printing id -> (USD market price, image URL), plus set names."""
    prices, names = {}, {}
    for kind in ("allSetCards", "allSTCards"):
        try:
            rows = json.loads(fetch(f"{API}/{kind}/", f"optcg_{kind}.json", ua="KingofOP-companion-dev"))
        except Exception as e:
            print(f"  WARN optcgapi {kind}: {e}", file=sys.stderr)
            continue
        for r in rows:
            pid = r.get("card_image_id") or r.get("card_set_id")
            if not pid:
                continue
            price = r.get("market_price") or r.get("inventory_price")
            prev = prices.get(pid)
            if prev is None or (price and not prev[0]):
                prices[pid] = (round(float(price), 2) if price else None, r.get("card_image"))
            if r.get("set_id") and r.get("set_name"):
                names[r["set_id"]] = r["set_name"]
            quals = re.findall(r"\(([^)]+)\)", r.get("card_name") or "")
            tags = [label for word, label in VARIANTS if word in quals]
            if tags and pid not in variants:
                variants[pid] = list(dict.fromkeys(tags))
    return prices, names


def main():
    print("products...")
    series = series_list()
    print(f"  {len(series)} products on the official list")
    print("cards (official list)...")
    cards, sets = bandai_cards(series)
    print(f"  {len(cards)} printings")
    # A printing, once shipped, stays: collections and deck lists hold its id.
    # Bandai's selector sometimes drops a product for a while (EB-05 went
    # missing a week before release), so what the last build had and this one
    # did not read is carried over as it was.
    if OUT.exists():
        previous = json.loads(OUT.read_text())
        codes = {s["code"] for s in sets}
        kept = [c for c in previous["cards"] if c["id"] not in cards and "_t" not in c["id"]]
        for c in kept:
            cards[c["id"]] = c
        for s in previous["sets"]:
            if s["code"] not in codes and any(c["set"] == s["code"] for c in kept):
                sets.append({k: v for k, v in s.items() if k in ("code", "name", "kind", "order", "released")})
        if kept:
            print(f"  {len(kept)} printings kept from the last build that the list no longer shows")
    # A card is filed under the product its number belongs to ("OP01-..." is
    # Romance Dawn) even when a starter deck that lists it as a reprint was
    # read first. Parallels stay with the product that prints them (a PRB-01
    # alt art of an OP01 card is pulled from PRB-01 boxes), and numbers
    # without a product of their own ("EB04-...", promos) stay where found.
    codes = {s["code"] for s in sets}
    for c in cards.values():
        if "_" in c["id"]:
            continue
        m = re.match(r"([A-Z]+)(\d+)-", c["num"])
        if m and f"{m.group(1)}-{m.group(2)}" in codes:
            c["set"] = f"{m.group(1)}-{m.group(2)}"
    print("prices + images (optcgapi)...")
    prices, names = optcg_prices()
    for s in sets:
        if s["code"] in names:
            s["name"] = names[s["code"]]
    print("release dates (products pages)...")
    upcoming = add_release_dates(sets)
    # Where a source has it wrong: optcgapi files ST-19 under OP-17's title, its
    # EB-05 title is cut short, and Bandai's products pages carry no date for
    # ST-05 (Limitless: 3 Feb 2023).
    for s in sets:
        s.update(SET_FIXES.get(s["code"], {}))
    print(f"  {sum('released' in s for s in sets)} sets dated, {len(upcoming)} announced")

    for pid, c in cards.items():
        if pid in variants and "_" in pid:
            c["var"] = variants[pid]
    priced = 0
    for pid, c in cards.items():
        price, img = prices.get(pid, (None, None))
        if price:
            c["usd"] = price
            priced += 1
        c["img"] = img or f"{BANDAI_IMG}{pid}.png"
    print(f"  {priced} printings priced")
    print("promo packs, event sets, sealed (tcgcsv.com)...")
    sealed = tcgplayer_extras.build(cards, sets, fetch)

    def sort_key(c):
        base, _, alt = c["id"].partition("_")
        return (base, alt)

    out = {
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "en.onepiece-cardgame.com + optcgapi.com + tcgcsv.com",
        "sets": sets,
        "upcoming": upcoming,
        "sealed": sorted(sealed, key=lambda s: (s["set"], s["name"])),
        "cards": sorted(cards.values(), key=sort_key),
    }
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    for path in [OUT] + COPIES:
        path.write_text(text)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(cards)} printings, {len(sets)} sets)")


def dates_only():
    """Refresh release dates and announced products in the existing file,
    without re-reading every card:  build_card_catalog.py --dates"""
    data = json.loads(OUT.read_text())
    data["upcoming"] = add_release_dates(data["sets"])
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for path in [OUT] + COPIES:
        path.write_text(text)
    print(f"dated {sum('released' in s for s in data['sets'])} sets, {len(data['upcoming'])} announced")


if __name__ == "__main__":
    dates_only() if "--dates" in sys.argv else main()
