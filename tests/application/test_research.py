import threading
import time

import pytest

from fakes import FakeResearchRunner, InMemoryEvidenceStore, StaticCatalog
from mlbooster.application.dto import ResearchOutcome, RunnerReadiness
from mlbooster.application.errors import ResearchUnavailable
from mlbooster.application.use_cases.evidence import RecordEvidence
from mlbooster.application.use_cases.research import WebResearch
from mlbooster.domain.errors import InvalidInput
from mlbooster.domain.services.evidence import EvidenceDraft


@pytest.fixture
def repo(catalog):
    return StaticCatalog(catalog)


def _wait(research, status):
    for _ in range(500):
        if research.status()["status"] == status:
            return research.status()
        time.sleep(0.01)
    raise AssertionError(research.status())


def test_nothing_runs_until_it_is_started(repo):
    runner = FakeResearchRunner()
    research = WebResearch(repo, runner, InMemoryEvidenceStore())
    assert research.status() == {"status": "idle", "label": "Arama başlatılmadı", "running": False} and runner.calls == []


def test_a_foreground_run_counts_and_takes_new_records_from_the_store(repo):
    store = InMemoryEvidenceStore()
    record = RecordEvidence(repo, store)
    draft = EvidenceDraft("Migros", 24.5, "revenue", "2024", "2025-03-01", "Migros", "https://www.migroskurumsal.com/x.pdf", "pazar payı %24,5")
    runner = FakeResearchRunner(record=lambda: record(draft))
    updates = []
    job = WebResearch(repo, runner, store).run("migros ticaret", "perakende", updates.append)
    assert runner.calls == [("Migros", "perakende")]
    assert job["status"] == "finished" and job["searches"] == 1 and job["pages"] == 1 and job["records"] == 1 and job["report"] == "Rapor"
    assert updates[0]["status"] == "searching" and not job["running"]


def test_one_job_at_a_time_and_cancel(repo):
    runner = FakeResearchRunner(release=threading.Event())
    research = WebResearch(repo, runner, InMemoryEvidenceStore())
    assert research.start("Migros")["running"]
    _wait(research, "searching")
    with pytest.raises(InvalidInput, match="sürüyor"):
        research.start("Togg")
    research.cancel()
    assert _wait(research, "cancelled")["message"] == "Arama durduruldu."
    with pytest.raises(InvalidInput, match="Süren"):
        research.cancel()
    runner.release.set()
    research.start("Togg")
    assert _wait(research, "finished")["brand"] == "Togg"


def test_a_missing_claude_is_said_before_anything_starts(repo):
    runner = FakeResearchRunner(RunnerReadiness(False, "Claude Code bulunamadı.", "Kurun."))
    research = WebResearch(repo, runner, InMemoryEvidenceStore())
    with pytest.raises(ResearchUnavailable, match="bulunamadı. Kurun."):
        research.start("Migros")
    assert research.status()["status"] == "idle" and runner.calls == []


def test_the_name_is_validated(repo):
    research = WebResearch(repo, FakeResearchRunner(), InMemoryEvidenceStore())
    for bad in ("x", "a\x00b" * 2, "y" * 200):
        with pytest.raises(InvalidInput):
            research.run(bad)
    with pytest.raises(InvalidInput):
        research.run("Migros", "../x")


def test_a_failure_ends_the_job(repo):
    outcome = ResearchOutcome("failed", "Bütçe sınırı.")
    assert WebResearch(repo, FakeResearchRunner(outcome=outcome), InMemoryEvidenceStore()).run("Migros")["message"] == "Bütçe sınırı."

    class Broken(FakeResearchRunner):
        def run(self, brand, sector, on_event, cancel):
            raise RuntimeError("/secret/path")

    job = WebResearch(repo, Broken(), InMemoryEvidenceStore()).run("Migros")
    assert job["status"] == "failed" and "/secret" not in job["message"]
