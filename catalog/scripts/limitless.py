"""Limitless TCG's print tables: for every English card number, each print
with its own TCGplayer product and its own Cardmarket product, kept by hand
(onepiece.limitlesstcg.com/cards/OP05-119). build_cardmarket_map.py takes
its Cardmarket pairs from here; audit_card_map.py checks the maps against it.

Limitless links Cardmarket by page (".../Singles/The-Best/Name-OP05-119-V2"),
not by product id. A link is resolved, on the day its page is read, against
Cardmarket's product list and price guide: the expansion by vote (the
expansions holding the numbers linked under that name), then the "-Vn"
product of the number there if its trend price is the euro Limitless shows,
else the one product of the number under that name at that price
(Cardmarket's V order is not always its idProduct order, and it keeps twin
expansions under one name). A link whose page name carries another number
than the card's own is Limitless's slip (EB04-025 linked to EB04-027) and is
dropped.

Pages are kept in a state file between runs ({number: {"day", "rows"}}),
each row with the product it resolved to that day, and re-read once a week,
a seventh of them each night.
"""
import collections, concurrent.futures, datetime, html, json, os, re, time, urllib.parse, urllib.request, zlib

BASE = "https://onepiece.limitlesstcg.com"
NUMBER = re.compile(r"^(?:[A-Z]{1,4}\d{0,2}-\d{3}|P-\d{3})$")
ROW = re.compile(r"<tr[^>]*>\s*<td>\s*<a[^>]*>\s*(.*?)\s*<span class=\"prints-table-card-number\">(.*?)</span>(.*?)</tr>", re.S)
TCG = re.compile(r"tcgplayer\.com%2Fproduct%2F(\d+)%2F([^\"&]*)\"[^>]*>([^<]*)<")
EUR = re.compile(r"cardmarket\.com/en/OnePiece/Products/Singles/([^/]+)/([^?\"]+)\?[^\"]*\"[^>]*>([^<]*)<")
MAX_AGE = 7          # days before a page is read again
BUDGET = 1200        # pages read in one run at most


def _get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KingofOP catalog build"})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
            time.sleep(0.25)
            return text
        except Exception as e:
            if getattr(e, "code", None) == 404:
                return ""
            time.sleep(2 + 3 * attempt)
    return None


def _money(s):
    try:
        return float(s.replace("$", "").replace("€", "").replace(",", "").strip())
    except ValueError:
        return None


def parse(text):
    """The print rows of one card page."""
    i = text.find("card-prints-versions")
    rows = []
    for name, label, rest in ROW.findall(text[i:] if i >= 0 else ""):
        t, e = TCG.search(rest), EUR.search(rest)
        rows.append({
            "set": html.unescape(re.sub(r"\s+", " ", name)).strip(),
            "label": label.strip(),
            "tcg": int(t.group(1)) if t else None,
            "usd": _money(t.group(3)) if t else None,
            "cm_exp": e.group(1) if e else None,
            "cm_card": urllib.parse.unquote(e.group(2)) if e else None,
            "eur": _money(e.group(3)) if e else None,
        })
    return rows


def set_numbers():
    """Every card number on Limitless's set pages (to find numbers we lack)."""
    page = _get(BASE + "/cards") or ""
    nums = set()
    for s in sorted(set(re.findall(r'href="?/cards/([a-z0-9-]+)"?', page)) - {"advanced"}):
        nums |= set(re.findall(r'href="?/cards/([A-Z0-9]+-\d{3})', _get(f"{BASE}/cards/{s}") or ""))
    return nums


class Resolver:
    """Limitless Cardmarket links -> Cardmarket product ids, by today's
    product list and price guide."""

    def __init__(self, products, guide, state, foreign=()):
        """`foreign`: Cardmarket's non-English expansion ids, left out.
        Limitless links English products only, and the Japanese twin of an
        expansion lists the same numbers at the same bulk prices."""
        self.guide = guide
        self.listed = collections.defaultdict(list)
        exps_of = collections.defaultdict(set)
        for p in products:
            m = re.search(r"\(([A-Z]{1,4}\d{0,2}-\d{3})\)", p["name"])
            if m and p["idExpansion"] not in foreign:
                self.listed[(p["idExpansion"], m.group(1))].append(p["idProduct"])
                exps_of[m.group(1)].add(p["idExpansion"])
        for v in self.listed.values():
            v.sort()
        votes = collections.defaultdict(collections.Counter)
        for num, page in state.items():
            for r in page["rows"]:
                if r["cm_exp"]:
                    for e in exps_of[num]:
                        votes[r["cm_exp"]][e] += 1
        self.expansions = {slug: [e for e, n in c.most_common() if n >= 0.3 * c.most_common(1)[0][1]]
                           for slug, c in votes.items()}

    def figures(self, pid):
        g = self.guide.get(pid) or {}
        return [x for x in (g.get("trend"), g.get("low"), g.get("avg"), g.get("avg1"), g.get("avg7"), g.get("avg30")) if x]

    def trend_is(self, pid, eur):
        t = (self.guide.get(pid) or {}).get("trend")
        return bool(eur) and t is not None and abs(t - eur) <= max(0.01, 0.005 * eur)

    def near(self, pid, eur):
        return bool(eur) and any(abs(x - eur) <= max(0.02, 0.03 * eur) for x in self.figures(pid))

    def resolve(self, num, r):
        """(product id, None) or (None, why)."""
        if not r["cm_exp"]:
            return None, "no link"
        named = re.findall(r"([A-Z]{1,4}\d{0,2}-\d{3})", r["cm_card"])
        if named and named[-1] != num:
            return None, "link names another number"
        pids = [p for e in self.expansions.get(r["cm_exp"], []) for p in self.listed.get((e, num), [])]
        if not pids:
            return None, "number not in expansion"
        m = re.search(r"-V(\d+)$", r["cm_card"])
        if len(pids) == 1 and not m:
            return pids[0], None
        first = self.listed.get((self.expansions[r["cm_exp"]][0], num), [])
        v = int(m.group(1)) if m else 1
        if v <= len(first) and self.trend_is(first[v - 1], r["eur"]):
            return first[v - 1], None
        hits = [p for p in pids if self.trend_is(p, r["eur"])] or [p for p in pids if self.near(p, r["eur"])]
        if len(hits) == 1:
            return hits[0], None
        return None, "several products at that price" if hits else "no product at Limitless's price"


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh(path, numbers, products, guide, foreign=(), today=None, budget=BUDGET, log=print):
    """Read the pages of `numbers` that are missing or a week old (a
    seventh of the numbers fall due each day), resolve their Cardmarket
    links and save the state. Returns the state."""
    today = today or datetime.date.today()
    state = load(path)

    def due(num):
        page = state.get(num)
        if not page:
            return True
        return (today - datetime.date.fromisoformat(page["day"])).days >= MAX_AGE

    todo = sorted((n for n in numbers if NUMBER.match(n) and due(n)), key=lambda n: n in state)[:budget]
    fresh = {}
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        for num, text in zip(todo, pool.map(lambda n: _get(f"{BASE}/cards/{n}"), todo)):
            if text is not None:
                fresh[num] = parse(text)
    for num, rows in fresh.items():
        state[num] = {"day": today.isoformat(), "rows": rows}
    resolver = Resolver(products, guide, state, foreign)
    for num in fresh:
        for r in state[num]["rows"]:
            r["cm"], r["why"] = resolver.resolve(num, r)
    with open(path, "w") as f:
        json.dump(state, f, separators=(",", ":"), sort_keys=True)
    log(f"  limitless: read {len(fresh)} of {len(todo)} due pages, {len(state)} numbers kept")
    return state


def resolve_all(state, products, guide, foreign=()):
    """Resolve every row again against today's lists (after the resolver
    changed; a row's euro is from the day its page was read)."""
    resolver = Resolver(products, guide, state, foreign)
    for num, page in state.items():
        for r in page["rows"]:
            r["cm"], r["why"] = resolver.resolve(num, r)
    return state


def seed_days(state, today=None):
    """Spread a state read in one go over the week, so the refresh does not
    fall due all at once."""
    today = today or datetime.date.today()
    for num, page in state.items():
        page["day"] = (today - datetime.timedelta(days=zlib.crc32(num.encode()) % MAX_AGE)).isoformat()
    return state


def pairs(state):
    """{TCGplayer product id: (number, Cardmarket product id)} from the
    resolved rows. A Cardmarket product two prints resolve to is nobody's
    (Limitless gives P-001's gift collection and its premium collection
    the same link)."""
    out, seen = {}, collections.Counter()
    for num, page in state.items():
        for r in page["rows"]:
            if r.get("tcg") and r.get("cm"):
                out[r["tcg"]] = (num, r["cm"])
                seen[r["cm"]] += 1
    return {t: v for t, v in out.items() if seen[v[1]] == 1}
