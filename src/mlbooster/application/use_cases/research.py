"""Web search for published share figures, started from the panel or the CLI. The search itself is the share-research
agent; a `ResearchRunner` runs it on the user's own Claude Code. It never starts by itself, and what it finds reaches the
reports only as stored evidence, apart from computed shares."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from mlbooster.application.dto import ResearchEvent, ResearchOutcome
from mlbooster.application.errors import ResearchUnavailable
from mlbooster.application.ports import CatalogRepository, EvidenceStore, ResearchRunner
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services import evidence as rules
from mlbooster.domain.validation import brand_query, slug

RUNNING = ("starting", "searching")
STATUS_LABELS = {"idle": "Arama başlatılmadı", "starting": "Claude Code başlatılıyor", "searching": "Kaynaklar taranıyor", "finished": "Tamamlandı",
                 "failed": "Tamamlanamadı", "cancelled": "Durduruldu"}  # fmt: skip


@dataclass(frozen=True)
class ResearchJob:
    id: str
    brand: str
    status: str = "starting"
    searches: int = 0
    pages: int = 0
    records: int = 0
    refused: int = 0
    message: str | None = None
    report: str | None = None  # the agent's closing report: text derived from web pages, shown as data
    started_at: str | None = None
    finished_at: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class WebResearch:
    """One job at a time. `start` runs it in the background for the panel, `run` waits for it for the CLI."""

    def __init__(self, catalog: CatalogRepository, runner: ResearchRunner, store: EvidenceStore):
        self._catalog = catalog
        self._runner = runner
        self._store = store
        self._lock = threading.Lock()
        self._job: ResearchJob | None = None
        self._cancel = threading.Event()

    def status(self) -> dict:
        job = self._job
        if job is None:
            return {"status": "idle", "label": STATUS_LABELS["idle"], "running": False}
        return asdict(job) | {"label": STATUS_LABELS[job.status], "running": job.status in RUNNING}

    def start(self, brand: str, sector: str | None = None) -> dict:
        name, sector_id = self._begin(brand, sector)
        threading.Thread(target=self._work, args=(name, sector_id, None), daemon=True).start()
        return self.status()

    def run(self, brand: str, sector: str | None = None, on_update: Callable[[dict], None] | None = None) -> dict:
        name, sector_id = self._begin(brand, sector)
        self._work(name, sector_id, on_update)
        return self.status()

    def cancel(self) -> dict:
        if self._job is None or self._job.status not in RUNNING:
            raise InvalidInput("Süren bir arama yok")
        self._cancel.set()
        return self.status()

    def _begin(self, brand: str, sector: str | None) -> tuple[str, str | None]:
        catalog = self._catalog.load()
        name = rules.canonical_brand(brand_query(brand), catalog.brands)
        sector_id = slug(sector, "sector") if sector else None
        with self._lock:
            if self._job is not None and self._job.status in RUNNING:
                raise InvalidInput(f"{self._job.brand} için bir arama sürüyor; bitmesini bekleyin ya da durdurun.")
            ready = self._runner.readiness()
            if not ready.ready:
                raise ResearchUnavailable(f"{ready.reason} {ready.next_step}")
            self._cancel = threading.Event()
            self._job = ResearchJob(uuid4().hex[:12], name, started_at=_now())
        return name, sector_id

    def _work(self, name: str, sector: str | None, on_update: Callable[[dict], None] | None) -> None:
        keys = [rules.brand_key(name, self._catalog.load().brands)]
        before = {e.id for e in self._store.list(keys)}

        def on_event(event: ResearchEvent) -> None:
            self._count(event)
            if on_update:
                on_update(self.status())

        try:
            outcome = self._runner.run(name, sector, on_event, self._cancel)
        except Exception as exc:  # the job must end, whatever the child did; details stay out of the panel
            outcome = ResearchOutcome("failed", f"Arama beklenmedik biçimde durdu ({type(exc).__name__}).")
        added = len({e.id for e in self._store.list(keys)} - before)
        self._job = replace(self._job, status=outcome.status, message=outcome.message, report=outcome.report, records=added, finished_at=_now())

    def _count(self, event: ResearchEvent) -> None:
        job = self._job
        if event.kind == "started":
            self._job = replace(job, status="searching")
        elif event.kind == "search":
            self._job = replace(job, searches=job.searches + 1)
        elif event.kind == "page":
            self._job = replace(job, pages=job.pages + 1)
        elif event.kind == "record":
            self._job = replace(job, records=job.records + 1)
        elif event.kind == "refused":
            self._job = replace(job, refused=job.refused + 1)
