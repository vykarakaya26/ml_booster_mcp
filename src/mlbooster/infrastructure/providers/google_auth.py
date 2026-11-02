"""Google OAuth access tokens from Application Default Credentials (ADC), without the google-auth library.

Supported credential files (the two kinds `gcloud` and the Cloud console create):
- `authorized_user` — written by `gcloud auth application-default login` to the default ADC path. The refresh token
  is exchanged at the token endpoint (`grant_type=refresh_token`).
- `service_account` — a key file downloaded for a service account. A JWT signed with its RSA key (RS256) is
  exchanged at the token endpoint (`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`).

The file is located through `GOOGLE_APPLICATION_CREDENTIALS` (a path, read from the environment only) or the gcloud
default path. Other kinds (external_account, impersonated_service_account) are refused with a clear message.

Every secret (refresh token, client secret, private key, access token) is held in a `Secret`; tokens go only to the
pinned token host oauth2.googleapis.com in a form body, and access tokens only into the Authorization header of the
pinned API host. Nothing is written to disk, URLs or logs; tokens are cached in memory only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mlbooster.application.errors import (
    AuthenticationFailed,
    CredentialsMissing,
    ProviderError,
    UnexpectedResponse,
)
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.secret_store import Secret, redact

CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_URL = f"https://{TOKEN_HOST}/token"
# Accepted spellings of the token endpoint in key files (older keys carry the accounts.google.com one).
_TOKEN_URIS = {TOKEN_URL, "https://accounts.google.com/o/oauth2/token", "https://www.googleapis.com/oauth2/v4/token"}
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
MAX_CREDENTIALS_BYTES = 64 * 1024
TOKEN_MARGIN_SECONDS = 300  # renew a little before Google lets the token expire
JWT_LIFETIME_SECONDS = 3600
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$")
_ACCESS_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,4096}$")


def default_adc_path() -> Path:
    """Where `gcloud auth application-default login` writes the credentials."""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "gcloud" / "application_default_credentials.json"
    config = os.environ.get("CLOUDSDK_CONFIG")
    return (Path(config) if config else Path.home() / ".config" / "gcloud") / "application_default_credentials.json"


@dataclass(frozen=True)
class GoogleCredentials:
    kind: str  # authorized_user | service_account
    fingerprint: str  # sha256 of the file content: cache key, never shown
    client_id: str | None = None
    client_secret: Secret | None = None
    refresh_token: Secret | None = None
    client_email: str | None = None
    private_key: Secret | None = None
    private_key_id: str | None = None
    quota_project_id: str | None = None
    project_id: str | None = None

    def secrets(self) -> list[Secret | None]:
        return [self.client_secret, self.refresh_token, self.private_key]


def credentials_path() -> Path | None:
    """The ADC file to use, or None when there is none (the healthcheck is then skipped)."""
    configured = os.environ.get(CREDENTIALS_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    default = default_adc_path()
    return default if default.is_file() else None


def load_credentials(path: Path | None = None) -> GoogleCredentials:
    path = path or credentials_path()
    if path is None:
        raise CredentialsMissing(
            "Google kimlik bilgisi bulunamadı. `gcloud auth application-default login` komutunu çalıştırın ya da "
            f"{CREDENTIALS_ENV} ortam değişkenine bir service account anahtar dosyasının yolunu yazın"
        )
    try:
        if not path.is_file():
            raise CredentialsMissing(f"{CREDENTIALS_ENV} ile verilen kimlik dosyası bulunamadı")
        if path.stat().st_size > MAX_CREDENTIALS_BYTES:
            raise CredentialsMissing("Google kimlik dosyası beklenenden büyük; doğru dosyayı gösterdiğinizden emin olun")
        raw = path.read_bytes()
    except OSError:
        raise CredentialsMissing("Google kimlik dosyası okunamadı") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise CredentialsMissing("Google kimlik dosyası geçerli bir JSON değil") from None
    if not isinstance(data, dict):
        raise CredentialsMissing("Google kimlik dosyası beklenen biçimde değil")
    return parse_credentials(data, hashlib.sha256(raw).hexdigest())


def _text(data: dict, key: str) -> str | None:
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_credentials(data: dict, fingerprint: str) -> GoogleCredentials:
    kind = _text(data, "type")
    if kind == "authorized_user":
        client_id, secret, refresh = _text(data, "client_id"), _text(data, "client_secret"), _text(data, "refresh_token")
        if not (client_id and secret and refresh):
            raise CredentialsMissing("ADC dosyasında client_id, client_secret ya da refresh_token eksik")
        return GoogleCredentials(
            kind, fingerprint, client_id=client_id, client_secret=Secret(secret), refresh_token=Secret(refresh),
            quota_project_id=_text(data, "quota_project_id"),
        )  # fmt: skip
    if kind == "service_account":
        email, key = _text(data, "client_email"), _text(data, "private_key")
        if not (email and key) or not _EMAIL.fullmatch(email):
            raise CredentialsMissing("Service account dosyasında client_email ya da private_key eksik veya hatalı")
        token_uri = _text(data, "token_uri") or TOKEN_URL
        if token_uri not in _TOKEN_URIS:
            raise CredentialsMissing("Service account dosyasındaki token_uri Google'ın token adresi değil")
        return GoogleCredentials(
            kind, fingerprint, client_email=email, private_key=Secret(key), private_key_id=_text(data, "private_key_id"),
            quota_project_id=_text(data, "quota_project_id"), project_id=_text(data, "project_id"),
        )  # fmt: skip
    raise CredentialsMissing(
        f"Desteklenmeyen Google kimlik türü: {str(kind)[:40]!r}. `gcloud auth application-default login` ile oluşan "
        "dosya (authorized_user) ya da bir service account anahtarı (service_account) kullanın"
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def signed_jwt(credentials: GoogleCredentials, scope: str, now: float) -> str:
    """RS256-signed assertion for the JWT bearer grant."""
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    assert credentials.private_key is not None and credentials.client_email is not None
    header = {"alg": "RS256", "typ": "JWT", **({"kid": credentials.private_key_id} if credentials.private_key_id else {})}
    claims = {
        "iss": credentials.client_email, "scope": scope, "aud": TOKEN_URL,
        "iat": int(now), "exp": int(now) + JWT_LIFETIME_SECONDS,
    }  # fmt: skip
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}".encode("ascii")
    try:
        key = serialization.load_pem_private_key(credentials.private_key.reveal().encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise CredentialsMissing("Service account anahtarı okunamadı (PEM biçiminde bir RSA anahtarı bekleniyor)") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CredentialsMissing("Service account anahtarı bir RSA anahtarı değil")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


@dataclass
class _Token:
    value: Secret
    expires: float


class TokenCache:
    """In-memory access-token cache shared by provider instances, keyed by credential fingerprint and scope."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._tokens: dict[str, _Token] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Secret | None:
        with self._lock:
            token = self._tokens.get(key)
            if token is None or token.expires <= self.clock():
                self._tokens.pop(key, None)
                return None
            return token.value

    def put(self, key: str, value: Secret, ttl_seconds: float) -> None:
        with self._lock:
            self._tokens[key] = _Token(value, self.clock() + ttl_seconds)

    def drop(self, key: str) -> None:
        with self._lock:
            self._tokens.pop(key, None)


TOKENS = TokenCache()


class GoogleTokenSource:
    def __init__(
        self,
        http: SecureHttpClient,
        scope: str,
        tokens: TokenCache | None = None,
        loader: Callable[[], GoogleCredentials] = load_credentials,
    ):
        self._http = http
        self._scope = scope
        self._tokens = tokens or TOKENS
        self._loader = loader
        self._credentials: GoogleCredentials | None = None

    @property
    def credentials(self) -> GoogleCredentials:
        if self._credentials is None:
            self._credentials = self._loader()
        return self._credentials

    def _key(self) -> str:
        return f"{self.credentials.fingerprint}|{self._scope}"

    def invalidate(self) -> None:
        self._tokens.drop(self._key())

    def token(self) -> Secret:
        credentials, key = self.credentials, self._key()
        if (cached := self._tokens.get(key)) is not None:
            return cached
        if credentials.kind == "authorized_user":
            assert credentials.client_id and credentials.client_secret and credentials.refresh_token
            fields = [
                ("grant_type", "refresh_token"), ("client_id", credentials.client_id),
                ("client_secret", credentials.client_secret.reveal()),
                ("refresh_token", credentials.refresh_token.reveal()),
            ]  # fmt: skip
        else:
            fields = [("grant_type", JWT_BEARER), ("assertion", signed_jwt(credentials, self._scope, self._tokens.clock()))]
        response = self._http.post_form(TOKEN_URL, fields, headers={"Accept": "application/json"})
        if response.status in (400, 401, 403):
            detail = _oauth_error(response.content)
            raise AuthenticationFailed(
                f"Google kimlik bilgisi kabul edilmedi ({redact(detail, credentials.secrets())}). "
                "`gcloud auth application-default login` komutunu yeniden çalıştırın ya da service account anahtarını "
                "kontrol edin"
            )
        if 300 <= response.status < 400:
            raise UnexpectedResponse(f"Google token servisi {response.status} yönlendirmesi döndürdü")
        if response.status != 200:
            raise ProviderError(f"Google erişim belirteci alınamadı: HTTP {response.status}")
        try:
            doc = json.loads(response.content)
        except ValueError:
            raise UnexpectedResponse("Google token servisi JSON döndürmedi") from None
        value = doc.get("access_token") if isinstance(doc, dict) else None
        if not isinstance(value, str) or not _ACCESS_TOKEN.fullmatch(value):
            raise UnexpectedResponse("Google token servisi beklenen biçimde bir erişim belirteci döndürmedi")
        expires_in = doc.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, int | float) and not isinstance(expires_in, bool) else 3600.0
        token = Secret(value)
        self._tokens.put(key, token, max(60.0, lifetime - TOKEN_MARGIN_SECONDS))
        return token


def _oauth_error(content: bytes) -> str:
    """`error` / `error_description` of an OAuth error body (short, never the request)."""
    try:
        doc = json.loads(content)
    except ValueError:
        return "yanıt çözümlenemedi"
    if not isinstance(doc, dict):
        return "yanıt çözümlenemedi"
    parts = [str(doc.get(k))[:120] for k in ("error", "error_description") if isinstance(doc.get(k), str)]
    return ": ".join(parts) or "ayrıntı yok"
