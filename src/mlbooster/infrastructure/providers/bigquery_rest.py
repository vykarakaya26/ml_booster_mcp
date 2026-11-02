"""Minimal BigQuery REST client: `jobs.query` + `jobs.getQueryResults` on the pinned host bigquery.googleapis.com.

Only parameterized standard-SQL queries written in this package are sent; values coming from the user or the
catalog travel as query parameters, never inside the SQL text. Every query carries `maximumBytesBilled`, so BigQuery
refuses it (without billing anything) instead of scanning more than the cap.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, urlencode

from mlbooster.application.errors import (
    AuthenticationFailed,
    CredentialsMissing,
    ProviderError,
    UnexpectedResponse,
)
from mlbooster.domain.errors import InvalidInput
from mlbooster.infrastructure.http_client import HttpResponse, SecureHttpClient
from mlbooster.infrastructure.providers.google_auth import GoogleTokenSource
from mlbooster.infrastructure.secret_store import Secret, redact

API_HOST = "bigquery.googleapis.com"
API_ROOT = f"https://{API_HOST}/bigquery/v2/projects/"
SCOPE = "https://www.googleapis.com/auth/bigquery"
PROJECT_ENV = "BQ_PROJECT_ID"
# Project IDs: 6–30 characters, lower-case letters, digits and hyphens, starting with a letter.
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
_LOCATION = re.compile(r"^[A-Za-z0-9-]{1,40}$")
DEFAULT_TIMEOUT_MS = 20_000
MAX_PAGES = 50
MAX_ROWS = 2_000_000


def project_id(value: str | None) -> str:
    if value is None or not _PROJECT.fullmatch(value):
        raise CredentialsMissing(
            f"{PROJECT_ENV} tanımlı değil ya da geçerli bir Google Cloud proje kimliği değil (ör. benim-projem-123). "
            "Sorgular bu projeye faturalanır; BigQuery sandbox'ı kart bilgisi olmadan kullanılabilir"
        )
    return value


@dataclass(frozen=True)
class Param:
    """A named query parameter. kind: STRING | INT64 | DATE; list values become ARRAY<kind>."""

    name: str
    kind: str
    value: str | int | date | Sequence[str]

    def to_api(self) -> dict:
        if isinstance(self.value, list | tuple):
            return {
                "name": self.name,
                "parameterType": {"type": "ARRAY", "arrayType": {"type": self.kind}},
                "parameterValue": {"arrayValues": [{"value": str(v)} for v in self.value]},
            }
        value = self.value.isoformat() if isinstance(self.value, date) else str(self.value)
        return {"name": self.name, "parameterType": {"type": self.kind}, "parameterValue": {"value": value}}


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[str | None]]
    bytes_processed: int
    bytes_billed: int


class BigQueryClient:
    def __init__(
        self,
        http: SecureHttpClient,
        tokens: GoogleTokenSource,
        project: str,
        max_bytes_billed: int,
        on_response: Callable[[str, bytes], None] | None = None,
    ):
        self._http = http
        self._tokens = tokens
        self._project = project_id(project)
        self._max_bytes_billed = max_bytes_billed
        self._on_response = on_response

    @property
    def project(self) -> str:
        return self._project

    def query(self, sql: str, params: Sequence[Param], *, dry_run: bool = False) -> QueryResult:
        body = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": [p.to_api() for p in params],
            "maximumBytesBilled": str(self._max_bytes_billed),
            "timeoutMs": DEFAULT_TIMEOUT_MS,
            "useQueryCache": True,
            "dryRun": dry_run,
            "labels": {"client": "mlbooster"},
        }
        doc = self._call("POST", f"{API_ROOT}{quote(self._project)}/queries", body)
        if dry_run:
            return QueryResult([], [], _int(doc.get("totalBytesProcessed")), 0)
        columns: list[str] = []
        rows: list[list[str | None]] = []
        job = doc.get("jobReference") if isinstance(doc.get("jobReference"), dict) else {}
        for _ in range(MAX_PAGES):
            if doc.get("jobComplete") is True:
                if not columns:
                    columns = _columns(doc)
                rows += _rows(doc, len(columns))
                if len(rows) > MAX_ROWS:
                    raise UnexpectedResponse("BigQuery sonucu beklenenden çok satır döndürdü")
                token = doc.get("pageToken")
                if not token:
                    return QueryResult(
                        columns, rows, _int(doc.get("totalBytesProcessed")), _int(doc.get("totalBytesBilled"))
                    )
            else:
                token = None
            doc = self._results(job, token if isinstance(token, str) else None)
        raise UnexpectedResponse("BigQuery sorgusu zamanında tamamlanmadı ya da çok fazla sayfa döndürdü")

    def _results(self, job: dict, page_token: str | None) -> dict:
        job_id, location = job.get("jobId"), job.get("location")
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise UnexpectedResponse("BigQuery yanıtında geçerli bir jobReference yok")
        query = {"timeoutMs": DEFAULT_TIMEOUT_MS}
        if isinstance(location, str) and _LOCATION.fullmatch(location):
            query["location"] = location
        if page_token:
            query["pageToken"] = page_token
        url = f"{API_ROOT}{quote(self._project)}/queries/{job_id}?{urlencode(query)}"
        return self._call("GET", url, None)

    def _call(self, method: str, url: str, body: dict | None) -> dict:
        for attempt in range(2):  # a second try only with a fresh token
            token = self._tokens.token()
            headers = {"Authorization": f"Bearer {token.reveal()}", "Accept": "application/json"}
            response = self._http.post_json(url, body, headers) if method == "POST" else self._http.get(url, headers)
            if response.status != 401:
                break
            self._tokens.invalidate()
            if attempt:
                raise AuthenticationFailed("BigQuery erişim belirtecini kabul etmedi (HTTP 401)")
        return self._json(response, token)

    def _json(self, response: HttpResponse, token: Secret) -> dict:
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"BigQuery {response.status} yönlendirmesi döndürdü")
        if "json" not in response.content_type:
            raise UnexpectedResponse(f"BigQuery JSON yerine başka bir içerik döndürdü ({response.content_type[:40]})")
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse("BigQuery yanıtı çözümlenemedi") from None
        if not isinstance(doc, dict):
            raise UnexpectedResponse("BigQuery yanıtı beklenen nesne biçiminde değil")
        if response.status != 200 or "error" in doc:
            raise _api_error(response.status, doc, token, self._tokens.credentials.secrets())
        if self._on_response is not None:
            self._on_response(response.url, response.content)
        return doc


def _api_error(status: int, doc: dict, token: Secret, secrets: list) -> ProviderError:
    error = doc.get("error") if isinstance(doc.get("error"), dict) else {}
    reasons = [e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)] if isinstance(error.get("errors"), list) else []
    message = redact(str(error.get("message", ""))[:300], [token, *secrets])
    # The documented reason is not stable across clients; the message text is ("Query exceeded limit for bytes billed").
    if "bytesBilledLimitExceeded" in reasons or "limit for bytes billed" in message:
        return InvalidInput(
            "Sorgu, kaynak için tanımlı maliyet tavanını (maximumBytesBilled) aşacağı için BigQuery tarafından "
            "çalıştırılmadı; daha kısa bir dönem isteyin"
        )
    if status == 403 or "accessDenied" in reasons:
        return AuthenticationFailed(
            "BigQuery erişimi reddedildi. Projede BigQuery API'nin açık olduğunu ve hesabınızın 'BigQuery Job User' "
            f"rolüne sahip olduğunu kontrol edin ({message})"
        )
    if status == 400 or "invalidQuery" in reasons or "invalid" in reasons:
        return UnexpectedResponse(f"BigQuery sorguyu kabul etmedi: {message}")
    if status == 404:
        return UnexpectedResponse(f"BigQuery projesi ya da tablosu bulunamadı: {message}")
    return ProviderError(f"BigQuery HTTP {status}: {message}")


def _int(value: object) -> int:
    try:
        return int(str(value)) if value is not None else 0
    except ValueError:
        return 0


def _columns(doc: dict) -> list[str]:
    schema = doc.get("schema")
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list) or not all(isinstance(f, dict) and isinstance(f.get("name"), str) for f in fields):
        raise UnexpectedResponse("BigQuery yanıtında schema.fields yok")
    return [f["name"] for f in fields]


def _rows(doc: dict, width: int) -> list[list[str | None]]:
    rows = doc.get("rows", [])
    if not isinstance(rows, list):
        raise UnexpectedResponse("BigQuery yanıtındaki rows alanı bir liste değil")
    out = []
    for row in rows:
        cells = row.get("f") if isinstance(row, dict) else None
        if not isinstance(cells, list) or len(cells) != width:
            raise UnexpectedResponse("BigQuery yanıtındaki bir satır şemayla uyuşmuyor")
        values = [c.get("v") if isinstance(c, dict) else None for c in cells]
        if not all(v is None or isinstance(v, str) for v in values):
            raise UnexpectedResponse("BigQuery yanıtında beklenmeyen bir hücre biçimi var (iç içe alan)")
        out.append(values)
    return out
