"""Tüm adapter'ların ortak sözleşmesi ve HTTP katmanı (retry, rate limit, timeout, UA)."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Literal

import httpx
import pandas as pd

from mlbooster.catalog import Source
from mlbooster.config import USER_AGENT

if TYPE_CHECKING:
    from mlbooster.store.bronze import BronzeStore

log = logging.getLogger(__name__)
# httpx INFO loglarında tam URL yazılır; anahtarlar URL'de olmadığı için güvenli, yine de sessiz tut.
logging.getLogger("httpx").setLevel(logging.WARNING)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AdapterError(RuntimeError):
    pass


class AuthError(AdapterError):
    """Kimlik doğrulama hatası; tekrar denenmez."""


class SchemaError(AdapterError):
    """Yanıt beklenen şemada değil (HTML, boş gövde, eksik alan)."""


class MissingCredentials(AdapterError):
    pass


class RateLimiter:
    def __init__(self, rps: float | None):
        self._interval = 1.0 / rps if rps else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


class HttpClient:
    def __init__(
        self,
        *,
        rps: float | None = 2.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        sleep=time.sleep,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # evds2 tipi sessiz 302→HTML kırılmalarını görünür kıl
            transport=transport,
        )
        self._limiter = RateLimiter(rps)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            self._limiter.wait()
            response: httpx.Response | None = None
            try:
                response = self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise AdapterError(f"Ağ hatası ({type(exc).__name__}): {url}") from exc
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                    return response
            delay = self._retry_delay(attempt, response)
            log.warning("Tekrar deneniyor (%s/%s) %.1fs sonra: %s", attempt + 1, self._max_retries, delay, url)
            self._sleep(delay)
            attempt += 1

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None and (retry_after := response.headers.get("Retry-After", "")).isdigit():
            return float(retry_after)
        return self._backoff_base * (2**attempt)

    def close(self) -> None:
        self._client.close()


@dataclass
class HealthResult:
    source_id: str
    status: Literal["ok", "fail", "skipped"]
    detail: str
    latency_ms: float | None = None
    checked: list[str] = field(default_factory=list)


class BaseAdapter(ABC):
    source_id: str

    def __init__(self, source: Source, *, http: HttpClient | None = None, bronze: BronzeStore | None = None):
        self.source = source
        self.http = http or HttpClient(rps=source.rate_limit.rps)
        self.bronze = bronze

    @abstractmethod
    def list_series(self, query: str | None = None) -> pd.DataFrame:
        """DataFrame[series_id, name, freq, start, end]"""

    @abstractmethod
    def fetch(
        self,
        series_ids: list[str],
        start: date,
        end: date,
        freq: str | None = None,
        agg: str | None = None,
    ) -> pd.DataFrame:
        """Silver uzun formatında DataFrame döner (schema.SILVER_COLUMNS)."""

    @abstractmethod
    def healthcheck(self) -> HealthResult:
        """Kanarya: kritik serilerden son gözlemleri çeker; HTML/boş/şema değişikliğinde fail."""

    def _save_bronze(self, url: str, response: httpx.Response) -> None:
        if self.bronze is not None:
            self.bronze.save(self.source.id, url, response.content, response.headers.get("content-type", ""))

    def close(self) -> None:
        self.http.close()
