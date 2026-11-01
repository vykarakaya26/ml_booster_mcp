"""Feature'lar için okunur ad ve açıklama. Katalogdaki seri adı, birim, frekans ve dönüşümden türetilir;
böylece kullanıcının sonradan eklediği feature'lar da ek tanım gerekmeden açıklanır."""

from __future__ import annotations

from mlbooster.domain.model import Catalog, Frequency, MacroFeature, SeedSeries, SeriesKind, Transform

UNITS = {
    "percent": "yüzde",
    "TRY": "TL",
    "index": "endeks",
    "million_usd": "milyon ABD doları",
    "thousand_try": "bin TL",
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

AGG_SHORT = {"avg": "ay ortalaması", "last": "ay sonu", "first": "ay başı", "sum": "ay toplamı", "min": "ay en düşüğü", "max": "ay en yükseği"}

HIGH_FREQ = {Frequency.HOURLY, Frequency.DAILY, Frequency.BUSINESS, Frequency.WEEKLY, Frequency.SEMI_MONTHLY}
LOW_FREQ = {Frequency.QUARTERLY, Frequency.SEMI_ANNUAL, Frequency.ANNUAL}


def _cap(text: str) -> str:
    """Türkçe büyük harf: 'iş günü' → 'İş günü' (str.capitalize 'Iş' üretir)."""
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
        return f"{_cap(FREQUENCIES[seed.freq.value])} veri aylığa {AGGREGATIONS[agg]} olarak indirgenir."
    if seed.freq in LOW_FREQ:
        return f"{_cap(FREQUENCIES[seed.freq.value])} veri, yeni değer yayımlanana kadar sonraki aylara taşınır."
    return None


def feature_info(catalog: Catalog, feature: MacroFeature) -> dict:
    """Arayüzde gösterilecek ad, birim, kaynak ve açıklama."""
    source = catalog.sources.get(feature.source)
    seed = source.seed(feature.series) if source and feature.series else None
    transform_label, transform_text = TRANSFORMS[feature.transform]
    name = seed.name if seed else _cap(feature.name.replace("_", " "))
    title = name
    if feature.agg and seed and seed.freq in HIGH_FREQ:
        title = f"{name} ({AGG_SHORT[feature.agg.value]})"  # aynı serinin ortalama/ay sonu sürümleri ayrışsın
    lag = source.lag_for(feature.series) if source and feature.series else None
    notes = [transform_text]
    if (note := _monthly_note(feature, seed)) is not None:
        notes.append(note)
    if feature.real:
        notes.append("TÜFE ile reelleştirilmiştir (enflasyon etkisi arındırılmış).")
    if lag:
        notes.append(f"Kaynak veriyi dönem sonundan yaklaşık {lag} gün sonra yayımlar; panelde bu gecikme dikkate alınır.")
    return {
        "name": feature.name,
        "label": title,
        "transform": feature.transform.value,
        "transform_label": transform_label,
        "agg": feature.agg.value if feature.agg else None,
        "agg_label": AGGREGATIONS.get(feature.agg.value) if feature.agg else None,
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


def sector_feature_info(catalog: Catalog, sector_id: str) -> dict[str, dict]:
    return {f.name: feature_info(catalog, f) for f in catalog.sector(sector_id).macro_features}
