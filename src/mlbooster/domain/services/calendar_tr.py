"""Türkiye takvimi: resmî tatiller, dini bayramlar ve aylık iş günü sayısı.

Ramazan ve Kurban bayramları hicri takvime bağlı olduğu için her yıl yaklaşık 11 gün öne kayar; aylık satış,
üretim ve harcama verisinde takvim etkisi yaratır. Tarihler `MOVING_HOLIDAYS` tablosundadır (kaynak: Diyanet
İşleri Başkanlığı dini günler takvimi; ayrıntı modül sonundaki SOURCES). Arife günleri yarım gün sayılır.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

# (ay, gün, ilk geçerli yıl, gün değeri): 15 Temmuz 2017'den itibaren resmî tatildir.
FIXED_HOLIDAYS: tuple[tuple[int, int, int, float], ...] = (
    (1, 1, 1900, 1.0),
    (4, 23, 1900, 1.0),
    (5, 1, 2009, 1.0),
    (5, 19, 1900, 1.0),
    (7, 15, 2017, 1.0),
    (8, 30, 1900, 1.0),
    (10, 28, 1900, 0.5),  # Cumhuriyet Bayramı arifesi, yarım gün
    (10, 29, 1900, 1.0),
)

# (yıl, bayram, ilk gün, resmî tatil gün sayısı). 2429 sayılı Kanun: Ramazan 3, Kurban 4 gün; arife 13.00'ten
# itibaren yarım gün. Tarihler Diyanet takvimidir (hesaplamaya dayalı); Suudi Arabistan takviminden bazı yıllarda
# bir gün farklıdır. Her tarih en az iki bağımsız kaynakla doğrulanmıştır. 2026–2027 Diyanet projeksiyonudur.
# Kamuda idari izinle uzatılan tatiller (ör. 2016 ve 2019 Ramazan, 2022 Kurban) resmî tatil olmadığı için dahil değildir.
MOVING_HOLIDAYS: tuple[tuple[int, str, str, int], ...] = (
    (2010, "ramazan", "2010-09-09", 3),
    (2010, "kurban", "2010-11-16", 4),
    (2011, "ramazan", "2011-08-30", 3),
    (2011, "kurban", "2011-11-06", 4),
    (2012, "ramazan", "2012-08-19", 3),
    (2012, "kurban", "2012-10-25", 4),
    (2013, "ramazan", "2013-08-08", 3),
    (2013, "kurban", "2013-10-15", 4),
    (2014, "ramazan", "2014-07-28", 3),
    (2014, "kurban", "2014-10-04", 4),
    (2015, "ramazan", "2015-07-17", 3),
    (2015, "kurban", "2015-09-24", 4),
    (2016, "ramazan", "2016-07-05", 3),
    (2016, "kurban", "2016-09-12", 4),
    (2017, "ramazan", "2017-06-25", 3),
    (2017, "kurban", "2017-09-01", 4),
    (2018, "ramazan", "2018-06-15", 3),
    (2018, "kurban", "2018-08-21", 4),
    (2019, "ramazan", "2019-06-04", 3),
    (2019, "kurban", "2019-08-11", 4),
    (2020, "ramazan", "2020-05-24", 3),
    (2020, "kurban", "2020-07-31", 4),
    (2021, "ramazan", "2021-05-13", 3),
    (2021, "kurban", "2021-07-20", 4),
    (2022, "ramazan", "2022-05-02", 3),
    (2022, "kurban", "2022-07-09", 4),
    (2023, "ramazan", "2023-04-21", 3),
    (2023, "kurban", "2023-06-28", 4),
    (2024, "ramazan", "2024-04-10", 3),
    (2024, "kurban", "2024-06-16", 4),
    (2025, "ramazan", "2025-03-30", 3),
    (2025, "kurban", "2025-06-06", 4),
    (2026, "ramazan", "2026-03-20", 3),
    (2026, "kurban", "2026-05-27", 4),
    (2027, "ramazan", "2027-03-09", 3),
    (2027, "kurban", "2027-05-16", 4),
)

SOURCES: tuple[str, ...] = (
    "https://www.mevzuat.gov.tr (2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun)",
    "https://github.com/vacanza/holidays (Turkey: Diyanet takvimine göre düzeltmeler)",
    "Diyanet İşleri Başkanlığı dini günler takvimine atıf yapan ulusal basın (NTV, CNN Türk, TRT Haber, Borsa İstanbul tatil tablosu)",
)


def holiday_days(start: date, end: date) -> dict[date, tuple[float, str | None]]:
    """[start, end] aralığındaki tatil günleri → (iş günü kaybı 0–1, bayram adı ya da None)."""
    out: dict[date, tuple[float, str | None]] = {}
    for year in range(start.year, end.year + 1):
        for month, day, since, weight in FIXED_HOLIDAYS:
            if year >= since:
                out[date(year, month, day)] = (weight, None)
    for year, name, first, days in MOVING_HOLIDAYS:
        d0 = date.fromisoformat(first)
        eve = d0 - timedelta(days=1)
        out.setdefault(eve, (0.5, None))
        for i in range(days):
            out[d0 + timedelta(days=i)] = (1.0, name)
    return {d: v for d, v in out.items() if start <= d <= end}


RAMADAN_DAYS = 30  # Ramazan ayı 29 ya da 30 gündür; bayramdan önceki 30 gün yaklaşık olarak kullanılır (±1 gün)
PRE_BAYRAM_DAYS = 7  # arifeden önceki alışveriş ve seyahat penceresi


def _windows() -> tuple[set[date], set[date]]:
    ramadan, pre = set(), set()
    for _, name, first, _days in MOVING_HOLIDAYS:
        d0 = date.fromisoformat(first)
        if name == "ramazan":
            ramadan |= {d0 - timedelta(days=i) for i in range(1, RAMADAN_DAYS + 1)}
        eve = d0 - timedelta(days=1)
        pre |= {eve - timedelta(days=i) for i in range(1, PRE_BAYRAM_DAYS + 1)}
    return ramadan, pre


def month_calendar(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Aylık indeks için takvim değişkenleri: ay (sin/cos), iş günü, iş günü farkı, bayram gün sayıları."""
    periods = index.to_period("M")
    first, last = periods.min().start_time.date(), periods.max().end_time.date()
    hol = holiday_days(first, last)
    covered = {y for y, *_ in MOVING_HOLIDAYS}
    ramadan_days, pre_days = _windows()
    rows = []
    for p in periods:
        days = pd.date_range(p.start_time, p.end_time, freq="D")
        work, ramazan, kurban, fasting, pre = 0.0, 0, 0, 0, 0
        for d in days:
            loss, name = hol.get(d.date(), (0.0, None))
            if d.weekday() < 5:
                work += 1 - loss
            ramazan += name == "ramazan"
            kurban += name == "kurban"
            fasting += d.date() in ramadan_days
            pre += d.date() in pre_days
        known = p.year in covered
        rows.append({
            "ay_sin": np.sin(2 * np.pi * p.month / 12), "ay_cos": np.cos(2 * np.pi * p.month / 12),
            "is_gunu": work if known else np.nan,
            "ramazan_bayrami_gun": float(ramazan) if known else np.nan,
            "kurban_bayrami_gun": float(kurban) if known else np.nan,
            "ramazan_ayi_gun": float(fasting) if known else np.nan,
            "bayram_oncesi_gun": float(pre) if known else np.nan,
        })  # fmt: skip
    cal = pd.DataFrame(rows, index=index)
    cal["is_gunu_fark"] = cal["is_gunu"] - cal["is_gunu"].shift(12)
    return cal
