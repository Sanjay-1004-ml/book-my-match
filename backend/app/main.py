import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from .database import get_db, ensure_indexes
from .routes import auth, catalog, bookings, food, admin, payments, tickets, staff, admin_tickets, favorites, seating, seating_admin, seat_booking
from .routes import hybrid_bookings
from .seating import SeatingError, sweep_expired_holds
from .seating_schema import ensure_seating_validators
from . import ticketing

log = logging.getLogger("bookmymatch")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_indexes()

        # Initialise hybrid SQLite database
        from .init_sqlite import init_sqlite
        init_sqlite()

        ensure_seating_validators()

    except Exception:
        log.exception(
            "Could not reach MongoDB at startup; check MONGODB_URL and the Atlas IP access list"
        )

    sweeper = asyncio.create_task(_release_expired_holds())
    yield
    sweeper.cancel()

async def _release_expired_holds():
    """Every minute, give back tickets held by unpaid reservations that ran out of time (also done lazily on bookings)."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(lambda: ticketing.expire_stale(get_db(), limit=200))
            await asyncio.to_thread(lambda: sweep_expired_holds(get_db()))   # also frees seats of holds orphaned by a crash
        except Exception:
            log.exception("Expiry sweep failed")


app = FastAPI(title="BOOK MY MATCH API", version="2.0.0", lifespan=lifespan)
# Local dev origins you already used, plus whatever is configured in CORS_ORIGINS (put your Vercel URL there).
_origins = list(dict.fromkeys(["http://localhost:5500", "http://127.0.0.1:5500", *settings.CORS_ORIGINS]))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SeatingError)
async def seating_error(request: Request, exc: SeatingError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error")  # details go to server logs only
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


for r in (auth.router, catalog.router, bookings.router, tickets.router, food.router, payments.router, staff.router,
          admin.router, admin_tickets.router, favorites.router, seating.router, seating_admin.router, seat_booking.router, hybrid_bookings.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/db")
def health_db():
    try:
        get_db().command("ping")
        return {"status": "ok", "database": "up"}
    except Exception:
        log.exception("MongoDB ping failed")
        return JSONResponse({"status": "error", "database": "down"}, status_code=503)


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
