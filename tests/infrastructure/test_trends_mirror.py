"""Local copy of the Google Trends tables: storage and download, against respx mocks with synthetic values."""

import json
import stat
from datetime import date, timedelta

import httpx
import pandas as pd
import pytest
import respx

from mlbooster.application.errors import UnexpectedResponse
from mlbooster.infrastructure.http_client import SecureHttpClient
from mlbooster.infrastructure.providers.bigquery_mirror import BigQueryMirror, TermLookups, _runs
from mlbooster.infrastructure.providers.bigquery_terms import ROW_COLUMNS, BigQueryTermLookup
from mlbooster.infrastructure.providers.bigquery_trends import DEFAULT_MAX_BYTES
from mlbooster.infrastructure.providers.google_auth import TOKEN_URL, TokenCache
from mlbooster.infrastructure.secret_store import EnvSecretProvider
from mlbooster.infrastructure.storage.trends_mirror import TrendsMirror

PROJECT = "benim-projem-123"
QUERY_URL = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries"
TODAY = date(2026, 11, 4)
LIST_COLUMNS = ["refresh_date", "term", "rank"]


def backfill(term, refresh, score=50.0):
    return pd.DataFrame(
        [[term, refresh, date(2026, 10, 18), "", None, score], [term, refresh, date(2026, 10, 18), "TR-34", "İstanbul", 100.0]],
        columns=ROW_COLUMNS,
    )


@pytest.fixture
def mirror(tmp_path):
    return TrendsMirror(tmp_path / "data" / "mirror" / "bq_trends")


# --- Storage -----------------------------------------------------------------------------


def test_choice_is_remembered_and_private(mirror, tmp_path):
    assert mirror.choice() is None
    mirror.set_choice("enabled", "2026-11-04T10:00:00+00:00")
    assert TrendsMirror(mirror._root).choice() == "enabled"
    assert stat.S_IMODE(mirror._settings.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "data" / "mirror").stat().st_mode) == 0o700


@pytest.mark.parametrize("text", ["{bad", "[]", '{"choice": "belki"}'])
def test_unreadable_choice_counts_as_never_asked(mirror, text):
    mirror.set_choice("enabled", "x")
    mirror._settings.write_text(text)
    assert mirror.choice() is None


def test_lists_keep_their_refresh_date(mirror):
    mirror.write_list("top", date(2026, 11, 1), pd.DataFrame({"term": ["togg", "faiz"], "rank": [1, 2]}))
    mirror.write_list("top", date(2026, 11, 2), pd.DataFrame({"term": [], "rank": []}))
    frame = mirror.lists("top", date.min, date.max)
    assert mirror.days("top") == {date(2026, 11, 1), date(2026, 11, 2)}
    assert frame["refresh_date"].tolist() == [date(2026, 11, 1)] * 2 and frame["term"].tolist() == ["togg", "faiz"]
    assert mirror.lists("rising", date.min, date.max).empty and mirror.days("rising") == set()


def test_day_files_with_foreign_names_are_ignored(mirror):
    mirror.write_list("top", date(2026, 11, 1), pd.DataFrame({"term": ["togg"], "rank": [1]}))
    (mirror._root / "top" / "lists" / "day=bozuk.parquet").write_bytes(b"x")
    assert mirror.days("top") == {date(2026, 11, 1)}


def test_newest_refresh_of_a_term_wins(mirror):
    assert mirror.write_backfill("top", backfill("togg", date(2026, 10, 1), 80.0)) == 1
    assert mirror.write_backfill("top", backfill("togg", date(2026, 11, 1), 40.0)) == 1
    assert mirror.write_backfill("top", backfill("togg", date(2026, 10, 15), 10.0)) == 0
    rows = mirror.backfill("top", ["togg"])
    assert rows[rows["region_code"] == ""]["score"].tolist() == [40.0]
    assert mirror.terms("top")["refresh_date"].tolist() == [date(2026, 11, 1)]
    assert mirror.backfill("top", ["yok"]).empty and mirror.write_backfill("top", backfill("togg", date(2026, 11, 1)).iloc[0:0]) == 0


def test_size_and_delete_keep_the_answer(mirror):
    assert mirror.size_bytes() == 0
    mirror.write_list("top", date(2026, 11, 1), pd.DataFrame({"term": ["togg"], "rank": [1]}))
    mirror.write_backfill("top", backfill("togg", date(2026, 11, 1)))
    mirror.set_choice("declined", "x")
    assert mirror.size_bytes() > 0
    mirror.delete()
    assert mirror.size_bytes() == 0 and mirror.days("top") == set() and mirror.choice() == "declined"


def test_runs_are_consecutive_and_at_most_seven_days():
    days = [date(2026, 10, d) for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 20)]
    assert _runs(days) == [(date(2026, 10, 1), date(2026, 10, 7)), (date(2026, 10, 8), date(2026, 10, 9)), (date(2026, 10, 20), date(2026, 10, 20))]


# --- Download ----------------------------------------------------------------------------


class FakeBigQuery:
    def __init__(self):
        self.lists = {date(2026, 11, 1): ["togg", "faiz"], date(2026, 10, 30): ["togg"]}
        self.calls = []

    def __call__(self, request):
        sent = json.loads(request.content)
        p = {q["name"]: q["parameterValue"].get("value") for q in sent["queryParameters"]}
        since, until = date.fromisoformat(p["since"]), date.fromisoformat(p["until"])
        assert p["country"] == "TR" and sent["maximumBytesBilled"] == str(DEFAULT_MAX_BYTES)
        assert sent["dryRun"] or (until - since).days < 7
        assert "togg" not in sent["query"] and "country_name" not in sent["query"] and "percent_gain" not in sent["query"]
        kind = "backfill" if "GROUPING SETS" in sent["query"] else "list"
        self.calls.append((kind, p["since"], p["until"], sent["dryRun"]))
        if sent["dryRun"]:
            return httpx.Response(200, json={"jobComplete": True, "totalBytesProcessed": str(1024**3)})
        days = {d: t for d, t in self.lists.items() if since <= d <= until}
        if kind == "list":
            rows = [[d.isoformat(), t, str(i + 1)] for d, terms in sorted(days.items()) for i, t in enumerate(terms)]
            return self._answer(LIST_COLUMNS, rows)
        rows = []
        for term in {t for terms in days.values() for t in terms}:
            refresh = max(d for d, terms in days.items() if term in terms).isoformat()
            rows += [[term, refresh, "2026-10-18", None, None, "26.5"], [term, refresh, "2026-10-18", "TR-34", "İstanbul", "100"]]
        return self._answer(ROW_COLUMNS, rows)

    @staticmethod
    def _answer(columns, rows):
        return httpx.Response(200, json={
            "jobComplete": True, "jobReference": {"jobId": "job_1", "location": "US"}, "totalBytesBilled": str(1024**3),
            "schema": {"fields": [{"name": c, "type": "STRING"} for c in columns]},
            "rows": [{"f": [{"v": v} for v in r]} for r in rows],
        })  # fmt: skip


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "adc.json"
    path.write_text(json.dumps({"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r"}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    monkeypatch.setenv("BQ_PROJECT_ID", PROJECT)
    trends = FakeBigQuery()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "ya29.token-0123456789abcdef", "expires_in": 3600}))
        mock.post(QUERY_URL).mock(side_effect=trends)
        yield trends


@pytest.fixture
def make(catalog, mirror):
    def factory(today=TODAY):
        return BigQueryMirror(
            catalog.source("bq_trends"), http=SecureHttpClient(rps=None, max_retries=0), secrets=EnvSecretProvider(),
            tokens=TokenCache(), today=today, mirror=mirror,
        )  # fmt: skip

    return factory


def test_estimate_only_dry_runs_and_writes_nothing(make, fake, mirror):
    out = make().estimate()
    assert all(call[3] for call in fake.calls) and mirror.size_bytes() == 0
    assert out["days"] == 36 and out["bytes"] == len(fake.calls) * 1024**3 and len(fake.calls) == 4  # both queries of both tables


def test_sync_copies_lists_and_backfills_once(make, fake, mirror):
    out = make().sync()
    assert out["days_added"] == 2 and out["queries"] > 0 and out["bytes_billed"] > 0
    assert date(2026, 11, 1) in mirror.days("top") and date(2026, 10, 15) in mirror.days("top")  # settled empty day is recorded
    assert TODAY not in mirror.days("top")  # may still appear upstream
    assert set(mirror.terms("top")["term"]) == {"togg", "faiz"}
    rows = mirror.backfill("top", ["togg"])
    assert set(rows["region_code"]) == {"", "TR-34"} and set(rows["refresh_date"]) == {date(2026, 11, 1)}
    first = len(fake.calls)
    make().sync()
    assert {(k, s) for k, s, _, _ in fake.calls[first:]} == {("list", "2026-11-02")}  # only the unsettled days, no backfill


def test_list_days_are_written_after_their_backfill(make, fake, mirror, monkeypatch):
    def failing(*args):
        raise UnexpectedResponse("yarıda kaldı")

    provider = make()
    monkeypatch.setattr(provider, "_read_backfill", failing)
    with pytest.raises(UnexpectedResponse):
        provider.sync()
    assert date(2026, 11, 1) not in mirror.days("rising") | mirror.days("top")


@pytest.mark.parametrize(
    ("kind", "rows", "match"),
    [
        ("list", [["bad", "togg", "1"]], "tanınmayan"),
        ("backfill", [["togg", "2026-11-01", "25.10.2026", None, None, "1"]], "tanınmayan"),
        ("backfill", [["togg", "2026-11-01", "2026-10-18", None, None, "120"]], "0–100"),
    ],
)
def test_bad_values_are_refused(make, fake, monkeypatch, kind, rows, match):
    original = fake._answer

    def answer(columns, real_rows):
        wanted = (kind == "list") == (columns == LIST_COLUMNS)
        return original(columns, rows if wanted and real_rows else real_rows)

    monkeypatch.setattr(fake, "_answer", answer)
    with pytest.raises(UnexpectedResponse, match=match):
        make().sync()


@pytest.mark.parametrize("columns", [["x"]])
def test_unexpected_columns_are_refused(make, fake, monkeypatch, columns):
    original = fake._answer
    monkeypatch.setattr(fake, "_answer", lambda c, r: original(columns, []))
    with pytest.raises(UnexpectedResponse, match="kolon"):
        make().sync()


def test_backfill_columns_are_checked(make, fake, monkeypatch):
    original = fake._answer
    monkeypatch.setattr(fake, "_answer", lambda c, r: original(c if c == LIST_COLUMNS else ["x"], r if c == LIST_COLUMNS else []))
    with pytest.raises(UnexpectedResponse, match="kolon"):
        make().sync()


def test_lookup_reads_the_copy_and_fetches_only_missing_days(make, fake):
    provider = make()
    listing = provider.listed_terms()
    assert set(listing["table"]) == {"top", "rising"} and set(listing["term"]) == {"togg", "faiz"}
    assert provider.interest("top", {"togg": date(2026, 10, 30)})["refresh_date"].tolist() == [date(2026, 11, 1)] * 2
    source = provider.data_source()
    assert source["mode"] == "local_copy" and "yalnızca eksik günler" in source["label"]


def test_history_outlives_the_upstream_window(make, fake):
    make().sync()
    fake.lists = {}
    later = TODAY + timedelta(days=60)
    listing = make(later).listed_terms()
    assert date(2026, 11, 1) in set(listing["refresh_date"])


def test_copy_answers_without_google_credentials(make, fake, monkeypatch, tmp_path):
    make().sync()
    calls = len(fake.calls)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "yok"))
    provider = make()
    assert not provider.listed_terms().empty and len(fake.calls) == calls
    assert "kimlik" in provider.data_source()["label"]


def test_a_failed_update_falls_back_to_the_copy(make, fake, monkeypatch):
    make().sync()
    provider = make()
    monkeypatch.setattr(provider, "_read_lists", lambda *a: (_ for _ in ()).throw(UnexpectedResponse("kaynak bozuk")))
    assert not provider.listed_terms().empty and "kaynak bozuk" in provider.data_source()["label"]


def test_lookups_follow_the_answer(catalog, mirror):
    lookups = TermLookups(lambda: catalog.source("bq_trends"), mirror)
    assert type(lookups.current()) is BigQueryTermLookup and lookups.current() is lookups.direct()
    assert lookups.current().data_source()["mode"] == "bigquery"
    mirror.set_choice("enabled", "x")
    assert isinstance(lookups.current(), BigQueryMirror) and lookups.current() is lookups.local()
    mirror.set_choice("declined", "x")
    assert type(lookups.current()) is BigQueryTermLookup
