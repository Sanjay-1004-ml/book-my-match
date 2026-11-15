"""DEVELOPMENT ONLY. Adds demo `tournament` labels to matches so the tournament filter has something to show on a LOCAL dev database.

It is built so that it cannot be pointed at your real database by accident. It refuses to run unless ALL of these hold:
  1. DATABASE_NAME ends with "_dev"  (your real database is book_my_match, which can never match)
  2. BMM_DEV_CONFIRM=yes-this-is-a-throwaway-dev-database
  3. MONGODB_URL is not a mongodb+srv:// (Atlas) URL, unless BMM_DEV_ALLOW_ATLAS=yes is ALSO set
  4. the dev database already has matches (it never creates a database, it only labels existing demo matches)
It only sets `tournament` on matches that don't have one, never touches dates/prices/bookings/users, and is safe to re-run.

  # example (local MongoDB, throwaway database):
  DATABASE_NAME=bmm_dev MONGODB_URL=mongodb://localhost:27017 BMM_DEV_CONFIRM=yes-this-is-a-throwaway-dev-database \
  python backend/dev/dev_demo_data.py
Create the dev database first with:  DATABASE_NAME=bmm_dev python -m app.init_db   (run from backend/)."""
import os
import sys

CONFIRM = "yes-this-is-a-throwaway-dev-database"
TOURNAMENTS = {"Cricket": "Demo Premier League", "Football": "Demo Football Cup", "Basketball": "Demo Hoops Series",
               "Hockey": "Demo Hockey League", "Badminton": "Demo Open"}


class Refused(Exception):
    pass


def check_safe(database_name: str, mongodb_url: str, env) -> None:
    if not (database_name or "").endswith("_dev"):
        raise Refused(f'Refusing: DATABASE_NAME must end with "_dev" (got "{database_name}"). This script never runs on your real database.')
    if env.get("BMM_DEV_CONFIRM") != CONFIRM:
        raise Refused(f"Refusing: set BMM_DEV_CONFIRM={CONFIRM} to confirm this is a throwaway database.")
    if (mongodb_url or "").startswith("mongodb+srv://") and env.get("BMM_DEV_ALLOW_ATLAS") != "yes":
        raise Refused("Refusing: MONGODB_URL looks like MongoDB Atlas. Use a local MongoDB, or also set BMM_DEV_ALLOW_ATLAS=yes if this really is a separate dev cluster.")


def run(db, database_name: str, mongodb_url: str, env=os.environ) -> dict:
    check_safe(database_name, mongodb_url, env)
    if not db.matches.find_one({}, {"_id": 1}):
        raise Refused("Refusing: this dev database has no matches. Create it first with `python -m app.init_db`.")
    sports = {s["_id"]: s["name"] for s in db.sports.find({}, {"name": 1})}
    sport_of = {s["_id"]: sports.get(s["sport_id"]) for s in db.stadiums.find({}, {"sport_id": 1})}
    labelled = 0
    for m in db.matches.find({"$or": [{"tournament": {"$exists": False}}, {"tournament": None}]}, {"stadium_id": 1}):
        name = TOURNAMENTS.get(sport_of.get(m["stadium_id"]))
        if name:
            labelled += db.matches.update_one({"_id": m["_id"], "$or": [{"tournament": {"$exists": False}}, {"tournament": None}]}, {"$set": {"tournament": name}}).modified_count
    return {"labelled": labelled}


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    try:
        from app.config import settings
        from app.database import get_db
        print(run(get_db(), settings.DATABASE_NAME, settings.MONGODB_URL))
    except Refused as e:
        print(e); sys.exit(1)
