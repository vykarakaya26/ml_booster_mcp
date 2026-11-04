"""Groups and distributors as a sum of the brands a source lists (no I/O).

No source publishes "Doğuş Otomotiv" as a player, but ODMD lists the brands it distributes. Their sum is an honest
computed share as long as a brand counts only in the dates its membership holds: a brand that joined later stays a
separate player before that date. A membership without a start date is applied to the whole period and the report says so.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from mlbooster.domain.model import BrandGroup, GroupMember
from mlbooster.domain.numfmt import tr_num

SUM_LABEL = "üye markaların toplamı"
SUM_TOLERANCE_PP = 0.05


def is_member_at(member: GroupMember, day: date) -> bool:
    return (member.start is None or day >= member.start) and (member.end is None or day <= member.end)


def merged_table(table: pd.DataFrame, group: BrandGroup, labels: dict[str, str]) -> pd.DataFrame:
    """Share table with the group as one player. `labels`: entity id → the label the table uses for it."""
    out = table.copy()
    dates = pd.to_datetime(out["date"])
    for member in group.members:
        rows = out["entity"] == labels.get(member.entity)
        if member.start is not None:
            rows &= dates >= pd.Timestamp(member.start)
        if member.end is not None:
            rows &= dates <= pd.Timestamp(member.end)
        out.loc[rows, "entity"] = group.name
    return out.groupby(["entity", "date"], as_index=False, sort=False)["value"].sum()


def _member_row(member: GroupMember, player: dict | None, last: date) -> dict:
    return {
        "name": member.name, "entity": member.entity, "share": player["share"] if player else None,
        "rank": player.get("rank") if player else None, "d_year_pp": player.get("d_year_pp") if player else None,
        "from": member.start.isoformat() if member.start else None, "to": member.end.isoformat() if member.end else None,
        "included": is_member_at(member, last), "url": member.url, "checked": member.checked.isoformat(), "note": member.note,
    }  # fmt: skip


def summary(group: BrandGroup, players: list[dict], labels: dict[str, str], share: float, last: date) -> dict:
    """The group next to its members. `players`: the analysis with the brands apart, so each member keeps its own share."""
    by_label = {p["entity"]: p for p in players}
    rows = [_member_row(m, by_label.get(labels.get(m.entity)), last) for m in group.members]
    rows.sort(key=lambda r: (not r["included"], -(r["share"] or 0), r["name"]))
    total = sum(r["share"] or 0 for r in rows if r["included"])
    undated = [m.name for m in group.members if m.start is None]
    notes = [f"Kaynak grubu ayrı bir oyuncu olarak yayımlamaz; grup payı {SUM_LABEL}dır. Sıra, HHI ve rakipler marka düzeyindeki öteki oyuncularla bu görünüme göre hesaplandı."]
    if undated:
        notes.append(f"Başlangıç tarihi doğrulanamadı, incelenen dönemin tamamında gruba dahil sayıldı: {', '.join(undated)}.")
    joined: dict[date, list[str]] = {}
    for member in group.members:
        if member.start is not None:
            joined.setdefault(member.start, []).append(member.name)
    for day, names in sorted(joined.items()):
        notes.append(f"{', '.join(names)}: {day.isoformat()} tarihinden itibaren gruba dahildir. Önceki dönemlerde ayrı sayılır; son 12 ay payı bu tarihten sonra kademeli yükselir.")
    if abs(total - share) > SUM_TOLERANCE_PP:
        notes.append(f"Üyelerin bugünkü payları toplamı %{tr_num(total, '.1f')}; grup payı %{tr_num(share, '.1f')}. Fark, son 12 ay içindeki üyelik değişikliğinden gelir.")
    if group.note:
        notes.append(group.note)
    return {"id": group.id, "name": group.name, "label": SUM_LABEL, "share": share, "members_sum": total, "members": rows, "notes": notes}
