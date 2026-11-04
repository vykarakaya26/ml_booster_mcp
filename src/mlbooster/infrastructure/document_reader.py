"""Reads the text layer of one public PDF for the evidence search. Web fetch tools return PDFs as raw streams, and most
competition board decisions and annual reports are PDFs. Nothing is rendered, and nothing read here is stored.

The URL has already passed the source policy. robots.txt of the host is respected (a missing file allows, an unreachable
one stops the read), redirects are not followed and only the `%PDF-` signature is accepted.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import pypdfium2 as pdfium  # installed with pdfplumber

from mlbooster.application.errors import ProviderUnavailable, UnexpectedResponse
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.settings import USER_AGENT

MAX_MB = 100
MAX_PAGES = 800


class PdfDocumentReader:
    def __init__(self, http: SecureHttpClient | None = None):
        self._http = http

    def _client(self) -> SecureHttpClient:
        if self._http is None:
            self._http = SecureHttpClient(rps=1.0, timeout=60.0, max_retries=1, max_bytes=MAX_MB * 1024 * 1024)
        return self._http

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        response = self._client().get(f"https://{parts.netloc}/robots.txt")
        if response.status != 200 or "html" in response.content_type:
            return True
        rules = RobotFileParser()
        rules.parse(response.content.decode("utf-8", errors="replace").splitlines())
        return rules.can_fetch(USER_AGENT, url)

    def read(self, url: str) -> list[str]:
        if not self._allowed(url):
            raise ProviderUnavailable("robots.txt bu belgenin okunmasına izin vermiyor; belge atlanmalıdır")
        response = self._client().get(url)
        if response.status != 200:
            raise ProviderUnavailable(f"Belge okunamadı (HTTP {response.status}); giriş ya da yönlendirme isteyen belgeler atlanır")
        if not response.content.startswith(b"%PDF-"):
            raise UnexpectedResponse("Belge PDF değil; WebFetch ile açılmalıdır")
        return _text_layer(response.content)


def _text_layer(content: bytes) -> list[str]:
    try:
        document = pdfium.PdfDocument(content)
    except pdfium.PdfiumError:
        raise UnexpectedResponse("Belge PDF olarak açılamadı") from None
    try:
        pages = []
        for index in range(min(len(document), MAX_PAGES)):
            page = document[index]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            textpage.close()
            page.close()
        return pages
    finally:
        document.close()
