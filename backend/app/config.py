import os
from decimal import Decimal

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv optional
    pass


def _csv(name: str, default: str):
    return [o.strip().rstrip("/") for o in os.environ.get(name, default).split(",") if o.strip()]


class Settings:
    def __init__(self):
        self.MONGODB_URL = os.environ.get("MONGODB_URL", "")
        self.DATABASE_NAME = os.environ.get("DATABASE_NAME", "book_my_match")
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "")
        if not self.MONGODB_URL:
            raise RuntimeError("MONGODB_URL is not set (see .env.example)")
        if len(self.SECRET_KEY) < 16:
            raise RuntimeError("SECRET_KEY must be set to a long random value (see .env.example)")
        self.TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
        # Browser origins allowed to call the API. Add your Vercel URL here on Render.
        self.CORS_ORIGINS = _csv("CORS_ORIGINS", "http://localhost:8000")
        # Optional regex, e.g. https://book-my-match.*\.vercel\.app to also allow Vercel preview deployments.
        self.CORS_ORIGIN_REGEX = os.environ.get("CORS_ORIGIN_REGEX", "").strip() or None
        self.FEE_PERCENT = Decimal(os.environ.get("FEE_PERCENT", "2"))
        self.TICKET_TAX_PERCENT = Decimal(os.environ.get("TICKET_TAX_PERCENT", "18"))
        self.FOOD_TAX_PERCENT = Decimal(os.environ.get("FOOD_TAX_PERCENT", "5"))
        # Optional food service fee (percent of the food subtotal). 0 by default so existing food prices are unchanged.
        self.FOOD_FEE_PERCENT = Decimal(os.environ.get("FOOD_FEE_PERCENT", "0"))
        self.MAX_TICKETS = int(os.environ.get("MAX_TICKETS_PER_BOOKING", "10"))
        self.CANCEL_CUTOFF_HOURS = int(os.environ.get("CANCEL_CUTOFF_HOURS", "24"))
        # Wall-clock timezone of the venues. Match times are stored as local times, so "now" must be local too.
        self.APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
        # --- demo payments / tickets ---
        self.RESERVATION_MINUTES = int(os.environ.get("RESERVATION_MINUTES", "10"))  # how long unpaid tickets are held
        self.PAYMENTS_DEMO_ENABLED = os.environ.get("PAYMENTS_DEMO_ENABLED", "true").strip().lower() in ("1", "true", "yes")
        # selectable = the demo screen lets you pick success/failure; success | failure | random = server decides
        self.DEMO_PAYMENT_MODE = os.environ.get("DEMO_PAYMENT_MODE", "selectable").strip().lower()
        if self.DEMO_PAYMENT_MODE not in ("selectable", "success", "failure", "random"):
            raise RuntimeError("DEMO_PAYMENT_MODE must be selectable, success, failure or random")
        self.DEMO_PAYMENT_SUCCESS_RATE = float(os.environ.get("DEMO_PAYMENT_SUCCESS_RATE", "0.8"))
        # Secret used to derive each ticket's QR token. Keep it stable: changing it makes already-issued QR codes unverifiable.
        self.TICKET_SIGNING_KEY = os.environ.get("TICKET_SIGNING_KEY", "").strip() or self.SECRET_KEY
        # --- Phase 3A seating limits (protect a free-tier Atlas cluster from accidental huge layouts) ---
        self.SEATING_MAX_SEATS_PER_LAYOUT = int(os.environ.get("SEATING_MAX_SEATS_PER_LAYOUT", "60000"))
        self.SEATING_MAX_SEATS_PER_ROW = int(os.environ.get("SEATING_MAX_SEATS_PER_ROW", "500"))
        self.ENFORCE_ENTRY_DATE = os.environ.get("ENFORCE_ENTRY_DATE", "false").strip().lower() in ("1", "true", "yes")


settings = Settings()
