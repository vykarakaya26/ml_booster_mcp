"""Market share report parts shared by the skill's render scripts: formats, competitor input, segment section."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from mlb_report import html as H
from mlb_report.data import read_json

TIERS = {"direct": "Doğrudan rakip", "indirect": "Dolaylı rakip", "potential": "Potansiyel rakip / yeni giren"}
CONFIDENCE = {"high": "Yüksek", "medium": "Orta", "low": "Düşük"}
TREND = {"gaining": ("good", "▲", "Pay kazanıyor"), "losing": ("critical", "▼", "Pay kaybediyor"),
         "flat": ("info", "■", "Yatay"), "insufficient_data": ("info", "·", "Yetersiz gözlem")}  # fmt: skip
HHI_CLASS = {"low": "düşük", "moderate": "orta", "high": "yüksek"}


class CompetitorsError(ValueError):
    pass


def num(v: float | None, d: int = 1) -> str:
    """Turkish number format: period as thousands separator, comma as decimal mark."""
    return H.num(v, d)


def pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"%{num(v, d)}"


def signed(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{'+' if v > 0 else ''}{num(v, d)}"


def pp(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{signed(v, d)} puan"


def month(iso: str | None) -> str:
    if not iso:
        return "—"
    y, m = iso[:4], int(iso[5:7])
    names = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    return f"{names[m - 1]} {y}"


def trend_badge(label: str) -> str:
    cls, icon, text = TREND.get(label, ("info", "·", label))
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span> {H.esc(text)}</span>'


def load_competitors(path: Path) -> dict:
    data = read_json(path)
    validate_competitors(data)
    return data


def validate_competitors(data: dict) -> None:
    items = data.get("competitors") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CompetitorsError("competitors.json: 'competitors' listesi gereklidir")
    for c in items:
        if not isinstance(c, dict) or not str(c.get("entity", "")).strip():
            raise CompetitorsError(f"Her rakip kaydında 'entity' bulunmalıdır: {c}")
        if c.get("tier") not in TIERS:
            raise CompetitorsError(f"tier şu değerlerden biri olmalıdır: {', '.join(TIERS)} ({c.get('entity')})")
        if c.get("confidence", "medium") not in CONFIDENCE:
            raise CompetitorsError(f"confidence şu değerlerden biri olmalıdır: {', '.join(CONFIDENCE)} ({c.get('entity')})")
        url = c.get("source_url")
        if url and urlsplit(str(url)).scheme not in {"https", "http"}:
            raise CompetitorsError(f"source_url http(s) olmalıdır: {url}")
    if data.get("as_of"):
        try:
            date.fromisoformat(str(data["as_of"]))
        except ValueError:
            raise CompetitorsError("as_of YYYY-AA-GG biçiminde olmalıdır") from None


def segment_section(seg: dict, focus: str | None, names: dict | None = None) -> str:
    """`names`: segment code → display name."""
    names = names or {}
    rows = []
    for s in seg["segments"]:
        rows.append((H.esc(names.get(s["segment"], s["segment"])), pct(s["weight"]), signed(s.get("weight_d_pp")), num(s["hhi"], 0),
                     H.esc(s.get("leader") or "—"), pct(s.get("leader_share")),
                     pct(s.get("focus_share")) if focus else "—", signed(s.get("focus_share_d_pp")) if focus else "—"))  # fmt: skip
    head = ["Segment", "Pazardaki ağırlık", "Ağırlık değişimi (puan)", "HHI", "Lider", "Lider payı"]
    head += [f"{focus} payı", f"{focus} yıllık değişim (puan)"] if focus else ["Odak payı", "Odak değişimi (puan)"]
    body = H.table(head, rows, numeric=[1, 2, 3, 5, 6, 7], raw=[0, 4])
    ss = seg.get("shift_share")
    if ss and focus:
        body += (
            f"<h3>{H.esc(focus)} pay değişiminin kaynağı ({H.esc(month(ss['from']))} → {H.esc(month(ss['to']))})</h3>"
            f"<p>Toplam pay değişimi <strong>{pp(ss['total_pp'])}</strong>: segment içi pay etkisi {pp(ss['share_effect_pp'])}, "
            f"segment karması etkisi {pp(ss['mix_effect_pp'])}. Pay etkisi oyuncunun segmentlerde rakiplerine karşı "
            "konumundaki değişimi, karma etkisi ise oyuncunun güçlü olduğu segmentlerin büyüyüp küçülmesini gösterir.</p>"
        )
        body += H.table(["Segment", "Pay etkisi (puan)", "Karma etkisi (puan)"],
                        [(H.esc(names.get(r["segment"], r["segment"])), signed(r["share_effect_pp"]), signed(r["mix_effect_pp"])) for r in ss["by_segment"]],
                        numeric=[1, 2], raw=[0])  # fmt: skip
    return body
