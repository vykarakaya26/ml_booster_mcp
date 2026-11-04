"""Brand report: assembles the share analyses of the series a brand appears in into one view (no I/O).

The input is the output of `market_share.analyze`, one per series; the first series is the primary one. Every number
comes from those analyses: a value that a series does not give stays None, nothing is filled in.
"""

from __future__ import annotations

from datetime import date

from mlbooster.domain.numfmt import tr_num, tr_pct
from mlbooster.domain.services.brand_resolution import is_placeholder
from mlbooster.domain.services.market_share import OVERLAP_MIN

AROUND_RANKS = 5  # gainers and losers are looked for this many places above and below the brand
MAX_MOVERS = 3
MAX_COMPETITORS = 10
MAX_OVERLAPS = 3

_POSITION_FIELDS = ("share", "d_prev_pp", "d_year_pp", "rank_year_ago", "relative_share", "trend", "trend_pp_per_year",
                    "trend_t", "volatility_pp", "growth_yoy_pct", "growth_vs_market_pp")  # fmt: skip


_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _rivals(players: list[dict], focus: str, exclude: set[str]) -> list[dict]:
    """Active players ranked by share, without the source's residual row and the brand's sibling entities."""
    kept = [
        p for p in players
        if p["entity"] == focus or (p["share"] > 0 and not is_placeholder(p["entity"]) and p["entity"] not in exclude)
    ]  # fmt: skip
    return sorted(kept, key=lambda p: (-p["share"], p["entity"]))


def concentration_summary(result: dict) -> dict:
    latest, year_ago = result["concentration"]["latest"], result["concentration"]["year_ago"]
    out = {key: latest.get(key) for key in ("hhi", "hhi_upper", "cr1", "cr3", "cr5", "equivalent_players", "class", "class_upper")}
    out["d_hhi_year"] = latest["hhi"] - year_ago["hhi"] if year_ago and year_ago.get("hhi") is not None else None
    return out


def series_summary(result: dict, code: str, name: str, exclude: set[str] = frozenset()) -> dict:
    """Position of the focus entity in one series."""
    meta = result["meta"]
    focus = meta["focus"]
    ranked = _rivals(result["players"], focus, set(exclude))
    me = next(p for p in ranked if p["entity"] == focus)
    above = [p for p in ranked if p["share"] > me["share"]]
    below = [p for p in ranked if p["share"] < me["share"]]
    coverage = result["market"]["coverage"]
    return {
        "series": code, "name": name, "focus": focus, "unit": meta["unit"], "basis": meta["basis"], "kind": meta["kind"],
        "measure": meta["measure"], "first": meta["first"], "last": meta["last"],
        "rank": len(above) + 1, "players": len(ranked), "leader": ranked[0]["entity"], "leader_share": ranked[0]["share"],
        "gap_to_leader_pp": ranked[0]["share"] - me["share"],
        "next_above": above[-1]["entity"] if above else None,
        "gap_to_next_pp": above[-1]["share"] - me["share"] if above else None,
        "next_below": below[0]["entity"] if below else None,
        "coverage": coverage[-1] if coverage else None,
        "concentration": concentration_summary(result),
        **{key: me.get(key) for key in _POSITION_FIELDS},
    }  # fmt: skip


def movers(result: dict, exclude: set[str] = frozenset(), display: dict[str, str] | None = None, around: int = AROUND_RANKS, limit: int = MAX_MOVERS) -> dict:
    """Largest one-year share gainers and losers among the players ranked near the brand."""
    display = display or {}
    focus = result["meta"]["focus"]
    ranked = _rivals(result["players"], focus, set(exclude))
    i = next(k for k, p in enumerate(ranked) if p["entity"] == focus)
    near = [p for p in ranked[max(0, i - around) : i + around + 1] if p["entity"] != focus and p.get("d_year_pp") is not None]
    rows = [{"entity": display.get(p["entity"], p["entity"]), "share": p["share"], "d_year_pp": p["d_year_pp"]} for p in near]
    return {
        "gainers": sorted((r for r in rows if r["d_year_pp"] > 0), key=lambda r: -r["d_year_pp"])[:limit],
        "losers": sorted((r for r in rows if r["d_year_pp"] < 0), key=lambda r: r["d_year_pp"])[:limit],
    }


def merge_competitors(
    results: list[tuple[str, dict]],
    segments: dict | None = None,
    exclude: set[str] = frozenset(),
    display: dict[str, str] | None = None,
) -> list[dict]:
    """One competitor list from all series: a rival named in more series ranks higher. Every reason says which series
    it comes from. `display` maps source labels to catalog brands, so one company's operators count as one rival. The
    share and correlation are those of the primary series (None if the rival is not listed there); players without
    volume in the primary series are left out."""
    display = display or {}
    primary = results[0][1]
    shares = {p["entity"]: p["share"] for p in primary["players"]}
    corr = {row["entity"]: row["r"] for row in primary.get("competitors", {}).get("substitution", [])}
    merged: dict[str, dict] = {}

    def add(entity: str, series: str, reason: str) -> None:
        if entity in exclude or is_placeholder(entity) or shares.get(entity) == 0:
            return
        name = display.get(entity, entity)
        row = merged.setdefault(name, {"entity": name, "share": None, "r": None, "series": [], "reasons": []})
        row["share"] = row["share"] if row["share"] is not None else shares.get(entity)
        row["r"] = row["r"] if row["r"] is not None else corr.get(entity)
        if series not in row["series"]:
            row["series"].append(series)
        row["reasons"].append({"series": series, "reason": reason})

    for code, result in results:
        for candidate in result.get("competitors", {}).get("candidates", []):
            for reason in candidate["reasons"]:
                add(candidate["entity"], code, reason)
    for entity, value in _overlapping(primary, segments, exclude):
        add(entity, "segment", f"segment karması örtüşüyor ({tr_pct(value * 100, '.0f')})")
    ranked = sorted(merged.values(), key=lambda c: (-len(c["series"]), -len(c["reasons"]), -(c["share"] or 0), c["entity"]))
    return ranked[:MAX_COMPETITORS]


def _overlapping(primary: dict, segments: dict | None, exclude: set[str]) -> list[tuple[str, float]]:
    """Players ranked near the brand with the most similar segment mix; many small players can match the mix exactly."""
    overlaps = ((segments or {}).get("segments") or {}).get("overlap", {})
    focus = primary["meta"]["focus"]
    ranked = _rivals(primary["players"], focus, exclude - {focus})
    i = next(k for k, p in enumerate(ranked) if p["entity"] == focus)
    near = ranked[max(0, i - AROUND_RANKS) : i + AROUND_RANKS + 1]
    found = [(p["entity"], overlaps[p["entity"]], p["share"]) for p in near if overlaps.get(p["entity"], 0) >= OVERLAP_MIN]
    return [(entity, value) for entity, value, _ in sorted(found, key=lambda x: (-x[1], -x[2]))[:MAX_OVERLAPS]]


def merged_issues(results: list[tuple[str, dict]]) -> list[dict]:
    """Issues of all series; a message repeated by several series is listed once with those series."""
    merged: dict[tuple[str, str], dict] = {}
    for code, result in results:
        for issue in result["issues"]:
            row = merged.setdefault((issue["check"], issue["message"]), {**issue, "series": []})
            row["series"].append(code)
    return list(merged.values())


def headline(brand: str, sector: str, primary: dict) -> str:
    basis = "son 12 ay" if primary["basis"] == "ttm" else "dönem"
    last = date.fromisoformat(primary["last"])
    text = (
        f"{brand}, {sector} sektöründe {primary['series']} serisinde {_MONTHS[last.month - 1]} {last.year} itibarıyla {basis} "
        f"bazında {tr_pct(primary['share'])} pay ile {primary['players']} aktif oyuncu arasında {primary['rank']}. sırada."
    )
    if primary.get("d_year_pp") is not None:
        text += f" Payı bir yılda {tr_num(primary['d_year_pp'], '+.1f')} puan değişti."
    return text


def source_note(source, is_proxy: bool) -> dict:
    """Attribution and licence of a source, as the report must show them."""
    return {
        "id": source.id, "name": source.name, "owner": source.owner, "attribution": f"Kaynak: {source.owner} — {source.name}",
        "license": source.license.notes, "commercial": source.license.commercial, "redistribution": source.license.redistribution,
        "verification": source.verification.value, "is_proxy": is_proxy,
    }  # fmt: skip
