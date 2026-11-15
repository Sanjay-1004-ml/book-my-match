from fastapi import APIRouter, Depends, HTTPException, Query
from ..database import get_db
from ..deps import get_current_user
from .. import favorites as fav
from ..favorites import FavoriteError

router = APIRouter(prefix="/favorites", tags=["favorites"])


def _run(fn, *a):
    try:
        fn(*a)
    except FavoriteError as e:
        raise HTTPException(e.status, e.message)


@router.get("")
def my_favorites(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """The signed-in user's favorite teams and matches (always scoped to the token's user, never to a client-sent id)."""
    return fav.summary(db, user["_id"], with_matches=True)


@router.put("/teams")
def follow_team(name: str = Query(min_length=1, max_length=60), user: dict = Depends(get_current_user), db=Depends(get_db)):
    _run(fav.add_team, db, user["_id"], name)
    return fav.summary(db, user["_id"])


@router.delete("/teams")
def unfollow_team(name: str = Query(min_length=1, max_length=60), user: dict = Depends(get_current_user), db=Depends(get_db)):
    fav.remove_team(db, user["_id"], name)
    return fav.summary(db, user["_id"])


@router.put("/matches/{match_id}")
def favorite_match(match_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    _run(fav.add_match, db, user["_id"], match_id)
    return fav.summary(db, user["_id"])


@router.delete("/matches/{match_id}")
def unfavorite_match(match_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    fav.remove_match(db, user["_id"], match_id)
    return fav.summary(db, user["_id"])
