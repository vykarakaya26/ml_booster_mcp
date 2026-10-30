"""MCP sunucusu (stdio). Claude Code'a ekleme:

    claude mcp add mlbooster -e EVDS_API_KEY=... -- mlbooster-mcp
"""

from __future__ import annotations

from datetime import date

from mcp.server.mcpserver import MCPServer

from mlbooster.mapping import render_markdown, sector_map
from mlbooster.pipeline import DEFAULT_START, Workspace

server = MCPServer(
    "mlbooster",
    instructions=(
        "Türkiye için ücretsiz veri kaynakları haritası ve ML'e hazır aylık panel üretici. "
        "Önce list_sectors / show_map ile kaynakları gör; EVDS serilerini discover_series ile keşfet; "
        "fetch_series ile çek; build_panel ile sektör panelini üret. Verilerde kaynak atfı zorunludur."
    ),
)


def _ws() -> Workspace:
    return Workspace()


@server.tool()
def list_sectors() -> list[dict]:
    """Tanımlı sektörleri (id, ad) listeler."""
    return [{"id": s.id, "name": s.name} for s in _ws().catalog.sectors.values()]


@server.tool()
def list_sources(sector: str | None = None) -> list[dict]:
    """Kaynak kataloğunu listeler; sector verilirse o sektörle ilgili kaynaklar."""
    catalog = _ws().catalog
    sources = catalog.sources_for_sector(sector) if sector else catalog.sources.values()
    return [
        {
            "id": s.id,
            "name": s.name,
            "access": s.access.value,
            "verification_status": s.verification_status.value,
            "adapter_ready": s.adapter is not None,
            "license": s.license.notes,
        }
        for s in sources
    ]


@server.tool()
def show_map(sector: str) -> str:
    """Bir sektörün veri kaynağı haritasını (kaynaklar, makro feature'lar, pazar payı) markdown olarak döner."""
    return render_markdown(sector_map(_ws().catalog, sector))


@server.tool()
def discover_series(datagroup: str | None = None, source: str = "evds3") -> list[dict]:
    """Seri keşfi. EVDS veri grubu kodu (örn. bie_dkdovizgn) verilirse canlı /serieList sonucu,
    verilmezse katalogdaki tohum seriler döner."""
    adapter = _ws().adapter(source)
    try:
        return adapter.list_series(datagroup).to_dict(orient="records")
    finally:
        adapter.close()


@server.tool()
def fetch_series(
    series: list[str],
    start: str = DEFAULT_START.isoformat(),
    end: str | None = None,
    source: str = "evds3",
    freq: str | None = None,
    agg: str | None = None,
    formula: int = 0,
    max_rows: int = 50,
) -> dict:
    """Serileri çeker ve yerel silver katmanına yazar. Tarihler YYYY-AA-GG.
    freq: D,B,W,SM,M,Q,S,A; agg: avg,min,max,first,last,sum; formula: 0 düzey, 1 %değişim, 3 yıllık %değişim.
    Özet ve son `max_rows` satırı döner."""
    kwargs = {"freq": freq, "agg": agg}
    if formula:
        kwargs["formula"] = formula
    df = _ws().fetch(
        source, series, date.fromisoformat(start), date.fromisoformat(end) if end else None, **kwargs
    )
    if df.empty:
        return {"rows": 0, "summary": [], "tail": []}
    summary = (
        df.groupby(["series_id", "metric", "freq"])["date"].agg(["count", "min", "max"]).reset_index()
    )
    tail = df.sort_values("date").tail(max_rows)[["series_id", "metric", "date", "value"]]
    return {
        "rows": len(df),
        "summary": summary.astype(str).to_dict(orient="records"),
        "tail": tail.astype({"date": str}).to_dict(orient="records"),
        "attribution": "Kaynak: TCMB EVDS" if source == "evds3" else source,
    }


@server.tool()
def build_panel(sector: str, start: str = DEFAULT_START.isoformat(), end: str | None = None) -> dict:
    """Sektörün makro feature'larından aylık panel üretir, Parquet'e yazar; yol, boyut ve atlananları döner."""
    ws = _ws()
    result = ws.build_panel(sector, date.fromisoformat(start), date.fromisoformat(end) if end else None)
    path = result.write(ws.root / "gold" / f"{sector}.parquet") if not result.panel.empty else None
    return {
        "path": str(path) if path else None,
        "months": result.panel.shape[0],
        "features": list(result.panel.columns),
        "skipped": result.skipped,
        "tail": result.panel.tail(6).round(4).reset_index().astype({"date": str}).to_dict(orient="records"),
    }


@server.tool()
def health(source: str | None = None) -> list[dict]:
    """Kanarya testleri: adapter'ların kritik serilerden son gözlemleri çekip çekemediği."""
    return [vars(r) for r in _ws().health(source)]


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
