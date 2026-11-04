"""Local web panel (Starlette). Calls only the use cases from the composition root.

Security (local panel section in SECURITY.md):
- The server binds to loopback only; requests whose Host header is not loopback are rejected (DNS rebinding).
- Every `/api` request must carry the random session token embedded in the page in the `X-MLB-Token` header.
  The custom header stops other sites from sending requests through the browser via forms/images (CSRF).
- No CORS; the CSP allows scripts and styles from the same origin only and forbids framing.
- Inputs are validated against allow-lists at the use case gate; the API takes no file paths. A market share table
  arrives as text in the JSON body (the browser reads the file the user picked); it is parsed in memory and never
  written to disk. Request bodies are size-capped while they are being read.
- Expected errors (DomainError) are shown to the user; anything else is logged without details for the client.
"""

from __future__ import annotations

import io
import json
import logging
import math
import secrets
import threading
from collections.abc import Callable
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mlbooster import __version__
from mlbooster.application.use_cases.panel import DEFAULT_START
from mlbooster.bootstrap import Container, build_container
from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.services.market_share import ShareConfig
from mlbooster.domain.validation import slug
from mlbooster.interfaces.presenters import (
    ACCESS_LABELS,
    BASIS_LABELS,
    HHI_CLASS_LABELS,
    SEVERITY_LABELS,
    STATUS_LABELS,
    TREND_LABELS,
    VERIFICATION_LABELS,
    brand_features_dict,
    health_dict,
    quality_dict,
    sector_map_dict,
)
from mlbooster.interfaces.web.labels import FREQUENCIES, UNITS, sector_feature_info

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_HEADER = "x-mlb-token"
MAX_BODY_BYTES = 64 * 1024
MAX_TABLE_BODY_BYTES = 8 * 1024 * 1024  # market share table sent as text (the reader allows at most 200,000 rows)
TABLE_FORMATS = ("csv", "json")
PANEL_FREQS = ("M", "W", "B")
SOURCE_SHARE_START = date(2015, 1, 1)  # monthly bulletins answer one month per request; the first pull stays short

SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}

UNEXPECTED = "Beklenmeyen bir hata oluştu; ayrıntılar sunucu günlüğüne yazıldı."


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def _host_name(header: str) -> str:
    """Host name part of the `Host` header: '127.0.0.1:8765' → '127.0.0.1', '[::1]:8765' → '::1'."""
    if header.startswith("["):
        return header[1 : header.find("]")] if "]" in header else ""
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


class LocalOnlyGuard:
    """Pure ASGI middleware: Host and token checks, security headers."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1")
        if not is_loopback(_host_name(host)):
            await _plain(400, "Geçersiz Host başlığı.")(scope, receive, send)
            return
        if scope["path"].startswith("/api/"):
            supplied = headers.get(TOKEN_HEADER.encode(), b"")
            if not secrets.compare_digest(supplied, self.token):
                await _plain(403, "Oturum belirteci geçersiz; paneli yeniden yükleyin.")(scope, receive, send)
                return
            origin = headers.get(b"origin")
            if origin is not None and origin.decode("latin-1") != f"http://{host}":
                await _plain(403, "İstek başka bir kaynaktan geldi.")(scope, receive, send)
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items() if k.encode() not in existing]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _plain(status: int, text: str) -> Response:
    return JSONResponse({"error": text}, status_code=status, headers=SECURITY_HEADERS)


# --- input helpers -------------------------------------------------------------------------------------


async def _body(request: Request, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
    size = f"{limit // 2**20} MB" if limit >= 2**20 else f"{limit // 2**10} KB"
    too_big = InvalidInput(f"İstek gövdesi çok büyük (en fazla {size}).")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_big
    raw = bytearray()
    async for chunk in request.stream():  # stop reading as soon as the limit is exceeded
        raw += chunk
        if len(raw) > limit:
            raise too_big
    if not raw:
        return {}
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise InvalidInput("İstek gövdesi JSON olmalı.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise InvalidInput("İstek gövdesi geçerli JSON değil.") from None
    if not isinstance(data, dict):
        raise InvalidInput("İstek gövdesi bir nesne olmalı.")
    return data


def _str(data: dict, key: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = data.get(key, default)
    if value in (None, ""):
        if required:
            raise InvalidInput(f"'{key}' alanı zorunlu.")
        return default
    if not isinstance(value, str) or len(value) > 200:
        raise InvalidInput(f"'{key}' alanı geçersiz.")
    return value


def _date(data: dict, key: str, default: date | None) -> date | None:
    value = _str(data, key)
    if value is None:
        return default
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise InvalidInput(f"'{key}' YYYY-AA veya YYYY-AA-GG biçiminde olmalı.")


def _int(data: dict, key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise InvalidInput(f"'{key}' {low} ile {high} arasında bir tam sayı olmalı.")
    return value


def _bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise InvalidInput(f"'{key}' doğru/yanlış olmalı.")
    return value


def _panel_args(data: dict) -> tuple[str, date, date | None, str]:
    sector = slug(_str(data, "sector", required=True), "sektör")
    freq = _str(data, "freq", "M")
    if freq not in PANEL_FREQS:
        raise InvalidInput("Panel frekansı M, W ya da B olmalı.")
    return sector, _date(data, "start", DEFAULT_START), _date(data, "end", None), freq


def _num(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="minutes")


def panel_payload(panel: pd.DataFrame) -> dict:
    """Column-oriented JSON for charts (NaN → null)."""
    if panel.empty:
        return {"dates": [], "columns": {}}
    return {
        "dates": [d.date().isoformat() for d in pd.to_datetime(panel.index)],
        "columns": {c: [_num(float(v)) for v in panel[c].to_numpy()] for c in panel.columns},
    }


# --- application ---------------------------------------------------------------------------------------------


def create_app(container_factory: Callable[[], Container] = build_container, token: str | None = None) -> Starlette:
    token = token or secrets.token_urlsafe(32)
    lock = threading.Lock()  # a single writer at a time for the local stores (Parquet/DuckDB)
    holder: dict[str, Container] = {}

    def container() -> Container:
        if "c" not in holder:
            holder["c"] = container_factory()
        return holder["c"]

    async def call(fn: Callable[[], Any]) -> Any:
        def locked():
            with lock:
                return fn()

        return await run_in_threadpool(locked)

    def endpoint(handler: Callable[[Request], Any]) -> Callable[[Request], Any]:
        async def wrapped(request: Request) -> Response:
            try:
                result = await handler(request)
            except DomainError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            except Exception:  # details must not reach the client
                log.exception("web panel: unexpected error (%s)", request.url.path)
                return JSONResponse({"error": UNEXPECTED}, status_code=500)
            return result if isinstance(result, Response) else JSONResponse(result)

        return wrapped

    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    async def index(request: Request) -> Response:
        html = index_html.replace("{{TOKEN}}", escape(token, quote=True)).replace("{{VERSION}}", escape(__version__))
        return HTMLResponse(html)

    async def meta(request: Request) -> dict:
        def run():
            c = container()
            return {
                "version": __version__,
                "providers": sorted(c.ready_providers()),
                "default_start": DEFAULT_START.isoformat(),
            }

        return await call(run)

    async def sectors(request: Request) -> list[dict]:
        def run():
            c = container()
            out = []
            for s in c.list_sectors():
                m = c.sector_map(s.id)
                statuses = [e.status for e in m.features]
                out.append({
                    "id": s.id, "name": s.name, "ml_use_cases": list(m.ml_use_cases),
                    "features": len(statuses), "ready": statuses.count("ready"),
                    "pending": statuses.count("pending_discovery"), "sources": len(m.sources),
                    "market_share_sources": len(m.market_share),
                })  # fmt: skip
            return out

        return await call(run)

    async def sector_map(request: Request) -> dict:
        sector = request.path_params["sector"]

        def run():
            c = container()
            m = c.sector_map(sector)  # validates the id
            labels = {"status": STATUS_LABELS, "access": ACCESS_LABELS, "verification": VERIFICATION_LABELS,
                      "frequency": FREQUENCIES}
            return sector_map_dict(m) | {"feature_info": sector_feature_info(m), "labels": labels}

        return await call(run)

    async def panel(request: Request) -> dict:
        sector, start, end, freq = _panel_args(await _body(request))

        def run():
            result = container().build_panel(sector, start, end, freq=freq)
            return {
                "sector": sector,
                "freq": freq,
                "rows": int(result.panel.shape[0]),
                "features": list(result.panel.columns),
                "skipped": result.skipped,
                "saved": result.location is not None,
                "generated_at": _now(),
                **panel_payload(result.panel),
            }

        return await call(run)

    async def panel_export(request: Request) -> Response:
        data = await _body(request)
        sector, start, end, freq = _panel_args(data)
        fmt = _str(data, "format", "csv")
        if fmt not in {"csv", "parquet"}:
            raise InvalidInput("Biçim csv ya da parquet olmalı.")

        def run():
            result = container().build_panel(sector, start, end, freq=freq, save=False)
            if result.panel.empty:
                raise InvalidInput(f"{sector} paneli boş; atlanan feature'lar: {result.skipped}")
            buf = io.BytesIO()
            if fmt == "csv":
                buf.write(result.panel.to_csv(date_format="%Y-%m-%d").encode("utf-8"))
            else:
                result.panel.to_parquet(buf)
            return buf.getvalue()

        content = await call(run)
        name = f"{sector}_{freq.lower()}_panel.{fmt}"  # built only from the validated slug and constants
        media = "text/csv; charset=utf-8" if fmt == "csv" else "application/vnd.apache.parquet"
        return Response(content, media_type=media, headers={"content-disposition": f'attachment; filename="{name}"'})

    async def audit(request: Request) -> dict:
        sector, start, end, _ = _panel_args(await _body(request))

        def run():
            report, result = container().audit_panel(sector, start, end)
            return quality_dict(report, result.skipped) | {
                "sector": sector,
                "generated_at": _now(),
                **panel_payload(result.panel),
            }

        return await call(run)

    labels = {"trend": TREND_LABELS, "hhi_class": HHI_CLASS_LABELS, "basis": BASIS_LABELS, "severity": SEVERITY_LABELS}

    async def market_share(request: Request) -> dict:
        data = await _body(request, MAX_TABLE_BODY_BYTES)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidInput("Pazar payı tablosu boş; bir dosya seçin, tabloyu yapıştırın ya da örnek veriyi yükleyin.")
        fmt = _str(data, "format", "csv")
        if fmt not in TABLE_FORMATS:
            raise InvalidInput("Tablo biçimi csv ya da json olmalı.")
        config = ShareConfig(  # enum values and ranges are validated by ShareConfig
            focus=_str(data, "focus"),
            measure=_str(data, "measure", "value"),
            kind=_str(data, "kind", "flow"),
            basis=_str(data, "basis", "auto"),
            unit=(_str(data, "unit", "") or "")[:40],
            trend_years=_int(data, "trend_years", 3, 1, 10),
            top_n=_int(data, "top_n", 8, 3, 20),
        )

        def run():
            result = container().market_share.from_text(text, fmt, config)
            return result | {"generated_at": _now(), "labels": labels}

        return await call(run)

    async def share_series(request: Request) -> list[dict]:
        return await call(lambda: container().share_series())

    async def market_share_source(request: Request) -> dict:
        data = await _body(request)
        source = _str(data, "source", required=True)
        series = _str(data, "series", required=True)
        start = _date(data, "start", SOURCE_SHARE_START)
        end = _date(data, "end", None)
        refresh = _bool(data, "refresh", False)
        config = ShareConfig(  # kind and unit come from the catalog series
            focus=_str(data, "focus"),
            basis=_str(data, "basis", "auto"),
            trend_years=_int(data, "trend_years", 3, 1, 10),
            top_n=_int(data, "top_n", 8, 3, 20),
        )

        def run():
            result = container().market_share.from_source(source, series, config, start, end, refresh=refresh)
            result["meta"]["unit"] = UNITS.get(result["meta"]["unit"], result["meta"]["unit"])
            return result | {"generated_at": _now(), "labels": labels}

        return await call(run)

    def _brand_args(data: dict) -> tuple[str, str | None, date | None, date | None]:
        return _str(data, "query", required=True), _str(data, "choice"), _date(data, "start", None), _date(data, "end", None)

    async def brand_analyze(request: Request) -> dict:
        data = await _body(request)
        query, choice, start, end = _brand_args(data)
        refresh = _bool(data, "refresh", False)
        trend_years = _int(data, "trend_years", 3, 1, 10)

        def run():
            result = container().analyze_brand(query, choice, start, end, refresh, trend_years)
            for summary in result.get("series", []):
                summary["unit"] = UNITS.get(summary["unit"], summary["unit"])
            return result | {"generated_at": _now(), "labels": labels}

        return await call(run)

    def _brand_panel(query, choice, start, end, save):
        result = container().brand_features(query, choice, start, end, save=save)
        if isinstance(result, dict):  # the text does not lead to one brand
            raise InvalidInput(result["message"])
        return result

    async def brand_features(request: Request) -> dict:
        args = _brand_args(await _body(request))
        return await call(lambda: brand_features_dict(_brand_panel(*args, save=True)) | {"generated_at": _now()})

    async def brand_export(request: Request) -> Response:
        data = await _body(request)
        args = _brand_args(data)
        fmt = _str(data, "format", "csv")
        if fmt not in {"csv", "parquet"}:
            raise InvalidInput("Biçim csv ya da parquet olmalı.")

        def run():
            result = _brand_panel(*args, save=False)
            buf = io.BytesIO()
            if fmt == "csv":
                buf.write(result.storable.to_csv(date_format="%Y-%m-%d").encode("utf-8"))
            else:
                result.storable.to_parquet(buf)
            return buf.getvalue(), result.panel_id

        content, stem = await call(run)
        media = "text/csv; charset=utf-8" if fmt == "csv" else "application/vnd.apache.parquet"
        return Response(content, media_type=media, headers={"content-disposition": f'attachment; filename="{stem}.{fmt}"'})

    async def health(request: Request) -> list[dict]:
        source = request.query_params.get("source") or None
        return await call(lambda: [health_dict(r) for r in container().health(source)])

    routes = [
        Route("/", index),
        Route("/api/meta", endpoint(meta)),
        Route("/api/sectors", endpoint(sectors)),
        Route("/api/sectors/{sector}/map", endpoint(sector_map)),
        Route("/api/panel", endpoint(panel), methods=["POST"]),
        Route("/api/panel/export", endpoint(panel_export), methods=["POST"]),
        Route("/api/audit", endpoint(audit), methods=["POST"]),
        Route("/api/market-share", endpoint(market_share), methods=["POST"]),
        Route("/api/market-share/source", endpoint(market_share_source), methods=["POST"]),
        Route("/api/share-series", endpoint(share_series)),
        Route("/api/brand/analyze", endpoint(brand_analyze), methods=["POST"]),
        Route("/api/brand/features", endpoint(brand_features), methods=["POST"]),
        Route("/api/brand/export", endpoint(brand_export), methods=["POST"]),
        Route("/api/health", endpoint(health)),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(LocalOnlyGuard, token=token)])
