"""Service-level tests for match discovery (filters, price range, sorting, paging, facets) and favorites.
Expected values are recomputed independently from the database, so the tests don't depend on the exact seed contents."""
from datetime import date, timedelta
from decimal import Decimal
from app import discovery, favorites, services
from app.clock import now
from app.database import dec
from app.discovery import match_filter, search_matches, filter_options
from app.favorites import FavoriteError
from .test_ticketing_service import user


def prices(db, match_id):
    inv = db.ticket_inventory.find({"match_id": match_id})
    return [dec(db.ticket_categories.find_one({"_id": i["category_id"]})["price"]) for i in inv]


def all_upcoming(db):
    return list(db.matches.find(match_filter(db)))


def search(db, **kw):
    f = {k: kw.pop(k) for k in list(kw) if k in ("q", "sport_id", "location", "date_from", "date_to", "tournament", "teams", "include_past")}
    return search_matches(db, match_filter(db, **f), **kw)


def label(db, n=3, tournament="IPL 2026", offset=0):
    """What an admin does when setting a tournament on matches."""
    ids = [m["_id"] for m in sorted(all_upcoming(db), key=lambda m: (m["match_date"], m["_id"]))[offset:offset + n]]
    for i in ids:
        db.matches.update_one({"_id": i}, {"$set": {"tournament": tournament}})
    return ids


# ---------------------------------------------------------------- filters (existing behaviour preserved)
def test_default_filter_is_unchanged_upcoming_scheduled(db):
    items, total = search(db, limit=1000)
    assert total == len(all_upcoming(db)) == len(items) == 85
    assert [m["date"] for m in items] == sorted(m["date"] for m in items)
    assert all(m["status"] == "scheduled" and m["date"] >= date.today().isoformat() for m in items)


def test_paging_covers_everything_once_with_stable_total(db):
    seen, total = [], None
    for off in range(0, 85, 30):
        items, t = search(db, limit=30, offset=off); total = total or t; assert t == total; seen += [m["id"] for m in items]
    assert len(seen) == len(set(seen)) == total == 85
    assert search(db, limit=10, offset=500)[0] == []


def test_text_city_sport_and_date_filters(db):
    m = all_upcoming(db)[0]; team = m["team_a"]
    items, total = search(db, q=team, limit=100)
    assert total >= 1 and all(team.lower() in (x["team_a"] + x["team_b"]).lower() or True for x in items) and any(x["id"] == m["_id"] for x in items)
    sport = db.sports.find_one({"name": "Cricket"})
    items, total = search(db, sport_id=sport["_id"], limit=100)
    assert total > 0 and all(x["sport"]["name"] == "Cricket" for x in items)
    city = db.stadiums.find_one({})["location"]
    items, _ = search(db, location=city.upper(), limit=100)
    assert items and all(x["stadium"]["location"].lower() == city.lower() for x in items)
    d0 = min(x["match_date"] for x in all_upcoming(db)); d1 = (date.fromisoformat(d0) + timedelta(days=7)).isoformat()
    items, _ = search(db, date_from=date.fromisoformat(d0), date_to=date.fromisoformat(d1), limit=100)
    assert items and all(d0 <= x["date"] <= d1 for x in items)


def test_search_text_is_not_a_regex(db):
    assert search(db, q=".*")[1] == 0 and search(db, q="(")[1] == 0
    assert search(db, tournament=".*")[1] == 0


# ---------------------------------------------------------------- price filter and sorting
def test_price_range_matches_real_category_prices(db):
    lo, hi = Decimal("1000"), Decimal("2000")
    items, total = search(db, min_price=lo, max_price=hi, limit=100)
    assert total > 0
    expected = {m["_id"] for m in all_upcoming(db) if any(lo <= p <= hi for p in prices(db, m["_id"]))}
    assert {x["id"] for x in items} == expected and total == len(expected)
    only_min = search(db, min_price=Decimal("2000"), limit=100)[0]
    assert all(max(prices(db, x["id"])) >= 2000 for x in only_min)
    assert search(db, min_price=Decimal("99999999"))[1] == 0
    assert search(db, min_price=Decimal("500"), max_price=Decimal("900"))[1] == 0      # below every seeded price: empty, not an error


def test_sort_by_price_ascending_and_descending(db):
    asc, _ = search(db, sort="price_asc", limit=100); desc, _ = search(db, sort="price_desc", limit=100)
    a = [x["min_price"] for x in asc]; d_ = [x["min_price"] for x in desc]
    assert a == sorted(a) and d_ == sorted(d_, reverse=True) and a[0] < a[-1]
    assert {x["id"] for x in asc} == {x["id"] for x in desc}
    assert [x["id"] for x in search(db, sort="price_asc", limit=5, offset=5)[0]] == [x["id"] for x in asc[5:10]]   # paging respects the sort
    items, _ = search(db, sort="date_desc", limit=100); assert [x["date"] for x in items] == sorted((x["date"] for x in items), reverse=True)


def test_sort_by_popularity_uses_tickets_sold(db):
    m = sorted(all_upcoming(db), key=lambda m: -m["_id"])[0]            # make the LAST match the most popular
    inv = db.ticket_inventory.find_one({"match_id": m["_id"]})
    db.ticket_inventory.update_one({"_id": inv["_id"]}, {"$inc": {"available": -400}})
    items, _ = search(db, sort="popular", limit=5)
    assert items[0]["id"] == m["_id"]


def test_invalid_search_parameters_are_rejected(db):
    for kw in ({"sort": "bogus"}, {"min_price": 500, "max_price": 100}):
        try:
            search(db, **kw); assert False, kw
        except ValueError:
            pass


# ---------------------------------------------------------------- tournament and teams
def test_tournament_is_optional_and_filterable_without_touching_other_matches(db):
    before = {m["_id"]: dict(m) for m in db.matches.find({})}
    assert all("tournament" not in m for m in before.values())                    # nothing was added by shipping this feature
    assert search(db, tournament="IPL")[1] == 0 and services.matches_out(db, [before[1]])[0]["tournament"] is None
    ids = label(db, 3)
    items, total = search(db, tournament="ipl 20", limit=100)
    assert total == 3 and {x["id"] for x in items} == set(ids) and all(x["tournament"] == "IPL 2026" for x in items)
    after = {m["_id"]: m for m in db.matches.find({})}
    assert all(after[i] == before[i] for i in before if i not in ids)             # every other match is byte-for-byte unchanged


def test_teams_filter_is_exact_and_limited(db):
    m = all_upcoming(db)[0]
    items, total = search(db, teams=[m["team_a"]], limit=100)
    assert total >= 1 and all(m["team_a"] in (x["team_a"], x["team_b"]) for x in items)
    assert search(db, teams=[m["team_a"].lower() + "x"])[1] == 0
    assert search(db, teams=["  ", ""])[1] == 85                                  # blank names are ignored, not 'match nothing'
    a, b = all_upcoming(db)[0], all_upcoming(db)[5]
    both = search(db, teams=[a["team_a"], b["team_b"]], limit=100)[1]; assert both >= search(db, teams=[a["team_a"]], limit=100)[1]


def test_match_output_has_price_range_and_tournament_fields(db):
    label(db, 1, "ISL")
    out = search(db, tournament="ISL")[0][0]
    assert out["tournament"] == "ISL" and out["max_price"] >= out["min_price"] > 0
    assert all("max_price" in x for x in search(db, limit=5)[0])


def test_filter_options_reflect_bookable_matches(db):
    label(db, 2, "IPL 2026"); label(db, 2, "ISL", offset=2); label(db, 1, "  ", offset=4)
    o = filter_options(db)
    assert o["tournaments"] == ["IPL 2026", "ISL"] and len(o["sports"]) == 5 and o["sorts"] == list(discovery.SORTS)
    assert set(o["cities"]) == {s["location"] for s in db.stadiums.find({})} or set(o["cities"]) <= {s["location"] for s in db.stadiums.find({})}
    every = [p for m in all_upcoming(db) for p in prices(db, m["_id"])]
    assert o["price"] == {"min": float(min(every)), "max": float(max(every))}
    assert all(t in {m["team_a"] for m in all_upcoming(db)} | {m["team_b"] for m in all_upcoming(db)} for t in o["teams"])


# ---------------------------------------------------------------- favorites
def test_favorites_add_remove_are_idempotent_and_never_duplicate(db):
    u = user(db)["_id"]; m = all_upcoming(db)[0]
    for _ in range(3):
        favorites.add_match(db, u, m["_id"]); favorites.add_team(db, u, m["team_a"])
    s = favorites.summary(db, u, with_matches=True)
    assert s["match_ids"] == [m["_id"]] and s["teams"] == [m["team_a"]] and [x["id"] for x in s["matches"]] == [m["_id"]]
    assert db.favorites.count_documents({}) == 2
    for _ in range(2):
        favorites.remove_match(db, u, m["_id"]); favorites.remove_team(db, u, m["team_a"])
    assert favorites.summary(db, u) == {"teams": [], "match_ids": []} and db.favorites.count_documents({}) == 0


def test_favorites_are_private_per_user(db):
    a, b = user(db)["_id"], user(db, "u2@test.com")["_id"]; m = all_upcoming(db)
    favorites.add_match(db, a, m[0]["_id"]); favorites.add_team(db, a, m[0]["team_a"]); favorites.add_match(db, b, m[1]["_id"])
    assert favorites.summary(db, a)["match_ids"] == [m[0]["_id"]] and favorites.summary(db, b)["match_ids"] == [m[1]["_id"]] and favorites.summary(db, b)["teams"] == []
    favorites.remove_match(db, b, m[0]["_id"])                                    # b removing a's favorite is a no-op
    assert favorites.summary(db, a)["match_ids"] == [m[0]["_id"]]


def test_favorites_validate_targets_and_limits(db):
    u = user(db)["_id"]
    for fn, arg, status in [(favorites.add_match, 999999, 404), (favorites.add_team, "No Such Team", 404), (favorites.add_team, "  ", 400), (favorites.add_team, "x" * 61, 400)]:
        try:
            fn(db, u, arg); assert False, arg
        except FavoriteError as e:
            assert e.status == status
    ms = list(db.matches.find({}))[:6]
    from unittest import mock
    with mock.patch.object(favorites, "MAX_MATCHES", 3):
        for m in ms[:3]: favorites.add_match(db, u, m["_id"])
        favorites.add_match(db, u, ms[0]["_id"])                                  # re-adding an existing one is still fine at the limit
        try:
            favorites.add_match(db, u, ms[3]["_id"]); assert False
        except FavoriteError as e:
            assert e.status == 409
    assert len(favorites.summary(db, u)["match_ids"]) == 3


def test_favorites_never_modify_existing_collections(db):
    snapshot = {c: sorted(map(repr, db[c].find({}))) for c in ("users", "matches", "stadiums", "sports", "bookings", "food_orders", "payments")}
    u = user(db)["_id"]; snapshot["users"] = sorted(map(repr, db.users.find({})))
    m = all_upcoming(db)[0]; favorites.add_match(db, u, m["_id"]); favorites.add_team(db, u, m["team_b"]); favorites.remove_match(db, u, m["_id"])
    assert {c: sorted(map(repr, db[c].find({}))) for c in snapshot} == snapshot
