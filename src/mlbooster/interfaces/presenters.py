"""Presentation helpers that turn use case outputs into text / JSON.

JSON keys and enum values stay in English (code side); markdown shown to users is Turkish."""

from __future__ import annotations

import math
from dataclasses import asdict

from mlbooster.domain.model import HealthResult, Source
from mlbooster.domain.numfmt import tr_num
from mlbooster.domain.services.quality import QualityReport
from mlbooster.domain.services.sector_map import SectorMap

# Turkish labels for the sector map status codes (domain.services.sector_map).
STATUS_LABELS = {"ready": "hazır", "pending_discovery": "seri kodu eksik", "no_provider": "otomatik çekim yok"}
# Turkish labels for catalog enums shown in the map (domain.model.Access / Verification).
ACCESS_LABELS = {"rest_api": "REST API", "file_download": "dosya indirme", "pdf_report": "PDF rapor", "scrape": "HTML sayfası"}
VERIFICATION_LABELS = {"verified": "canlı doğrulandı", "reported": "yalnızca araştırmada geçiyor", "unverified": "doğrulanmadı"}
# Turkish labels for audit/market share severities and market share enums (domain.services.market_share).
SEVERITY_LABELS = {"high": "kritik", "medium": "uyarı", "info": "bilgi"}
TREND_LABELS = {"gaining": "pay kazanıyor", "losing": "pay kaybediyor", "flat": "yatay", "insufficient_data": "yetersiz gözlem"}
HHI_CLASS_LABELS = {"low": "düşük", "moderate": "orta", "high": "yüksek"}
BASIS_LABELS = {"period": "dönemlik", "ttm": "TTM (son 12 ay toplamı)"}
MAX_COMPARISON_LINES = 3


def _auth_label(source: Source) -> str:
    if not source.auth.env_vars:
        return source.auth.kind.value
    return f"{source.auth.kind.value} ({', '.join(source.auth.env_vars)})"


def source_dict(source: Source, provider_ready: bool | None = None) -> dict:
    out = {
        "id": source.id,
        "name": source.name,
        "owner": source.owner,
        "access": source.access.value,
        "auth": _auth_label(source),
        "frequency": list(source.frequencies),
        "publication_lag_days": source.publication_lag_days,
        "verification_status": source.verification.value,
        "license": source.license.notes,
        "base_url": source.base_url,
    }
    if provider_ready is not None:
        out["provider_ready"] = provider_ready
    return out


def sector_map_dict(m: SectorMap) -> dict:
    return {
        "sector": {"id": m.sector_id, "name": m.sector_name, "ml_use_cases": list(m.ml_use_cases)},
        "sources": [source_dict(e.source, e.provider_ready) | {"roles": list(e.roles)} for e in m.sources],
        "macro_features": [
            {
                "name": e.feature.name,
                "source": e.feature.source,
                "series": e.feature.series,
                "transform": e.feature.transform.value,
                "status": e.status,
                "discover_hint": e.feature.discover_hint,
            }
            for e in m.features
        ],
        "market_share": [asdict(s) for s in m.market_share],
    }


def sector_map_markdown(m: SectorMap) -> str:
    lines = [f"# {m.sector_name} ({m.sector_id}) — veri kaynağı haritası", ""]
    if m.ml_use_cases:
        lines += ["**ML kullanım alanları:** " + ", ".join(m.ml_use_cases), ""]
    lines += [
        "## Kaynaklar",
        "",
        "| Kaynak | Rol | Erişim | Auth | Frekans | Yayın gecikmesi (gün) | Doğrulama | Otomatik çekim |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in m.sources:
        s = e.source
        lines.append(
            f"| {s.name} (`{s.id}`) | {', '.join(e.roles)} | {ACCESS_LABELS.get(s.access.value, s.access.value)} | "
            f"{_auth_label(s)} | {', '.join(s.frequencies)} | {s.publication_lag_days} | "
            f"{VERIFICATION_LABELS.get(s.verification.value, s.verification.value)} | "
            f"{'✓' if e.provider_ready else '—'} |"
        )
    lines += ["", "## Makro feature'lar", "", "| Feature | Kaynak | Seri | Dönüşüm | Durum |", "|---|---|---|---|---|"]
    for e in m.features:
        f = e.feature
        series = f"`{f.series}`" if f.series else f"_{f.discover_hint}_"
        lines.append(f"| {f.name} | {f.source} | {series} | {f.transform.value} | {STATUS_LABELS.get(e.status, e.status)} |")
    lines += ["", "## Pazar payı", ""]
    lines += [f"- `{s.source}`: {s.metric}{' _(proxy)_' if s.is_proxy else ''}" for s in m.market_share]
    lines += ["", "## Lisans notları", ""]
    lines += [f"- `{e.source.id}`: {e.source.license.notes}" for e in m.sources]
    return "\n".join(lines) + "\n"


def health_dict(r: HealthResult) -> dict:
    return asdict(r) | {"status": r.status.value, "checked": list(r.checked)}


def _clean(value):
    """For JSON: NaN/inf → None, date → ISO string."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return value


def quality_dict(report: QualityReport, skipped: dict[str, str] | None = None) -> dict:
    return {
        "as_of": report.as_of.isoformat(),
        "months": report.months,
        "counts": report.counts,
        "skipped_features": skipped or {},
        "issues": [
            {"severity": i.severity.value, "feature": i.feature, "check": i.check, "message": i.message}
            for i in report.issues
        ],
        "series": [
            {
                "feature": s.feature,
                "first": _clean(s.first),
                "last": _clean(s.last),
                "observations": s.observations,
                "missing_ratio": s.missing_ratio,
                "months_since_last": s.months_since_last,
                "outliers": [d.isoformat() for d in s.outliers],
                "breaks": [{"date": b.date.isoformat(), "t_stat": b.t_stat, "event": b.event} for b in s.breaks],
            }
            for s in report.series
        ],
        "correlated_pairs": [{"a": a, "b": b, "corr": c} for a, b, c in report.correlated_pairs],
        "diagnostics": [{k: _clean(v) for k, v in asdict(d).items()} for d in report.diagnostics],
        "notable_pairs": [{"a": a, "b": b, "corr": c, "note": n} for a, b, c, n in report.notable_pairs],
    }


def _deep(value):
    """_clean for nested structures: numpy scalars are converted to Python types."""
    if isinstance(value, dict):
        return {str(k): _deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    return _clean(value)


def _profile_dict(p) -> dict:
    return _deep({
        "feature": p.feature, "n": p.n, "missing_ratio": p.missing_ratio, "mean": p.mean, "std": p.std, "median": p.median,
        "mad": p.mad, "iqr": p.iqr, "skew": p.skew, "excess_kurtosis": p.excess_kurtosis, "min": p.min, "max": p.max,
        "outlier_share": p.outlier_share, "stationarity": p.stationarity, "center_scale": p.center_scale, "reasons": p.reasons,
    })  # fmt: skip


def prep_dict(result, sector_id: str, skipped: dict[str, str] | None = None) -> dict:
    cfg = result.config
    after = {p.feature: p for p in result.after}
    return {
        "sector": sector_id,
        "config": {"target": cfg.target, "train_end": result.train_end.isoformat(), "model_family": cfg.model_family,
                   "missing": cfg.missing, "max_gap": cfg.max_gap, "outliers": cfg.outliers, "outlier_k": cfg.outlier_k,
                   "power": cfg.power, "scaling": cfg.scaling, "difference": cfg.difference},  # fmt: skip
        "columns": [
            {"feature": plan.feature, "is_target": plan.is_target, "before": _profile_dict(prof),
             "after": _profile_dict(after[plan.feature]), "steps": _deep(plan.steps)}
            for prof, plan in zip(result.profiles, result.plans, strict=True)
        ],  # fmt: skip
        "added_columns": [c for c in result.panel.columns if "__" in c],
        "period": {"first": result.panel.index[0].date().isoformat(), "last": result.panel.index[-1].date().isoformat(),
                   "rows": len(result.panel)},  # fmt: skip
        "notes": result.notes,
        "skipped": skipped or {},
    }


def features_dict(result, sector_id: str, max_screening: int = 150, skipped_panel: dict[str, str] | None = None) -> dict:
    families: dict[str, int] = {}
    for s in result.specs:
        families[s.family] = families.get(s.family, 0) + 1
    specs = {s.name: s for s in result.specs}
    screening = []
    if result.screening is not None:
        for row in result.screening.head(max_screening).to_dict("records"):
            spec = specs.get(row["name"])
            row["family"] = spec.family if spec else None
            row["description"] = spec.description if spec else None
            screening.append(_deep(row))
    return {
        "sector": sector_id,
        "label": result.label,
        "label_mode": result.label_mode,
        "train_end": result.train_end.isoformat() if result.train_end else None,
        "n_features": len(result.specs),
        "families": families,
        "recipes": [{"name": s.name, "inputs": list(s.inputs), "description": s.description, **_deep(s.params)}
                    for s in result.specs if s.family == "sector_recipe"],  # fmt: skip
        "skipped_recipes": result.skipped,
        "skipped_panel": skipped_panel or {},
        "selected": [{"name": n, "family": specs[n].family, "description": specs[n].description} for n in result.selected],
        "screening": screening,
        "period": {"first": result.features.index[0].date().isoformat(), "last": result.features.index[-1].date().isoformat(),
                   "rows": len(result.features)},  # fmt: skip
        "notes": result.notes,
    }


def brand_features_dict(result, tail: int = 6) -> dict:
    """Summary of a brand feature panel; the rows stay in the Parquet file."""
    families: dict[str, int] = {}
    for s in result.specs:
        families[s.family] = families.get(s.family, 0) + 1
    panel = result.panel
    own = [s for s in result.specs if s.family in {"brand", "search_interest"} or (s.family == "base" and s.name.startswith("marka_pay_"))]
    rows = panel[[s.name for s in own]].tail(tail).round(4).reset_index()
    return {
        "brand": result.brand,
        "path": result.location,
        "months": int(panel.shape[0]),
        "columns": int(panel.shape[1]),
        "first": panel.index[0].date().isoformat(),
        "last": panel.index[-1].date().isoformat(),
        "families": families,
        "brand_columns": [{"name": s.name, "family": s.family, "description": s.description} for s in own],
        "macro_columns": sum(s.family == "base" and not s.name.startswith("marka_") for s in result.specs),
        "skipped": result.skipped,
        "withheld": result.withheld,
        "notes": result.notes,
        "sources": result.sources,
        "is_proxy": result.is_proxy,
        "tail": [_deep(r) for r in rows.astype({"date": str}).to_dict(orient="records")],
    }


def _evidence_line(e: dict) -> list[str]:
    scope = " · ".join(x for x in (e["basis_label"], e["segment"], None if e["geography"] == "Türkiye" else e["geography"]) if x)
    flags = " · ikinci el" if e["second_hand"] else ""
    origin = f" · özgün kaynak: {e['original_source']} ({e['original_date'] or 'tarih yok'})" if e["original_source"] else ""
    return [f"  {e['claim_text']} · {e['period']} · {scope} · {e['tier_label']} · {e['publisher']}{origin}{flags}", f"    {e['url']}  [{e['id']}]"]


def search_lines(section: dict) -> list[str]:
    """Text form of the search-interest block. The heading keeps it apart from shares and published findings."""
    lines = [f"Arama ilgisi ({section['note']})"]
    if section["status"] != "done":
        return [*lines, f"  {section['message']}"]
    coverage = section["coverage"]
    lines.append(f"  Kapsam: {coverage['first_day']} – {coverage['last_day']}, {coverage['list_days']} günlük liste. {section['data_source']['label']}")
    for t in section["terms"]:
        if t["status"] == "not_found":
            continue
        detail = f"{t['list_days']} gün, en iyi sıra {t['best_rank']}" if t["list_days"] else ", ".join(t["related_terms"][:3])
        lines.append(f"  {t['mode_label']:<9} {t['label']:<24} {t['status_label']}: {detail}")
    missing = [t["label"] for t in section["terms"] if t["status"] == "not_found"]
    if missing:
        lines.append(f"  Listede yok: {', '.join(missing)}.")
    lines.append(f"  {section['withheld']['arama_payi']}")
    cost = section["cost"]
    lines.append(f"  BigQuery: {tr_num(cost['gib'], '.2f')} GiB, {cost['queries']} sorgu. {section['attribution']}.")
    return lines


def mirror_lines(status: dict) -> list[str]:
    """Text form of the local copy's status."""
    if status["state"] == "on":
        lines = [f"Yerel kopya açık: {status['days']} gün ({status['first_day']} – {status['last_day']}), {tr_num(status['disk_mb'], '.1f')} MB."]
    else:
        lines = ["Yerel kopya kapalı." + (" Tercihiniz kaydedildi." if status["state"] == "declined" else "")]
        if status["days"]:
            lines.append(f"Diskte yarım kalmış {status['days']} gün var ({tr_num(status['disk_mb'], '.1f')} MB).")
    if offer := status.get("offer"):
        lines.append(offer)
    return lines


def evidence_lines(section: dict, historical: bool = False) -> list[str]:
    """Text form of the published findings. The heading keeps them apart from computed shares."""
    lines = [f"Yayımlanmış pay bulguları ({section['label']})"]
    for e in section["current"]:
        lines += _evidence_line(e)
    if not section["current"]:
        lines.append("  Güncel bulgu yok.")
    if section["single_source"]:
        lines.append("  Bulgular tek bir bağımsız kaynağa dayanıyor.")
    for c in section["comparison"][:MAX_COMPARISON_LINES]:
        lines.append(f"  Karşılaştırma: {c['series_name']} {c['computed_text']} ({c['computed_period']}) / bulgu {c['evidence_text']} ({c['evidence_period']}). {' '.join(c['reasons'])}")
    if (more := len(section["comparison"]) - MAX_COMPARISON_LINES) > 0:
        lines.append(f"  {more} karşılaştırma daha var: --json çıktısı, panel ve rapor hepsini gösterir.")
    lines += [f"  Çelişki: {c['message']}" for c in section["contradictions"]]
    if historical:
        for e in section["historical"]:
            lines += _evidence_line(e)
    elif section["historical"]:
        lines.append(f"  {len(section['historical'])} eski bulgu var: mlbooster evidence list")
    if section["next_step"]:
        lines.append(f"  {section['next_step']}")
    return lines
