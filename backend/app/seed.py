"""Idempotent seed: loads database/seed_data.json (extracted from the original script).
Does nothing if sports already exist. Never drops or deletes anything.

Original match dates were 2023-2024, i.e. in the past. They are shifted forward by one constant
offset so the earliest match is SEED_START_DAYS from today; gaps between matches are preserved.
(Not used when you migrate existing data: migrated matches keep their current dates.)"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from .database import d128, sync_counters
from .services import parse_start_time, time_str

SEED_FILE = Path(__file__).resolve().parents[2] / "database" / "seed_data.json"
SEED_START_DAYS = 14


def _teams(raw: str):
    parts = re.split(r"vs", raw, maxsplit=1, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (raw.strip(), "TBD")


def run(db, today: date | None = None) -> bool:
    if db.sports.find_one({}, {"_id": 1}):
        return False
    data = json.loads(SEED_FILE.read_text())
    today = today or date.today()
    originals = [date.fromisoformat(m["date"]) for m in data["matches"]]
    offset = (today + timedelta(days=SEED_START_DAYS)) - min(originals)

    seq = {}

    def nid(coll):
        seq[coll] = seq.get(coll, 0) + 1
        return seq[coll]

    sports = {n: {"_id": nid("sports"), "name": n, "description": None} for n in data["sports"]}
    stadiums = {s["key"]: {"_id": nid("stadiums"), "name": s["name"], "location": s["location"], "capacity": s["capacity"],
                           "image_url": None, "sport_id": sports[s["sport"]]["_id"]} for s in data["stadiums"]}
    cats = {}
    for c in data["categories"]:
        cats.setdefault(c["stadium"], []).append({"_id": nid("ticket_categories"), "stadium_id": stadiums[c["stadium"]]["_id"],
                                                  "name": c["name"], "price": d128(c["price"])})
    matches, inventory = [], []
    for m in data["matches"]:
        a, b = _teams(m["teams"])
        st = stadiums[m["stadium"]]
        doc = {"_id": nid("matches"), "stadium_id": st["_id"], "team_a": a, "team_b": b,
               "match_date": (date.fromisoformat(m["date"]) + offset).isoformat(), "timing": m["timing"],
               "start_time": time_str(parse_start_time(m["timing"])) if parse_start_time(m["timing"]) else None,
               "status": "scheduled"}
        matches.append(doc)
        each = st["capacity"] // len(cats[m["stadium"]])
        for c in cats[m["stadium"]]:
            inventory.append({"_id": nid("ticket_inventory"), "match_id": doc["_id"], "category_id": c["_id"], "total": each, "available": each})
    fcats = {n: {"_id": nid("food_categories"), "name": n} for n in data["food"]}
    # each food entry is [name, price] or [name, price, description]
    fitems = [{"_id": nid("food_items"), "category_id": fcats[n]["_id"], "name": it[0], "price": d128(it[1]), "is_active": True,
               "description": it[2] if len(it) > 2 else None, "image_url": None}
              for n, items in data["food"].items() for it in items]

    db.sports.insert_many(list(sports.values()))
    db.stadiums.insert_many(list(stadiums.values()))
    db.ticket_categories.insert_many([c for v in cats.values() for c in v])
    db.matches.insert_many(matches)
    db.ticket_inventory.insert_many(inventory)
    db.food_categories.insert_many(list(fcats.values()))
    db.food_items.insert_many(fitems)
    sync_counters(db)
    return True
