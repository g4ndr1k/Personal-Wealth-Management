"""Authentication — session-based for PWA, API-key for Mac Mini."""

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext

from api.db import get_db

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE = "household_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days

# In-memory session store: { token: { user_id, username, display_name, expires_at } }
_sessions: dict[str, dict] = {}

API_KEY_FILE = os.environ.get("API_KEY_FILE", "/app/secrets/household_api.key")
_api_key: str | None = None


def load_api_key() -> str | None:
    global _api_key
    try:
        with open(API_KEY_FILE) as f:
            _api_key = f.read().strip()
    except FileNotFoundError:
        _api_key = None
    return _api_key


def verify_api_key(request: Request) -> bool:
    """Check X-Api-Key header against loaded key. Constant-time comparison."""
    if _api_key is None:
        return False
    provided = request.headers.get("X-Api-Key", "")
    if len(provided) != len(_api_key):
        return False
    return hmac.compare_digest(provided, _api_key)


def create_session(user: dict) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
    }
    return token


def get_session(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token not in _sessions:
        return None
    return _sessions[token]


def require_auth(request: Request) -> dict:
    """Dependency: valid session OR valid API key. Returns session dict or API-key marker."""
    session = get_session(request)
    if session:
        return session
    if verify_api_key(request):
        return {"username": "api-key", "display_name": "Mac Mini"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesi habis, silakan masuk kembali",
    )


def login(username: str, password: str, response: Response) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, password_hash, is_active FROM app_users WHERE username = ?",
            (username,),
        ).fetchone()

    if not row or not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")

    if not pwd_ctx.verify(password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")

    user = {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}
    token = create_session(user)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return user


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie(SESSION_COOKIE)
