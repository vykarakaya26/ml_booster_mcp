"""Yerel web paneli (Starlette). Yalnızca composition root'tan alınan use case'leri çağırır.

Güvenlik (SECURITY.md'deki yerel panel bölümü):
- Sunucu yalnızca loopback adrese bağlanır; Host başlığı loopback değilse istek reddedilir (DNS rebinding).
- Her `/api` isteği, sayfaya açılışta gömülen rastgele oturum belirtecini `X-MLB-Token` başlığında taşımalıdır.
  Özel başlık, başka sitelerin tarayıcı üzerinden form/görsel ile istek göndermesini (CSRF) engeller.
- CORS açılmaz; CSP yalnızca aynı kaynaktan betik ve stile izin verir, sayfa çerçeveye alınamaz.
- Girdiler use case kapısında izin listesiyle doğrulanır; API dosya yolu almaz.
- Beklenen hatalar (DomainError) kullanıcıya gösterilir; diğerleri günlüğe yazılır, istemciye ayrıntı gönderilmez.
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
from mlbooster.domain.services.backtest import MODELS, BacktestConfig
from mlbooster.domain.validation import slug
from mlbooster.interfaces.presenters import backtest_dict, health_dict, quality_dict, sector_map_dict

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_HEADER = "x-mlb-token"
MAX_BODY_BYTES = 64 * 1024
MAX_PREDICTION_DATES = 120
PANEL_FREQS = ("M", "W", "B")

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
    """`Host` başlığından ad kısmı: '127.0.0.1:8765' → '127.0.0.1', '[::1]:8765' → '::1'."""
    if header.startswith("["):
        return header[1 : header.find("]")] if "]" in header else ""
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


class LocalOnlyGuard:
    """Saf ASGI ara katmanı: Host ve belirteç denetimi, güvenlik başlıkları."""

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


# --- girdi yardımcıları -------------------------------------------------------------------------------------


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise InvalidInput("İstek gövdesi çok büyük.")
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


def _models(data: dict) -> tuple[str, ...]:
    value = data.get("models") or list(MODELS)
    if not isinstance(value, list) or not all(isinstance(m, str) for m in value):
        raise InvalidInput("'models' bir metin listesi olmalı.")
    unknown = sorted(set(value) - set(MODELS))
    if unknown:
        raise InvalidInput(f"Tanımsız model: {', '.join(unknown)}")
    return tuple(dict.fromkeys(value))


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


def panel_payload(panel: pd.DataFrame) -> dict:
    """Paneli grafik için sütun bazlı JSON'a çevirir (NaN → null)."""
    if panel.empty:
        return {"dates": [], "columns": {}}
    return {
        "dates": [d.date().isoformat() for d in pd.to_datetime(panel.index)],
        "columns": {c: [_num(float(v)) for v in panel[c].to_numpy()] for c in panel.columns},
    }


# --- uygulama ---------------------------------------------------------------------------------------------


def create_app(container_factory: Callable[[], Container] = build_container, token: str | None = None) -> Starlette:
    token = token or secrets.token_urlsafe(32)
    lock = threading.Lock()  # yerel depolara (Parquet/DuckDB) aynı anda tek yazıcı
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
            except Exception:  # noqa: BLE001 — ayrıntı istemciye sızmasın
                log.exception("web paneli: beklenmeyen hata (%s)", request.url.path)
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
                "models": list(MODELS),
                "providers": sorted(c.providers.available()),
                "default_start": DEFAULT_START.isoformat(),
            }

        return await call(run)

    async def sectors(request: Request) -> list[dict]:
        return await call(lambda: [{"id": s.id, "name": s.name} for s in container().list_sectors()])

    async def sector_map(request: Request) -> dict:
        sector = request.path_params["sector"]
        return await call(lambda: sector_map_dict(container().sector_map(sector)))

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
        name = f"{sector}_{freq.lower()}_panel.{fmt}"  # yalnızca doğrulanmış slug ve sabitlerden
        media = "text/csv; charset=utf-8" if fmt == "csv" else "application/vnd.apache.parquet"
        return Response(content, media_type=media, headers={"content-disposition": f'attachment; filename="{name}"'})

    async def audit(request: Request) -> dict:
        sector, start, end, _ = _panel_args(await _body(request))

        def run():
            report, result = container().audit_panel(sector, start, end)
            return quality_dict(report, result.skipped) | {"sector": sector, **panel_payload(result.panel)}

        return await call(run)

    async def backtest(request: Request) -> dict:
        data = await _body(request)
        sector, start, end, _ = _panel_args(data)
        config = BacktestConfig(
            target=_str(data, "target", required=True),
            horizon=_int(data, "horizon", 1, 1, 24),
            min_train=_int(data, "min_train", 48, 12, 240),
            models=_models(data),
            exogenous=_bool(data, "exogenous", True),
            anchor=_str(data, "anchor"),
        )

        def run():
            result, panel_result = container().run_backtest(sector, config, start, end)
            out = backtest_dict(result, sector, max_predictions=MAX_PREDICTION_DATES)
            target = panel_result.panel[config.target] if config.target in panel_result.panel else pd.Series()
            out["history"] = {
                "dates": [d.date().isoformat() for d in pd.to_datetime(target.index)],
                "values": [_num(float(v)) for v in target.to_numpy()],
            }
            return out

        return await call(run)

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
        Route("/api/backtest", endpoint(backtest), methods=["POST"]),
        Route("/api/health", endpoint(health)),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(LocalOnlyGuard, token=token)])
