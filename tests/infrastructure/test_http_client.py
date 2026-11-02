import hashlib
import ssl

import httpx
import pytest
import respx

from mlbooster.application.errors import ProviderUnavailable, UnexpectedResponse
from mlbooster.infrastructure.http_client import INTERMEDIATES_PEM, SecureHttpClient, _tls_context
from mlbooster.infrastructure.secret_store import EnvSecretProvider, Secret, redact

URL = "https://example.test/data"


def client(**kw):
    return SecureHttpClient(rps=None, max_retries=2, sleep=lambda _: None, **kw)


def test_rejects_plain_http():
    with pytest.raises(UnexpectedResponse, match="HTTPS"):
        client().get("http://example.test/")


def test_tls_context_verifies_and_adds_only_the_pinned_intermediates():
    context = _tls_context()
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname
    assert not context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN  # an intermediate is never a trust anchor
    blocks = INTERMEDIATES_PEM.read_text(encoding="ascii").split("-----BEGIN CERTIFICATE-----")[1:]
    digests = {hashlib.sha256(ssl.PEM_cert_to_DER_cert("-----BEGIN CERTIFICATE-----" + b)).hexdigest() for b in blocks}
    # GlobalSign GCC R46 OV TLS CA 2025 (verisistemi.tbb.org.tr omits it from its chain)
    assert digests == {"d160e2de4e56cb10b66c1cb0adcb79cf93c78dcd9db30c2018220262c04063f9"}


@respx.mock
def test_retries_429_then_succeeds():
    route = respx.get(URL).mock(side_effect=[httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, content=b"ok")])
    assert client().get(URL).content == b"ok" and route.call_count == 2


@respx.mock
def test_post_json_sends_body_and_retries():
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})])
    response = client().post_json(URL, [{"id": "İ", "filterValues": ["1"]}], headers={"Accept": "application/json"})
    request = route.calls.last.request
    assert response.status == 200 and route.call_count == 2
    assert request.headers["content-type"] == "application/json" and request.headers["accept"] == "application/json"
    assert request.content.decode("utf-8") == '[{"id": "İ", "filterValues": ["1"]}]'
    with pytest.raises(UnexpectedResponse, match="HTTPS"):
        client().post_json("http://example.test/", {})


@respx.mock
def test_post_form_sends_repeated_fields_urlencoded():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"success": True}))
    response = client().post_form(URL, [("tabloNo", "1"), ("taraf", "10001"), ("taraf", "10009"), ("ad", "Sektör")])
    request = route.calls.last.request
    assert response.status == 200 and request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.content == b"tabloNo=1&taraf=10001&taraf=10009&ad=Sekt%C3%B6r"
    with pytest.raises(UnexpectedResponse, match="HTTPS"):
        client().post_form("http://example.test/", [])


@respx.mock
def test_gives_up_after_retries():
    respx.get(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable, match="503"):
        client().get(URL)


@respx.mock
def test_network_error_message_has_no_internal_detail():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom secret-ish detail"))
    with pytest.raises(ProviderUnavailable) as exc:
        client().get(URL)
    assert "boom" not in str(exc.value)


@respx.mock
def test_response_size_limit():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 2048))
    with pytest.raises(UnexpectedResponse, match="limit|büyük"):
        client(max_bytes=1024).get(URL)


@respx.mock
def test_does_not_follow_redirects():
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": "https://elsewhere.test/"}))
    assert client().get(URL).status == 302


def test_secret_never_rendered(monkeypatch):
    s = Secret("super-secret")
    assert "super-secret" not in repr(s) and "super-secret" not in str(s) and "super-secret" not in f"{s}"
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(s)
    assert redact("x super-secret y", [s]) == "x ******** y"
    monkeypatch.setenv("SOME_KEY", "  ")
    assert EnvSecretProvider().get("SOME_KEY") is None
