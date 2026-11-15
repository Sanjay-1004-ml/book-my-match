"""Phase 3A read-only seating API for everyone (like matches and stadiums). Nothing here can change data."""
from typing import Optional
from fastapi import APIRouter, Depends, Header
from ..database import get_db
from ..security import decode_token
from .. import seating as S
from ..seating_schemas import SeatQuoteIn

router = APIRouter(tags=["seating"])


def optional_user_id(authorization: Optional[str] = Header(default=None)):
    """Seat maps are public; a valid login only adds the 'held by you' marker. A bad token is simply treated as anonymous."""
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization[7:].strip())
        try:
            return int(payload["sub"]) if payload else None
        except (KeyError, ValueError, TypeError):
            return None
    return None


@router.get("/stadiums/{stadium_id}/seating")
def stadium_seating(stadium_id: int, db=Depends(get_db)):
    return S.stadium_seating(db, stadium_id)


@router.get("/matches/{match_id}/seating")
def match_seating(match_id: int, db=Depends(get_db)):
    return S.match_seating(db, match_id)


@router.get("/matches/{match_id}/seating/sections/{section_id}")
def section_seats(match_id: int, section_id: int, uid=Depends(optional_user_id), db=Depends(get_db)):
    return S.match_section_seats(db, match_id, section_id, uid)


@router.post("/matches/{match_id}/seat-quote")
def seat_quote(match_id: int, body: SeatQuoteIn, db=Depends(get_db)):
    return S.quote_seats(db, match_id, body.seat_ids)
