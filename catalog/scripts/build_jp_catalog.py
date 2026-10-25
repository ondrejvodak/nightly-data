#!/usr/bin/env python3
"""Japanese printings for the KingofOP catalog (docs/projects-2027/06).

Writes `ios/KingofOP/Resources/cards_ja.json` and the web app's
`website/app/data/cards_ja.json`: every printing on Bandai's Japanese card
list (https://www.onepiece-cardgame.com/cardlist/), which uses the same page
markup as the English list, so build_card_catalog's parser reads it.

A Japanese printing's id is the English-form id plus ".ja" ("OP01-001.ja",
"OP01-001_p1.ja"): the same card number, its own copies in a collection.
Names, traits and text stay in Japanese; card types come in English on the
page; colours are mapped to the English names the app keys on.

There are no Japanese prices: Cardrush bans fetching them and Yuyu-tei bans
reproduction (docs/research/2026-10-24-jp-price-sources.md), so a Japanese
card shows "No price" until a source is agreed.

Run after each Japanese release:  python3 scripts/build_jp_catalog.py
"""
import datetime
import html
import json
import os
import pathlib
import re
import sys
import time

import build_card_catalog as en

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_APP = ROOT / "ios/KingofOP/Resources/cards_ja.json"
OUT_WEB = ROOT / "website/app/data/cards_ja.json"
# KOP_CATALOG_OUT (see build_card_catalog.py): only the nightly job's folder.
OUTS = ([pathlib.Path(os.environ["KOP_CATALOG_OUT"]) / "cards_ja.json"] if os.environ.get("KOP_CATALOG_OUT")
        else [OUT_APP, ROOT / "android/app/src/main/assets/cards_ja.json", OUT_WEB])

LIST = "https://www.onepiece-cardgame.com/cardlist/"
IMG = "https://www.onepiece-cardgame.com/images/cardlist/card/"
COLOURS = {"赤": "Red", "緑": "Green", "青": "Blue", "紫": "Purple", "黒": "Black", "黄": "Yellow"}


def series_list():
    """[(series id, code, label, kind)], in the selector's id order."""
    landing = en.fetch(LIST, "bandai_ja_landing.html")
    out = []
    for sid, label in re.findall(r'<option value="(5\d{5})"[^>]*>([^<]*)', landing):
        label = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(label)))
        label = re.sub(r"\s+", " ", label).strip()
        code = re.search(r"【([^】]+)】\s*$", label)
        head = label
        if "プロモーション" in head:
            code, kind = "PROMO-JA", "promo"
        elif not code:
            code, kind = f"JA-{sid}", "other"
        else:
            code = code.group(1)
            kind = ("starter" if ("スタートデッキ" in head or "アルティメットデッキ" in head) else
                    "extra" if "エクストラブースター" in head else
                    "premium" if "プレミアムブースター" in head else "booster")
        out.append((sid, code, label, kind))
    return sorted(set(out), key=lambda s: s[0])


def set_name(label):
    """'ブースターパック 世界最強の戦士【OP-17】' -> '世界最強の戦士'."""
    name = re.sub(r"【[^】]+】\s*$", "", label)
    name = re.sub(r"^(ブースターパック|エクストラブースター|プレミアムブースター|スタートデッキ|アルティメットデッキ)\s*", "", name)
    return name.strip() or label


def main():
    series = series_list()
    print(f"  {len(series)} products on the Japanese list")
    cards, sets = {}, []
    for order, (sid, code, label, kind) in enumerate(series):
        try:
            page = en.fetch(f"{LIST}?series={sid}", f"bandai_ja_{sid}.html")
        except Exception as e:
            print(f"  WARN series {sid} {code}: {e}", file=sys.stderr)
            continue
        found = 0
        for raw_id, body in en.BLOCK.findall(page):
            jid = raw_id + ".ja"
            if jid in cards:
                continue
            try:
                card = en.parse_card(raw_id, body, code)
            except Exception as e:
                print(f"  WARN card {raw_id}: {e}", file=sys.stderr)
                continue
            card["id"] = jid
            card["lang"] = "ja"
            card["col"] = [COLOURS.get(c, c) for c in card.get("col", [])]
            card["img"] = IMG + raw_id + ".png"
            cards[jid] = card
            found += 1
        sets.append({"code": code, "name": set_name(label), "kind": kind, "order": order})
        print(f"  {code:<10} {found:4d} printings")
        time.sleep(0.25)

    out = {
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "www.onepiece-cardgame.com (Japanese card list)",
        "sets": sets,
        "cards": sorted(cards.values(), key=lambda c: c["id"]),
    }
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    for path in OUTS:
        path.write_text(text)
    print(f"  {len(cards)} Japanese printings → {', '.join(str(p) for p in OUTS)}")


if __name__ == "__main__":
    main()
