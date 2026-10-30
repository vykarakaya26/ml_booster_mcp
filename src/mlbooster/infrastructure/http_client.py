"""Güvenli HTTP istemcisi: TLS doğrulama, yönlendirme yok, zaman aşımı, yanıt boyutu limiti,
rate limit, üstel backoff ile tekrar deneme. Dönüş değeri httpx'ten bağımsız bir değer nesnesidir."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from mlbooster.application.errors import ProviderUnavailable, UnexpectedResponse
from mlbooster.infrastructure.settings import USER_AGENT

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_RETRY_AFTER_SECONDS = 120


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    content: bytes
    url: str

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").lower()

    def text(self, limit: int = 200) -> str:
        return self.content[:limit].decode("utf-8", errors="replace")


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


class SecureHttpClient:
    def __init__(
        self,
        *,
        rps: float | None = 2.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # sessiz 302→HTML kırılmalarını ve başka hosta sızmayı engelle
            verify=True,
            trust_env=False,  # ortamdaki proxy/netrc ayarları istekleri (ve header'ları) başka yere taşımasın
            transport=transport,
        )
        self._limiter = RateLimiter(rps)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_bytes = max_bytes
        self._sleep = sleep

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        if not url.startswith("https://"):
            raise UnexpectedResponse("Yalnızca HTTPS isteklerine izin verilir")
        attempt = 0
        while True:
            self._limiter.wait()
            response: HttpResponse | None = None
            try:
                response = self._get_once(url, headers)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderUnavailable(f"Ağ hatası ({type(exc).__name__}): {url}") from None
            else:
                if response.status not in RETRYABLE_STATUS:
                    return response
                if attempt >= self._max_retries:
                    raise ProviderUnavailable(f"HTTP {response.status}, {attempt} tekrar denemeden sonra: {url}")
            delay = self._retry_delay(attempt, response)
            log.warning("Tekrar deneniyor (%s/%s) %.1fs sonra: %s", attempt + 1, self._max_retries, delay, url)
            self._sleep(delay)
            attempt += 1

    def _get_once(self, url: str, headers: dict[str, str] | None) -> HttpResponse:
        with self._client.stream("GET", url, headers=headers) as r:
            declared = r.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self._max_bytes:
                raise UnexpectedResponse(f"Yanıt çok büyük ({declared} bayt): {url}")
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise UnexpectedResponse(f"Yanıt {self._max_bytes} bayt limitini aştı: {url}")
                chunks.append(chunk)
            return HttpResponse(r.status_code, {k.lower(): v for k, v in r.headers.items()}, b"".join(chunks), url)

    def _retry_delay(self, attempt: int, response: HttpResponse | None) -> float:
        if response is not None and (retry_after := response.headers.get("retry-after", "")).isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        return self._backoff_base * (2**attempt)

    def close(self) -> None:
        self._client.close()
