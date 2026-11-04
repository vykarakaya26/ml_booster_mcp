"""The local copy is a per-user opt-in: nothing starts it but the user, on every surface."""

import inspect
import json
from datetime import date

import pandas as pd
import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from fakes import (
    BrandFactory,
    FakeMirrorSource,
    FakeTermLookup,
    InMemoryEvidenceStore,
    InMemoryObservationStore,
    InMemoryPanelStore,
    StaticSearchTerms,
)
from mlbooster.application.errors import CredentialsMissing
from mlbooster.application.use_cases.trends_mirror import TrendsMirrorControl, offer_text
from mlbooster.bootstrap import Container
from mlbooster.domain.errors import InvalidInput
from mlbooster.infrastructure.storage.trends_mirror import TrendsMirror
from mlbooster.interfaces import cli, mcp_server
from mlbooster.interfaces.presenters import mirror_lines
from mlbooster.interfaces.web.app import create_app

TOKEN = "t" * 32
BASE = "http://127.0.0.1:8765"
HEADERS = {"X-MLB-Token": TOKEN, "Origin": BASE}


@pytest.fixture
def state(tmp_path):
    return TrendsMirror(tmp_path / "mirror" / "bq_trends")


@pytest.fixture
def source(state):
    return FakeMirrorSource(state)


@pytest.fixture
def control(state, source):
    return TrendsMirrorControl(state, lambda: source)


@pytest.fixture
def container(catalog_repo, state, source):
    return Container(
        catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(),
        overrides=catalog_repo, evidence_store=InMemoryEvidenceStore(), search_terms=StaticSearchTerms(),
        term_lookup=FakeTermLookup, mirror_state=state, mirror_source=lambda: source,
    )  # fmt: skip


def test_off_by_default_and_status_copies_nothing(control, state, source):
    status = control.status()
    assert status == {"state": "off", "days": 0, "first_day": None, "last_day": None, "disk_mb": 0.0}
    assert source.synced == 0 and source.estimates == 0 and state.choice() is None


def test_offer_states_what_it_does_and_costs(control, source):
    offer = control.status(offer=True)["offer"]
    assert source.synced == 0 and source.estimates == 1
    for part in ("Türkiye satırları", "30 günle sınırlı kalmaz", "42,2 GiB", "ücretsiz aylık 1 TiB", "MB yer kaplar", "yalnızca yeni günleri"):
        assert part in offer


def test_offer_falls_back_to_the_measured_figure(state):
    def no_google():
        raise CredentialsMissing("kimlik yok")

    offer = TrendsMirrorControl(state, no_google).status(offer=True)["offer"]
    assert "son canlı ölçüm" in offer and "42,2 GiB" in offer


def test_no_offer_when_the_copy_is_on(control):
    control.enable()
    assert "offer" not in control.status(offer=True)


def test_enable_update_and_disable(control, state, source):
    out = control.enable()
    assert out["state"] == "on" and out["days_added"] == 2 and out["gib"] == 2.0 and state.choice() == "enabled"
    assert out["first_day"] == "2026-11-01" and out["last_day"] == "2026-11-02" and out["disk_mb"] >= 0
    assert control.update()["state"] == "on" and source.synced == 2
    out = control.disable()
    assert out["state"] == "declined" and out["days"] == 0 and state.size_bytes() == 0 and "removed_mb" in out


def test_answer_is_kept_only_after_a_successful_copy(state):
    def no_google():
        raise CredentialsMissing("kimlik yok")

    with pytest.raises(CredentialsMissing):
        TrendsMirrorControl(state, no_google).enable()
    assert state.choice() is None


def test_update_needs_a_yes_first(control):
    with pytest.raises(InvalidInput, match="kapalı"):
        control.update()


def test_declining_is_remembered(control, state):
    assert control.decline()["state"] == "declined" and state.choice() == "declined"


def test_offer_text_never_shows_zero_disk():
    assert "yaklaşık 1 MB" in offer_text(1024**3, True, 0)


def test_plain_brand_and_search_runs_never_start_a_copy(container, source):
    container.analyze_brand("Togg")
    assert container.analyze_brand("Togg", search_interest=True)["search_interest"]["data_source"]["mode"] == "bigquery"
    assert container.analyze_brand("Migros", search_interest=True)["status"] == "not_found"
    assert source.synced == 0 and source.estimates == 0


def test_output_says_when_the_copy_was_used(catalog_repo, state, source):
    class Local(FakeTermLookup):
        def data_source(self):
            return {"mode": "local_copy", "label": "Yerel kopya; yalnızca eksik günler BigQuery'den alındı."}

    c = Container(
        catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(),
        overrides=catalog_repo, search_terms=StaticSearchTerms(), term_lookup=Local,
    )  # fmt: skip
    block = c.analyze_brand("Togg", search_interest=True)["search_interest"]
    assert block["data_source"]["mode"] == "local_copy" and "yerel kopyadaki" in block["coverage"]["note"]


def test_container_without_a_copy_reports_it(catalog_repo):
    c = Container(catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(), overrides=catalog_repo)
    with pytest.raises(Exception, match="yapılandırılmamış"):
        _ = c.trends_mirror


def test_mirror_lines():
    assert mirror_lines({"state": "off", "days": 0, "disk_mb": 0.0}) == ["Yerel kopya kapalı."]
    assert "yarım kalmış 3 gün" in " ".join(mirror_lines({"state": "off", "days": 3, "disk_mb": 1.0}))
    assert "Tercihiniz kaydedildi" in mirror_lines({"state": "declined", "days": 0, "disk_mb": 0.0})[0]
    assert "2 gün" in mirror_lines({"state": "on", "days": 2, "first_day": "a", "last_day": "b", "disk_mb": 1.0})[0]


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def run(monkeypatch, container):
    monkeypatch.setattr(cli, "container", lambda: container)

    def invoke(*args, text=None):
        return CliRunner().invoke(cli.app, ["trends-mirror", *args], input=text)

    return invoke


def test_cli_status(run, source):
    out = run("status")
    assert out.exit_code == 0 and "Yerel kopya kapalı" in out.output and source.estimates == 0
    assert "42,2 GiB" in run("status", "--offer").output


def test_cli_enable_asks_first(run, source, state):
    out = run("enable", text="n\n")
    assert out.exit_code == 0 and "42,2 GiB" in out.output and "Oluşturulmadı" in out.output
    assert source.synced == 0 and state.choice() == "declined"
    out = run("enable", text="y\n")
    assert out.exit_code == 0 and source.synced == 1 and state.choice() == "enabled" and "2 yeni gün" in out.output
    assert "zaten açık" in run("enable").output


def test_cli_enable_with_yes_and_update(run, source):
    assert run("enable", "--yes").exit_code == 0 and source.estimates == 0
    assert "2 yeni gün" in run("update").output


def test_cli_update_when_off_fails(run):
    out = run("update")
    assert out.exit_code == 1 and "kapalı" in out.output


def test_cli_disable_asks_before_deleting(run, state):
    run("enable", "--yes")
    out = run("disable", text="n\n")
    assert "Silinmedi" in out.output and state.days("top")
    out = run("disable", "--yes")
    assert out.exit_code == 0 and "silindi" in out.output and not state.days("top")


def test_cli_errors_are_safe(monkeypatch, catalog_repo):
    bare = Container(catalog=catalog_repo, providers=BrandFactory(), observations=InMemoryObservationStore(), panels=InMemoryPanelStore(), overrides=catalog_repo)
    monkeypatch.setattr(cli, "container", lambda: bare)
    for args in (["status"], ["enable", "--yes"], ["update"], ["disable", "--yes"]):
        out = CliRunner().invoke(cli.app, ["trends-mirror", *args])
        assert out.exit_code == 1 and "yapılandırılmamış" in out.output


# --- MCP ---------------------------------------------------------------------------------


def test_mcp_tools(monkeypatch, container, source, state):
    monkeypatch.setattr(mcp_server, "container", lambda: container)
    assert mcp_server.trends_mirror_status()["state"] == "off" and source.synced == 0
    assert "42,2 GiB" in mcp_server.trends_mirror_status(offer=True)["offer"]
    with pytest.raises(Exception, match="kapalı"):
        mcp_server.trends_mirror_update()
    assert source.synced == 0
    assert mcp_server.trends_mirror_update(enable=True)["state"] == "on" and state.choice() == "enabled"
    assert mcp_server.trends_mirror_update()["days_added"] == 2
    json.dumps(mcp_server.trends_mirror_status())


def test_mcp_tool_tells_the_assistant_to_ask_first():
    doc = inspect.getdoc(mcp_server.trends_mirror_update.__wrapped__ if hasattr(mcp_server.trends_mirror_update, "__wrapped__") else mcp_server.trends_mirror_update)
    assert "KENDİLİĞİNDEN ÇAĞIRMA" in doc and "onay" in doc


# --- Web ---------------------------------------------------------------------------------


@pytest.fixture
def client(container):
    return TestClient(create_app(lambda: container, token=TOKEN), base_url=BASE, raise_server_exceptions=False)


def post(client, action):
    return client.post("/api/trends-mirror", json={"action": action}, headers=HEADERS)


def test_web_flow(client, source, state):
    res = post(client, "status")
    assert res.status_code == 200 and "42,2 GiB" in res.json()["offer"] and source.synced == 0
    assert post(client, "decline").json()["state"] == "declined"
    assert post(client, "enable").json()["state"] == "on" and source.synced == 1
    assert post(client, "update").json()["days_added"] == 2
    assert post(client, "disable").json()["state"] == "declined" and not state.days("top")


def test_web_refuses_other_actions_and_needs_the_token(client, source):
    assert post(client, "sil").status_code == 400
    assert client.post("/api/trends-mirror", json={"action": "enable"}).status_code in {401, 403}
    assert source.synced == 0


def test_nothing_is_read_from_a_disabled_copy_day(state):
    state.write_list("top", date(2026, 11, 1), pd.DataFrame({"term": ["togg"], "rank": [1]}))
    state.delete()
    assert state.lists("top", date.min, date.max).empty
