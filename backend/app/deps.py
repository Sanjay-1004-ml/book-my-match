from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .database import get_db
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer), db=Depends(get_db)) -> dict:
    err = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if not creds:
        raise err
    payload = decode_token(creds.credentials)
    if not payload:
        raise err
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        raise err
    user = db.users.find_one({"_id": uid})
    if not user or not user.get("is_active", False):
        raise err
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    # role is read from the database, never from the token, so demotion takes effect immediately
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_staff(user: dict = Depends(get_current_user)) -> dict:
    """Gate/entry verification: stadium staff or administrators. Normal users can never mark a ticket as used."""
    if user.get("role") not in ("staff", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff access required")
    return user
