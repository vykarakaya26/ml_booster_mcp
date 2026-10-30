"""mlbooster CLI (typer). Yalnızca composition root'tan alınan use case'leri çağırır."""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Annotated

import typer

from mlbooster.application.dto import FetchRequest
from mlbooster.application.use_cases.panel import DEFAULT_START
from mlbooster.bootstrap import Container, build_container
from mlbooster.domain.errors import DomainError
from mlbooster.domain.model import HealthStatus
from mlbooster.interfaces.presenters import sector_map_dict, sector_map_markdown

app = typer.Typer(help="ML Booster Map: Türkiye ücretsiz veri kaynakları → ML'e hazır panel", no_args_is_help=True)
catalog_app = typer.Typer(help="Kaynak kataloğu", no_args_is_help=True)
map_app = typer.Typer(help="Sektör → kaynak haritası", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
app.add_typer(map_app, name="map")


@cache
def container() -> Container:
    return build_container()


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
def catalog_list(sector: Annotated[str | None, typer.Option(help="Sektör id (ör. bankacilik)")] = None):
    """Katalogdaki kaynakları listeler."""
    try:
        sources = container().list_sources(sector)
    except DomainError as exc:
        _fail(exc)
    ready = container().providers.available()
    for s in sources:
        mark = "✓" if s.provider in ready else "—"
        typer.echo(f"{mark} {s.id:<8} {s.access.value:<14} {s.verification.value:<10} {s.name}")


@catalog_app.command("sectors")
def catalog_sectors():
    """Tanımlı sektörleri listeler."""
    for s in container().list_sectors():
        typer.echo(f"{s.id:<12} {s.name}")


@catalog_app.command("series")
def catalog_series(
    source: Annotated[str, typer.Option(help="Kaynak id")] = "evds3",
    datagroup: Annotated[str | None, typer.Option(help="EVDS veri grubu kodu (ör. bie_dkdovizgn)")] = None,
):
    """Serileri listeler: datagroup verilirse kaynaktan canlı çeker, yoksa katalog tohumları."""
    try:
        df = container().discover_series(source, datagroup)
    except DomainError as exc:
        _fail(exc)
    typer.echo(df.to_string(index=False))


@map_app.command("show")
def map_show(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    format: Annotated[str, typer.Option(help="markdown | json")] = "markdown",
):
    """Bir sektörün veri kaynağı haritasını gösterir."""
    try:
        m = container().sector_map(sector)
    except DomainError as exc:
        _fail(exc)
    typer.echo(json.dumps(sector_map_dict(m), ensure_ascii=False, indent=2) if format == "json" else sector_map_markdown(m))


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
    """Serileri çeker, yerel depoya yazar ve özet basar."""
    try:
        request = FetchRequest.create(source, series, _date(start), _date(end), freq, agg, formula)
        summary = container().fetch_series.summarize(request, max_rows=0)
    except DomainError as exc:
        _fail(exc)
    if not summary.rows:
        typer.echo("Gözlem dönmedi.")
        return
    for row in summary.by_series:
        typer.echo(f"{row['series_id']:<20} {row['metric']:<12} {row['freq']:<3} {row['count']:>6}  {row['min']} → {row['max']}")
    typer.echo(summary.attribution)


@app.command("build-panel")
def build_panel(
    sector: Annotated[str, typer.Option(help="Sektör id")],
    out: Annotated[Path | None, typer.Option(help="Ek olarak bu .parquet yoluna da yaz")] = None,
    start: Annotated[str, typer.Option(help="YYYY-AA[-GG]")] = DEFAULT_START.isoformat(),
    end: Annotated[str | None, typer.Option(help="YYYY-AA[-GG]")] = None,
    refresh: Annotated[bool, typer.Option(help="Yerel önbelleği yok sayıp yeniden çek")] = False,
):
    """Sektörün makro feature'larından aylık gold panel üretir."""
    try:
        result = container().build_panel(sector, _date(start), _date(end), refresh)
        for name, reason in result.skipped.items():
            typer.secho(f"atlandı  {name}: {reason}", fg=typer.colors.YELLOW)
        if result.panel.empty:
            _fail(RuntimeError("Panel boş; atlanan feature'lara bakın."))
        location = result.location
        if out is not None:
            location = container().panels.export(result.panel, out)
    except (DomainError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"{result.panel.shape[0]} ay × {result.panel.shape[1]} feature → {location}")


@app.command()
def health(source: Annotated[str | None, typer.Option(help="Tek kaynak")] = None):
    """Kanarya testleri: her sağlayıcının kritik serilerinden son gözlemleri çeker."""
    try:
        results = container().health(source)
    except DomainError as exc:
        _fail(exc)
    colors = {HealthStatus.OK: typer.colors.GREEN, HealthStatus.FAIL: typer.colors.RED, HealthStatus.SKIPPED: typer.colors.YELLOW}
    for r in results:
        typer.secho(f"{r.status.value:<8} {r.source_id:<8} {r.detail}", fg=colors[r.status])
    if any(r.status is HealthStatus.FAIL for r in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
