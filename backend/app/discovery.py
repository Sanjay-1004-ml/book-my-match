"""Match discovery: filtering, price range, sorting, pagination and filter options. Framework-free (no FastAPI) so it is
unit-tested directly. `match_filter` is the existing catalog filter, moved here unchanged and extended with optional
`tournament` and `teams` conditions (callers that don't pass them behave exactly as before).
Read-only: nothing here writes to the database."""
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from .database import dec
from .services import matches_out

SORT = [("match_date", 1), ("start_time", 1), ("_id", 1)]
SORT_DESC = [("match_date", -1), ("start_time", -1), ("_id", -1)]
SORTS = ("date", "date_desc", "price_asc", "price_desc", "popular")
MAX_TEAMS_FILTER = 20


def rx(text: str) -> dict:
    """Case-insensitive 'contains' with user input escaped (no regex injection)."""
    return {"$regex": re.escape(text.strip()), "$options": "i"}


def match_filter(db, q=None, sport_id=None, stadium_id=None, location=None, date_from=None, date_to=None,
                 include_past=False, status="scheduled", tournament=None, teams=None) -> dict:
    conds = []
    if status:
        conds.append({"status": status})
    rng = {}
    if date_from:
        rng["$gte"] = date_from.isoformat()
    elif not include_past:
        rng["$gte"] = date.today().isoformat()
    if date_to:
        rng["$lte"] = date_to.isoformat()
    if rng:
        conds.append({"match_date": rng})
    if stadium_id:
        conds.append({"stadium_id": stadium_id})
    st = {}
    if sport_id:
        st["sport_id"] = sport_id
    if location and location.strip():
        st["location"] = rx(location)
    if st:
        conds.append({"stadium_id": {"$in": [s["_id"] for s in db.stadiums.find(st, {"_id": 1})]}})
    if q and q.strip():
        sport_ids = [s["_id"] for s in db.sports.find({"name": rx(q)}, {"_id": 1})]
        st_ids = [s["_id"] for s in db.stadiums.find(
            {"$or": [{"name": rx(q)}, {"location": rx(q)}, {"sport_id": {"$in": sport_ids}}]}, {"_id": 1})]
        conds.append({"$or": [{"team_a": rx(q)}, {"team_b": rx(q)}, {"stadium_id": {"$in": st_ids}}]})
    if tournament and tournament.strip():
        conds.append({"tournament": rx(tournament)})
    if teams:
        names = [t.strip() for t in teams if t and t.strip()][:MAX_TEAMS_FILTER]
        if names:
            conds.append({"$or": [{"team_a": {"$in": names}}, {"team_b": {"$in": names}}]})
    return {"$and": conds} if conds else {}


def _price_stats(db, match_ids):
    """{match_id: {'prices': [Decimal...], 'sold': int}} from the match's ticket inventory and category prices."""
    if not match_ids:
        return {}
    inv = list(db.ticket_inventory.find({"match_id": {"$in": list(match_ids)}}, {"match_id": 1, "category_id": 1, "total": 1, "available": 1}))
    cats = {c["_id"]: dec(c["price"]) for c in db.ticket_categories.find({"_id": {"$in": list({i["category_id"] for i in inv})}}, {"price": 1})}
    out = defaultdict(lambda: {"prices": [], "sold": 0})
    for i in inv:
        if i["category_id"] in cats:
            out[i["match_id"]]["prices"].append(cats[i["category_id"]])
        out[i["match_id"]]["sold"] += i["total"] - i["available"]
    return out


def search_matches(db, flt: dict, min_price=None, max_price=None, sort="date", limit=24, offset=0):
    """Returns (matches_out list for the requested page, total number of matches). Sorting and price filtering use the
    ticket categories' real prices. A match 'fits' a price range when at least one of its categories is inside it."""
    min_price = None if min_price is None else Decimal(str(min_price))
    max_price = None if max_price is None else Decimal(str(max_price))
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("min_price cannot be greater than max_price")
    if min_price is None and max_price is None and sort in ("date", "date_desc"):   # cheap path: let the database sort and page
        cur = db.matches.find(flt).sort(SORT if sort == "date" else SORT_DESC).skip(offset).limit(limit)
        return matches_out(db, list(cur)), db.matches.count_documents(flt)
    cand = list(db.matches.find(flt, {"_id": 1, "match_date": 1, "start_time": 1}))
    stats = _price_stats(db, [m["_id"] for m in cand])
    def fits(m):
        prices = stats[m["_id"]]["prices"] if m["_id"] in stats else []
        return any((min_price is None or p >= min_price) and (max_price is None or p <= max_price) for p in prices)
    if min_price is not None or max_price is not None:
        cand = [m for m in cand if fits(m)]
    def low(m):   # matches without prices sort last either way
        pr = stats[m["_id"]]["prices"] if m["_id"] in stats else []
        return min(pr) if pr else None
    by_date = lambda m: (m["match_date"], m.get("start_time") or "", m["_id"])
    if sort == "date":
        cand.sort(key=by_date)
    elif sort == "date_desc":
        cand.sort(key=by_date, reverse=True)
    elif sort in ("price_asc", "price_desc"):
        priced = sorted([m for m in cand if low(m) is not None], key=lambda m: (low(m), by_date(m)), reverse=(sort == "price_desc"))
        cand = priced + sorted([m for m in cand if low(m) is None], key=by_date)
    else:  # popular: most tickets sold first
        cand.sort(key=lambda m: (-(stats[m["_id"]]["sold"] if m["_id"] in stats else 0), by_date(m)))
    ids = [m["_id"] for m in cand[offset:offset + limit]]
    docs = {m["_id"]: m for m in db.matches.find({"_id": {"$in": ids}})}
    return matches_out(db, [docs[i] for i in ids if i in docs]), len(cand)


def filter_options(db) -> dict:
    """Values for the filter controls, taken from what is actually bookable (upcoming, scheduled matches)."""
    ms = list(db.matches.find(match_filter(db), {"stadium_id": 1, "tournament": 1, "team_a": 1, "team_b": 1}))
    st = {s["_id"]: s for s in db.stadiums.find({"_id": {"$in": list({m["stadium_id"] for m in ms})}}, {"location": 1})}
    stats = _price_stats(db, [m["_id"] for m in ms])
    prices = [p for v in stats.values() for p in v["prices"]]
    clean = lambda xs: sorted({x.strip() for x in xs if x and x.strip()}, key=str.lower)
    return {"sports": [{"id": s["_id"], "name": s["name"]} for s in db.sports.find({}, {"name": 1}).sort("_id", 1)],
            "cities": clean(st[m["stadium_id"]]["location"] for m in ms if m["stadium_id"] in st),
            "tournaments": clean(m.get("tournament") for m in ms),
            "teams": clean([m["team_a"] for m in ms] + [m["team_b"] for m in ms])[:300],
            "price": {"min": float(min(prices)) if prices else None, "max": float(max(prices)) if prices else None},
            "sorts": list(SORTS)}
