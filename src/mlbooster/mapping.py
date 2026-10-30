"""Sektör → kaynak haritası. Tek doğruluk kaynağı katalog YAML'larıdır."""

from __future__ import annotations

from mlbooster.adapters import REGISTRY
from mlbooster.catalog import Catalog, Source


def _feature_status(catalog: Catalog, source_id: str, series: str | None) -> str:
    if catalog.source(source_id).adapter not in REGISTRY:
        return "adapter_yok"
    return "hazir" if series else "kesif_bekliyor"


def _source_row(source: Source, roles: list[str]) -> dict:
    auth = source.auth.type + (f" ({source.auth.env_var or ', '.join(source.auth.env_vars)})" if source.auth.type != "none" else "")
    return {
        "id": source.id,
        "name": source.name,
        "roles": roles,
        "access": source.access.value,
        "auth": auth,
        "frequency": source.frequency,
        "publication_lag_days": source.publication_lag_days,
        "license": source.license.notes,
        "verification_status": source.verification_status.value,
        "adapter_ready": source.adapter in REGISTRY,
        "base_url": source.base_url,
    }


def sector_map(catalog: Catalog, sector_id: str) -> dict:
    sector = catalog.sector(sector_id)
    roles: dict[str, list[str]] = {}
    for source_id in dict.fromkeys(f.source for f in sector.macro_features):
        roles.setdefault(source_id, []).append("makro")
    for s in sector.sector_sources:
        roles.setdefault(s, []).append("sektör")
    for m in sector.market_share_sources:
        roles.setdefault(m.source, []).append("pazar payı (proxy)" if m.is_proxy else "pazar payı")
    return {
        "sector": {"id": sector.id, "name": sector.name, "ml_use_cases": sector.ml_use_cases},
        "sources": [_source_row(s, roles[s.id]) for s in catalog.sources_for_sector(sector_id)],
        "macro_features": [
            {
                "name": f.name,
                "source": f.source,
                "series": f.series,
                "transform": f.transform,
                "status": _feature_status(catalog, f.source, f.series),
                "discover_hint": f.discover_hint,
            }
            for f in sector.macro_features
        ],
        "market_share": [m.model_dump() for m in sector.market_share_sources],
    }


def render_markdown(m: dict) -> str:
    sector = m["sector"]
    lines = [f"# {sector['name']} ({sector['id']}) — veri kaynağı haritası", ""]
    if sector["ml_use_cases"]:
        lines += ["**ML kullanımları:** " + ", ".join(sector["ml_use_cases"]), ""]
    lines += [
        "## Kaynaklar",
        "",
        "| Kaynak | Rol | Erişim | Auth | Frekans | Gecikme (gün) | Doğrulama | Adapter |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in m["sources"]:
        lines.append(
            f"| {s['name']} (`{s['id']}`) | {', '.join(s['roles'])} | {s['access']} | {s['auth']} | "
            f"{', '.join(s['frequency'])} | {s['publication_lag_days']} | {s['verification_status']} | "
            f"{'✓' if s['adapter_ready'] else '—'} |"
        )
    lines += ["", "## Makro feature'lar", "", "| Feature | Kaynak | Seri | Dönüşüm | Durum |", "|---|---|---|---|---|"]
    for f in m["macro_features"]:
        series = f"`{f['series']}`" if f["series"] else f"_{f['discover_hint']}_"
        lines.append(f"| {f['name']} | {f['source']} | {series} | {f['transform']} | {f['status']} |")
    lines += ["", "## Pazar payı", ""]
    for ms in m["market_share"]:
        proxy = " _(proxy)_" if ms["is_proxy"] else ""
        lines.append(f"- `{ms['source']}`: {ms['metric']}{proxy}")
    lines += ["", "## Lisans notları", ""]
    lines += [f"- `{s['id']}`: {s['license']}" for s in m["sources"] if s["license"]]
    return "\n".join(lines) + "\n"
