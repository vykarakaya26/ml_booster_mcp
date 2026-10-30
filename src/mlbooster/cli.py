"""mlbooster CLI."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from mlbooster.adapters.base import AdapterError
from mlbooster.mapping import render_markdown, sector_map
from mlbooster.pipeline import DEFAULT_START, Workspace

app = typer.Typer(help="ML Booster Map: Türkiye ücretsiz veri kaynakları → ML'e hazır panel", no_args_is_help=True)
catalog_app = typer.Typer(help="Kaynak kataloğu", no_args_is_help=True)
map_app = typer.Typer(help="Sektör → kaynak haritası", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
app.add_typer(map_app, name="map")


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise typer.BadParameter(f"Tarih YYYY-AA veya YYYY-AA-GG olmalı: {value}")


def _fail(exc: Exception) -> None:
    typer.secho(f"Hata: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@catalog_app.command("list")
def catalog_list(sector: Annotated[str | None, typer.Option(help="Sektör id (örn. bankacilik)")] = None):
    """Katalogdaki kaynakları listeler."""
    ws = Workspace()
    sources = ws.catalog.sources_for_sector(sector) if sector else sorted(
        ws.catalog.sources.values(), key=lambda s: s.priority or 99
    )
    for s in sources:
        ready = "✓" if s.adapter else "—"
        typer.echo(f"{ready} {s.id:<8} {s.access.value:<14} {s.verification_status.value:<10} {s.name}")


@catalog_app.command("sectors")
def catalog_sectors():
    """Tanımlı sektörleri listeler."""
    for s in Workspace().catalog.sectors.values():
        typer.echo(f"{s.id:<12} {s.name}")


@catalog_app.command("series")
def catalog_series(
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    datagroup: Annotated[str | None, typer.Option(help="EVDS veri grubu kodu (örn. bie_dkdovizgn)")] = None,
):
    """Serileri listeler: datagroup verilirse kaynaktan canlı çeker, yoksa katalog tohumlarını gösterir."""
    ws = Workspace()
    adapter = ws.adapter(source)
    try:
        df = adapter.list_series(datagroup)
    except AdapterError as exc:
        _fail(exc)
    finally:
        adapter.close()
    typer.echo(df.to_string(index=False))


@map_app.command("show")
def map_show(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    format: Annotated[str, typer.Option(help="markdown | json")] = "markdown",
):
    """Bir sektörün veri kaynağı haritasını gösterir."""
    m = sector_map(Workspace().catalog, sector)
    typer.echo(json.dumps(m, ensure_ascii=False, indent=2) if format == "json" else render_markdown(m))


@app.command()
def fetch(
    series: Annotated[list[str], typer.Option(help="Seri kodu (birden fazla verilebilir)")],
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG], varsayılan bugün")] = None,
    freq: Annotated[str | None, typer.Option(help="D,B,W,SM,M,Q,S,A")] = None,
    agg: Annotated[str | None, typer.Option(help="avg,min,max,first,last,sum")] = None,
    formula: Annotated[int, typer.Option(help="0 düzey, 1 %değişim, 3 yıllık %değişim, ...")] = 0,
):
    """Serileri çeker, bronze + silver katmanına yazar ve özet basar."""
    kwargs = {"freq": freq, "agg": agg}
    if formula:
        kwargs["formula"] = formula
    try:
        df = Workspace().fetch(source, series, _date(start), _date(end), **kwargs)
    except (AdapterError, ValueError) as exc:
        _fail(exc)
    if df.empty:
        typer.echo("Gözlem dönmedi.")
        return
    summary = df.groupby(["series_id", "metric", "freq"])["date"].agg(["count", "min", "max"])
    typer.echo(summary.to_string())


@app.command("build-panel")
def build_panel(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    out: Annotated[Path | None, typer.Option(help="Parquet çıktı yolu")] = None,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    refresh: Annotated[bool, typer.Option(help="Silver önbelleğini yok sayıp yeniden çek")] = False,
):
    """Sektörün makro feature'larından aylık gold panel üretir."""
    ws = Workspace()
    result = ws.build_panel(sector, _date(start), _date(end), refresh)
    for name, reason in result.skipped.items():
        typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
    if result.panel.empty:
        _fail(RuntimeError("Panel boş; atlanan feature'lara bakın."))
    path = result.write(out or ws.root / "gold" / f"{sector}.parquet")
    typer.echo(f"{result.panel.shape[0]} ay × {result.panel.shape[1]} feature → {path}")


@app.command()
def health(source: Annotated[str | None, typer.Option(help="Tek kaynak")] = None):
    """Kanarya testleri: her adapter'ın kritik serilerinden son gözlemleri çeker."""
    results = Workspace().health(source)
    colors = {"ok": typer.colors.GREEN, "fail": typer.colors.RED, "skipped": typer.colors.YELLOW}
    for r in results:
        typer.secho(f"{r.status:<8} {r.source_id:<8} {r.detail}", fg=colors[r.status])
    if any(r.status == "fail" for r in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
