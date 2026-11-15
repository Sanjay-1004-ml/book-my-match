"""One clock for the whole app: naive wall-clock time in APP_TIMEZONE (default Asia/Kolkata).
Match dates/times are venue-local, so comparing them with the server's UTC clock (Render) would be off by hours."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .config import settings

try:
    _TZ = ZoneInfo(settings.APP_TIMEZONE)
except ZoneInfoNotFoundError:
    raise RuntimeError(f"APP_TIMEZONE '{settings.APP_TIMEZONE}' is not a valid timezone (e.g. Asia/Kolkata). On Windows also: pip install tzdata")


def now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)
