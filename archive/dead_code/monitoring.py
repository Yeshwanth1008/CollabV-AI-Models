"""
CollabV AI - Monitoring & Structured Logging
==============================================
Wires Sentry (optional), structured JSON request logging, and a verbose
health endpoint for production deployments.

Wire it from api.py by calling:
    from .monitoring import install_monitoring
    install_monitoring(app)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Callable

from fastapi import FastAPI, Request


# ─── Structured JSON logger ─────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("request_id", "user_id", "duration_ms", "endpoint", "method", "status"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# ─── Sentry ────────────────────────────────────────────────────────────────

def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.environ.get("ENVIRONMENT", "prod"),
            integrations=[FastApiIntegration()],
        )
        logging.getLogger(__name__).info("Sentry initialized")
    except ImportError:
        logging.getLogger(__name__).info("sentry-sdk not installed; skipping")


# ─── Request middleware ────────────────────────────────────────────────────

async def log_requests_middleware(request: Request, call_next: Callable[..., Any]):
    start = time.time()
    request_id = request.headers.get("x-request-id") or f"req-{int(start * 1000)}"
    log = logging.getLogger("request")
    try:
        response = await call_next(request)
        duration = (time.time() - start) * 1000
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "endpoint": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration, 1),
            },
        )
        response.headers["x-request-id"] = request_id
        return response
    except Exception as e:
        duration = (time.time() - start) * 1000
        log.error(
            f"request_failed: {e}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "endpoint": request.url.path,
                "duration_ms": round(duration, 1),
            },
            exc_info=True,
        )
        raise


# ─── Detailed health ───────────────────────────────────────────────────────

def install_monitoring(app: FastAPI, db_path: str = "") -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    init_sentry()

    @app.middleware("http")
    async def _log(request: Request, call_next):
        return await log_requests_middleware(request, call_next)

    @app.get("/health/detailed")
    async def detailed_health():
        components = {}
        # DB
        if db_path and os.path.exists(db_path):
            components["database"] = {"status": "ok", "type": "sqlite"}
        else:
            components["database"] = {"status": "missing"}
        # Embeddings
        try:
            from .embeddings import EmbeddingEngine
            ee = EmbeddingEngine()
            components["embeddings"] = {
                "status": "ok" if ee.is_ready else "disabled",
                "model": ee.model_name,
                "use_faiss": ee.use_faiss,
            }
        except Exception as e:
            components["embeddings"] = {"status": "error", "error": str(e)}
        # Anthropic
        components["anthropic"] = {
            "status": "configured" if os.environ.get("ANTHROPIC_API_KEY") else "missing"
        }
        return {
            "status": "ok",
            "components": components,
            "timestamp": time.time(),
        }


__all__ = [
    "install_monitoring", "configure_logging", "init_sentry",
    "log_requests_middleware", "JsonFormatter",
]
