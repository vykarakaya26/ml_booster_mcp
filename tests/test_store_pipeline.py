from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd
import pytest
import respx

from mlbooster.pipeline import Workspace
from mlbooster.schema import validate_silver
from mlbooster.store.silver import SilverStore


def _silver(value, vintage, dates=("2026-08-31",)):
    return pd.DataFrame(
        {
            "source_id": "evds3", "dataset_id": "x", "series_id": "TP.FG.J0", "entity_id": "",
            "entity_name": None, "metric": "level", "date": [date.fromisoformat(d) for d in dates],
            "freq": "M", "value": value, "unit": "index", "currency": None, "is_proxy": False,
            "vintage_ts": vintage, "publication_lag_days": 3,
        }
    )  # fmt: skip


def test_silver_latest_vintage_and_as_of(tmp_path):
    store = SilverStore(tmp_path)
    v1 = datetime(2026, 9, 5, tzinfo=UTC)
    v2 = v1 + timedelta(days=30)
    store.write(_silver(100.0, v1))
    store.write(_silver(101.0, v2))  # revizyon
    assert store.read("evds3")["value"].tolist() == [101.0]
    assert store.read("evds3", as_of=v1 + timedelta(days=1))["value"].tolist() == [100.0]
    assert store.read("tuik").empty


def test_validate_silver_rejects_duplicates_and_nulls():
    v = datetime(2026, 9, 5, tzinfo=UTC)
    with pytest.raises(ValueError, match="tekrar"):
        validate_silver(pd.concat([_silver(1.0, v), _silver(1.0, v)]))
    with pytest.raises(ValueError, match="null"):
        validate_silver(_silver(None, v))


@respx.mock
def test_build_panel_end_to_end(tmp_path, monkeypatch, catalog):
    monkeypatch.setenv("EVDS_API_KEY", "test-key-not-real")
    months = pd.date_range("2024-01-31", "2026-09-30", freq="ME")
    days = pd.bdate_range("2024-01-01", "2026-09-30")

    def responder(request):
        url = str(request.url)
        if "TP.FG.J0" in url:
            items = [{"Tarih": f"{d.year}-{d.month}", "TP_FG_J0": str(100 + i)} for i, d in enumerate(months)]
        elif "TP.DK.USD.A.YTL" in url:
            items = [{"Tarih": d.strftime("%d-%m-%Y"), "TP_DK_USD_A_YTL": str(30 + i / 100)} for i, d in enumerate(days)]
        else:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": items})

    route = respx.get(url__startswith="https://evds3.tcmb.gov.tr/").mock(side_effect=responder)
    ws = Workspace(root=tmp_path, catalog=catalog)
    result = ws.build_panel("telekom", start=date(2024, 1, 1), end=date(2026, 9, 30))

    assert set(result.panel.columns) == {"tufe_yoy", "usdtry_logdiff"}
    assert "haberlesme_tufe" in result.skipped
    # TÜFE 3 gün gecikmeli: Eylül değeri Ekim satırına düşer, panel Eylül'de biter → son değer Ağustos'unki.
    assert result.panel["tufe_yoy"].dropna().index.min() == pd.Timestamp("2025-02-28")
    calls_first = route.call_count
    ws.build_panel("telekom", start=date(2024, 1, 1), end=date(2026, 9, 30))
    assert route.call_count == calls_first  # ikinci çalışma silver önbelleğinden
    out = result.write(tmp_path / "gold" / "telekom.parquet")
    assert pd.read_parquet(out).shape == result.panel.shape
