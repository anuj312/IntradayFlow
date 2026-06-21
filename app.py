# app.py (REDIRECT LOGIN + SERVER-SIDE COOKIE PROTECTION)
# Now supports:
#   - allowed_numbers.txt for valid usernames (one per line)
#   - One active session per number
#   - If already logged in, new login attempt is REJECTED (not killed)

import os
import time
import base64
import hmac
import hashlib
import threading
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
LOGIN_PASS = os.getenv("APP_LOGIN_PASS", "Momentum@123#")

AUTH_COOKIE_NAME = os.getenv("APP_AUTH_COOKIE", "tt_auth")
AUTH_TTL_SEC = int(os.getenv("APP_AUTH_TTL_SEC", "43200"))  # 12 hours
AUTH_SECRET = os.getenv("APP_AUTH_SECRET", "").strip()

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")

if not AUTH_SECRET:
    raise RuntimeError("Missing APP_AUTH_SECRET (set a long random string).")

# Path to allowed numbers file
NUMBERS_FILE = Path(__file__).resolve().parent / "allowed_numbers.txt"


# =============================================================================
# ALLOWED NUMBERS LOADER
# =============================================================================
def load_allowed_numbers() -> set:
    """Read allowed_numbers.txt and return a set of valid numbers."""
    if not NUMBERS_FILE.exists():
        raise RuntimeError(f"Missing allowed_numbers.txt at {NUMBERS_FILE}")
    numbers = set()
    for line in NUMBERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and line.isdigit():
            numbers.add(line)
    if not numbers:
        raise RuntimeError("allowed_numbers.txt is empty or has no valid numbers.")
    return numbers


# =============================================================================
# ACTIVE SESSION STORE
# One token per number — new login is BLOCKED if already active
# =============================================================================
_session_lock = threading.Lock()
# { number: { "token": str, "exp": int } }
_active_sessions: dict[str, dict] = {}


def _cleanup_expired():
    """Remove expired sessions so numbers can login again after expiry."""
    now = int(time.time())
    expired = [n for n, s in _active_sessions.items() if s["exp"] < now]
    for n in expired:
        del _active_sessions[n]


def _is_number_already_logged_in(number: str) -> bool:
    """Check if this number has an active (non-expired) session."""
    with _session_lock:
        _cleanup_expired()
        return number in _active_sessions


def _register_session(number: str, token: str, exp: int):
    """Store the session for this number."""
    with _session_lock:
        _cleanup_expired()
        _active_sessions[number] = {"token": token, "exp": exp}


def _is_session_valid(number: str, token: str) -> bool:
    """Check if this token is the active one for this number."""
    with _session_lock:
        _cleanup_expired()
        session = _active_sessions.get(number)
        if not session:
            return False
        return session["token"] == token


def _remove_session(number: str):
    """Remove the session for this number (logout)."""
    with _session_lock:
        _active_sessions.pop(number, None)


# =============================================================================
# TOKEN HELPERS
# =============================================================================
def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _make_token(number: str, exp_epoch: int) -> str:
    payload = f"{number}|{exp_epoch}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(sig)}"


def _verify_token(token: str) -> tuple[bool, str]:
    """
    Returns (is_valid, number).
    Checks:
      1. HMAC signature
      2. Token not expired
      3. Number is still in allowed_numbers.txt
      4. This token is still the active session for that number
    """
    try:
        p64, s64 = token.split(".", 1)
        payload = _b64u_dec(p64)
        sig = _b64u_dec(s64)

        expected = hmac.new(
            AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(sig, expected):
            return False, ""

        number, exp_s = payload.decode("utf-8").split("|", 1)

        # Check expiry
        if int(exp_s) < int(time.time()):
            return False, ""

        # Check number still allowed
        allowed = load_allowed_numbers()
        if number not in allowed:
            return False, ""

        # Check this is the active session
        if not _is_session_valid(number, token):
            return False, ""

        return True, number

    except Exception:
        return False, ""


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
    if not tok:
        return False
    valid, _ = _verify_token(tok)
    return valid


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
    user: str      # this is the NUMBER from allowed_numbers.txt
    password: str


@app.get("/login")
def login_page(next: str = "/dash/"):
    if not LOGIN_HTML_PATH.exists():
        return JSONResponse({"error": "assets/login.html not found"}, status_code=404)
    return FileResponse(LOGIN_HTML_PATH, media_type="text/html")


@app.get("/auth/status")
def auth_status(request: Request):
    tok = request.cookies.get(AUTH_COOKIE_NAME)
    valid, number = _verify_token(tok) if tok else (False, "")
    return {"authed": valid, "number": number if valid else None}


@app.post("/auth/login")
def auth_login(payload: LoginIn):
    number = (payload.user or "").strip()
    pwd = payload.password or ""

    # Check password first
    if pwd != LOGIN_PASS:
        return JSONResponse({"detail": "Incorrect password"}, status_code=401)

    # Check number is in allowed list
    try:
        allowed = load_allowed_numbers()
    except RuntimeError as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

    if number not in allowed:
        return JSONResponse(
            {"detail": "This number is not authorized to login"},
            status_code=401,
        )

    # =========================================================================
    # CHECK: Is this number already logged in on another device?
    # =========================================================================
    if _is_number_already_logged_in(number):
        return JSONResponse(
            {
                "detail": (
                    f"Number {number} is already logged in on another device. "
                    "Please logout from the other device first, or wait for "
                    "the session to expire."
                )
            },
            status_code=409,  # 409 Conflict
        )

    # Create new token
    exp = int(time.time()) + AUTH_TTL_SEC
    tok = _make_token(number, exp)

    # Register session
    _register_session(number, tok, exp)

    resp = JSONResponse({
        "ok": True,
        "authed": True,
        "message": f"Logged in as {number}",
    })

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
def auth_logout(request: Request):
    tok = request.cookies.get(AUTH_COOKIE_NAME)
    if tok:
        valid, number = _verify_token(tok)
        if valid and number:
            _remove_session(number)

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
        if (
            path.startswith("/login")
            or path.startswith("/auth/")
            or path in ("/health", "/favicon.ico")
        ):
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
            resp = RedirectResponse(
                url="/login?next=" + quote(nxt), status_code=303
            )
            return await resp(scope, receive, send)

        return await self.app(scope, receive, send)


app.add_middleware(AuthRedirectMiddleware)


# =============================================================================
# Startup/shutdown
# =============================================================================
@app.on_event("startup")
async def _startup():
    try:
        nums = load_allowed_numbers()
        print(f"[auth] Loaded {len(nums)} allowed numbers from allowed_numbers.txt")
    except RuntimeError as e:
        raise RuntimeError(str(e))

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