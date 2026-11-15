import re
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from ..database import get_db, dec
from ..services import matches_out, stadiums_out, stadium_out
from ..discovery import rx, match_filter, search_matches, filter_options, SORT, SORTS, MAX_TEAMS_FILTER
from ..ticketing import expire_stale

router = APIRouter(tags=["catalog"])

# the filter now lives in discovery.py (unchanged behaviour, plus optional tournament/teams)
_rx = rx
_match_filter = match_filter


def _find(db, flt, limit=None, skip=0):
    cur = db.matches.find(flt).sort(SORT).skip(skip)
    if limit:
        cur = cur.limit(limit)
    return list(cur)


@router.get("/sports")
def sports(db=Depends(get_db)):
    counts = {r["_id"]: r["n"] for r in db.stadiums.aggregate([{"$group": {"_id": "$sport_id", "n": {"$sum": 1}}}])}
    return [{"id": s["_id"], "name": s["name"], "description": s.get("description"), "stadium_count": counts.get(s["_id"], 0)}
            for s in db.sports.find().sort("_id", 1)]


@router.get("/sports/{sport_id}")
def sport(sport_id: int, db=Depends(get_db)):
    s = db.sports.find_one({"_id": sport_id})
    if not s:
        raise HTTPException(404, "Sport not found")
    sts = list(db.stadiums.find({"sport_id": sport_id}).sort("_id", 1))
    return {"id": s["_id"], "name": s["name"], "description": s.get("description"), "stadiums": stadiums_out(db, sts)}


@router.get("/stadiums")
def stadiums(sport_id: Optional[int] = None, location: Optional[str] = None, db=Depends(get_db)):
    flt = {}
    if sport_id: flt["sport_id"] = sport_id
    if location and location.strip(): flt["location"] = _rx(location)
    return stadiums_out(db, list(db.stadiums.find(flt).sort([("sport_id", 1), ("name", 1)])))


@router.get("/stadiums/{stadium_id}")
def stadium(stadium_id: int, db=Depends(get_db)):
    s = db.stadiums.find_one({"_id": stadium_id})
    if not s:
        raise HTTPException(404, "Stadium not found")
    ms = _find(db, _match_filter(db, stadium_id=stadium_id), limit=20)
    return stadium_out(db, s, matches_out(db, ms))


@router.get("/stadiums/{stadium_id}/matches")
def stadium_matches(stadium_id: int, include_past: bool = False, db=Depends(get_db)):
    if not db.stadiums.find_one({"_id": stadium_id}, {"_id": 1}):
        raise HTTPException(404, "Stadium not found")
    return matches_out(db, _find(db, _match_filter(db, stadium_id=stadium_id, include_past=include_past)))


@router.get("/matches")
def matches(q: Optional[str] = None, sport_id: Optional[int] = None, stadium_id: Optional[int] = None,
            location: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
            include_past: bool = False, status: str = "scheduled", limit: int = Query(50, ge=1, le=100),
            offset: int = Query(0, ge=0), db=Depends(get_db)):
    flt = _match_filter(db, q, sport_id, stadium_id, location, date_from, date_to, include_past, status)
    return matches_out(db, _find(db, flt, limit=limit, skip=offset))


@router.get("/matches/discover")
def discover(q: Optional[str] = Query(None, max_length=60), sport_id: Optional[int] = None, location: Optional[str] = Query(None, max_length=60),
             date_from: Optional[date] = None, date_to: Optional[date] = None, tournament: Optional[str] = Query(None, max_length=80),
             teams: Optional[str] = Query(None, max_length=1200, description="comma-separated exact team names"),
             min_price: Optional[float] = Query(None, ge=0, le=10_000_000), max_price: Optional[float] = Query(None, ge=0, le=10_000_000),
             sort: str = "date", include_past: bool = False, limit: int = Query(24, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
             db=Depends(get_db)):
    """Discovery search with filters (text, sport, city, dates, tournament, teams, price range), sorting and paging.
    Returns {items, total, limit, offset, has_more}. Public, read-only. The older GET /matches is unchanged."""
    team_list = [t for t in (teams or "").split(",") if t.strip()]
    if len(team_list) > MAX_TEAMS_FILTER:
        raise HTTPException(400, f"At most {MAX_TEAMS_FILTER} teams can be filtered at once")
    flt = match_filter(db, q, sport_id, None, location, date_from, date_to, include_past, "scheduled", tournament, team_list)
    try:
        items, total = search_matches(db, flt, min_price, max_price, sort, limit, offset)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": items, "total": total, "limit": limit, "offset": offset, "has_more": offset + len(items) < total}


@router.get("/matches/filters")
def filters(db=Depends(get_db)):
    """Options for the filter controls (sports, cities, tournaments, teams, price bounds, sort keys) from bookable matches."""
    return filter_options(db)


@router.get("/matches/search")
def search(q: str = Query(min_length=1, max_length=60), db=Depends(get_db)):
    return matches_out(db, _find(db, _match_filter(db, q=q), limit=30))


@router.get("/matches/upcoming")
def upcoming(limit: int = Query(8, ge=1, le=50), db=Depends(get_db)):
    return matches_out(db, _find(db, _match_filter(db), limit=limit))


@router.get("/matches/popular")
def popular(limit: int = Query(6, ge=1, le=50), db=Depends(get_db)):
    """Popular = upcoming matches with the most tickets already sold."""
    cand = list(db.matches.find(_match_filter(db), {"_id": 1, "match_date": 1, "start_time": 1}))
    sold = {}
    for i in db.ticket_inventory.find({"match_id": {"$in": [m["_id"] for m in cand]}}, {"match_id": 1, "total": 1, "available": 1}):
        sold[i["match_id"]] = sold.get(i["match_id"], 0) + (i["total"] - i["available"])
    cand.sort(key=lambda m: (-sold.get(m["_id"], 0), m["match_date"], m.get("start_time") or "", m["_id"]))
    ids = [m["_id"] for m in cand[:limit]]
    docs = {m["_id"]: m for m in db.matches.find({"_id": {"$in": ids}})}
    return matches_out(db, [docs[i] for i in ids])


@router.get("/matches/{match_id}")
def match(match_id: int, db=Depends(get_db)):
    m = db.matches.find_one({"_id": match_id})
    if not m:
        raise HTTPException(404, "Match not found")
    return matches_out(db, [m])[0]


@router.get("/matches/{match_id}/ticket-categories")
def ticket_categories(match_id: int, db=Depends(get_db)):
    if not db.matches.find_one({"_id": match_id}, {"_id": 1}):
        raise HTTPException(404, "Match not found")
    expire_stale(db, limit=25)  # abandoned unpaid holds give their tickets back before availability is shown
    inv = list(db.ticket_inventory.find({"match_id": match_id}).sort("_id", 1))
    cats = {c["_id"]: c for c in db.ticket_categories.find({"_id": {"$in": [i["category_id"] for i in inv]}})}
    rows = [{"category_id": i["category_id"], "name": cats[i["category_id"]]["name"], "price": float(dec(cats[i["category_id"]]["price"])),
             "available": i["available"], "total": i["total"]} for i in inv]
    return sorted(rows, key=lambda r: -r["price"])
