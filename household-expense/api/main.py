"""FastAPI application — CORS, static files, routers, startup."""

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.auth import load_api_key, login, logout
from api.db import DB_PATH, init_db
from api.models import LoginRequest, LoginResponse
from api.routers import cash_pools, categories, export, transactions
from api.seed import seed_all

app = FastAPI(title="Household Expense API", version="1.0.0")

# ── CORS (LAN only — no wildcard) ────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://192.168.1.44:8088", "http://ds920plus:8088"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup ───────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    seed_all()
    key = load_api_key()
    if key:
        print(f"[startup] API key loaded ({len(key)} chars)")
    else:
        print("[startup] WARNING: No API key file found — export/reconcile endpoints will reject requests")


# ── Health (unauthenticated) ──────────────────────────────────────────
@app.get("/api/household/health")
def health():
    return {"status": "ok"}


# ── Auth endpoints ────────────────────────────────────────────────────
@app.post("/api/household/auth/login", response_model=LoginResponse)
def auth_login(body: LoginRequest, response: Response):
    user = login(body.username, body.password, response)
    return LoginResponse(username=user["username"], display_name=user["display_name"])


@app.post("/api/household/auth/logout")
def auth_logout(request: Request, response: Response):
    logout(request, response)
    return {"ok": True}


# ── Routers ───────────────────────────────────────────────────────────
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(cash_pools.router)
app.include_router(export.router)


# ── Static PWA files (must be last — catches all remaining routes) ────
try:
    app.mount("/", StaticFiles(directory="dist", html=True), name="pwa")
except Exception:
    print("[startup] No dist/ directory found — PWA not served")
