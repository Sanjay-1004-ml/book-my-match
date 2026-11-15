"""Favorite teams and matches. Framework-free. Stored in the NEW `favorites` collection only; no existing collection is touched.
Document: {_id: "<user>:<kind>:<key>", user_id, kind: "team"|"match", key, created_at}. The string _id makes a duplicate
impossible (even with two simultaneous requests) and makes add/remove idempotent. Every query is scoped to the user id."""
from .clock import now
from .services import matches_out
from .discovery import SORT

MAX_TEAMS, MAX_MATCHES = 50, 100


class FavoriteError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message); self.status, self.message = status, message


def _id(uid: int, kind: str, key) -> str:
    return f"{uid}:{kind}:{str(key).strip().lower()}"


def _add(db, uid, kind, key, limit):
    fid = _id(uid, kind, key)
    if not db.favorites.find_one({"_id": fid}, {"_id": 1}):
        if db.favorites.count_documents({"user_id": uid, "kind": kind}) >= limit:
            raise FavoriteError(409, f"You can follow up to {limit} {kind}s. Remove one first.")
    db.favorites.update_one({"_id": fid}, {"$setOnInsert": {"user_id": uid, "kind": kind, "key": key, "created_at": now()}}, upsert=True)


def add_team(db, uid: int, name: str):
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise FavoriteError(400, "Team name is required (max 60 characters)")
    real = db.matches.find_one({"$or": [{"team_a": name}, {"team_b": name}]}, {"team_a": 1, "team_b": 1})
    if not real:
        raise FavoriteError(404, "Team not found")
    _add(db, uid, "team", name, MAX_TEAMS)


def remove_team(db, uid: int, name: str):
    db.favorites.delete_one({"_id": _id(uid, "team", name or ""), "user_id": uid})


def add_match(db, uid: int, match_id: int):
    if not db.matches.find_one({"_id": match_id}, {"_id": 1}):
        raise FavoriteError(404, "Match not found")
    _add(db, uid, "match", match_id, MAX_MATCHES)


def remove_match(db, uid: int, match_id: int):
    db.favorites.delete_one({"_id": _id(uid, "match", match_id), "user_id": uid})


def summary(db, uid: int, with_matches: bool = False) -> dict:
    docs = list(db.favorites.find({"user_id": uid}))
    teams = sorted((d["key"] for d in docs if d["kind"] == "team"), key=str.lower)
    ids = sorted(int(d["key"]) for d in docs if d["kind"] == "match")
    out = {"teams": teams, "match_ids": ids}
    if with_matches:
        ms = list(db.matches.find({"_id": {"$in": ids}}).sort(SORT)) if ids else []
        out["matches"] = matches_out(db, ms)
    return out
