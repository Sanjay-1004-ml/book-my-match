# BOOK MY MATCH (MongoDB edition)

FastAPI + **PyMongo / MongoDB Atlas** backend, plain HTML/CSS/JS frontend (Vercel), backend on Render.
The SQLAlchemy / SQLite / PostgreSQL / Alembic layer has been replaced. API URLs and JSON responses are unchanged.

## Phase 3B-3D: seat selection, seat locking, payment and tickets (NEW)
Matches with a seating layout (Phase 3A) are now booked by seat: interactive seat map -> temporary atomic seat hold (default 10 minutes, `RESERVATION_MINUTES`) ->
existing demo payment -> seats permanently booked -> one QR ticket per seat -> My Bookings, PDF download and staff scan. Matches without a layout keep the
category-ticket flow unchanged; food orders and the 5% food tax stay separate. Design, concurrency strategy, schema/index changes, API list and limitations:
**[docs/PHASE_3BCD_SEAT_BOOKING.md](docs/PHASE_3BCD_SEAT_BOOKING.md)**. Payments remain **demo** payments (no real gateway).
**Try it:** admin -> Seating -> create/assign a layout to a match -> log in as a user -> open that match -> *Select seats*.
Test command (frontend) now also includes `seatmap.test.js` and `globals.test.js`.

---

## Phase 3A: stadium seating database, APIs and admin management (NEW)
Adds the seating foundation only: stadium layouts (stands > sections > rows > seats), seat categories and prices, per-match seat inventory, admin APIs and a
**Seating** tab in the existing Admin dashboard. There is **no seat-selection UI, no seat holding/locking and no seat booking yet** (Phase 3B/3C/3D). Existing
category-based ticket booking, payments, QR tickets, food ordering (5% food tax, separate bill) and the scanner are unchanged.
Full design, collections, indexes, API reference and the Phase 3B/3C locking design: **[docs/PHASE_3A_SEATING.md](docs/PHASE_3A_SEATING.md)**.

**Use it:** log in as admin -> Admin -> **Seating** -> pick a stadium -> *Create from sample* (or build your own) -> edit categories/prices, stands, sections, rows -> under
*Matches at this stadium* press **Assign layout** (creates that match's own seat inventory). Matches without a layout keep working exactly as before.
**Database:** no manual steps and no migration. Indexes (and best-effort MongoDB validators) are created automatically at startup. Optional env vars: `SEATING_MAX_SEATS_PER_LAYOUT`, `SEATING_MAX_SEATS_PER_ROW` (see `.env.example`).
**New backend files:** `app/seating.py` (service), `app/seating_schema.py` (MongoDB validators), `app/seating_schemas.py` (request models), `app/routes/seating.py` (public read), `app/routes/seating_admin.py` (admin), `tests/test_seating_service.py`, `tests/test_seating_api.py`.
**Modified backend files:** `app/database.py` (indexes, `next_ids`), `app/main.py` (routers, error handler, validators), `app/config.py`, `app/pricing.py` (`seat_quote`), `app/routes/admin.py` (match delete also removes seat inventory).
**Frontend:** new `seating.js`, `tests/seating.test.js`; modified `app.js` (one tab entry + one route line), `index.html` (script tag), `styles.css` (appended block).

### Run locally on Windows (PowerShell)
```powershell
# 1) Backend
cd book-my-match\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy ..\.env.example .env          # then edit .env: MONGODB_URL (Atlas), DATABASE_NAME, SECRET_KEY (long random string)
# first time only, on an EMPTY database:  python -m app.init_db   then   $env:ADMIN_EMAIL="you@example.com"; $env:ADMIN_PASSWORD="StrongPass123"; python -m app.create_admin
# existing database: do NOT run init_db (see the migration section below)
uvicorn app.main:app --reload
# 2) Open http://127.0.0.1:8000  (the backend also serves the frontend; API docs at http://127.0.0.1:8000/docs)
```
Alternatively serve `frontend` separately (for example `python -m http.server 5500` inside `frontend`); the frontend talks to `http://127.0.0.1:8000/api`.

### Run the tests
```powershell
cd book-my-match\backend
pytest                                   # all backend tests (in-memory mongomock; no Atlas needed)
cd ..
node --test frontend/tests/food-cart.test.js frontend/tests/ui-art.test.js frontend/tests/discover.test.js frontend/tests/seating.test.js frontend/tests/seatmap.test.js frontend/tests/globals.test.js   # Node 18+
```

---

## Honest status
Written in a sandbox with **no network, no MongoDB, and without FastAPI/pydantic/PyMongo installed**, so the real HTTP stack and a real
MongoDB were **never run**. What *was* run for real, with a small sandbox-only stand-in for PyMongo/FastAPI (not shipped):
the ticketing/payment/verification code (28 tests, incl. forced race conditions and a mutation check that deliberately broke 7 safeguards),
the real route handlers through the whole journey (60 checks), and **your real frontend in headless Chromium** driving that journey
end to end (51 checks: register, book, pay, fail, retry, QR tickets, PDF download, QR decode, staff scan, admin views; also in
India and New York browser timezones). PDF QR codes were rasterised and decoded with OpenCV. **Not run:** real MongoDB/Atlas, real FastAPI
request validation, `pytest` itself (`tests/test_tickets_api.py` was written but never executed), live camera scanning, Render/Vercel.
Run `pytest` before deploying (section 7).

## New: demo payment -> QR tickets -> gate entry
```
select match -> select tickets -> POST /api/bookings   (tickets HELD, status pending_payment, expires in RESERVATION_MINUTES)
  -> payment page -> POST /api/payments/pay            (DEMO: no real money)
       success -> booking CONFIRMED + payment SUCCESS + transaction id -> ONE ticket per purchased ticket, each with its own QR token
       failure -> booking stays held, no tickets, tickets not lost; retry or cancel      expiry -> tickets released automatically
  -> My Tickets / digital ticket / PDF download (one page per ticket)
  -> staff scans QR -> POST /api/staff/verify -> MongoDB atomically flips the ticket valid -> used (once only) -> entry logged
```
**Rules worth knowing**
- Payment is simulated. `PayIn` accepts only `booking_id`, `method` (upi, debit_card, credit_card, net_banking) and an optional `simulate`; any extra
  field (card number, CVV, UPI PIN...) is rejected with 422. `DEMO_PAYMENT_MODE`: `selectable` (the payment screen has a success/failure switch),
  `success`, `failure`, or `random` (`DEMO_PAYMENT_SUCCESS_RATE`); in the last three the server decides and ignores the browser. `PAYMENTS_DEMO_ENABLED=false` switches it off.
- The QR contains only `BMMT:<32-char token>`. MongoDB stores only the SHA-256 of the token. The token is re-derived on demand from
  `HMAC(TICKET_SIGNING_KEY, ticket_id:nonce)`, so users can re-open their ticket without any clear-text token in the database.
  **Keep `TICKET_SIGNING_KEY` (default: `SECRET_KEY`) unchanged, or already-issued QR codes stop verifying.** Anyone who sees a ticket ID alone cannot enter: only the token is accepted.
- Tickets are category-based (open seating): `seat_number` is `null`. There are no individual seats in this app.
- Roles: `user`, `staff` (scanner only), `admin` (everything). Grant staff in Admin -> Users, or `PUT /api/admin/users/{id}` `{"role":"staff"}`. Registration can never create staff/admin.
- Cancelling a paid booking cancels its tickets first (QR stops working) and records a *demo* refund; it is refused once any ticket was used or inside `CANCEL_CUTOFF_HOURS` of the match.
  Cancelling a match cancels its unused tickets too. Edge: a scan that lands in the same instant a user cancels can still admit that one ticket.
- Time: all server times are venue-local wall-clock in `APP_TIMEZONE` (default `Asia/Kolkata`), so the cancellation cutoff and expiry are correct on a UTC server such as Render. The
  payment countdown uses `expires_in_seconds` from the server, not the browser clock.
- Optional `ENFORCE_ENTRY_DATE=true` rejects scans on days other than the match date (default off so demos work any day).
- The gate's match drop-down lists scheduled matches from yesterday to 14 days ahead.

## MongoDB changes in this version
New collections (indexes are created automatically at startup by `ensure_indexes`; nothing to run):
| Collection | Purpose | Indexes |
|---|---|---|
| `payments` | one document per payment attempt: `transaction_id`, `booking_id`, `user_id`, `method`, `amount` (Decimal128), `status` (successful/failed), `failure_reason`, `created_at`, `completed_at` | unique `transaction_id`; `(booking_id, created_at)`; `user_id` |
| `tickets` | one per ticket: `ticket_id`, `booking_id`, `booking_reference`, `user_id`, `match_id`, `stadium_id`, `category_id`, `ticket_category`, `seat_number`, `seq`, `payment_id`, `payment_status`, `ticket_status` (valid/used/cancelled), `token_nonce`, `verification_token_hash`, `created_at`, `used_at`, `verified_by`, `verified_by_name`, `cancelled_at` | unique `ticket_id`; unique `verification_token_hash`; **unique `(booking_id, seq)`** (makes duplicate issuing impossible); `(match_id, ticket_status)`; `(user_id, created_at)`; `booking_reference` |
| `entry_logs` | every scan attempt (success or not): `result`, `scanned_at`, `scanned_by`, `scanned_by_name`, `ticket_id`, `booking_reference`, `match_id`, `expected_match_id`, `token_fingerprint` (never the token) | `scanned_at`; `(match_id, scanned_at)`; `ticket_id` |
Changed: `bookings` gain `status` values `pending_payment` / `expired` (besides `confirmed` / `cancelled`) and fields `expires_at`, `payment_method`, `paid_at`, `paid_amount`, `expired_at`; new index `(status, expires_at)`.
**Atomicity:** payment = `update_one({_id, status:"pending_payment", expires_at:{$gt:now}}, {$set: confirmed...})` (only one request can win); entry =
`find_one_and_update({_id, ticket_status:"valid", payment_status:"successful"}, {$set: used...})` (only one scanner can win); expiry releases inventory only if its own conditional update won.
No SQLAlchemy or Alembic anywhere.

## API (all under `/api`; JSON; `Authorization: Bearer <token>`)
| Method & path | Who | Notes |
|---|---|---|
| `GET /payments/config` | public | demo mode, methods, reservation minutes |
| `POST /payments/pay` | owner | `{booking_id, method, simulate?}` -> `{outcome: success\|failed\|already_paid, payment, booking}` (HTTP 200; 404 not yours; 409 not payable; 410 reservation expired) |
| `GET /payments`, `GET /payments/{transaction_id}` | owner | receipts |
| `GET /tickets?scope=all\|upcoming\|completed` | owner | list (no QR) |
| `GET /tickets/{ticket_id}` | owner | full ticket with `qr_image` (SVG data URI), `qr_payload`, `backup_code` |
| `GET /tickets/{ticket_id}/pdf` | owner | one-page PDF |
| `GET /bookings/{id}/tickets`, `GET /bookings/{id}/tickets/pdf` | owner | all tickets / one PDF, one page per ticket |
| `POST /staff/verify` | staff, admin | `{token, expected_match_id?}` -> always 200 with `result`: `success`, `already_used`, `invalid`, `cancelled`, `unpaid`, `wrong_match`, `not_match_day` |
| `GET /staff/matches`, `GET /staff/recent-scans` | staff, admin | gate configuration, own recent scans |
| `GET /admin/tickets?q=&match_id=&status=valid\|used\|cancelled` | admin | search by ticket ID or booking reference; per-match; unused/used/cancelled lists |
| `GET /admin/tickets/{ticket_id}` | admin | ticket + entry history (no token) |
| `GET /admin/entry-logs?match_id=&result=&ticket_id=` | admin | entry verification history |
| `GET /admin/ticket-stats?match_id=` | admin | totals + per-match: issued, sold, valid, used, cancelled, remaining_for_sale, held_unpaid, attendance % |
| `GET /admin/payments` | admin | recent payments |
**Removed:** `POST /bookings/{id}/pay` (the old one-click demo pay) - it bypassed the payment page and ticket issuing. Interactive docs: `/docs`.
**Changed:** `POST /bookings` now creates a *held* booking (`pending_payment`), not a confirmed one.

## Upgrade steps for your existing project
1. Copy the files over your project (see the file list below). New env vars are all optional (section "Environment").
2. `pip install -r requirements-dev.txt` (adds `tzdata`; no other new packages: QR codes use ReportLab, already required).
3. Start the app once: the new indexes are created automatically. (Do not run `init_db` on an existing database.)
4. **Run `python -m app.backfill_tickets --dry-run`, then without `--dry-run`.** Your existing paid booking(s) get a payment record and QR tickets; old
   "confirmed but never paid" bookings become `pending_payment` with a fresh hold, so they must be paid via the new page or they expire.
5. Create gate staff: register an account, then Admin -> Users -> role `staff`.
6. Frontend: `index.html` now loads `tickets.js` **before** `app.js` (order matters). `app.js` has ~26 changed lines. Its API base is still the
   hard-coded `http://127.0.0.1:8000/api`; for Vercel point it at your Render URL.

## Environment (all optional; defaults shown; see `.env.example`)
`RESERVATION_MINUTES=10`, `PAYMENTS_DEMO_ENABLED=true`, `DEMO_PAYMENT_MODE=selectable`, `DEMO_PAYMENT_SUCCESS_RATE=0.8`,
`TICKET_SIGNING_KEY=` (defaults to `SECRET_KEY`), `ENFORCE_ENTRY_DATE=false`, `APP_TIMEZONE=Asia/Kolkata`.
On Render nothing new is required; consider setting `TICKET_SIGNING_KEY` explicitly so it survives a future `SECRET_KEY` rotation.

## Using the scanner
Log in as staff/admin -> **Scanner**. Choose the match for this gate (optional), press *Start camera*, allow camera access, point at the ticket QR.
The result appears immediately (green ENTRY ALLOWED / amber ALREADY USED / red INVALID, CANCELLED, NOT PAID, WRONG MATCH). Manual entry accepts the
*backup code* printed under each QR. Camera access requires **HTTPS** (Vercel is fine) or localhost. The scanning library (`html5-qrcode@2.3.8`) loads from unpkg when the page opens;
if it cannot load, manual entry still works. Live camera scanning has **not** been tested.

## Files changed in this version (vs. the previous MongoDB delivery)
**Created:** `backend/app/backfill_tickets.py`, `backend/app/clock.py`, `backend/app/qr.py`, `backend/app/routes/admin_tickets.py`, `backend/app/routes/payments.py`, `backend/app/routes/staff.py`, `backend/app/routes/tickets.py`, `backend/app/ticket_pdf.py`, `backend/app/ticketing.py`, `backend/tests/test_ticketing_service.py`, `backend/tests/test_tickets_api.py`, `frontend/tickets.js`

**Modified:** `env.example`, `README.md`, `backend/app/config.py`, `backend/app/create_admin.py`, `backend/app/database.py`, `backend/app/deps.py`, `backend/app/main.py`, `backend/app/routes/admin.py`, `backend/app/routes/auth.py`, `backend/app/routes/bookings.py`, `backend/app/routes/catalog.py`, `backend/app/routes/food.py`, `backend/app/schemas.py`, `backend/app/services.py`, `backend/requirements.txt`, `backend/tests/conftest.py`, `backend/tests/test_api.py`, `frontend/app.js`, `frontend/index.html`, `frontend/styles.css`

**Removed:** none (the old `POST /api/bookings/{id}/pay` endpoint was deleted from `routes/bookings.py`).

## 1. Original architecture (what I found)
11 SQL tables: `users, sports, stadiums, matches, ticket_categories, ticket_inventory, food_categories, food_items, bookings,
food_orders, food_order_items`, integer primary keys, foreign keys, CHECK/UNIQUE constraints, Alembic "create_all" migration.
Overselling was prevented by an atomic `UPDATE ticket_inventory ... WHERE available >= qty`; duplicate requests by a unique
`(user_id, idempotency_key)` constraint. Your SQLite file held 1 user, 1 booking, 2 standalone food orders, 85 matches, 300 inventory rows.

## 2. MongoDB design
| Collection | Notes |
|---|---|
| `users` | unique `email`; bcrypt `password_hash` copied as-is, so existing logins keep working |
| `sports`, `stadiums`, `matches` | `matches.match_date` = `"YYYY-MM-DD"`, `start_time` = `"HH:MM:SS"` strings (no date-only type in Mongo; sorts correctly) |
| `ticket_categories` | per-stadium prices (`Decimal128`), unique `(stadium_id, name)` |
| `ticket_inventory` | one document per (match, category): `total`, `available`; unique `(match_id, category_id)`. **Kept separate on purpose** so a single atomic conditional `$inc` guards each category |
| `bookings` | unique `reference`; money fields `Decimal128` |
| `food_categories`, `food_items` | unchanged shape |
| `food_orders` | **items embedded** (name/price snapshot), replaces `food_order_items` |
| `idempotency_keys` | `_id = "<user_id>:<key>"` -> `booking_id`; TTL 7 days (replaces the SQL unique constraint) |
| `counters` | atomic integer id generator |
| `migration_log` | marker written by the migration script |

**Decision: integer `_id`s instead of ObjectIds.** The unchanged frontend converts ids with `+id` and builds URLs like `#/match/12`;
ObjectId strings would break it and change every API response. Every document therefore keeps the id it had in SQL, which
also preserves all relationships exactly. New ids come from `counters`.

**Overbooking / duplicates (no multi-document transactions needed):**
- Reserve: `update_one({_id, available: {$gte: qty}}, {$inc: {available: -qty}})` is atomic; if it matches nothing -> HTTP 409.
- Then insert the booking (+food order). On any failure the code puts the tickets back and removes partial records.
- Cancel: `update_one({_id, status: "confirmed"}, ...)` first, so a double click cannot release tickets twice.
- Admin inventory edit: compare-and-set loop, so it cannot clobber a concurrent booking.
- Idempotency: a claim document is inserted first; a replay returns the original booking with HTTP 200.
- Known limit: if the server process is killed between "reserve" and "insert booking", those tickets stay reserved until an admin fixes the inventory. A replica-set transaction would close that gap; Atlas supports it if you want it later.

## 3. Files
**Modified:** `backend/app/{config,database,deps,services,seed,init_db,create_admin,main}.py`, `backend/app/routes/{auth,catalog,bookings,food,admin}.py`,
`backend/tests/{conftest,test_api}.py`, `backend/requirements.txt`, `.env.example`, `.gitignore`, `README.md`.
**Created:** `backend/app/migrate_to_mongo.py`, `backend/app/models_util.py` (booking reference generator), `backend/tests/test_migration.py`,
`backend/requirements-dev.txt`, `backend/requirements-migrate.txt`.
**Removed (no longer used):** `backend/app/models.py` (SQLAlchemy models; replaced by the collection design above),
`backend/alembic.ini`, `backend/migrations/` (Alembic has no role with MongoDB; indexes are created by `ensure_indexes`).
**Untouched:** all of `frontend/`, `backend/app/{schemas,security,pricing}.py`, `database/seed_data.json`. Your `backend/.env` and `book_my_match.db` were not copied or modified.

**Two behaviour fixes beyond the DB change**
- `main.py` only allowed `localhost:5500`, so a Vercel frontend would be blocked by CORS and `CORS_ORIGINS` was ignored. It now uses `CORS_ORIGINS` (still allowing your localhost:5500 origins).
- `requirements.txt` no longer lists sqlalchemy, pymysql, cryptography, alembic, psycopg (psycopg moved to `requirements-migrate.txt`, needed only to read a Postgres source).

**One frontend line you must change (I did not touch the frontend):** `frontend/app.js` line 2 hardcodes
`http://127.0.0.1:8000/api`. For the Vercel build it must point at Render, e.g. `https://<your-service>.onrender.com/api`
(or define `window.BMM_API_BASE` before the script).

## 4. Create the Atlas database
1. cloud.mongodb.com -> create a free **M0** cluster.
2. *Database Access* -> add a user with a strong password (letters+digits avoids URL-encoding problems; otherwise URL-encode it).
3. *Network Access* -> allow Render. Simplest: `0.0.0.0/0` (protected by the password); stricter: add the outbound IPs shown in your Render service.
4. *Connect -> Drivers -> Python*: copy the `mongodb+srv://...` string. You do **not** create collections by hand; they appear on first write.

## 5. Local setup
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```
Edit `backend/.env`: **add** `MONGODB_URL` and `DATABASE_NAME=book_my_match`; keep your `SECRET_KEY`, pricing values and `CORS_ORIGINS`.
`DATABASE_URL` is no longer read by the app (leave it while migrating; the migration script uses it as the source).

## 6. Migrate your existing data
```bash
# 1) Safe preview: reads + converts + validates. Connects to NO database.
python -m app.migrate_to_mongo --dry-run
# 2) Real run (into an EMPTY Atlas database)
python -m app.migrate_to_mongo
# Re-check later
python -m app.migrate_to_mongo --verify-only
```
- Default source is `DATABASE_URL` from `.env` when it is sqlite/postgres, else `backend/book_my_match.db`. Override with `--source`.
- **Render PostgreSQL:** if your live data is there, run `pip install -r requirements-migrate.txt` and
  `python -m app.migrate_to_mongo --dry-run --source "postgresql://USER:PASS@HOST/DB"` (use the *External* URL; allow your IP in Render). Migrate **one** source into one Mongo database; if both SQLite and Postgres hold different data, decide which is authoritative first.
- Safe to repeat: insert-if-absent by id, never overwrites documents the app changed later. It refuses to import into a database that was already seeded by `init_db` (use `--force` only if you know why).
- The source is opened read-only and never modified.
- **Do not run `python -m app.init_db` on a database you migrate into.** `init_db` is only for starting empty with the sample data.
- Fresh start instead (no old data): `python -m app.init_db` then `ADMIN_EMAIL=... ADMIN_PASSWORD=... python -m app.create_admin`.

## 7. Test locally
```bash
pytest                                   # in-memory mongomock
TEST_MONGODB_URL="mongodb+srv://..." pytest   # optional: real MongoDB semantics, uses a throw-away bmm_test_* database
uvicorn app.main:app --reload            # then open http://127.0.0.1:8000/docs and http://127.0.0.1:8000/api/health/db
```
Manual check list: register -> login -> book 1 ticket -> pay (demo) -> My Bookings -> cancel -> availability returns; order food;
log in as admin and edit a match, a ticket price and a food item. Tell me any test that fails.

## 8. Render (backend)
Root directory `backend` - Build `pip install -r requirements.txt` - Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

| Variable | Action |
|---|---|
| `MONGODB_URL` | **Add** (Atlas SRV string) |
| `DATABASE_NAME` | **Add** `book_my_match` |
| `SECRET_KEY` | **Keep the existing value** (changing it logs everyone out) |
| `CORS_ORIGINS` | **Set** to your Vercel URL, no trailing slash, e.g. `https://book-my-match.vercel.app` |
| `CORS_ORIGIN_REGEX` | optional, for Vercel preview URLs |
| `DATABASE_URL` | **Remove** after the migration is verified (the app ignores it) |
| pricing / `ACCESS_TOKEN_EXPIRE_MINUTES` | keep as they are |

Then push to git (Render redeploys) or *Manual Deploy -> Deploy latest commit*. Check `https://<service>.onrender.com/api/health/db`
returns `{"status":"ok","database":"up"}`. If it says down: Atlas IP access list or a wrong `MONGODB_URL`.
Make sure the Vercel build uses the Render API URL (see the frontend line above).

## 9. Checklist: what has actually been verified
- [x] Migration read/convert/validate on a copy of your real SQLite DB (11 tables, 0 orphans); source file unchanged
- [x] Ticketing service: 28 tests (payment success/failure/retry/expiry, duplicate and simultaneous payments, one-ticket-per-seat, unique QR tokens, token never stored in clear, scans: valid/used/invalid/cancelled/unpaid/wrong match, forced races, stats, search, backfill)
- [x] Mutation check: 7 deliberately broken safeguards (non-atomic scan, double payment, double expiry release, tickets on failure, removed wrong-match check, clear-text token, missing unique index) were each caught by a test
- [x] Real route handlers, whole journey + authorization: 60 checks (JWT role claim is ignored; users cannot verify; owners only)
- [x] Real frontend in headless Chromium, whole journey: 51 checks, no JS errors, also with browser timezones India and New York; ticket fits a 390px phone
- [x] PDF tickets: one page per ticket, QR rasterised at 100/150/200 dpi and decoded back to the ticket's token (OpenCV)
- [x] Backfill upgraded your real migrated booking (1 ticket, scans OK, re-run changes nothing)
- [ ] **Not run:** real MongoDB/Atlas, `pytest` (incl. `test_api.py`, `test_tickets_api.py`, `test_migration.py` import path), real FastAPI/pydantic validation, live camera scan, Render/Vercel deployment, behaviour of `mongomock` under threads

---

## Food section enhancement (menu, cart, bill, payment, confirmation)

Enhances the existing food feature; no new collections, no migration. Existing `food_categories`, `food_items` and `food_orders`
documents keep working unchanged (new fields are optional: `description`, `image_url` on items; `fee`, `payment_ref`,
`payment_method`, `paid_at`, `paid_amount` on orders).

**Behaviour**
- Menu: search, category chips, cards with image (or emoji tile when an item has no `image_url`), description, price, Add-to-cart / quantity stepper.
- Cart (`localStorage` key `bmm_cart`, same shape as before): +/−, remove, item-count badge, unavailable-item handling, server-priced subtotal / fee / tax / total via `POST /api/food/quote`.
- Food during ticket booking: tick "Include my food cart" on the match page; one payment covers tickets + food (unchanged mechanism, now itemised).
- Food after booking / standalone: `Place order & pay` on the Food page. Orders on an **unpaid** booking are paid with the tickets; orders on a **paid** booking or standalone are paid on their own.
- Payment reuses `POST /api/payments/pay` (now accepts `booking_id` **or** `food_order_id`) and the same `payments` collection. Still a **demo**: no real money, no card data accepted.
- Confirmation `#/food/order/<id>`: order ID (`FO000123`), itemised bill, payment status, transaction ID, pickup details, pickup code + QR, PDF receipt.
- Duplicate-charge protection: atomic conditional update on the order (same pattern as bookings) + `Idempotency-Key` on order creation.

**New / changed API**: `POST /food/quote`, `GET /food/orders/{id}`, `POST /food/orders/{id}/cancel`, `GET /food/orders/{id}/receipt`,
`POST /food/orders` (+`Idempotency-Key`), `POST /payments/pay` (+`food_order_id`), `/bookings/quote` (+`food_lines`). Item responses gain `description`, `image_url`.

**Config**: `FOOD_FEE_PERCENT` (default `0`, so food prices are unchanged). Fee applies to the food subtotal, tax to subtotal + fee, as for tickets.

**Existing database**: run once `python -m app.backfill_food --dry-run`, then `python -m app.backfill_food` to add descriptions to existing items (never overwrites). Set photos per item in Admin > Food (Image URL).

**Tests**: `cd backend && pytest` (adds `test_food_service.py`, `test_food_api.py`); `node --test frontend/tests/food-cart.test.js`.
**Files**: backend `food_service.py`, `food_receipt.py`, `backfill_food.py` (new); `routes/{food,payments,bookings,admin}.py`, `services.py`, `ticketing.py`, `schemas.py`, `pricing.py`, `config.py`, `database.py`, `seed.py` (modified). Frontend `food.js` (new), `app.js`, `tickets.js`, `index.html`, `styles.css` (modified).

---

## Phase 1 (UI/UX foundation) and Phase 2 (match discovery)

**Phase 1 (frontend only, no database impact):** new design system (tokens, sticky glass nav with logo and user avatar, hero, card styles, dark/light),
skeleton loaders, empty and error states with "Try again", confirm dialogs with titles and danger styling, dismissible toasts (`role=alert` for errors),
inline form validation on login/register (mirrors the server rules; the server stays authoritative), skip link, focus rings, keyboard-activatable cards,
reduced-motion support, responsive layouts. No image files are needed: teams get SVG initial badges and every sport has SVG stadium art; a real
`image_url` (stadiums: Admin > Stadiums, food: Admin > Food) is shown over the art and falls back to it if it fails to load.
Navigation now cancels the previous page's pending GET requests so a slow old page can never overwrite the page you moved to.
Food is paid separately by default; paying a food cart together with tickets is an explicit opt-in checkbox on the match page.
`frontend/art.js`, `ui.js`, `discover.js` are new; `app.js`, `tickets.js`, `food.js`, `index.html`, `styles.css` changed.

**Phase 2 (match discovery):**
- `GET /api/matches/discover` (public, read-only): `q, sport_id, location (city), date_from, date_to, tournament, teams (comma list), min_price, max_price, sort (date|date_desc|price_asc|price_desc|popular), include_past, limit (1-100), offset` -> `{items, total, limit, offset, has_more}`. A match fits a price range when at least one of its ticket categories is inside it. `GET /api/matches` is unchanged.
- `GET /api/matches/filters`: sports, cities, tournaments, teams and price bounds taken from bookable matches.
- Favorites (login required, always scoped to the token's user): `GET /api/favorites`, `PUT|DELETE /api/favorites/matches/{id}`, `PUT|DELETE /api/favorites/teams?name=`.
- Matches page: search, sport/city/tournament/date/price filters, sorting, removable filter chips, "Load more", shareable URLs, favorites tab; hearts on cards and follow-team buttons on the match page.

**Database changes (Phase 2), nothing existing is modified or migrated:**
1. New collection `favorites` (created empty when the backend starts and creates its index `(user_id, kind)`); documents have string ids like `12:team:csk` so duplicates are impossible.
2. `matches.tournament` is an OPTIONAL field. Nothing backfills it; it is only written when an admin sets it on a match (Admin > Matches). Existing matches have none.
Also: stadium `image_url` values are now validated on write (http(s):// or /), existing stored values are untouched.

**Dev-only demo data:** `backend/dev/dev_demo_data.py` adds demo tournament names to a throwaway dev database. It refuses unless `DATABASE_NAME` ends in `_dev`, `BMM_DEV_CONFIRM` is set, the URL is not Atlas (unless also `BMM_DEV_ALLOW_ATLAS=yes`) and the database already has matches. See the file header.

**Tests:** `cd backend && pytest` (new: `test_discovery.py`, `test_discovery_api.py`, `test_dev_demo_data.py`, plus the food tests); `node --test frontend/tests/food-cart.test.js frontend/tests/ui-art.test.js frontend/tests/discover.test.js`.
