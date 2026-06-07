# app.py (REDIRECT LOGIN + SERVER-SIDE COOKIE PROTECTION)
#
# Uses a SIGNED HttpOnly COOKIE for auth.
# Redirects unauthenticated users from /dash/* and /openinterest/* to /login?next=...
#
# IMPORTANT (your setup):
# - Your big dashboard code must be renamed to: dashboard_impl.py
# - It must expose:
#     server = dash_app.server
#     import optioninterest as openinterest   (and openinterest.app exists)
# - You must have: assets/login.html
#
# Local run:
#   export KITE_API_KEY="..."
#   export KITE_ACCESS_TOKEN="..."
#   export APP_AUTH_SECRET="a-very-long-random-string"
#   export COOKIE_SECURE=0
#   uvicorn app:app --reload --workers 1 --port 8001
#
# Render (HTTPS):
#   Set env:
#     APP_LOGIN_USER=Momentum
#     APP_LOGIN_PASS=Allstate@123
#     APP_AUTH_SECRET=(long random string)
#     COOKIE_SECURE=1
#     KITE_API_KEY=...
#     KITE_ACCESS_TOKEN=...
#   Start:
#     uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1

import os
import time
import base64
import hmac
import hashlib
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from pydantic import BaseModel
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

import dashboard_impl as impl


# =============================================================================
# AUTH CONFIG
# =============================================================================
LOGIN_USER = os.getenv("APP_LOGIN_USER", "momentum")
LOGIN_PASS = os.getenv("APP_LOGIN_PASS", "Momentum@123#")

AUTH_COOKIE_NAME = os.getenv("APP_AUTH_COOKIE", "tt_auth")

# NOTE: even if we use a session cookie (no max_age), we still sign with an exp timestamp
# so a stolen cookie won't work forever within the same browser session.
AUTH_TTL_SEC = int(os.getenv("APP_AUTH_TTL_SEC", "43200"))  # 12 hours

AUTH_SECRET = os.getenv("APP_AUTH_SECRET", "").strip()

# Local (http): COOKIE_SECURE=0
# Render (https): COOKIE_SECURE=1
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")

if not AUTH_SECRET:
    raise RuntimeError("Missing APP_AUTH_SECRET (set a long random string).")


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _make_token(user: str, exp_epoch: int) -> str:
    payload = f"{user}|{exp_epoch}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(sig)}"


def _verify_token(token: str) -> bool:
    try:
        p64, s64 = token.split(".", 1)
        payload = _b64u_dec(p64)
        sig = _b64u_dec(s64)

        expected = hmac.new(AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False

        user, exp_s = payload.decode("utf-8").split("|", 1)
        if user != LOGIN_USER:
            return False

        return int(exp_s) >= int(time.time())
    except Exception:
        return False


def _cookies_from_scope(scope: Scope) -> dict:
    hdrs = dict(scope.get("headers") or [])
    raw = hdrs.get(b"cookie")
    if not raw:
        return {}
    c = SimpleCookie()
    c.load(raw.decode("utf-8", "ignore"))
    return {k: morsel.value for k, morsel in c.items()}


def _is_authed_scope(scope: Scope) -> bool:
    tok = _cookies_from_scope(scope).get(AUTH_COOKIE_NAME)
    return bool(tok and _verify_token(tok))


# =============================================================================
# FASTAPI APP
# =============================================================================
app = FastAPI(title="TurboTrades (Login Redirect)")

HERE = Path(__file__).resolve().parent
LOGIN_HTML_PATH = HERE / "assets" / "login.html"


# =============================================================================
# AUTH ROUTES
# =============================================================================
class LoginIn(BaseModel):
    user: str
    password: str


@app.get("/login")
def login_page(next: str = "/dash/"):
    # login.html reads ?next=... from window.location.search
    if not LOGIN_HTML_PATH.exists():
        return JSONResponse({"error": "assets/login.html not found"}, status_code=404)
    return FileResponse(LOGIN_HTML_PATH, media_type="text/html")


@app.get("/auth/status")
def auth_status(request: Request):
    tok = request.cookies.get(AUTH_COOKIE_NAME)
    return {"authed": bool(tok and _verify_token(tok))}


@app.post("/auth/login")
def auth_login(payload: LoginIn):
    user = (payload.user or "").strip()
    pwd = payload.password or ""

    if user != LOGIN_USER or pwd != LOGIN_PASS:
        return JSONResponse({"detail": "Incorrect user id and password"}, status_code=401)

    exp = int(time.time()) + AUTH_TTL_SEC
    tok = _make_token(user, exp)

    resp = JSONResponse({"ok": True, "authed": True})

    # SESSION COOKIE (logs out when browser is fully closed):
    # Do NOT set max_age or expires.
    resp.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=tok,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return resp


@app.get("/auth/logout")
def auth_logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return resp


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({}, status_code=204)


# =============================================================================
# REDIRECT MIDDLEWARE (protect /dash/* and /openinterest/*)
# =============================================================================
class AuthRedirectMiddleware:
    def __init__(self, app: ASGIApp, **kwargs):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = (scope.get("path") or "/").strip()

        # Public endpoints
        if path.startswith("/login") or path.startswith("/auth/") or path in ("/health", "/favicon.ico"):
            return await self.app(scope, receive, send)

        authed = _is_authed_scope(scope)

        # WebSocket protection
        if scope["type"] == "websocket":
            if path.startswith("/openinterest") and not authed:
                await send({"type": "websocket.close", "code": 4401})
                return
            return await self.app(scope, receive, send)

        # HTTP protection
        if (path.startswith("/dash") or path.startswith("/openinterest")) and not authed:
            nxt = path
            qs = scope.get("query_string", b"").decode("utf-8", "ignore")
            if qs:
                nxt += "?" + qs
            resp = RedirectResponse(url="/login?next=" + quote(nxt), status_code=303)
            return await resp(scope, receive, send)

        return await self.app(scope, receive, send)


app.add_middleware(AuthRedirectMiddleware)


# =============================================================================
# Startup/shutdown (delegate to dashboard_impl if it has hooks)
# =============================================================================
@app.on_event("startup")
async def _startup():
    if hasattr(impl, "_startup") and callable(impl._startup):
        await impl._startup()


@app.on_event("shutdown")
async def _shutdown():
    if hasattr(impl, "_shutdown") and callable(impl._shutdown):
        await impl._shutdown()


# =============================================================================
# Root redirect
# =============================================================================
@app.get("/")
def root():
    return RedirectResponse(url="/dash/", status_code=307)


# =============================================================================
# Mount apps
# =============================================================================
app.mount("/openinterest", impl.openinterest.app)
app.mount("/dash", WSGIMiddleware(impl.server))