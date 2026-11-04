"""The optional local copy of the Google Trends tables. Off by default: nothing is copied until the user says yes, and
the offer states what it reads, what it costs and how much disk it takes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Protocol

import pandas as pd

from mlbooster.domain.errors import DomainError, InvalidInput
from mlbooster.domain.numfmt import tr_num

ENABLED, DECLINED = "enabled", "declined"
TABLES = ("top", "rising")
GIB = 1024**3
# Measured live 2026-11-04 with a full copy: 33 listed days, 42.2 GiB read when nothing is in BigQuery's query cache, 16 MB
# on disk, about 8 minutes. The figures stand in when the dry run cannot be made.
FIRST_COPY_BYTES = int(42.2 * GIB)
DAILY_BYTES = int(1.27 * GIB)
DISK_MB_PER_DAY = 0.5
RETAINED_DAYS = 33


class MirrorState(Protocol):
    def choice(self) -> str | None: ...

    def set_choice(self, choice: str | None, at: str) -> None: ...

    def days(self, table: str) -> set: ...

    def lists(self, table: str, since: date, until: date) -> pd.DataFrame: ...

    def size_bytes(self) -> int: ...

    def delete(self) -> None: ...


class MirrorSource(Protocol):
    def estimate(self) -> dict:
        """{'bytes', 'days'}: what downloading the missing days would read (free dry runs)."""
        ...

    def sync(self) -> dict:
        """{'days_added', 'bytes_billed', 'queries'}"""
        ...


def _gib(value: float) -> str:
    return tr_num(value / GIB, ".1f")


def offer_text(read_bytes: int, measured: bool, days: int) -> str:
    source = "BigQuery'nin ücretsiz dry run ölçümü" if measured else "son canlı ölçüm"
    disk = max(1.0, days * DISK_MB_PER_DAY)  # the dry run counts days upstream may not have, so this stays an upper bound
    return (
        "Google Trends'in Türkiye satırları bu bilgisayardaki yerel depoya kopyalanır; liste geçmişi 30 günle sınırlı kalmaz. "
        f"İlk kopya yaklaşık {_gib(read_bytes)} GiB okur ({source}) ve Google Cloud projenizin ücretsiz aylık 1 TiB sorgu "
        f"kotasından düşer. Kopyalama yaklaşık 8 dakika sürer, diskte yaklaşık {tr_num(disk, '.0f')} MB yer kaplar. Sonraki "
        f"güncellemeler yalnızca yeni günleri okur (gün başına yaklaşık {_gib(DAILY_BYTES)} GiB). Kopyayı istediğiniz zaman "
        "kapatıp silebilirsiniz."
    )


class TrendsMirrorControl:
    def __init__(self, state: MirrorState, source: Callable[[], MirrorSource]):
        self._state = state
        self._source = source

    def status(self, offer: bool = False) -> dict:
        """Local facts only; `offer` adds the offer with a dry-run measurement (free, but it needs Google credentials)."""
        choice = self._state.choice()
        days = sorted({d for t in TABLES for d in self._state.lists(t, date.min, date.max)["refresh_date"]})
        out = {
            "state": "on" if choice == ENABLED else DECLINED if choice == DECLINED else "off",
            "days": len(days), "first_day": days[0].isoformat() if days else None, "last_day": days[-1].isoformat() if days else None,
            "disk_mb": round(self._state.size_bytes() / 1024**2, 1),
        }  # fmt: skip
        if offer and choice != ENABLED:
            out["offer"] = self._offer()
        return out

    def _offer(self) -> str:
        try:
            estimate = self._source().estimate()
            return offer_text(estimate["bytes"], True, estimate["days"] or RETAINED_DAYS)
        except DomainError:
            return offer_text(FIRST_COPY_BYTES, False, RETAINED_DAYS)

    def enable(self) -> dict:
        """The user said yes: downloads the retained days, then remembers the answer. A download that stops halfway keeps
        its days, so running this again continues from there."""
        report = self._source().sync()
        self._state.set_choice(ENABLED, _now())
        return self._report(report)

    def update(self) -> dict:
        if self._state.choice() != ENABLED:
            raise InvalidInput("Yerel kopya kapalı; önce açın (CLI: mlbooster trends-mirror enable)")
        return self._report(self._source().sync())

    def decline(self) -> dict:
        self._state.set_choice(DECLINED, _now())
        return self.status()

    def disable(self) -> dict:
        """Turns the copy off and deletes it; the next search-interest run asks BigQuery directly again."""
        removed = self._state.size_bytes()
        self._state.delete()
        self._state.set_choice(DECLINED, _now())
        return {**self.status(), "removed_mb": round(removed / 1024**2, 1)}

    def _report(self, report: dict) -> dict:
        return {**self.status(), **report, "gib": round(report["bytes_billed"] / GIB, 2)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


