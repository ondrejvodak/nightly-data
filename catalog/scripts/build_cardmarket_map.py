#!/usr/bin/env python3
"""Build ios/KingofOP/Resources/cardmarket_map.json: Cardmarket idProduct
(One Piece, idGame 18) -> our printing id, so the app can read Cardmarket's
public daily price guide (price_guide_18.json) in euros. English printings
and Japanese ones ("OP05-119.ja") each get the product of their own language.

Cardmarket's product list names every single "Name (OP01-016)": the number
is in the name, the version and the language are not. What does say which
is which:

- LANGUAGE. Cardmarket files Japanese and Asia-legal printings under
  expansions of their own, often listed months before the English one. An
  expansion is English when none of its sealed products is "(Non-English)",
  "(Japanese)", "(Asia Region Legal)", Chinese or Korean
  (products_nonsingles_18.json), and Japanese when one is "(Non-English)",
  "(Japanese)" or "(Asia Region Legal)".
- SET. Each of our sets has its own Cardmarket expansion in each language:
  the one holding the most of the set's card numbers (OP-05 -> Awakening of
  the New Era, PRB-01 -> The Best, ST-17 -> Starter Deck: Donquixote
  Doflamingo, OP14-RE -> its release event pack). A set whose best expansion
  holds under 55% of its numbers has none, unless the expansion's own cards
  are mostly that set's (EB-05 while Cardmarket is still listing it). A
  printing is looked for only in its own set's expansion: a PRB-01 alt art
  of an OP05 card never takes the OP05 alt art's product.
- VERSION. Inside one expansion, one number's products are told apart by
  price, checked against the TCGplayer price we already have (Cardmarket's
  product order does not follow our _p1/_p2 numbering):
  - the base print is the cheapest product, kept only within 10x of its
    TCGplayer price in the set's own expansion (European bulk is often a
    quarter of American), 4x in a fallback one;
  - every other printing (parallel, reprint, stamped, TCGplayer-only) takes
    the product nearest its TCGplayer price, most valuable first, kept only
    within 2x;
  - a printing with no TCGplayer price is left out, unless it is the only
    printing and the only product of its number in its set's own expansion
    (a starter deck's reprint TCGplayer does not sell apart).
- JAPANESE versions: the set's Japanese expansion lists as many products
  of a number as Bandai's Japanese list has printings of it in the set ->
  they pair rank for rank by price, the printings ranked by their English
  twin's worth (the versions keep their order of worth across languages:
  base, alt art, manga). Otherwise only the base (the cheapest) is paired,
  or a lone product with a lone printing.

Promos ("P-..." and promo sets) are left out: Cardmarket spreads one promo
number over expansions that mix languages and event versions under one name,
with nothing to tell them apart but price (24 Oct 2026: P-085's
participation pack was paired with a 2-cent product, P-110's base with the
Winner print). Anything that does not pass stays out of the map: the app
then shows the TCGplayer price converted, never a wrong euro price.

Run after every set release, after build_card_catalog.py and
build_jp_catalog.py.
"""
import json, os, re, sys, urllib.request, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# KOP_CATALOG_OUT (see build_card_catalog.py): the nightly job's folder.
CATALOG_OUT = os.environ.get("KOP_CATALOG_OUT")
RES = CATALOG_OUT or os.path.join(ROOT, "ios/KingofOP/Resources")
CARDS = os.path.join(RES, "cards.json")
CARDS_JA = os.path.join(RES, "cards_ja.json")
OUTS = [os.path.join(RES, "cardmarket_map.json")] + (
    [] if CATALOG_OUT else [os.path.join(ROOT, "android/app/src/main/assets/cardmarket_map.json")])
SEALED = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_18.json"
PRODUCTS = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_18.json"
GUIDE = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_18.json"
NUMBER = re.compile(r"\(([A-Z]{1,4}\d{0,2}-\d{3})\)")
NOT_ENGLISH = re.compile(r"Non-English|Japanese|Asia Region|Chinese|Korean", re.I)
# Cardmarket's Japanese-language boxes: "(Non-English)", later "(Asia
# Region Legal)". Chinese and Korean printings are not ours.
JAPANESE = re.compile(r"Non-English|Japanese|Asia Region", re.I)
PROMO_SETS = ("PROMO", "OTHER", "PROMO-JA")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KingofOP catalog build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def trend(g):
    return g.get("trend") or g.get("avg7") or g.get("avg")


def ratio(a, b):
    return max(a, b) / min(a, b)


def prefix(num):
    return num.split("-")[0]


def is_promo(c):
    return c["num"].startswith("P-") or c["set"].startswith("P-") or c["set"] in PROMO_SETS or c["cat"] == "don"


def expansion_languages():
    """(English, Japanese) expansion ids, read off their sealed products'
    names. One without sealed products (promo-only lists) is neither."""
    sealed = collections.defaultdict(list)
    for p in get(SEALED)["products"]:
        sealed[p["idExpansion"]].append(p["name"])
    english = {e for e, names in sealed.items() if not any(NOT_ENGLISH.search(n) for n in names)}
    japanese = {e for e, names in sealed.items() if any(JAPANESE.search(n) for n in names)}
    return english, japanese


def set_expansions(cards, singles, pool):
    """{set code: [expansion ids]}: the expansions in `pool` holding a good
    share of the set's card numbers, best first. An expansion whose own
    cards are mostly the set's ("ST18-" in Starter Deck: Purple Monkey.D.Luffy)
    ranks first, then the one holding the most of the set's numbers. A card
    number is looked for in the first of them that lists it: a starter deck's
    own cards in its deck, its reprints where Cardmarket filed them."""
    held = collections.defaultdict(set)      # expansion -> numbers
    for (e, num) in singles:
        held[e].add(num)
    numbers = collections.defaultdict(set)   # set -> numbers
    for c in cards:
        if not is_promo(c):
            numbers[c["set"]].add(c["num"])
    out = {}
    for code, nums in numbers.items():
        # "ST-18" -> "ST18", "EB-05" -> "EB05"; "OP14-RE", "OP14-EB04" -> "OP14".
        head, _, tail = code.partition("-")
        mine = head + tail if tail.isdigit() else head

        def own(e):
            return collections.Counter(prefix(n) for n in held[e]).most_common(1)[0][0] == mine

        # Ties go to the expansion that is most nearly just this set: a
        # release event pack over the booster that also lists every number.
        ranked = sorted((e for e in pool if e in held and nums & held[e]
                         and (own(e) or len(nums & held[e]) >= 0.3 * len(nums))),
                        key=lambda e: (own(e), len(nums & held[e]), len(nums & held[e]) / len(held[e])),
                        reverse=True)
        if ranked and (len(nums & held[ranked[0]]) >= 0.55 * len(nums) or own(ranked[0])):
            out[code] = ranked
    return out


def home(ranked, num, listed):
    """The first of a set's expansions that lists the number, priced or not:
    a deck reprint Cardmarket has no price for yet stays unpriced rather
    than borrow the booster's copy."""
    return next((e for e in ranked if (e, num) in listed), None)


def picture_twins(japanese_cards, cards, same_set):
    """{Japanese id: English id} for Japanese printings whose own id has no
    English printing in the same set: the English printing of the same set
    and number whose picture's art is nearest (tcgplayer_extras' hash of the
    art, not the frame, so the language of the text does not count)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tcgplayer_extras as tx
    by_id = {c["id"]: c for c in cards}
    lonely = [c for c in japanese_cards if not is_promo(c) and not c["set"].startswith("JA-")
              and not ((t := by_id.get(c["id"].removesuffix(".ja"))) and same_set(t["set"], c["set"]))]
    # A reprint is told from a foil of the same art by its id, not its
    # picture: only reprints are compared with reprints.
    def kind(cid):
        return "r" if "_r" in cid else "p" if "_" in cid else "b"

    candidates = {c["id"]: [e for e in cards if e["num"] == c["num"] and not is_promo(e)
                            and same_set(e["set"], c["set"]) and kind(e["id"]) == kind(c["id"])]
                  for c in lonely}
    jobs = [(c["id"], f"https://www.onepiece-cardgame.com/images/cardlist/card/{c['id'].removesuffix('.ja')}.png",
             f"j_{c['id']}.png") for c in lonely if candidates[c["id"]]]
    jobs += [(e["id"], f"https://en.onepiece-cardgame.com/images/cardlist/card/{e['id']}.png", f"b_{e['id']}.png")
             for e in {e["id"]: e for es in candidates.values() for e in es}.values()]
    h = tx.hashes(jobs)
    out = {}
    for c in lonely:
        scored = sorted((bin(h[c["id"]] ^ h[e["id"]]).count("1"), e["id"])
                        for e in candidates[c["id"]] if c["id"] in h and e["id"] in h)
        # Clearly nearest: within 40 of 256 bits, and 15 closer than the next.
        if scored and scored[0][0] <= 40 and (len(scored) == 1 or scored[1][0] - scored[0][0] >= 15):
            out[c["id"]] = scored[0][1]
    print(f"  {len(out)} of {len(lonely)} Japanese printings matched to an English one by picture", file=sys.stderr)
    return out


def main():
    cards = [c for c in json.load(open(CARDS))["cards"]]
    japanese_cards = json.load(open(CARDS_JA))["cards"]
    japanese_ids = {c["id"] for c in japanese_cards}

    english, japanese = expansion_languages()
    guide = {g["idProduct"]: g for g in get(GUIDE)["priceGuides"]}
    singles = collections.defaultdict(list)   # (expansion, number) -> products with a price
    listed = collections.defaultdict(list)    # (expansion, number) -> every product
    for p in get(PRODUCTS)["products"]:
        m = NUMBER.search(p["name"])
        if m:
            listed[(p["idExpansion"], m.group(1))].append(p)
            if trend(guide.get(p["idProduct"], {})):
                singles[(p["idExpansion"], m.group(1))].append(p)

    def price(p):
        return trend(guide[p["idProduct"]])

    en_home = set_expansions(cards, listed, english)
    ja_home = set_expansions(japanese_cards, listed, japanese)

    # English: per expansion and number, every printing of the sets it is home to.
    mapping, bases, others, refused = {}, 0, 0, 0
    groups = collections.defaultdict(list)
    for c in cards:
        if not is_promo(c) and c["set"] in en_home:
            e = home(en_home[c["set"]], c["num"], listed)
            if e:
                groups[(e, c["num"])].append(c)
    for key, printings in groups.items():
        prods = singles.get(key, [])
        # Listed, not yet sold (a set out this week): one product, one
        # printing, is paired now so its price shows the day it sells.
        if not prods:
            if len(listed[key]) == 1 and len(printings) == 1:
                mapping[str(listed[key][0]["idProduct"])] = printings[0]["id"]
                others += 1
            continue
        taken = set()
        base = next((c for c in printings if "_" not in c["id"]), None)
        if base:
            cheapest = min(prods, key=price)
            # Within the set's own expansion the cheapest single is the base
            # print; the check only catches a set mapped to the wrong
            # expansion (bulk in Europe is often a quarter of US bulk).
            own = key[0] == en_home[base["set"]][0]
            if not (base.get("usd") and ratio(price(cheapest), base["usd"]) <= (10 if own else 4)):
                refused += 1
            else:
                mapping[str(cheapest["idProduct"])] = base["id"]
                taken.add(cheapest["idProduct"])
                bases += 1
        # A deck's reprint TCGplayer does not sell apart: one printing, one
        # product, in the set's own expansion, is the same card.
        if (not base and len(printings) == 1 and not printings[0].get("usd") and len(prods) == 1
                and key[0] == en_home[printings[0]["set"]][0]):
            mapping[str(prods[0]["idProduct"])] = printings[0]["id"]
            others += 1
            continue
        rest = [c for c in printings if c is not base and c.get("usd")]
        for c in sorted(rest, key=lambda c: -c["usd"]):
            left = [p for p in prods if p["idProduct"] not in taken]
            if not left:
                break
            best = min(left, key=lambda p: ratio(price(p), c["usd"]))
            if ratio(price(best), c["usd"]) <= 2:
                mapping[str(best["idProduct"])] = c["id"]
                taken.add(best["idProduct"])
                others += 1

    # Japanese: the set's Japanese expansion. Its products of a number pair
    # rank for rank with the Japanese printings ranked by their English
    # twin's worth (its Cardmarket euros, else its TCGplayer dollars in
    # euros), when every printing has a twin and the counts agree.
    euros = {cid: trend(guide[int(pid)]) for pid, cid in mapping.items()}
    by_id = {c["id"]: c for c in cards}

    def same_set(en_set, ja_set):
        # "OP14-EB04" (English) holds what Japan sold as OP-14 and EB-04.
        a, b = en_set.replace("-", ""), ja_set.replace("-", "")
        return a == b or b in a

    twins = picture_twins(japanese_cards, cards, same_set)

    def worth(c):
        """The English twin's worth: the same id in the same set, else the
        English printing of the same set and number with the same picture
        (a Japanese _p2 is often another card than the English _p2:
        OP06-063_p2 is a PRB-02 foil in Japan, a $300 alt art in English)."""
        twin = by_id.get(c["id"].removesuffix(".ja"))
        if not twin or not same_set(twin["set"], c["set"]):
            twin = by_id.get(twins.get(c["id"]))
        if not twin:
            return None
        if twin["id"] in euros:
            return euros[twin["id"]]
        return twin["usd"] / 1.1 if twin.get("usd") else None

    jp_count = 0
    jp_groups = collections.defaultdict(list)
    key_set = {}
    for c in japanese_cards:
        # Japan's own premium collections ("JA-550801") have no English set
        # to rank against, and Cardmarket files them in mixed lists.
        if not is_promo(c) and c["set"] in ja_home and not c["set"].startswith("JA-"):
            e = home(ja_home[c["set"]], c["num"], listed)
            if e:
                jp_groups[(e, c["num"])].append(c)
                key_set[(e, c["num"])] = c["set"]
    for key, printings in jp_groups.items():
        jprods = sorted(singles.get(key, []), key=price)
        if not jprods:
            if len(listed[key]) == 1 and len(printings) == 1:
                mapping[str(listed[key][0]["idProduct"])] = printings[0]["id"]
                jp_count += 1
            continue
        paired = {}
        worths = [(worth(c), c["id"]) for c in printings]
        # Cardmarket's Japanese "The Best" does not list a plain reprint
        # apart from the card it reprints: rank the rest.
        if len(printings) > len(jprods):
            worths = [(w, cid) for w, cid in worths if "_r" not in cid]
        if len(worths) == len(jprods) and all(w for w, _ in worths):
            for (_, jid), p in zip(sorted(worths), jprods):
                paired[str(p["idProduct"])] = jid
        elif (en := home(en_home.get(key_set[key], []), key[1], listed)) and len(singles[(en, key[1])]) == len(jprods):
            # As many English products as Japanese ones: each Japanese one
            # takes the printing of the English one of the same rank.
            mine = {c["id"] for c in printings}
            for e, j in zip(sorted(singles[(en, key[1])], key=price), jprods):
                cid = mapping.get(str(e["idProduct"]))
                if cid and f"{cid}.ja" in mine:
                    paired[str(j["idProduct"])] = f"{cid}.ja"
        base = next((c["id"] for c in printings if "_" not in c["id"]), None)
        if base and base not in paired.values() and str(jprods[0]["idProduct"]) not in paired:
            paired[str(jprods[0]["idProduct"])] = base
        if not paired and len(jprods) == 1 and len(printings) == 1:
            paired[str(jprods[0]["idProduct"])] = printings[0]["id"]
        mapping.update(paired)
        jp_count += len(paired)

    for out in OUTS:
        json.dump({"source": PRODUCTS, "map": dict(sorted(mapping.items()))}, open(out, "w"),
                  separators=(",", ":"))
    print(f"cardmarket_map: English {bases} base prints, {others} other printings, "
          f"{refused} bases refused (no TCGplayer price, or too far from it); "
          f"Japanese {jp_count} printings; sets with an expansion: "
          f"{len(en_home)} English, {len(ja_home)} Japanese", file=sys.stderr)


if __name__ == "__main__":
    main()
