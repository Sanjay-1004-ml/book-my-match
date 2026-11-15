# Phase 3B / 3C / 3D – interactive seat booking, seat locking, payment and tickets

Built on the Phase 3A seating foundation (`docs/PHASE_3A_SEATING.md`). A seat booking reuses the existing booking, payment, ticket, QR, PDF,
My Bookings and staff-verification code instead of adding a parallel system.

## 1. User flow
1. User opens a match. If the match has a seating layout the page offers **Select seats** (`#/seats/<match id>`); matches without a layout show category tickets exactly as before.
2. Seat map: stands -> sections -> rows -> seats, all from `GET /api/matches/{id}/seating` and `.../seating/sections/{section_id}`. Colours: green available, blue selected (also "held by you"), yellow held by another user, red booked, grey unavailable. Up to `MAX_TICKETS_PER_BOOKING` seats; the price and total come from `POST /api/matches/{id}/seat-quote`.
3. **Reserve seats and continue** -> `POST /api/bookings/seats` locks the seats and creates a `pending_payment` booking, then the existing payment page (`#/pay/<booking id>`) shows the seats, the price breakdown and a countdown. **Cancel checkout** releases the seats.
4. Pay with the existing `POST /api/payments/pay`. On success the seats become `booked`, one ticket per seat (unique ticket id, QR, backup code) is issued and the booking appears in My Bookings with seats, QR and the PDF download. Staff scan the QR with the existing scanner.

## 2. Concurrency strategy (Phase 3C)
* **The booking is the hold.** A seat booking is a `bookings` document with `booking_type: "seat"`, `status: pending_payment`, `expires_at` (now + `RESERVATION_MINUTES`, default 10) and a frozen `seats` snapshot (stand, section, row, number, category, unit price).
* **The lock lives on the per-match seat row** (`match_seat_inventory`): `status: held`, `booking_id`, `hold_user_id`, `hold_expires_at`.
* **Atomic seat lock.** For each seat (ascending id order) one conditional `find_one_and_update`:
  `{match_id, seat_id, is_active: true, $or: [{status: "available"}, {status: "held", hold_expires_at: {$lte: now}}]}` -> set held + booking_id + expiry.
  MongoDB applies a single-document update atomically, so of any number of simultaneous requests exactly one can win a seat; the others get `None` -> HTTP 409. **Expiry is checked inside the lock itself** (not by a TTL index and not by a sweeper), so an expired hold never blocks anyone.
* **All-or-nothing.** If any seat cannot be locked, the seats this request already locked are released by `booking_id` and nothing is created. A crash between locking and creating the booking leaves rows whose `hold_expires_at` passes in minutes; they are lockable immediately and are tidied by the sweeper.
* **Ownership-checked release/confirm.** Release (expiry, cancel, failed race) and confirm only touch rows that still carry *this* `booking_id` and the right status. An expired booking can therefore never free or book a seat that someone else has since locked.
* **Housekeeping, not correctness.** `ticketing.expire_stale` (existing loop, now seat-aware) flips expired bookings and releases their seats; `sweep_expired_holds` (also run in that loop and before reads) frees any other expired holds. Both are idempotent.
* **Duplicates:** the same seat twice in a request is rejected (422); one active hold per user per match (409) prevents seat hoarding; `(match_id, seat_id)` is unique so a seat has exactly one inventory row per match; the same physical seat is independent across matches.
* **Not using multi-document transactions.** They need a replica set and add latency; the per-seat atomic update plus compensation gives the same guarantee for this operation and also works on Atlas free tier.

## 3. Payment and confirmation (Phase 3D)
* The amount is computed by the server from the category/seat price using the existing `FEE_PERCENT` and `TICKET_TAX_PERCENT` (`pricing.seat_quote`: fee on subtotal, tax on subtotal + fee) and stored on the booking. `POST /payments/pay` accepts only `booking_id`, `method`, `simulate`; any other field (an amount) is rejected with 422.
* Payments stay **demo payments** (`simulated: true`, exactly as in the existing system); nothing is a real gateway transaction. Food stays separate: seat bookings take no food, food orders have their own payment and the 5% food tax.
* Success path (existing `process_payment`): conditional flip `pending_payment -> confirmed` (only if the hold has not expired) -> **seats `held -> booked`** (all seats or the payment is rolled back: booking `expired`, no payment record, HTTP 409, "you were not charged") -> payment record -> tickets. Duplicate/parallel payment requests are idempotent (one winner, others `already_paid`). If the server dies after the flip, the existing healing routine completes seat confirmation and ticket issue.
* Failed payment: seats are **not** booked; the hold is kept so the user can retry until it expires (existing behaviour: `payment_status: failed`, booking still `pending_payment`); cancelling or expiry releases the seats.
* Cancelling a **paid** seat booking (existing cancel rules and refund status) cancels its tickets and returns the seats to `available`.
* Tickets: one per seat; `seat_number` holds a printable label ("North Stand · Block A · Row A1 · Seat 7"), plus a structured `seat` object. The existing PDF prints the label; the QR still carries only an opaque token.
* Staff: the existing `POST /staff/verify` works unchanged; its result now includes `seat_number` and `seat`. Already-used / invalid / wrong-match rules are the existing ones.
* Mixed selling is prevented: a match with a seating layout refuses category bookings (409); a layout cannot be assigned to a match that already has live category bookings (409).

## 4. MongoDB changes
No new collections beyond 3A. Changes:
* `bookings`: new optional fields `booking_type` ("seat"; absent = category booking), `seat_ids`, `seats[]` (snapshot), `category_id: null` for seat bookings. Existing documents are untouched.
* `match_seat_inventory`: fields `booking_id`, `hold_user_id`, `hold_expires_at`, `booked_at` are now written/removed by the holds; indexes from 3A (`(match_id, seat_id)` unique, `(status, hold_expires_at)`, `(booking_id)`, `(hold_id)`) are used. `(booking_id)` is the one that matters for release/confirm; add nothing by hand (created at startup).
* `tickets`: seat tickets carry `seat_number` (label) and `seat {seat_id, seat_key, stand, section, row, number}`; category tickets keep `seat_number: null`.
* `seat_holds` (created in 3A) is **not used**: the booking is the hold record. It is empty and harmless; drop it if you like.
* No migration, no destructive change.

## 5. API
| Method & path | Auth | Notes |
|---|---|---|
| `POST /api/bookings/seats` `{match_id, seat_ids}` (+optional `Idempotency-Key` header) | user | locks seats, creates the pending booking; 201 (200 on idempotent replay); 404/409/422/400 on invalid or unavailable seats; response has `expires_in_seconds`, `seats[]`, `ticket_total` |
| `GET /api/matches/{id}/seating` | public | now also returns `max_seats`, per-section `held`/`booked` |
| `GET /api/matches/{id}/seating/sections/{section_id}` | public (+optional login) | seat `status`: `available`, `held`, `booked`, `unavailable`, or `mine` for the logged-in holder |
| `POST /api/matches/{id}/seat-quote` `{seat_ids}` | public | server price (unchanged from 3A) |
| `POST /api/bookings/{id}/cancel` | owner | now releases seats for seat bookings |
| `POST /api/payments/pay` | user | unchanged contract; seat-aware inside |
| `GET /api/bookings`, `GET /api/bookings/{id}`, `.../tickets`, `.../tickets/pdf` | owner | now include `booking_type`, `seats[]` |
| `POST /api/staff/verify` | staff | result now includes `seat_number`, `seat` |
| `POST /api/bookings` | user | now answers 409 for a match that is sold by seat |
| `GET /api/admin/matches/{id}/seat-inventory` | admin | items now include `booking_reference`, `booking_status`, `customer`, `hold_expires_at` |

## 6. Limitations (honest list)
* Payments are the existing **demo** payments; there is no real gateway.
* The seat map refreshes every 20 s and on Refresh; there is no push (WebSocket) update. A seat taken meanwhile is rejected by the server at reservation time with a clear message.
* Seat selection is by individual click (no "best available" or seat-range helper).
* Category tickets and seats are never mixed on one match (by design, see 3); matches that already have category bookings cannot be switched to seating.
* One active hold per user per match.
* The seat map for a very large section (hundreds of seats per row x many rows) is rendered in one block; sections of normal size (tens of seats per row) are fine.
