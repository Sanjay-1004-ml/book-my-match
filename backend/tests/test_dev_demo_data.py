"""The dev-only demo script must be unable to touch a real database, and harmless on a dev one."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("dev_demo_data", Path(__file__).resolve().parents[1] / "dev" / "dev_demo_data.py")
dev = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(dev)
OK_ENV = {"BMM_DEV_CONFIRM": dev.CONFIRM}


def snapshot(db):
    return {c: sorted(map(repr, db[c].find({}))) for c in ("users", "stadiums", "sports", "bookings", "food_items", "ticket_inventory")}


def refused(db, name, url="mongodb://localhost:27017", env=OK_ENV):
    try:
        dev.run(db, name, url, env)
    except dev.Refused as e:
        return str(e)
    return None


def test_refuses_anything_that_is_not_a_dev_database(db):
    before = repr(sorted(map(repr, db.matches.find({}))))
    for name in ("book_my_match", "bmm_prod", "dev", "dev_bmm", "", "my_dev_backup2", None):
        assert refused(db, name), name
    assert repr(sorted(map(repr, db.matches.find({})))) == before and db.matches.count_documents({"tournament": {"$exists": True}}) == 0


def test_refuses_without_explicit_confirmation_and_for_atlas_urls(db):
    assert "BMM_DEV_CONFIRM" in refused(db, "bmm_dev", env={})
    assert "BMM_DEV_CONFIRM" in refused(db, "bmm_dev", env={"BMM_DEV_CONFIRM": "yes"})
    assert "Atlas" in refused(db, "bmm_dev", url="mongodb+srv://user:pw@cluster0.example.mongodb.net/")
    assert refused(db, "bmm_dev", url="mongodb+srv://x/", env={**OK_ENV, "BMM_DEV_ALLOW_ATLAS": "yes"}) is None


def test_refuses_an_empty_database(db):
    db.matches.delete_many({})
    assert "no matches" in refused(db, "bmm_dev")


def test_labels_only_unlabelled_matches_and_is_idempotent(db):
    db.matches.update_one({"_id": 1}, {"$set": {"tournament": "My Real Tournament"}})
    other_before = snapshot(db)
    matches_before = {m["_id"]: dict(m) for m in db.matches.find({})}
    r = dev.run(db, "bmm_dev", "mongodb://localhost:27017", OK_ENV)
    assert r["labelled"] == 84
    after = {m["_id"]: m for m in db.matches.find({})}
    assert after[1]["tournament"] == "My Real Tournament"                                # never overwritten
    assert all(after[i]["tournament"].startswith("Demo ") for i in after if i != 1)
    assert all({k: v for k, v in after[i].items() if k != "tournament"} == {k: v for k, v in matches_before[i].items() if k != "tournament"} for i in after)
    assert snapshot(db) == other_before                                                  # nothing else touched
    assert dev.run(db, "bmm_dev", "mongodb://localhost:27017", OK_ENV)["labelled"] == 0
