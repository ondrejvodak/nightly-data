#!/usr/bin/env python3
"""Check a built catalog's maps against Limitless TCG, and fail when they
drift (the nightly job runs it before publishing).

Limitless (limitless.py) pairs every English print's TCGplayer product with
its Cardmarket product by hand. Checked here:

- TCGPLAYER: every TCGplayer product Limitless lists for a number is one of
  our printings (tcg_products.json), of that number.
- CARDMARKET: the printing holding that TCGplayer product holds the
  Cardmarket product Limitless pairs it with (cardmarket_map.json), or one
  at the same trend price (Cardmarket's twin listings).

build_cardmarket_map.py takes Limitless's pairs, so Cardmarket disagreeing
means a resolver or builder regression; TCGplayer disagreeing means our
catalog builder paired a product wrong. Japanese printings have no outside
reference (Limitless's Japanese pages link the English products).

    python3 scripts/audit_card_map.py [catalog dir] [--sets]

The catalog dir holds cards.json, tcg_products.json, cardmarket_map.json and
limitless.json (default: the nightly checkout, ../nightly-data/catalog, with
scripts/.cache/limitless.json). --sets also reads Limitless's set pages to
find numbers we lack. Writes the detail to .cache/card_map_audit.json next
to this script. Exit 1 when over the limits below.
"""
import collections, json, os, sys, time, urllib.request
import limitless

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CM = "https://downloads.s3.cardmarket.com/productCatalog/"
# What a publish may carry: TCGplayer products Limitless lists that the
# catalog lacks (a product TCGplayer posted after tcgcsv's nightly copy), and
# Cardmarket pairs that differ (each one is a printing priced in the wrong
# euros).
LIMITS = {"tcgplayer product not in our catalog": 10, "tcgplayer product on another number": 0,
          "different cardmarket product": 10}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KingofOP catalog audit"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    catalog = args[0] if args else os.path.join(os.path.dirname(ROOT), "nightly-data/catalog")
    state_path = os.path.join(catalog, "limitless.json")
    if not os.path.exists(state_path):
        state_path = os.path.join(ROOT, "scripts/.cache/limitless.json")
    state = limitless.load(state_path)
    cat = json.load(open(os.path.join(catalog, "cards.json")))
    cards = {c["id"]: c for c in cat["cards"]}
    by_pid = {int(pid): cid for g in json.load(open(os.path.join(catalog, "tcg_products.json")))["groups"].values()
              for pid, cid in g.items()}
    cm_map = {int(k): v for k, v in json.load(open(os.path.join(catalog, "cardmarket_map.json")))["map"].items()}
    cm_of = collections.defaultdict(set)
    for k, v in cm_map.items():
        cm_of[v].add(k)
    guide = {g["idProduct"]: g for g in get(CM + "priceGuide/price_guide_18.json")["priceGuides"]}

    def trend(pid):
        return (guide.get(pid) or {}).get("trend")

    counts, issues, seen = collections.Counter(), collections.defaultdict(list), set()
    for num, page in sorted(state.items()):
        for r in page["rows"]:
            counts["rows"] += 1
            if not r.get("tcg"):
                counts["rows without TCGplayer link"] += 1
                continue
            cid = by_pid.get(r["tcg"])
            item = {"num": num, "set": r["set"], "label": r["label"], "tcg": r["tcg"], "usd": r["usd"],
                    "cm_link": r["cm_card"] and f"{r['cm_exp']}/{r['cm_card']}", "eur": r["eur"],
                    "cm": r.get("cm"), "ours": cid}
            if not cid:
                issues["tcgplayer product not in our catalog"].append(item)
                continue
            if cards[cid]["num"] != num:
                issues["tcgplayer product on another number"].append(dict(item, our_num=cards[cid]["num"]))
                continue
            seen.add(cid)
            counts["tcgplayer agrees"] += 1
            mine = cm_of.get(cid, set())
            item["our_cm"] = sorted(mine)
            if not r.get("cm"):
                counts[f"cardmarket link unresolved: {r.get('why')}"] += 1
            elif r["cm"] in mine:
                counts["cardmarket agrees"] += 1
            elif any(trend(p) is not None and trend(p) == trend(r["cm"]) for p in mine):
                counts["cardmarket agrees (twin listing, same price)"] += 1
            elif mine:
                issues["different cardmarket product"].append(dict(item, cm_owner=cm_map.get(r["cm"])))
            elif cm_map.get(r["cm"]):
                issues["cardmarket product with a sibling"].append(dict(item, cm_owner=cm_map[r["cm"]]))
            else:
                issues["no euro price, Limitless has one"].append(item)

    for cid, c in cards.items():
        if state.get(c["num"], {}).get("rows") and cid not in seen:
            counts["our printings Limitless does not list"] += 1
    if "--sets" in sys.argv:
        ours = {c["num"] for c in cards.values()}
        for num in sorted(limitless.set_numbers() - ours):
            issues["number on Limitless we do not have"].append({"num": num})

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "card_map_audit.json")
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "catalog": cat.get("built"), "counts": dict(counts),
               "issues": issues}, open(out, "w"), indent=1, ensure_ascii=False)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    for k, v in sorted(issues.items()):
        print(f"  {k}: {len(v)}")
    over = [f"{k} {len(issues[k])} > {n}" for k, n in LIMITS.items() if len(issues[k]) > n]
    if over:
        for k in LIMITS:
            for x in issues[k][:10]:
                print(f"    {k}: {x}")
        print("AUDIT FAILED: " + "; ".join(over))
        sys.exit(1)
    print(f"audit passed -> {out}")


if __name__ == "__main__":
    main()
