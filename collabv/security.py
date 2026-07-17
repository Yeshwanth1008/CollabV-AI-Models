"""
CollabV AI - Security hardening primitives
============================================
Drops into api.py via `install_security(app)`. Provides:

  - Configurable CORS (ALLOWED_ORIGINS env var)
  - Security-header middleware (HSTS, X-Frame-Options, nosniff, XSS, CSP)
  - Server header removal
  - Request-ID middleware (UUID per request, surfaced in logs + response)
  - Global exception handler that hides stack traces unless DEBUG=true
  - Per-IP / per-token rate limiting helpers
  - API-key OR JWT-bearer auth gate (`require_auth_or_api_key`)
  - Sensitive-value redaction for logging
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


# ─── CORS ──────────────────────────────────────────────────────────────────

def _parse_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    # Safe development default
    return ["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:3000"]


def configure_cors(app: FastAPI) -> None:
    """Install CORS middleware.

    In dev (when ALLOWED_ORIGINS isn't set), permit any localhost / 127.0.0.1
    origin on any port via regex — Next.js falls back to 3001/3002/etc. when
    3000 is held, and we don't want a port-mismatch CORS reject to look like
    a real bug. In prod, ALLOWED_ORIGINS pins the allowed list explicitly.
    """
    raw = os.environ.get("ALLOWED_ORIGINS")
    kwargs: Dict[str, Any] = dict(
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    if raw:
        kwargs["allow_origins"] = _parse_origins()
    else:
        # Dev mode: regex allows http://localhost:* and http://127.0.0.1:*
        kwargs["allow_origin_regex"] = r"^http://(localhost|127\.0\.0\.1):\d+$"
    app.add_middleware(CORSMiddleware, **kwargs)


# ─── Headers + request id ─────────────────────────────────────────────────

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                                "style-src 'self' 'unsafe-inline'; "
                                "script-src 'self' 'unsafe-inline'; "
                                "connect-src *; "
                                "font-src 'self' data:",
}


async def security_headers_middleware(request: Request, call_next: Callable):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id

    response = await call_next(request)

    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    response.headers["X-Request-ID"] = request_id
    # Hide framework's Server header (Starlette MutableHeaders supports del/in)
    for key in ("server", "Server"):
        if key in response.headers:
            del response.headers[key]
    return response


# ─── Global exception handler ─────────────────────────────────────────────

def install_exception_handlers(app: FastAPI) -> None:
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "-")
        logger.exception("unhandled_exception request_id=%s", rid)
        payload = {"error": "internal_server_error", "request_id": rid}
        if debug:
            payload["debug"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=500, content=payload)

    @app.exception_handler(HTTPException)
    async def http_err(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", "-")
        # If detail is a dict (from api_error), surface its keys flat at the
        # response root so clients see {"error": "CODE", "message": "...", ...}
        if isinstance(exc.detail, dict):
            content = {**exc.detail, "request_id": rid}
        else:
            content = {"error": exc.detail, "request_id": rid}
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=exc.headers or {},
        )


# ─── Rate limiting (per-IP token bucket, in-memory) ───────────────────────

class _Bucket:
    __slots__ = ("tokens", "refill_at")

    def __init__(self, tokens: float, refill_at: float):
        self.tokens = tokens
        self.refill_at = refill_at


class IPRateLimiter:
    """Simple token bucket per remote IP. For production, swap to Redis."""

    def __init__(self, requests_per_minute: int = 60, burst: int = 20):
        self.rate_per_sec = requests_per_minute / 60.0
        self.burst = burst
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, ip: str) -> bool:
        now = time.time()
        bucket = self._buckets.get(ip)
        if bucket is None:
            self._buckets[ip] = _Bucket(self.burst - 1, now)
            return True
        elapsed = now - bucket.refill_at
        bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate_per_sec)
        bucket.refill_at = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        return False


_match_limiter: Optional[IPRateLimiter] = None
_contract_limiter: Optional[IPRateLimiter] = None


def get_match_limiter() -> IPRateLimiter:
    global _match_limiter
    if _match_limiter is None:
        _match_limiter = IPRateLimiter(
            requests_per_minute=int(os.environ.get("RATE_LIMIT_MATCH_PER_MIN", "20")),
            burst=int(os.environ.get("RATE_LIMIT_MATCH_BURST", "10")),
        )
    return _match_limiter


def get_contract_limiter() -> IPRateLimiter:
    global _contract_limiter
    if _contract_limiter is None:
        _contract_limiter = IPRateLimiter(
            requests_per_minute=int(os.environ.get("RATE_LIMIT_CONTRACT_PER_MIN", "10")),
            burst=int(os.environ.get("RATE_LIMIT_CONTRACT_BURST", "5")),
        )
    return _contract_limiter


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For when present (ALB sets this)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_match(request: Request) -> None:
    ip = _client_ip(request)
    if not get_match_limiter().allow(ip):
        raise HTTPException(429, "Too many match requests - please slow down")


def rate_limit_contract(request: Request) -> None:
    ip = _client_ip(request)
    if not get_contract_limiter().allow(ip):
        raise HTTPException(429, "Too many contract requests")


# ─── Combined auth gate (JWT or API key) ──────────────────────────────────

PUBLIC_PATHS = {"/", "/health", "/health/deep", "/docs", "/redoc", "/openapi.json"}


def require_auth_or_api_key(request: Request):
    """Optional auth: accept either Authorization: Bearer <jwt> or X-API-Key.

    If neither is provided and AUTH_REQUIRED env var is "true", reject the
    request. Otherwise allow (development mode).
    """
    auth_required = os.environ.get("AUTH_REQUIRED", "false").lower() == "true"
    if request.url.path in PUBLIC_PATHS:
        return None

    api_key = request.headers.get("X-API-Key")
    auth = request.headers.get("Authorization")

    if api_key:
        # Validate against users.api_key
        db_path = getattr(request.app.state, "db_path", None)
        if not db_path:
            if auth_required:
                raise HTTPException(503, "Auth backend not configured")
            return None
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT id, email, tier, role FROM users WHERE api_key=?", (api_key,)).fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(401, "Invalid API key")
        return {"id": row[0], "email": row[1], "tier": row[2], "role": row[3], "via": "api_key"}

    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        try:
            from .auth import _decode_token  # type: ignore
            payload = _decode_token(token)
            return {"id": payload.get("sub"), "role": payload.get("role"), "tier": payload.get("tier"), "via": "jwt"}
        except Exception:
            raise HTTPException(401, "Invalid bearer token")

    if auth_required:
        raise HTTPException(401, "Authentication required")
    return None


# ─── Log sanitization ─────────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{20,})"),
    re.compile(r"(eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,})"),
    re.compile(r"(\"password\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(\"api_key\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(X-API-Key:\s*)(\S+)"),
]


def redact(text: str) -> str:
    """Replace anything that looks like a secret with a stable placeholder."""
    if not text:
        return text
    out = text
    for pat in _SENSITIVE_PATTERNS:
        if pat.groups == 1:
            out = pat.sub("[REDACTED]", out)
        elif pat.groups == 3:
            out = pat.sub(r"\1[REDACTED]\3", out)
        else:
            out = pat.sub(r"\1[REDACTED]", out)
    return out


# ─── Install everything ──────────────────────────────────────────────────

def install_security(app: FastAPI) -> None:
    configure_cors(app)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        return await security_headers_middleware(request, call_next)

    install_exception_handlers(app)


__all__ = [
    "install_security", "configure_cors", "security_headers_middleware",
    "install_exception_handlers", "IPRateLimiter",
    "rate_limit_match", "rate_limit_contract", "require_auth_or_api_key",
    "redact", "PUBLIC_PATHS",
]
