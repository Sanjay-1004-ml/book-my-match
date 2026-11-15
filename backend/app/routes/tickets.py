from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from ..database import get_db
from ..deps import get_current_user
from ..ticket_pdf import build_tickets_pdf
from .. import ticketing

router = APIRouter(tags=["tickets"])


def _pdf(rows: list, name: str) -> Response:
    return Response(build_tickets_pdf(rows), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


def _own_booking(db, booking_id: int, user: dict) -> dict:
    b = db.bookings.find_one({"_id": booking_id})
    if not b or b["user_id"] != user["_id"]:  # 404, not 403: do not reveal other users' ids
        raise HTTPException(404, "Booking not found")
    return b


def _booking_tickets(db, booking_id: int, user: dict) -> list:
    b = _own_booking(db, booking_id, user)
    if b["status"] == "confirmed" and b["payment_status"] == "successful":
        ticketing.finalize_confirmed_booking(db, b)  # self-heal if tickets are missing
    return ticketing.tickets_out(db, list(db.tickets.find({"booking_id": booking_id}).sort("seq", 1)), with_qr=True)


@router.get("/tickets")
def my_tickets(scope: Literal["all", "upcoming", "completed"] = "all", user: dict = Depends(get_current_user), db=Depends(get_db)):
    return ticketing.user_tickets(db, user["_id"], scope)


@router.get("/tickets/{ticket_id}")
def my_ticket(ticket_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    t = db.tickets.find_one({"ticket_id": ticket_id.strip().upper()})
    if not t or t["user_id"] != user["_id"]:
        raise HTTPException(404, "Ticket not found")
    return ticketing.tickets_out(db, [t], with_qr=True)[0]


@router.get("/tickets/{ticket_id}/pdf")
def my_ticket_pdf(ticket_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    t = db.tickets.find_one({"ticket_id": ticket_id.strip().upper()})
    if not t or t["user_id"] != user["_id"]:
        raise HTTPException(404, "Ticket not found")
    return _pdf(ticketing.tickets_out(db, [t], with_qr=True), t["ticket_id"])


@router.get("/bookings/{booking_id}/tickets")
def booking_tickets(booking_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    return _booking_tickets(db, booking_id, user)


@router.get("/bookings/{booking_id}/tickets/pdf")
def booking_tickets_pdf(booking_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    rows = _booking_tickets(db, booking_id, user)
    if not rows:
        raise HTTPException(409, "No tickets yet: tickets are issued after a successful payment")
    return _pdf(rows, rows[0]["booking_reference"] + "-tickets")
