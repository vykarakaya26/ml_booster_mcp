"""Girdi ve varsayım kaydı (assumptions.json): raporun "neye dayanarak" kısmı.

Amaç güçlendirici şeffaflıktır. Okur her yorumun hangi veriye, hangi varsayıma ve hangi
çıkarıma dayandığını ayırt edebilmeli; onaylanmamış varsayımlar görünür biçimde işaretlenir.

Şema (anahtarlar İngilizce, değerler Türkçe):
{
  "context": {"purpose", "decision", "audience", "target", "horizon", "units", "period"},
  "assumptions": [{"id": "A1", "topic", "statement", "kind": "user|sector|default|data",
                   "confirmed": bool, "source", "as_of": "YYYY-AA-GG", "confidence": "low|medium|high",
                   "impact", "check"}],
  "scenarios": [{"name", "description"}],          # opsiyonel
  "open_questions": ["..."]                        # opsiyonel
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
}
CONTEXT_LABELS = [
    ("purpose", "Amaç"), ("decision", "Desteklenen karar"), ("audience", "Okur"),
    ("target", "Hedef değişken"), ("horizon", "Ufuk"), ("units", "Birim (nominal/reel)"), ("period", "Dönem"),
]  # fmt: skip
_ID = re.compile(r"^[A-Z]\d{1,3}$")
STALE_DAYS = 92  # ~3 ay: daha eski sektör/regülasyon bilgisi "bayat — doğrula"
CONFIDENCE = {"low": "düşük", "medium": "orta", "high": "yüksek"}


class AssumptionsError(ValueError):
    pass


def load(path: str | Path) -> dict:
    data = read_json(path)
    validate(data)
    return data


def validate(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("context"), dict):
        raise AssumptionsError("assumptions.json: 'context' nesnesi gerekli")
    items = data.get("assumptions")
    if not isinstance(items, list) or not items:
        raise AssumptionsError("assumptions.json: en az bir varsayım gerekli ('assumptions' listesi)")
    seen = set()
    for a in items:
        missing = {"id", "topic", "statement", "kind", "confirmed"} - set(a)
        if missing:
            raise AssumptionsError(f"Varsayım alanları eksik {sorted(missing)}: {a}")
        if not _ID.match(str(a["id"])) or a["id"] in seen:
            raise AssumptionsError(f"Geçersiz/tekrarlı varsayım kimliği: {a['id']} (ör. A1)")
        if a["kind"] not in KINDS:
            raise AssumptionsError(f"kind {list(KINDS)} olmalı: {a['kind']}")
        if a.get("confidence") not in (None, *CONFIDENCE):
            raise AssumptionsError(f"{a['id']}: confidence {list(CONFIDENCE)} olmalı")
        if a.get("as_of"):
            try:
                date.fromisoformat(str(a["as_of"]))
            except ValueError:
                raise AssumptionsError(f"{a['id']}: as_of YYYY-AA-GG olmalı") from None
        if a["kind"] == "user" and not a["confirmed"]:
            raise AssumptionsError(f"{a['id']}: 'user' türü varsayım tanım gereği onaylıdır (confirmed: true)")
        seen.add(a["id"])


def stale(data: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [
        a for a in data["assumptions"]
        if a.get("as_of") and a["kind"] in {"sector", "default"} and (today - date.fromisoformat(a["as_of"])).days > STALE_DAYS
    ]  # fmt: skip


def unconfirmed(data: dict) -> list[dict]:
    return [a for a in data["assumptions"] if not a["confirmed"] and a["kind"] in {"default", "sector"}]


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
        return text + (' <span class="badge serious"><span aria-hidden="true">▲</span> bayat — doğrula</span>' if a["id"] in old else "")

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
            "koşulludur; onaylandığında ya da değiştiğinde sonuçlar güncellenmelidir.</p>"
        )
    if old:
        body += (
            f'<p><span class="badge serious"><span aria-hidden="true">▲</span> Bayat</span> {len(old)} varsayımın bilgisi '
            f"{STALE_DAYS} günden eski ({H.esc(', '.join(sorted(old)))}); birincil kaynaktan doğrulanmalı.</p>"
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
        "<strong>varsayım</strong> (A# kimliğiyle), <strong>model</strong> (tahmin/backtest, yöntem ve belirsizlikle) "
        "ya da <strong>yorum</strong> (analistin çıkarımı, güven düzeyiyle).</p>"
    )
    return H.section("Girdiler ve varsayımlar", legend + body, "varsayimlar")
