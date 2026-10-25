#!/usr/bin/env python3
"""Nightly card catalog for the King of OP apps.

Rebuilds the card lists and the price maps with the app repo's own build
scripts (copied into scripts/ by the app repo's scripts/nightly_catalog.py)
and publishes them here when anything but prices changed:

    cards.json          every English printing (Bandai + TCGplayer extras)
    cards_ja.json       every Japanese printing
    tcg_products.json   TCGplayer group -> product -> printing id
    cardmarket_map.json Cardmarket product -> printing id (EN and ".ja")
    manifest.json       {"schema", "built", "files": {name: {sha256, bytes}}}

The apps read manifest.json once a day and download the four files when
`built` is newer than the catalog they have. tcg_map.json (printing ids,
pinned so an id never moves) and img_hashes.json (picture hashes) are the
build's own state, kept here between runs.

A build that loses more than 2% of the printings, or leaves a map empty, is
not published: the last good catalog stays.

Run: python3 catalog/build.py [--force]   (needs Pillow)
"""
import datetime, hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
FILES = ["cards.json", "cards_ja.json", "tcg_products.json", "cardmarket_map.json"]
STATE = ["tcg_map.json", "img_hashes.json"]
SCHEMA = 1


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def without_prices(value):
    """The file as the apps use it between price downloads: no build stamp,
    no bundled prices (those change every day and are fetched anyway)."""
    if isinstance(value, dict):
        return {k: without_prices(v) for k, v in value.items() if k not in ("built", "usd")}
    if isinstance(value, list):
        return [without_prices(v) for v in value]
    return value


def check(work):
    old, new = load(os.path.join(HERE, "cards.json")), load(os.path.join(work, "cards.json"))
    problems = []
    for name in FILES:
        if not load(os.path.join(work, name)):
            problems.append(f"{name} missing or empty")
    if old and new and len(new["cards"]) < 0.98 * len(old["cards"]):
        problems.append(f"cards.json shrank {len(old['cards'])} -> {len(new['cards'])}")
    old_ja, new_ja = load(os.path.join(HERE, "cards_ja.json")), load(os.path.join(work, "cards_ja.json"))
    if old_ja and new_ja and len(new_ja["cards"]) < 0.98 * len(old_ja["cards"]):
        problems.append(f"cards_ja.json shrank {len(old_ja['cards'])} -> {len(new_ja['cards'])}")
    for name in ("tcg_products.json", "cardmarket_map.json"):
        data = load(os.path.join(work, name)) or {}
        if len(data.get("groups") or data.get("map") or {}) < 10:
            problems.append(f"{name} has almost nothing")
    return problems


def main(force=False):
    work = tempfile.mkdtemp(prefix="catalog-")
    for name in FILES + STATE:
        if os.path.exists(os.path.join(HERE, name)):
            shutil.copy(os.path.join(HERE, name), work)
    env = dict(os.environ, KOP_CATALOG_OUT=work)
    for script in ("build_card_catalog.py", "build_jp_catalog.py", "build_cardmarket_map.py"):
        print(f"== {script}", flush=True)
        subprocess.run([sys.executable, os.path.join(SCRIPTS, script)], env=env, cwd=SCRIPTS, check=True)

    problems = check(work)
    if problems:
        print("NOT PUBLISHED: " + "; ".join(problems))
        sys.exit(1)
    for name in STATE:
        if os.path.exists(os.path.join(work, name)):
            shutil.copy(os.path.join(work, name), HERE)
    changed = [n for n in FILES
               if without_prices(load(os.path.join(work, n))) != without_prices(load(os.path.join(HERE, n)))]
    if not changed and not force:
        print("catalog unchanged (prices aside) - nothing published")
        return
    for name in FILES:
        shutil.copy(os.path.join(work, name), HERE)
    files = {}
    for name in FILES:
        with open(os.path.join(HERE, name), "rb") as f:
            data = f.read()
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    built = load(os.path.join(HERE, "cards.json"))["built"]
    manifest = {"schema": SCHEMA, "built": built, "files": files,
                "published": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    print(f"published catalog {built}: changed {', '.join(changed) or '(forced)'}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
