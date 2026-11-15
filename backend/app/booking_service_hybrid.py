"""Hybrid booking service that coordinates MongoDB event lookup and SQLite persistence.

The existing FastAPI project already has a `services.py` module that implements
booking logic directly against MongoDB.  This new module re‑uses the same
MongoDB helper (`get_db`) for reading the event catalogue, but writes the
customer/booking/payment records to SQLite.  All writes are wrapped in a single
SQLite transaction so the relational data stays consistent.

Typical usage (from a route or from the console app)::

    from .booking_service_hybrid import hybrid_book_ticket
    result = hybrid_book_ticket(
        event_id="EVT101",
        category="VIP",
        qty=2,
        customer={"name": "Alice", "email": "alice@example.com", "phone": "12345"},
        payment_method="credit_card",
    )

`result` contains a combined view that can be shown to the user.
"""

import os
from datetime import datetime
from typing import Dict, List, Any

from .database import get_db  # existing MongoDB accessor
from .sqlite_db import get_connection
from .sql_repository import create_full_booking, HybridBookingError


def _fetch_event(event_id: str) -> Dict[str, Any]:
    """Retrieve the event document from MongoDB.
    Raises ``ValueError`` if the event does not exist.
    """
    db = get_db()
    event = db.events.find_one({"event_id": event_id})
    if not event:
        raise ValueError(f"Event with id {event_id!r} not found")
    return event


def _select_category(event: Dict[str, Any], category_name: str) -> Dict[str, Any]:
    """Find the pricing entry for the requested category.
    ``event`` is the MongoDB document returned by ``_fetch_event``.
    Raises ``ValueError`` if the category is not present.
    """
    categories = event.get("categories", [])
    for cat in categories:
        if cat.get("category") == category_name:
            return cat
    raise ValueError(f"Category {category_name!r} not found for event {event.get('event_id')}")


def hybrid_book_ticket(
    *,
    event_id: str,
    category: str,
    qty: int,
    customer: Dict[str, str],
    payment_method: str,
) -> Dict[str, Any]:
    """Perform the full booking workflow.

    1️⃣  Read the event from MongoDB (catalogue & price).
    2️⃣  Compute the total price (price * quantity).
    3️⃣  Insert customer, booking header, booking items and payment into SQLite
        inside a single transaction.
    4️⃣  Return a dictionary that merges the SQL booking information with a
        snapshot of the event data – exactly what the console UI needs to display.

    Errors are raised as ``ValueError`` (bad user input) or ``HybridBookingError``
    (SQL failure).  The caller should translate these to appropriate HTTP status
    codes or console messages.
    """
    # ------------------------------------------------------------------
    # 1️⃣  MongoDB catalogue lookup (read‑only – no side‑effects)
    # ------------------------------------------------------------------
    event_doc = _fetch_event(event_id)
    cat_doc = _select_category(event_doc, category)
    unit_price = float(cat_doc.get("price", 0))
    subtotal = unit_price * qty

    # ------------------------------------------------------------------
    # 2️⃣  Prepare data structures for the SQL layer
    # ------------------------------------------------------------------
    booking_date = datetime.utcnow().isoformat()
    booking_status = "pending_payment"
    items = [
        {
            "event_id": event_id,
            "event_name_snapshot": event_doc.get("match_name", ""),
            "category": category,
            "ticket_price_snapshot": unit_price,
            "quantity": qty,
            "subtotal": subtotal,
        }
    ]
    payment = {
        "method": payment_method,
        "amount": subtotal,
        "status": "initiated",
        "date": datetime.utcnow().isoformat(),
    }

    # ------------------------------------------------------------------
    # 3️⃣  Write everything to SQLite atomically
    # ------------------------------------------------------------------
    try:
        booking_id = create_full_booking(
            customer=customer,
            booking_date=booking_date,
            status=booking_status,
            items=items,
            payment=payment,
        )
    except HybridBookingError as exc:
        # Propagate a clear message – the caller can turn this into a 500/400.
        raise HybridBookingError(str(exc))

    # ------------------------------------------------------------------
    # 4️⃣  Build the combined view for the caller
    # ------------------------------------------------------------------
    result = {
        "booking_id": booking_id,
        "event": {
            "event_id": event_id,
            "match_name": event_doc.get("match_name"),
            "stadium": event_doc.get("stadium"),
            "category": category,
            "unit_price": unit_price,
            "quantity": qty,
            "subtotal": subtotal,
        },
        "customer": customer,
        "total_amount": subtotal,
        "payment_method": payment_method,
        "status": booking_status,
        "booking_date": booking_date,
    }
    return result
