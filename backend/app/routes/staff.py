from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from ..database import get_db
from ..deps import require_staff
from ..schemas import VerifyIn
from ..services import matches_out
from .. import ticketing

router = APIRouter(prefix="/staff", tags=["staff"], dependencies=[Depends(require_staff)])


@router.get("/matches")
def gate_matches(db=Depends(get_db)):
    """Matches a gate can be configured for: today and the next two weeks, plus yesterday (late finishes)."""
    lo = (date.today() - timedelta(days=1)).isoformat()
    hi = (date.today() + timedelta(days=14)).isoformat()
    rows = list(db.matches.find({"status": "scheduled", "match_date": {"$gte": lo, "$lte": hi}}).sort([("match_date", 1), ("start_time", 1)]).limit(100))
    return matches_out(db, rows)


@router.post("/verify")
def verify(body: VerifyIn, staff: dict = Depends(require_staff), db=Depends(get_db)):
    """All checks run here against MongoDB; the QR content alone is never trusted.
    Always HTTP 200 with a `result` (success, already_used, invalid, cancelled, unpaid, wrong_match, not_match_day)."""
    return ticketing.verify_ticket(db, staff, body.token, body.expected_match_id)


@router.get("/recent-scans")
def recent_scans(limit: int = Query(20, ge=1, le=100), staff: dict = Depends(require_staff), db=Depends(get_db)):
    return ticketing.list_entry_logs(db, staff_id=staff["_id"], limit=limit)
