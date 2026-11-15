from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError
from ..clock import now as clock_now
from ..database import get_db, next_id
from ..deps import get_current_user
from ..schemas import RegisterIn, LoginIn, ProfileIn
from ..security import hash_password, verify_password, create_token

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(u: dict):
    return {"id": u["_id"], "name": u["name"], "email": u["email"], "phone": u.get("phone"), "role": u["role"]}


@router.post("/register", status_code=201)
def register(body: RegisterIn, db=Depends(get_db)):
    email = body.email.lower()
    if db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(409, "An account with this email already exists")
    user = {"_id": next_id(db, "users"), "name": body.name.strip(), "email": email, "phone": body.phone,
            "password_hash": hash_password(body.password), "role": "user", "is_active": True, "created_at": clock_now()}
    try:
        db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(409, "An account with this email already exists")
    return {"token": create_token(user["_id"], user["role"]), "user": user_out(user)}


@router.post("/login")
def login(body: LoginIn, db=Depends(get_db)):
    user = db.users.find_one({"email": body.email.lower()})
    if not user or not user.get("is_active") or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(user["_id"], user["role"]), "user": user_out(user)}


@router.post("/logout")
def logout(user: dict = Depends(get_current_user)):
    # Tokens are stateless; the client discards its token. (No server-side revocation list.)
    return {"message": "Logged out"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user_out(user)


@router.put("/me")
def update_me(body: ProfileIn, user: dict = Depends(get_current_user), db=Depends(get_db)):
    upd = {"name": body.name.strip(), "phone": body.phone or None}
    db.users.update_one({"_id": user["_id"]}, {"$set": upd})
    user.update(upd)
    return user_out(user)
