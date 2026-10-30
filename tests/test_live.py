"""Canlı contract testleri: pytest -m live (EVDS_API_KEY gerekir)."""

import os
from datetime import date, timedelta

import pytest

from mlbooster.adapters.evds3 import Evds3Adapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("EVDS_API_KEY"), reason="EVDS_API_KEY yok"),
]


def test_evds3_healthcheck(catalog):
    adapter = Evds3Adapter(catalog.source("evds3"))
    result = adapter.healthcheck()
    assert result.status == "ok", result.detail


def test_evds3_seed_series_return_data(catalog):
    adapter = Evds3Adapter(catalog.source("evds3"))
    for seed in catalog.source("evds3").series:
        df = adapter.fetch([seed.code], date.today() - timedelta(days=800), date.today())
        assert not df.empty, seed.code
        assert set(df["freq"]) == {seed.freq}, seed.code


def test_evds3_serielist_schema(catalog):
    df = Evds3Adapter(catalog.source("evds3")).list_series("bie_dkdovizgn")
    assert "TP.DK.USD.A.YTL" in set(df["series_id"])
