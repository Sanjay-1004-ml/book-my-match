"""One-off, re-runnable upgrade that gives EXISTING food items a description (the new menu UI shows it).

  python -m app.backfill_food --dry-run     # show what would change
  python -m app.backfill_food               # apply

Only fills a missing/empty `description` on food items whose name matches an entry in database/seed_data.json.
It never overwrites a description you (or an admin) already wrote, never creates or deletes items, and never touches
prices, availability, food orders or any other collection. Running it twice changes nothing the second time.
Images are not touched: set an item's image URL from Admin > Food, otherwise the menu shows an emoji tile."""
import argparse
import json
from .database import get_db
from .seed import SEED_FILE


def backfill(db, dry_run: bool = False) -> dict:
    data = json.loads(SEED_FILE.read_text())
    wanted = {it[0].strip().lower(): it[2] for items in data["food"].values() for it in items if len(it) > 2 and it[2]}
    report = {"checked": 0, "updated": 0, "already_described": 0, "no_seed_match": 0}
    for item in db.food_items.find({}):
        report["checked"] += 1
        if (item.get("description") or "").strip():
            report["already_described"] += 1
            continue
        desc = wanted.get(item["name"].strip().lower())
        if not desc:
            report["no_seed_match"] += 1
            continue
        report["updated"] += 1
        if not dry_run:
            # conditional on still being empty, so a concurrent admin edit is never overwritten
            db.food_items.update_one({"_id": item["_id"], "$or": [{"description": None}, {"description": ""},
                                                                  {"description": {"$exists": False}}]},
                                     {"$set": {"description": desc}})
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(backfill(get_db(), dry_run=args.dry_run))
