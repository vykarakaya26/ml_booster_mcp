"""Input and assumption register (assumptions.json): the "on what basis" part of the report.

The aim is transparency that strengthens the analysis. The reader must be able to tell which data, which
assumption and which inference each interpretation rests on; unconfirmed assumptions are visibly flagged.

Schema (keys are fixed; values are free text):
{
  "context": {"purpose", "decision", "audience", "target", "horizon", "units", "period"},
  "assumptions": [{"id": "A1", "topic", "statement", "kind": "user|sector|default|data|external",
                   "confirmed": bool, "source", "as_of": "YYYY-MM-DD", "confidence": "low|medium|high",
                   "impact", "check"}],
  "scenarios": [{"name", "description"}],          # optional
  "open_questions": ["..."]                        # optional
}
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from mlb_report import html as H
from mlb_report.data import read_json

KINDS = {
    "user": ("good", "✓", "Kullanıcı"),
    "sector": ("info", "◆", "Sektör varsayımı"),
    "default": ("serious", "▲", "Varsayılan"),
    "data": ("info", "≡", "Veriden"),
    "external": ("info", "↗", "Dış tahmin"),
}
CONTEXT_LABELS = [
    ("purpose", "Amaç"), ("decision", "Desteklenen karar"), ("audience", "Okur"),
    ("target", "Hedef değişken"), ("horizon", "Ufuk"), ("units", "Birim (nominal/reel)"), ("period", "Dönem"),
]  # fmt: skip
_ID = re.compile(r"^[A-Z]\d{1,3}$")
STALE_DAYS = 92  # ~3 months: older sector/regulatory information is flagged as out of date
CONFIDENCE = {"low": "düşük", "medium": "orta", "high": "yüksek"}


class AssumptionsError(ValueError):
    pass


def load(path: str | Path) -> dict:
    data = read_json(path)
    validate(data)
    return data


def validate(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("context"), dict):
        raise AssumptionsError("assumptions.json: 'context' nesnesi gereklidir")
    items = data.get("assumptions")
    if not isinstance(items, list) or not items:
        raise AssumptionsError("assumptions.json: en az bir varsayım gereklidir ('assumptions' listesi)")
    seen = set()
    for a in items:
        missing = {"id", "topic", "statement", "kind", "confirmed"} - set(a)
        if missing:
            raise AssumptionsError(f"Varsayım alanları eksik {sorted(missing)}: {a}")
        if not _ID.match(str(a["id"])) or a["id"] in seen:
            raise AssumptionsError(f"Geçersiz ya da tekrarlanan varsayım kimliği: {a['id']} (ör. A1)")
        if a["kind"] not in KINDS:
            raise AssumptionsError(f"kind şu değerlerden biri olmalıdır: {list(KINDS)} ({a['kind']})")
        if a.get("confidence") not in (None, *CONFIDENCE):
            raise AssumptionsError(f"{a['id']}: confidence şu değerlerden biri olmalıdır: {list(CONFIDENCE)}")
        if a.get("as_of"):
            try:
                date.fromisoformat(str(a["as_of"]))
            except ValueError:
                raise AssumptionsError(f"{a['id']}: as_of YYYY-AA-GG biçiminde olmalıdır") from None
        if a["kind"] == "user" and not a["confirmed"]:
            raise AssumptionsError(f"{a['id']}: 'user' türündeki varsayım tanım gereği onaylıdır (confirmed: true)")
        seen.add(a["id"])


def stale(data: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [
        a for a in data["assumptions"]
        if a.get("as_of") and a["kind"] in {"sector", "default", "external"} and (today - date.fromisoformat(a["as_of"])).days > STALE_DAYS
    ]  # fmt: skip


def unconfirmed(data: dict) -> list[dict]:
    return [a for a in data["assumptions"] if not a["confirmed"] and a["kind"] in {"default", "sector", "external"}]


def _kind_badge(kind: str, confirmed: bool) -> str:
    cls, icon, label = KINDS[kind]
    suffix = "" if confirmed or kind in {"user", "data"} else " — onaysız"
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span> {H.esc(label + suffix)}</span>'


def section(data: dict) -> str:
    ctx = data["context"]
    ctx_rows = [(label, ctx.get(key) or "—") for key, label in CONTEXT_LABELS if key in ctx]
    old = {a["id"] for a in stale(data)}

    def when(a: dict) -> str:
        text = H.esc(a.get("as_of") or "—")
        return text + (' <span class="badge serious"><span aria-hidden="true">▲</span> güncel değil — doğrulanmalı</span>' if a["id"] in old else "")

    rows = [
        (a["id"], H.esc(a["topic"]), H.esc(a["statement"]), _kind_badge(a["kind"], a["confirmed"]),
         H.esc(CONFIDENCE.get(a.get("confidence"), "—")), H.esc(a.get("source") or "—"), when(a),
         H.esc(a.get("impact") or "—"), H.esc(a.get("check") or "—"))
        for a in data["assumptions"]
    ]  # fmt: skip
    body = H.table(["Girdi", "Değer"], ctx_rows)
    pending = unconfirmed(data)
    if pending:
        ids = ", ".join(a["id"] for a in pending)
        body += (
            f'<p><span class="badge serious"><span aria-hidden="true">▲</span> Dikkat</span> '
            f"{len(pending)} varsayım kullanıcı tarafından onaylanmadı ({H.esc(ids)}). Bunlara dayanan yorumlar "
            "koşulludur; varsayımlar onaylandığında ya da değiştiğinde sonuçlar güncellenmelidir.</p>"
        )
    if old:
        body += (
            f'<p><span class="badge serious"><span aria-hidden="true">▲</span> Güncel değil</span> {len(old)} varsayıma ilişkin bilgi '
            f"{STALE_DAYS} günden eski ({H.esc(', '.join(sorted(old)))}); birincil kaynaktan doğrulanmalıdır.</p>"
        )
    body += "<h3>Varsayımlar</h3>" + H.table(
        ["#", "Konu", "Varsayım", "Tür", "Güven", "Kaynak", "Tarih", "Etkisi", "Nasıl kontrol edilir"],
        rows, raw=[1, 2, 3, 4, 5, 6, 7, 8],
    )
    if data.get("scenarios"):
        body += "<h3>Senaryolar</h3>" + H.table(["Senaryo", "Açıklama"], [(s["name"], s["description"]) for s in data["scenarios"]])
    if data.get("open_questions"):
        body += "<h3>Açık sorular</h3><ul>" + "".join(f"<li>{H.esc(q)}</li>" for q in data["open_questions"]) + "</ul>"
    legend = (
        '<p class="muted">Raporda her ifade bir dayanağa bağlanır: <strong>veri</strong> (yayımlanmış gözlem), '
        "<strong>varsayım</strong> (A# kimliğiyle), <strong>model</strong> (forecast/backtest; yöntem ve belirsizlikle birlikte) "
        "ya da <strong>yorum</strong> (analistin çıkarımı, güven düzeyiyle).</p>"
    )
    return H.section("Girdiler ve varsayımlar", legend + body, "assumptions")
