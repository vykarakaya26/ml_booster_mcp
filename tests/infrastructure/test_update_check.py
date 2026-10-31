import json

import httpx
import pytest
import respx

from mlbooster.application.use_cases import CheckForUpdates
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.update_check import TAGS_URL, GitHubReleaseSource, parse_version


@pytest.fixture(autouse=True)
def _allow_check(monkeypatch):
    monkeypatch.delenv("MLBOOSTER_NO_UPDATE_CHECK", raising=False)


@respx.mock
def test_latest_tag_and_cache(tmp_path):
    route = respx.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=[{"name": "v0.4.0"}, {"name": "v0.10.1"}, {"name": "nightly"}, {"name": "v0.3.0"}])
    )
    src = GitHubReleaseSource(tmp_path / "u.json", SecureHttpClient(rps=None, max_retries=0))
    assert src.latest_version() == "0.10.1"  # sayısal karşılaştırma (0.10 > 0.4)
    assert src.latest_version() == "0.10.1" and route.call_count == 1  # 24 saat önbellek
    assert json.loads((tmp_path / "u.json").read_text())["latest"] == "0.10.1"


@respx.mock
def test_network_failure_is_silent(tmp_path):
    respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("down"))
    assert GitHubReleaseSource(tmp_path / "u.json", SecureHttpClient(rps=None, max_retries=0)).latest_version() is None


def test_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("MLBOOSTER_NO_UPDATE_CHECK", "1")
    assert GitHubReleaseSource(tmp_path / "u.json").latest_version() is None


def test_check_for_updates_use_case():
    class Src:
        def __init__(self, v):
            self.v = v

        def latest_version(self):
            return self.v

    assert CheckForUpdates("0.4.0", Src("0.5.0"))()["update_available"]
    assert not CheckForUpdates("0.4.0", Src("0.4.0"))()["update_available"]
    assert not CheckForUpdates("0.10.0", Src("0.9.9"))()["update_available"]
    assert CheckForUpdates("0.4.0", Src(None))()["latest"] is None
    assert parse_version("v1.2.3") == (1, 2, 3) and parse_version("1.2") is None
