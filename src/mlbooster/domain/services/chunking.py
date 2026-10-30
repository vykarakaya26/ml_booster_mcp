"""Sağlayıcı başına gözlem limiti için tarih aralığı parçalama."""

from __future__ import annotations

from datetime import date, timedelta

from mlbooster.domain.model import Frequency


def date_chunks(start: date, end: date, freq: Frequency, max_obs: int) -> list[tuple[date, date]]:
    """[start, end] aralığını, her parça en fazla `max_obs` gözlem içerecek biçimde böler."""
    if max_obs < 1:
        raise ValueError("max_obs ≥ 1 olmalı")
    span = timedelta(days=max_obs * freq.min_period_days - 1)
    chunks, cursor = [], start
    while cursor <= end:
        chunk_end = min(cursor + span, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks
