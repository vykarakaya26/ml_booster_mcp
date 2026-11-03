"""Readable feature names and descriptions for the web panel.

Derived from the series name, unit, frequency and transform already present in the sector map, so features the
user adds later get a description without any extra catalog fields. User-facing strings are Turkish with English
technical terms (CLAUDE.md language standard).
"""

from __future__ import annotations

from mlbooster.domain.model import Frequency, MacroFeature, SeedSeries, SeriesKind, Source, Transform
from mlbooster.domain.services.sector_map import SectorMap

UNITS = {
    "percent": "yüzde",
    "TRY": "TL",
    "index": "endeks",
    "million_usd": "milyon ABD doları",
    "thousand_try": "bin TL",
    "million_try": "milyon TL",
    "count": "adet",
}

FREQUENCIES = {
    "H": "saatlik", "D": "günlük", "B": "iş günü", "W": "haftalık", "SM": "yarım aylık",
    "M": "aylık", "Q": "çeyreklik", "S": "altı aylık", "A": "yıllık",
}  # fmt: skip

TRANSFORMS = {
    Transform.LEVEL: ("Düzey", "Serinin kendi değeri; dönüşüm uygulanmaz."),
    Transform.DIFF: ("Aylık fark", "Bir önceki döneme göre değişim, serinin kendi biriminde."),
    Transform.LOG_DIFF: (
        "Aylık değişim (log)",
        "Bir önceki döneme göre logaritmik değişim. Küçük değerlerde yüzde değişime yakındır: 0,02 yaklaşık %2 artış, "
        "-0,01 yaklaşık %1 düşüş demektir.",
    ),
    Transform.YOY_PCT: ("Yıllık değişim (%)", "Geçen yılın aynı dönemine göre yüzde değişim."),
}

AGGREGATIONS = {
    "avg": "ay içi ortalama", "last": "ay sonu değeri", "first": "ay başı değeri",
    "sum": "ay toplamı", "min": "ay içi en düşük", "max": "ay içi en yüksek",
}  # fmt: skip
AGG_SHORT = {
    "avg": "ay ortalaması", "last": "ay sonu", "first": "ay başı",
    "sum": "ay toplamı", "min": "ay en düşüğü", "max": "ay en yükseği",
}  # fmt: skip

HIGH_FREQ = {Frequency.HOURLY, Frequency.DAILY, Frequency.BUSINESS, Frequency.WEEKLY, Frequency.SEMI_MONTHLY}
LOW_FREQ = {Frequency.QUARTERLY, Frequency.SEMI_ANNUAL, Frequency.ANNUAL}


def _cap(text: str) -> str:
    """Turkish-aware capitalisation: 'iş günü' → 'İş günü' (str.capitalize gives 'Iş')."""
    return (text[0].replace("i", "İ").upper() + text[1:]) if text else text


def _unit(feature: MacroFeature, seed: SeedSeries | None) -> str:
    if feature.transform is Transform.LOG_DIFF:
        return "log değişim (≈ oran)"
    if feature.transform is Transform.YOY_PCT:
        return "yüzde"
    base = UNITS.get(seed.unit, seed.unit) if seed and seed.unit else "kaynak birimi"
    return f"{base}, reel" if feature.real else base


def _monthly_note(feature: MacroFeature, seed: SeedSeries | None) -> str | None:
    if seed is None:
        return None
    if seed.freq in HIGH_FREQ:
        agg = feature.agg.value if feature.agg else ("sum" if seed.kind is SeriesKind.FLOW else "avg")
        freq = "iş günü frekanslı" if seed.freq is Frequency.BUSINESS else FREQUENCIES[seed.freq.value]
        return f"{_cap(freq)} veri aylık frekansa {AGGREGATIONS[agg]} olarak indirgenir."
    if seed.freq in LOW_FREQ:
        return f"{_cap(FREQUENCIES[seed.freq.value])} veri, yeni değer yayımlanana kadar sonraki aylara taşınır."
    return None


def _fallback_name(feature: MacroFeature) -> str:
    """Name of a feature without a series: a short discover hint reads better than the ASCII catalog name
    ('Taşıt kredisi faiz oranı' instead of 'Tasit kredisi faizi'); long hints are search notes, not names."""
    hint = (feature.discover_hint or "").strip()
    if hint and len(hint) <= 32 and ":" not in hint:
        return _cap(hint)
    return _cap(feature.name.replace("_", " "))


def feature_info(feature: MacroFeature, source: Source | None) -> dict:
    """Display name, unit, source and plain-language description of one feature."""
    seed = source.seed(feature.series) if source and feature.series else None
    transform_label, transform_text = TRANSFORMS[feature.transform]
    name = seed.name if seed else _fallback_name(feature)
    label = name
    if feature.agg and seed and seed.freq in HIGH_FREQ:
        label = f"{name} ({AGG_SHORT[feature.agg.value]})"  # tells the average and month-end versions apart
    lag = source.lag_for(feature.series) if source and feature.series else None
    notes = [transform_text]
    if (note := _monthly_note(feature, seed)) is not None:
        notes.append(note)
    if feature.real:
        notes.append("TÜFE ile enflasyondan arındırılmıştır (reel değer).")
    if lag:
        notes.append(
            f"Kaynak veriyi dönem sonundan yaklaşık {lag} gün sonra yayımlar; panel bu gecikmeyi uygular (point-in-time)."
        )
    return {
        "name": feature.name,
        "label": label,
        "transform": feature.transform.value,
        "transform_label": transform_label,
        "agg": feature.agg.value if feature.agg else None,
        "unit": _unit(feature, seed),
        "frequency": FREQUENCIES.get(seed.freq.value) if seed else None,
        "real": feature.real,
        "series": feature.series,
        "source": feature.source,
        "source_name": source.name if source else feature.source,
        "source_owner": source.owner if source else None,
        "publication_lag_days": lag,
        "description": " ".join(notes),
    }


def sector_feature_info(sector_map: SectorMap) -> dict[str, dict]:
    sources = {entry.source.id: entry.source for entry in sector_map.sources}
    return {e.feature.name: feature_info(e.feature, sources.get(e.feature.source)) for e in sector_map.features}
