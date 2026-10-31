"""Dış (kurumsal) tahminler: büyük bankalar, uluslararası kuruluşlar, TCMB anketi.

Bu tahminler **veri değil, varsayım/girdi**dir. Raporda kaynak, yayın tarihi ve birincil/ikincil
ayrımıyla gösterilir; konsensüs, görüş dağılımı genişse ortalama yerine medyanla özetlenir.

Şema (external_forecasts.json):
{
  "retrieved": "YYYY-AA-GG",
  "forecasts": [
    {"institution": "JPMorgan", "variable": "usdtry|cpi_yoy|policy_rate|...", "horizon": "2026-12",
     "value": 43.5, "unit": "TRY|%", "published": "YYYY-AA-GG", "source_url": "https://...",
     "primary": false, "note": "..."}
  ]
}
"""

from __future__ import annotations

import statistics
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from mlb_report import html as H
from mlb_report.data import read_json

STALE_DAYS = 92
DISPERSION_RULE = 0.10  # IQR / |medyan| bu oranı aşarsa konsensüs = medyan (ortalama aykırı görüşe duyarlı)
VARIABLES = {
    "usdtry": "USD/TRY (dönem sonu)",
    "eurtry": "EUR/TRY (dönem sonu)",
    "cpi_yoy": "TÜFE yıllık % (dönem sonu)",
    "policy_rate": "Politika faizi % (dönem sonu)",
    "gdp_growth": "GSYH büyümesi %",
}


class ExternalForecastError(ValueError):
    pass


def load(path: str | Path) -> dict:
    data = read_json(path)
    validate(data)
    return data


def validate(data: dict) -> None:
    items = data.get("forecasts") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ExternalForecastError("external_forecasts.json: 'forecasts' listesi gerekli")
    for f in items:
        missing = {"institution", "variable", "horizon", "value", "published", "source_url"} - set(f)
        if missing:
            raise ExternalForecastError(f"Eksik alanlar {sorted(missing)}: {f}")
        if not isinstance(f["value"], int | float):
            raise ExternalForecastError(f"value sayı olmalı: {f}")
        try:
            date.fromisoformat(str(f["published"]))
        except ValueError:
            raise ExternalForecastError(f"published YYYY-AA-GG olmalı: {f['published']}") from None
        if urlsplit(str(f["source_url"])).scheme not in {"https", "http"}:
            raise ExternalForecastError(f"source_url http(s) olmalı: {f['source_url']}")


def consensus(values: list[float]) -> dict:
    """Ortalama, medyan, çeyrekler ve hangisinin konsensüs sayıldığı (dağılım kuralı)."""
    n = len(values)
    srt = sorted(values)
    median = statistics.median(srt)
    mean = statistics.fmean(srt)
    if n >= 4:
        q1, _, q3 = statistics.quantiles(srt, n=4, method="inclusive")
    else:
        q1, q3 = srt[0], srt[-1]
    iqr = q3 - q1
    dispersed = n >= 3 and median != 0 and iqr / abs(median) > DISPERSION_RULE
    return {"n": n, "mean": mean, "median": median, "min": srt[0], "max": srt[-1], "q1": q1, "q3": q3,
            "dispersed": dispersed, "consensus": median if dispersed or n < 3 else mean,
            "rule": "medyan (görüş dağılımı geniş)" if dispersed else ("medyan (az gözlem)" if n < 3 else "ortalama")}  # fmt: skip


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def section(data: dict, today: date | None = None, latest: dict[str, tuple[str, float]] | None = None) -> str:
    """`latest`: değişken → (ay, son gerçekleşen değer); yıl sonuna ima edilen değişimi göstermek için."""
    today = today or date.today()
    groups: dict[tuple[str, str], list[dict]] = {}
    for f in data["forecasts"]:
        groups.setdefault((f["variable"], f["horizon"]), []).append(f)

    summary_rows, detail_rows = [], []
    for (var, horizon), items in sorted(groups.items()):
        c = consensus([float(f["value"]) for f in items])
        last = (latest or {}).get(var)
        implied = f"{(c['consensus'] / last[1] - 1) * 100:+.1f}% ({H.esc(last[0])}: {_fmt(last[1])})" if last and var in {"usdtry", "eurtry"} and last[1] else "—"
        summary_rows.append((
            H.esc(VARIABLES.get(var, var)), H.esc(horizon), c["n"], f"<strong>{_fmt(c['consensus'])}</strong>",
            H.esc(c["rule"]), _fmt(c["median"]), _fmt(c["mean"]), f"{_fmt(c['min'])} – {_fmt(c['max'])}", implied,
        ))  # fmt: skip
        for f in sorted(items, key=lambda x: x["published"], reverse=True):
            age = (today - date.fromisoformat(f["published"])).days
            flag = ' <span class="badge serious"><span aria-hidden="true">▲</span> bayat</span>' if age > STALE_DAYS else ""
            kind = "birincil" if f.get("primary") else "ikincil (haber/özet)"
            link = f'<a href="{H.esc(f["source_url"])}" rel="noopener noreferrer">kaynak</a>'
            detail_rows.append((
                H.esc(f["institution"]), H.esc(VARIABLES.get(var, var)), H.esc(horizon), _fmt(float(f["value"])),
                H.esc(f["published"]) + flag, H.esc(kind), link, H.esc(f.get("note") or ""),
            ))  # fmt: skip

    body = (
        '<p class="muted">Dış tahminler veri değil, <strong>varsayım</strong>tır. Görüş dağılımı genişse '
        f"(çeyrekler arası açıklık medyanın %{int(DISPERSION_RULE * 100)}'undan fazlaysa) konsensüs olarak medyan "
        "kullanılır, çünkü ortalama tek bir aykırı görüşe duyarlıdır. "
        f"{STALE_DAYS} günden eski tahminler 'bayat' diye işaretlenir.</p>"
        + H.table(
            ["Değişken", "Ufuk", "n", "Konsensüs", "Kural", "Medyan", "Ortalama", "Aralık", "Son değere göre"],
            summary_rows, numeric=[2, 3, 5, 6], raw=[0, 1, 3, 4, 7, 8],
        )
        + "<h3>Kurum bazında</h3>"
        + H.table(["Kurum", "Değişken", "Ufuk", "Tahmin", "Yayın", "Kaynak türü", "Bağlantı", "Not"], detail_rows,
                  numeric=[3], raw=[0, 1, 2, 3, 4, 5, 6, 7])  # fmt: skip
    )
    if data.get("retrieved"):
        body += f'<p class="muted">Derlenme tarihi: {H.esc(data["retrieved"])}. Değerler kurum notlarından ve haberlerden derlenmiştir; birincil kaynaktan doğrulayın.</p>'
    return H.section("Dış tahminler ve konsensüs", body, "dis-tahminler")
