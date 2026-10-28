"""Everything Bandai's English card list does not file product by product,
from TCGplayer's catalog (as tcgcsv.com mirrors it, category 68):

- PROMO SETS: Bandai lists every promotion card under one "Promotion Cards"
  page. TCGplayer names each one with the product it came in ("Crocodile
  (Promotion Pack 2022)", "Nami (Tournament Pack 2026 Vol. 4)"), so the
  promos split into one set per pack, filed under a family (tournament and
  winner packs, event packs, championship sets, judge packs, premium
  collections...).
- EVENT SETS: the stamped release-event, pre-release and anniversary
  printings ("OP17 RE"), demo and revision decks, Learn Together / Set Sail
  deck sets. Bandai's list does not carry these at all.
- SEALED: every booster box, case, pack, deck, tin and collection box.
- PRICES for every printing Bandai's list does carry: each is paired with its
  own TCGplayer product (pair_listed), so the app reads TCGplayer's daily
  numbers for it, not optcgapi's, which lag by months on some printings and
  file reprint rows under the base id (docs/research/2026-10-25-price-
  mapping-audit.md).

A Bandai promo printing that TCGplayer also sells keeps its Bandai id (what
collections and deck lists already store) and moves to its pack's set; the
pair is found by card number and then by comparing the two pictures. A
TCGplayer printing Bandai does not have gets a new id, "<number>_t<product
id>". Both are remembered in tcg_map.json so an id, once given, never moves.
"""
import concurrent.futures
import io
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / ".cache"
# KOP_CATALOG_OUT (see build_card_catalog.py): the nightly job's folder.
CATALOG_OUT = pathlib.Path(os.environ["KOP_CATALOG_OUT"]) if os.environ.get("KOP_CATALOG_OUT") else None
MAP = (CATALOG_OUT or HERE) / "tcg_map.json"
# What the app refreshes prices for each day: {group id: {product id: catalog id}}.
PRICE_MAP = HERE.parent / "ios/KingofOP/Resources/tcg_products.json"
PRICE_MAP_ANDROID = HERE.parent / "android/app/src/main/assets/tcg_products.json"
TCGCSV = "https://tcgcsv.com/tcgplayer/68"
TCG_IMG = "https://tcgplayer-cdn.tcgplayer.com/product/{}_400w.jpg"
UA = "KingofOP-catalog-builder"


def get_json(url, name, fetch):
    return json.loads(fetch(url, name, ua=UA))


def ext(p, key):
    return next((e["value"] for e in p.get("extendedData", []) if e["name"] == key), None)


# --- Groups ---------------------------------------------------------------

def group_code(abbr):
    """TCGplayer's abbreviation -> the set code the catalog files it under,
    or None for a group Bandai's list already covers card by card.
    'OP17 RE' -> 'OP17-RE', 'ST-01 PRE' -> 'ST01-PRE', 'OP-DD' -> 'DEMO'."""
    abbr = abbr.strip()
    if m := re.fullmatch(r"(OP|EB)(\d+)\s+(RE|PRE|ANN)", abbr):
        return f"{m.group(1)}{m.group(2)}-{m.group(3)}"
    if m := re.fullmatch(r"ST-(\d+)\s+PRE", abbr):
        return f"ST{m.group(1)}-PRE"
    return {"OP-DD": "DEMO", "OP-RP": "REVISION", "LT-01": "LT-01", "SD01": "SD-01",
            "OP-CS": "COLLECTION", "OP-PR": "PROMO"}.get(abbr)


def booster_code(abbr):
    """'OP01' -> 'OP-01', 'EB01' -> 'EB-01'; ST-01, PRB-01, OP14-EB04 as is."""
    if m := re.fullmatch(r"(OP|EB)(\d+)", abbr.strip()):
        return f"{m.group(1)}-{m.group(2)}"
    return abbr.strip()


EVENT_NAMES = {"RE": "Release Event", "PRE": "Pre-Release", "ANN": "Anniversary Tournament"}


def event_set_name(group):
    """'Royal Blood Release Event Cards' -> 'Royal Blood · Release Event'."""
    name = re.sub(r"\s+Cards$", "", group["name"])
    name = re.sub(r"^ST-\d+:\s*", "", name)
    name = re.sub(r":\s*", " · ", name)
    name = re.sub(r"\s+(Release Event|Pre-Release)$", r" · \1", name)
    return name.replace(" (Super Pre-Release Edition)", " · Super Pre-Release")


# --- Promo sources --------------------------------------------------------

SOURCE_WORDS = re.compile(
    r"Pack|Promo|Set\b|Collection|Regional|Championship|\bCS\b|Cup|Event|Tournament|Anniversary|"
    r"Battle|Box|Fest|Party|Kit|Edition|Expo|\bCon\b|Release|Magazine|Campaign|Loader|Deck|\bDay\b|"
    r"Qualifier|Judge|League|Film|Final|Trophy|World|Dodgers|Basketball|Year|Red Bull|Coliseum|"
    r"Playmat|Convention|Retail", re.I)
# Qualifiers that say which copy, not where it came from.
TIERS = {"Winner": "Winner", "Store": "Store", "Finalist": "Finalist"}

FAMILIES = [
    ("Release event promos", r"Release Event|Pre-Release|Anniversary Tournament|New Year Event"),
    ("Judge packs", r"Judge"),
    ("Premium collections", r"Premium Card Collection|Gift Collection|Illustration Box|Devil Fruits|Binder|"
                            r"Anniversary Set|Tin Pack|Special Goods|Sound Loader|Special DON|DON!! Card Pack|"
                            r"Treasure Booster|Playmat|Film Red|FILM RED|Uta Promo"),
    ("Championship sets", r"\bCS\b|Championship|Regional|Finalist|Top Player|Celebration|Trophy|World Final|"
                          r"Pirates League|Large Scale|Grand Battle"),
    ("Tournament & winner packs", r"Tournament Pack|Winner Pack|Store Tournament|Treasure Cup|2-on-2|3-on-3"),
    ("Event packs", r"Event Pack|Beginners Deck Party|Pirates Party|Dash Pack|Welcome Pack|Promotion Pack|"
                    r"Round 1|Sealed Battle|Battle Pack|Deck Battle|Coliseum|Campaign|Learn Together|Heroines"),
]
OTHER_FAMILY = "Other promotions"


def family(source):
    for name, pattern in FAMILIES:
        if re.search(pattern, source):
            return name
    return OTHER_FAMILY


def promo_source(name):
    """'Nami (OP01-016) (Tournament Pack 2026 Vol. 4)' -> 'Tournament Pack 2026 Vol. 4';
    '... (Beginners Deck Party [ST-31] - [ST-36]) (Winner)' gets ' · Winner'."""
    quals = re.findall(r"\(([^()]+)\)", name)
    tier = next((TIERS[q] for q in quals if q in TIERS), None)
    source = next((q for q in reversed(quals) if q not in TIERS and SOURCE_WORDS.search(q)), None)
    if not source:
        return OTHER_FAMILY
    source = source.strip()
    if tier and tier.lower() not in source.lower():
        source = f"{source} · {tier}"
    return source


def slug(text):
    return "P-" + re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


# --- Card data for a printing Bandai does not list ------------------------

CATS = {"Leader": "leader", "Character": "character", "Event": "event", "Stage": "stage", "DON!!": "don"}


def card_from_tcg(p, number, set_code, base):
    """A printing from a TCGplayer product. Rules data comes from Bandai's
    own printing of the same number when there is one."""
    name = re.sub(r"\s*\([^()]*\)", "", p["name"])
    name = re.sub(r"\s+-\s+[A-Z]+\d*-\d+.*$", "", name).strip()
    name = re.sub(r"\s*\[[^\]]*\]$", "", name).strip()
    if number == "DON":
        name = p["name"]          # "DON!! Card (Reiju) (Gold)": the qualifiers are the card
    if base:
        card = {k: v for k, v in base.items() if k not in ("id", "set", "img", "usd", "var", "rar")}
    else:
        card = {"num": number, "name": name, "cat": CATS.get(ext(p, "CardType") or "", "other")}
        for key, field in (("Cost", "cost"), ("Power", "pow"), ("Counterplus", "ctr"), ("Life", "life")):
            v = ext(p, key)
            if v and re.fullmatch(r"\d+", v):
                card[field] = int(v)
        if card["cat"] == "leader" and "cost" in card:
            card["life"] = card.pop("cost")
        if col := ext(p, "Color"):
            card["col"] = [c.strip() for c in re.split(r"[;/]", col) if c.strip()]
        if tr := ext(p, "Subtypes"):
            card["tr"] = [t.strip() for t in tr.split(";") if t.strip()]
        if attr := ext(p, "Attribute"):
            card["attr"] = [a.strip() for a in re.split(r"[;/]", attr) if a.strip()]
        if text := ext(p, "Description"):
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text).replace("\r", "")
            card["text"] = re.sub(r"\n\s*\n", "\n", text).strip()
    card["id"] = None
    card["rar"] = ext(p, "Rarity") or "PR"
    card["set"] = set_code
    card["img"] = TCG_IMG.format(p["productId"])
    return card


# --- Matching Bandai promo printings to TCGplayer products ---------------

def dhash(data, size=16):
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    img = img.crop((int(w * .06), int(h * .1), int(w * .94), int(h * .55)))  # the art, not the frame
    img = img.resize((size + 1, size))
    px = list(img.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = bits << 1 | (px[y * (size + 1) + x] > px[y * (size + 1) + x + 1])
    return bits


def image_bytes(url, name):
    folder = CACHE / "img"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    if f.exists() and f.stat().st_size:
        return f.read_bytes()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        f.write_bytes(data)
        return data
    except Exception:
        return None


# Picture hashes by cache name, kept so a rerun (or the nightly job, which
# keeps this file and not the gigabyte of pictures) fetches only new ones.
HASHES = (CATALOG_OUT or CACHE) / "img_hashes.json"
_known_hashes = None


def hashes(jobs):
    """{key: hash} for [(key, url, cache name)], fetched in parallel."""
    global _known_hashes
    if _known_hashes is None:
        _known_hashes = json.loads(HASHES.read_text()) if HASHES.exists() else {}
    out = {}

    def one(job):
        key, url, name = job
        if name in _known_hashes:
            return key, name, int(_known_hashes[name], 16)
        data = image_bytes(url, name)
        try:
            return key, name, dhash(data) if data else None
        except Exception:
            return key, name, None

    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        for key, name, h in pool.map(one, jobs):
            if h is not None:
                out[key] = h
                _known_hashes[name] = format(h, "x")
    HASHES.parent.mkdir(parents=True, exist_ok=True)
    HASHES.write_text(json.dumps(_known_hashes, sort_keys=True, separators=(",", ":")))
    return out


def match_promos(bandai, products):
    """Pair Bandai promo printings with TCGplayer products of the same card
    number by how alike their pictures are. {product id: bandai id}."""
    by_num = {}
    for c in bandai:
        by_num.setdefault(c["num"], [[], []])[0].append(c)
    for p, number in products:
        by_num.setdefault(number, [[], []])[1].append(p)
    pairs = {k: v for k, v in by_num.items() if v[0] and v[1]}
    jobs = []
    for cs, ps in pairs.values():
        jobs += [(c["id"], c.get("img") or f"https://en.onepiece-cardgame.com/images/cardlist/card/{c['id']}.png",
                  f"b_{c['id']}.png") for c in cs]
        jobs += [(p["productId"], p["imageUrl"], f"t_{p['productId']}.jpg") for p in ps]
    print(f"  comparing {len(jobs)} pictures for {len(pairs)} card numbers...")
    h = hashes(jobs)
    matched = {}
    for cs, ps in pairs.values():
        scored = sorted((bin(h[c["id"]] ^ h[p["productId"]]).count("1"), c["id"], p["productId"])
                        for c in cs for p in ps if c["id"] in h and p["productId"] in h)
        used_c, used_p = set(), set()
        for dist, cid, pid in scored:
            if dist > 70 or cid in used_c or pid in used_p:   # of 256 bits
                continue
            matched[pid] = cid
            used_c.add(cid)
            used_p.add(pid)
    return matched


# --- Pairing Bandai's own printings with TCGplayer products ---------------

def norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("\u2019", "'"))


def pair_listed(cards, groups, products, tracked, fetch):
    """{product id: printing id} for the printings Bandai's list carries and
    no promo or event set already paired. optcgapi names every printing id
    with TCGplayer's own product name and group ("Lucky.Roux (Alternate
    Art)", "Premium Booster -The Best- Vol. 2"), so the pair is read off that:
    the same number and name in the same group, else in the group the
    printing is filed under, else the one product with that number and name.
    A printing optcgapi has no name for is paired only when it is the last
    unpaired one of its number in its set's group and one product is left."""
    rows = []
    for kind in ("allSetCards", "allSTCards"):
        try:
            rows += json.loads(fetch(f"https://optcgapi.com/api/{kind}/", f"optcg_{kind}.json",
                                     ua="KingofOP-companion-dev"))
        except Exception as e:
            print(f"  WARN optcgapi {kind}: {e}", file=sys.stderr)
    group_of = {g["groupId"]: g for g in groups}
    by_key, by_set = {}, {}
    for gid, ps in products.items():
        code = booster_code(group_of[gid]["abbreviation"]) if not group_code(group_of[gid]["abbreviation"]) else None
        for p in ps:
            if (number := ext(p, "Number")):
                by_key.setdefault((number, norm(p["name"])), []).append(p)
                if code:
                    by_set.setdefault((code, number), []).append(p)
    names = {}
    for r in rows:
        if (pid := r.get("card_image_id")) and r.get("card_name"):
            names.setdefault(pid, []).append(r)

    pairs, used = {}, {int(p) for g in tracked.values() for p in g}
    for cid, c in sorted(cards.items()):
        if cid in tracked_ids(tracked) or c["cat"] == "don" or "_t" in cid:
            continue
        found = {}
        for r in names.get(cid, []):
            for p in by_key.get((r["card_set_id"], norm(r["card_name"])), []):
                if p["productId"] not in used:
                    found[p["productId"]] = p
        # optcgapi sometimes names one id twice ("Gum-Gum Giant (Manga)" in
        # PRB-02 and "(Reprint)" in ST-26 for OP09-078_p1): then only a
        # product in the set Bandai files the printing under will do.
        if len({norm(r["card_name"]) for r in names.get(cid, [])}) > 1:
            found = {k: p for k, p in found.items()
                     if booster_code(group_of[p["groupId"]]["abbreviation"]) == c["set"]}
        if len(found) > 1:
            named = {norm(r.get("set_name") or "") for r in names.get(cid, [])}
            for test in (lambda p: norm(group_of[p["groupId"]]["name"]) in named,
                         lambda p: booster_code(group_of[p["groupId"]]["abbreviation"]) == c["set"]):
                narrowed = {k: p for k, p in found.items() if test(p)}
                if narrowed:
                    found = narrowed
        if len(found) == 1:
            p = next(iter(found.values()))
            pairs[p["productId"]] = cid
            used.add(p["productId"])
    # Printings with no optcgapi name: the last one of a number in its set.
    paired = set(pairs.values()) | tracked_ids(tracked)
    for (code, number), ps in by_set.items():
        left = [p for p in ps if p["productId"] not in used]
        mine = [c for c in cards.values() if c["set"] == code and c["num"] == number
                and c["id"] not in paired and c["cat"] != "don" and "_t" not in c["id"]]
        if len(left) == 1 and len(mine) == 1:
            pairs[left[0]["productId"]] = mine[0]["id"]
            used.add(left[0]["productId"])
    # What is still unpaired: by picture, inside its own set's group (a
    # starter deck's reprints, PRB foils, a new set's parallels before
    # optcgapi names them).
    paired = set(pairs.values()) | tracked_ids(tracked)
    pool = [c for c in cards.values() if c["id"] not in paired and c["cat"] != "don" and "_t" not in c["id"]
            and (c["set"], c["num"]) in by_set]
    fresh = [(p, n) for (code, n), ps in by_set.items() for p in ps
             if p["productId"] not in used and any(c["set"] == code and c["num"] == n for c in pool)]
    for c in pool:      # Bandai's own picture: the printing as Bandai numbers it
        c.setdefault("_bandai", f"https://en.onepiece-cardgame.com/images/cardlist/card/{c['id']}.png")
    by_picture = match_promos([dict(c, img=c["_bandai"]) for c in pool], fresh)
    for c in pool:
        c.pop("_bandai", None)
    for pid, cid in by_picture.items():
        p = next(p for p, _ in fresh if p["productId"] == pid)
        if booster_code(group_of[p["groupId"]]["abbreviation"]) == cards[cid]["set"]:
            pairs[pid] = cid
            used.add(pid)
    return pairs


def tracked_ids(tracked):
    return {cid for g in tracked.values() for cid in g.values()}


# --- Pictures TCGplayer has not taken yet -------------------------------

# TCGplayer lists a new set's parallels before it photographs them (EB-05,
# Oct 2026: half its parallels 403), and Bandai's list never carries a
# parallel it has not numbered. Limitless shows every print of a number on
# its card page ("?v=1  aa", "?v=2  manga"), so a booster printing with no
# TCGplayer photo takes Limitless's picture: matched by its variant label,
# else as the one print of its number left over. Checked again each night,
# so TCGplayer's own photo takes over once it is up.
LIMITLESS = "https://onepiece.limitlesstcg.com/cards/"
LIMITLESS_LABEL = {"Alt art": "aa", "Manga": "manga", "SP": "sp"}
PICTURES = (CATALOG_OUT or CACHE) / "missing_pictures.json"


def _get(url, head=False):
    req = urllib.request.Request(url, method="HEAD" if head else "GET", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (b"" if head else r.read())
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return None, b""


def limitless_prints(number):
    """[(label, picture url)] for a number's prints in the set of its base
    print, from Limitless's card page and each print's own page."""
    status, page = _get(LIMITLESS + number)
    if status != 200:
        return None
    rows = re.findall(r'<tr[^>]*>\s*<td>\s*<a\s*(?:href="/cards/[^"?]+\?v=(\d+)")?\s*>\s*([^<]*?)\s*'
                      r'<span class="prints-table-card-number">([^<]*)</span>', page.decode("utf-8", "replace"))
    home = next((name for v, name, _ in rows if not v), None)
    out = []
    for v, name, label in rows:
        if v and name == home:
            status, sub = _get(f"{LIMITLESS}{number}?v={v}")
            m = re.search(rf'https://limitlesstcg\.[a-z0-9.]+/one-piece/[A-Z0-9]+/{re.escape(number)}_p{v}_EN\.webp',
                          sub.decode("utf-8", "replace")) if status == 200 else None
            if m:
                out.append((label.strip().lower(), m.group(0)))
    return out


def fill_missing_pictures(cards):
    """Limitless pictures for the TCGplayer-only booster printings whose
    TCGplayer photo is not up yet."""
    state = json.loads(PICTURES.read_text()) if PICTURES.exists() else {"ok": []}
    ok = set(state["ok"])                    # product ids whose photo is up
    home = {c["num"]: c["set"] for c in cards.values() if "_" not in c["id"]}
    mine = [c for c in cards.values() if "_t" in c["id"] and home.get(c["num"]) == c["set"]]
    check = [c for c in mine if c["id"].split("_t")[1] not in ok]

    def up(c):
        status, _ = _get(c["img"], head=True)
        return c, status == 200

    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        missing = []
        for c, good in pool.map(up, check):
            if good:
                ok.add(c["id"].split("_t")[1])
            else:
                missing.append(c)
    filled = 0
    for number in sorted({c["num"] for c in missing}):
        prints = limitless_prints(number)
        if not prints:
            continue
        # Every TCGplayer-only printing of the number takes a print, photo or not,
        # so the ones with a photo don't leave theirs for another.
        siblings = sorted((c for c in mine if c["num"] == number), key=lambda c: c["id"])
        # A print Bandai's list already carries (OP01-016_p1) is that printing's, not ours.
        left = [p for p in prints if not re.search(r"/([A-Z0-9]+-\d+_p\d+)_EN\.webp$", p[1])
                or re.search(r"/([A-Z0-9]+-\d+_p\d+)_EN\.webp$", p[1]).group(1) not in cards]
        pairs = {}
        for c in siblings:
            want = [LIMITLESS_LABEL.get(v) for v in c.get("var", [])]
            hit = next((p for p in left if p[0] in want), None)
            if hit:
                pairs[c["id"]] = hit
                left.remove(hit)
        rest = [c for c in siblings if c["id"] not in pairs]
        if len(rest) == 1 and len(left) == 1:
            pairs[rest[0]["id"]] = left[0]
        for c in missing:
            if c["num"] == number and c["id"] in pairs:
                c["img"] = pairs[c["id"]][1]
                filled += 1
    PICTURES.parent.mkdir(parents=True, exist_ok=True)
    PICTURES.write_text(json.dumps({"ok": sorted(ok)}, indent=0))
    print(f"  {len(missing)} booster printings with no TCGplayer photo yet, {filled} given Limitless's")


# --- Build ----------------------------------------------------------------

def build(cards, sets, fetch):
    """Adds promo and event sets, their printings and every sealed product
    to the catalog in place. Returns the sealed list."""
    known = json.loads(MAP.read_text()) if MAP.exists() else {}
    groups = get_json(f"{TCGCSV}/groups", "tcg_groups.json", fetch)["results"]
    have = {s["code"] for s in sets}
    bases = {}
    for c in cards.values():
        if "_" not in c["id"]:
            bases.setdefault(c["num"], c)
        else:
            bases.setdefault(c["num"] + "~", c)

    def base_for(number):
        return bases.get(number) or bases.get(number + "~")

    products, prices = {}, {}
    sold = set()        # products with a market price (sales), not just listings
    for g in groups:
        gid = g["groupId"]
        try:
            products[gid] = get_json(f"{TCGCSV}/{gid}/products", f"tcg_{gid}.json", fetch)["results"]
            for row in get_json(f"{TCGCSV}/{gid}/prices", f"tcg_{gid}_prices.json", fetch)["results"]:
                price = row.get("marketPrice") or row.get("midPrice")
                if price and (row["productId"] not in prices or row.get("subTypeName") == "Normal"):
                    prices[row["productId"]] = round(float(price), 2)
                if row.get("marketPrice"):
                    sold.add(row["productId"])
        except Exception as e:
            print(f"  WARN tcgcsv group {gid} {g['abbreviation']}: {e}", file=sys.stderr)
            products[gid] = []

    order = max((s["order"] for s in sets), default=0) + 1
    new_sets = {}
    price_map = {}

    def track(p, cid):
        price_map.setdefault(str(p["groupId"]), {})[str(p["productId"])] = cid

    def add_set(code, name, kind, released=None, fam=None, first=None):
        if code in have or code in new_sets:
            return
        s = {"code": code, "name": name, "kind": kind, "order": 0}
        if released:
            s["released"] = released
        if fam:
            s["family"] = fam
        s["_first"] = first or 0
        new_sets[code] = s

    def put(p, number, set_code):
        """File a product's printing: a Bandai id it was matched to before
        or now, else a TCGplayer id of its own."""
        pid = str(p["productId"])
        cid = known.get(pid)
        if cid and cid in cards and "_t" not in cid:
            c = cards[cid]
            c["set"] = set_code
        else:
            cid = cid or f"{number}_t{pid}"
            c = card_from_tcg(p, number, set_code, base_for(number))
            c["id"] = cid
            cards[cid] = c
        known[pid] = cid
        track(p, cid)
        if p["productId"] in prices:
            c["usd"] = prices[p["productId"]]
        quals = re.findall(r"\(([^()]+)\)", p["name"])
        tags = [t for t in ("Alternate Art", "Textured", "Serial Numbered", "Jumbo", "Oversized", "Manga", "SP",
                            "Full Art", "Winner", "Judge", "Oda Stamped Signature") if t in quals]
        if tags:
            c["var"] = ["Alt art" if t == "Alternate Art" else t for t in tags]

    # Promotion cards: one set per pack.
    promo_group = next((g for g in groups if g["abbreviation"].strip() == "OP-PR"), None)
    promo_cards = []
    if promo_group:
        for p in products[promo_group["groupId"]]:
            number = ext(p, "Number")
            if not number and ext(p, "CardType") == "DON!!":
                number = "DON"
            if number:
                promo_cards.append((p, number))
    # Bandai's promo printings with no pack yet are the ones to pair.
    taken = set(known.values())
    pool = [c for c in cards.values() if c["set"] in ("PROMO", "OTHER") and c["id"] not in taken]
    fresh = [(p, n) for p, n in promo_cards if str(p["productId"]) not in known]
    for pid, cid in match_promos(pool, fresh).items():
        known[str(pid)] = cid
    print(f"  {sum(1 for v in known.values() if '_t' not in v)} Bandai promo printings paired with their pack")
    for p, number in promo_cards:
        source = promo_source(p["name"])
        code = slug(source)
        add_set(code, source, "promo", fam=family(source), first=p["productId"])
        new_sets[code]["_first"] = min(new_sets[code]["_first"], p["productId"])
        put(p, number, code)

    # Event sets and the decks Bandai does not list.
    for g in groups:
        code = group_code(g["abbreviation"])
        if not code or code in ("PROMO", "COLLECTION"):
            continue
        rows = [(p, ext(p, "Number")) for p in products[g["groupId"]]]
        rows = [(p, n) for p, n in rows if n]
        if not rows:
            continue
        kind = "event" if re.search(r"-(RE|PRE|ANN)$", code) else "other"
        name = event_set_name(g) if kind == "event" else g["name"]
        add_set(code, name, kind, released=g["publishedOn"][:10],
                fam="Release events" if kind == "event" else None, first=g["groupId"])
        for p, number in rows:
            put(p, number, code)

    # Printings a booster's TCGplayer page has and Bandai's list does not
    # (an extra booster's SPs while Bandai's page is down, Dash Pack copies
    # TCGplayer files under the booster). Only numbers where TCGplayer has
    # more printings than the list are looked at; the pictures say which of
    # them the list already has. A copy named for a promo pack goes to it.
    promo_names = {s["name"]: c for c, s in new_sets.items() if s["kind"] == "promo"}

    def promo_home(product, booster):
        """'(Dash Pack)' on an OP-14 card: the Dash Pack that came with OP-14,
        not another year's."""
        source = promo_source(product["name"])
        if source == OTHER_FAMILY:
            return None
        if source in promo_names:
            return promo_names[source]
        source = f"{source} · {booster}"
        if source in promo_names:
            return promo_names[source]
        code = slug(source)
        add_set(code, source, "promo", fam=family(source), first=product["productId"])
        promo_names[source] = code
        return code

    for g in groups:
        code = booster_code(g["abbreviation"])
        if group_code(g["abbreviation"]) or code not in have:
            continue
        by_num = {}
        for p in products[g["groupId"]]:
            if (number := ext(p, "Number")):
                by_num.setdefault(number, []).append(p)
        listed = {}
        for c in cards.values():
            if c["set"] == code:
                listed.setdefault(c["num"], []).append(c)
        gaps = [(n, ps) for n, ps in by_num.items() if len(ps) > len(listed.get(n, []))]
        if not gaps:
            continue
        taken = set(known.values())
        pool = [c for n, _ in gaps for c in listed.get(n, []) if c["id"] not in taken]
        fresh = [(p, n) for n, ps in gaps for p in ps if str(p["productId"]) not in known]
        # The base print pairs by name, not picture: the one product with no
        # variant in its name ("Shirahoshi", not "Shirahoshi (Manga)").
        for n, _ in gaps:
            base = next((c for c in pool if c["num"] == n and "_" not in c["id"]), None)
            plain = [p for p, pn in fresh if pn == n and not re.search(r"\((?![A-Z]*\d*-?\d{3}\))[^()]*\)", p["name"])]
            if base and len(plain) == 1:
                known[str(plain[0]["productId"])] = base["id"]
                pool.remove(base)
                fresh = [(p, pn) for p, pn in fresh if p is not plain[0]]
        for pid, cid in match_promos(pool, fresh).items():
            known[str(pid)] = cid
        added = 0
        for n, ps in gaps:
            for p in ps:
                cid = known.get(str(p["productId"]))
                if cid and "_t" not in cid:
                    continue            # Bandai's own printing of it
                put(p, n, promo_home(p, next(s["name"] for s in sets if s["code"] == code)) or code)
                added += 1
        print(f"  {code:<10} {added:3d} printings Bandai's list does not have")

    # The DON!! cards boosters carry (character, gold, box-topper DON!!):
    # TCGplayer gives them no number and Bandai's list leaves them out.
    for g in groups:
        code = booster_code(g["abbreviation"])
        if group_code(g["abbreviation"]) or code not in have:
            continue
        for p in products[g["groupId"]]:
            if not ext(p, "Number") and ext(p, "CardType") == "DON!!":
                put(p, "DON", code)

    # Sealed product, filed under the set it opens into.
    group_set = {}
    for g in groups:
        abbr = g["abbreviation"].strip()
        code = group_code(abbr) or booster_code(abbr)
        group_set[g["groupId"]] = code
    def key(name):
        name = re.sub(r"(?i)one piece card game:?|one piece|\(sealed promotional bundle\)", "", name)
        name = re.sub(r"(?i)\s+(display case|display|case|box|\[set of \d+\])$", "", name.strip())
        return re.sub(r"[^a-z0-9]", "", name.lower())

    sources = {key(s["name"]): c for c, s in new_sets.items() if s["kind"] == "promo"}
    sealed, seen = [], set()
    for g in groups:
        for p in products[g["groupId"]]:
            if ext(p, "Number") or ext(p, "CardType") or p["productId"] in seen:
                continue
            seen.add(p["productId"])
            code = group_set[g["groupId"]]
            if code == "PROMO":
                code = sources.get(key(p["name"]), "PROMO")
            track(p, f"S{p['productId']}")
            item = {"id": f"S{p['productId']}", "name": p["name"], "set": code,
                    "img": TCG_IMG.format(p["productId"])}
            if p["productId"] in prices:
                item["usd"] = prices[p["productId"]]
            if g["publishedOn"]:
                item["released"] = (p.get("presaleInfo") or {}).get("releasedOn", None) or g["publishedOn"]
                item["released"] = item["released"][:10]
            sealed.append(item)

    # Bandai's own printings: TCGplayer's daily price, from their product.
    # A product nobody has bought yet has only its listings' midpoint
    # (OP05-119 Manga: $30,000 asked); that fills a gap, never replaces
    # optcgapi's last sale.
    listed = pair_listed(cards, groups, products, price_map, fetch)
    gid_of = {p["productId"]: gid for gid, ps in products.items() for p in ps}
    for pid, cid in listed.items():
        price_map.setdefault(str(gid_of[pid]), {})[str(pid)] = cid
        if pid in prices and (pid in sold or not cards[cid].get("usd")):
            cards[cid]["usd"] = prices[pid]
    print(f"  {len(listed)} of Bandai's printings paired with their TCGplayer product")
    fill_missing_pictures(cards)

    # Newest pack last in `order`, as the shelf expects (it sorts -order).
    for s in sorted(new_sets.values(), key=lambda s: (s.get("released") or "", s.pop("_first"))):
        s["order"] = order
        order += 1
        sets.append(s)
    for s in sets:
        if s["code"] == "PROMO":
            s.update(name="More promotion cards", family=OTHER_FAMILY)
    MAP.write_text(json.dumps(dict(sorted(known.items())), indent=0))
    for out in ([CATALOG_OUT / "tcg_products.json"] if CATALOG_OUT else [PRICE_MAP, PRICE_MAP_ANDROID]):
        out.write_text(json.dumps({"groups": price_map}, separators=(",", ":"), sort_keys=True))
    print(f"  {len(new_sets)} promo and event sets, {len(sealed)} sealed products")
    return sealed
