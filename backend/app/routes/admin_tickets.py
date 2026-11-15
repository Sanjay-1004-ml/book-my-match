from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from ..database import get_db
from ..deps import require_admin
from .. import ticketing

router = APIRouter(prefix="/admin", tags=["admin-tickets"], dependencies=[Depends(require_admin)])


@router.get("/tickets")
def tickets(q: Optional[str] = None, match_id: Optional[int] = None, status: Optional[str] = Query(None, pattern="^(valid|used|cancelled)$"),
            limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), db=Depends(get_db)):
    """Search by ticket id or booking reference (q); list per match; filter unused (valid) / used / cancelled."""
    return ticketing.search_tickets(db, q, match_id, status, limit, offset)


@router.get("/tickets/{ticket_id}")
def ticket(ticket_id: str, db=Depends(get_db)):
    t = db.tickets.find_one({"ticket_id": ticket_id.strip().upper()})
    if not t:
        raise HTTPException(404, "Ticket not found")
    out = ticketing.tickets_out(db, [t])[0]  # no QR/token for admins: they never need to see it
    out["entry_history"] = ticketing.list_entry_logs(db, ticket_id=t["ticket_id"], limit=50)
    return out


@router.get("/entry-logs")
def entry_logs(match_id: Optional[int] = None, result: Optional[str] = None, ticket_id: Optional[str] = None,
               limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), db=Depends(get_db)):
    return ticketing.list_entry_logs(db, match_id, result, ticket_id, None, limit, offset)


@router.get("/ticket-stats")
def stats(match_id: Optional[int] = None, db=Depends(get_db)):
    return ticketing.ticket_stats(db, match_id)


@router.get("/payments")
def payments(limit: int = Query(100, ge=1, le=200), db=Depends(get_db)):
    return [ticketing.payment_out(p) for p in db.payments.find().sort("_id", -1).limit(limit)]
