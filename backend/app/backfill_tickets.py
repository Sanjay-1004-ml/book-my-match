"""One-off, re-runnable upgrade for bookings created BEFORE payments/tickets existed.

  python -m app.backfill_tickets --dry-run     # show what would change
  python -m app.backfill_tickets               # apply

  * confirmed + payment successful, no tickets  -> a payment record and one ticket per purchased ticket are created
  * confirmed + payment pending (old flow held inventory without payment) -> becomes pending_payment with a fresh
    reservation window, so it must now be paid through the new payment page (or it expires and releases its tickets)
Nothing is deleted; running it again changes nothing that is already converted."""
import argparse
from datetime import datetime, timedelta
from .clock import now as clock_now
from .config import settings
from .database import get_db, ensure_indexes
from . import ticketing


def backfill(db, dry_run: bool = False, at: datetime | None = None) -> dict:
    at = at or clock_now()
    report = {"paid_without_tickets": 0, "tickets_created": 0, "unpaid_made_pending": 0}
    for b in db.bookings.find({"status": "confirmed", "payment_status": "pending"}):
        report["unpaid_made_pending"] += 1
        if not dry_run:
            db.bookings.update_one({"_id": b["_id"], "status": "confirmed", "payment_status": "pending"},
                                   {"$set": {"status": "pending_payment", "expires_at": at + timedelta(minutes=settings.RESERVATION_MINUTES)}})
    for b in db.bookings.find({"status": "confirmed", "payment_status": "successful"}):
        have = db.tickets.count_documents({"booking_id": b["_id"]})
        if have >= b["quantity"]:
            continue
        report["paid_without_tickets"] += 1
        report["tickets_created"] += b["quantity"] - have
        if not dry_run:
            if not b.get("payment_ref"):
                b["payment_ref"] = ticketing.new_transaction_id(at)
                db.bookings.update_one({"_id": b["_id"]}, {"$set": {"payment_ref": b["payment_ref"]}})
            if not b.get("paid_at"):
                b["paid_at"] = b.get("created_at") or at
                db.bookings.update_one({"_id": b["_id"]}, {"$set": {"paid_at": b["paid_at"]}})
            ticketing.finalize_confirmed_booking(db, b)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = get_db()
    ensure_indexes(db)
    r = backfill(db, a.dry_run)
    print(("DRY RUN - nothing changed. " if a.dry_run else "Done. ") + str(r))


if __name__ == "__main__":
    main()
