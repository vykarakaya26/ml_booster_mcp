import httpx
import pytest
import respx

from mlbooster.application.errors import ProviderUnavailable, UnexpectedResponse
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.secret_store import EnvSecretProvider, Secret, redact

URL = "https://example.test/data"


def client(**kw):
    return SecureHttpClient(rps=None, max_retries=2, sleep=lambda _: None, **kw)


def test_rejects_plain_http():
    with pytest.raises(UnexpectedResponse, match="HTTPS"):
        client().get("http://example.test/")


@respx.mock
def test_retries_429_then_succeeds():
    route = respx.get(URL).mock(side_effect=[httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, content=b"ok")])
    assert client().get(URL).content == b"ok" and route.call_count == 2


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
