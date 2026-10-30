"""Use case çıktılarını metne / JSON'a çeviren sunum yardımcıları."""

from __future__ import annotations

from dataclasses import asdict

from mlbooster.domain.model import HealthResult, Source
from mlbooster.domain.services.sector_map import SectorMap


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
        lines += ["**ML kullanımları:** " + ", ".join(m.ml_use_cases), ""]
    lines += [
        "## Kaynaklar",
        "",
        "| Kaynak | Rol | Erişim | Auth | Frekans | Gecikme (gün) | Doğrulama | Sağlayıcı |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in m.sources:
        s = e.source
        lines.append(
            f"| {s.name} (`{s.id}`) | {', '.join(e.roles)} | {s.access.value} | {_auth_label(s)} | "
            f"{', '.join(s.frequencies)} | {s.publication_lag_days} | {s.verification.value} | "
            f"{'✓' if e.provider_ready else '—'} |"
        )
    lines += ["", "## Makro feature'lar", "", "| Feature | Kaynak | Seri | Dönüşüm | Durum |", "|---|---|---|---|---|"]
    for e in m.features:
        f = e.feature
        series = f"`{f.series}`" if f.series else f"_{f.discover_hint}_"
        lines.append(f"| {f.name} | {f.source} | {series} | {f.transform.value} | {e.status} |")
    lines += ["", "## Pazar payı", ""]
    lines += [f"- `{s.source}`: {s.metric}{' _(proxy)_' if s.is_proxy else ''}" for s in m.market_share]
    lines += ["", "## Lisans notları", ""]
    lines += [f"- `{e.source.id}`: {e.source.license.notes}" for e in m.sources]
    return "\n".join(lines) + "\n"


def health_dict(r: HealthResult) -> dict:
    return asdict(r) | {"status": r.status.value, "checked": list(r.checked)}
