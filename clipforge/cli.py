"""CLI de ClipForge."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from clipforge import ClipForgeError
from clipforge.config import load_config
from clipforge.job import DATA_DIR, create_job, open_job
from clipforge.pipeline import STAGES, rerender_clip, run_pipeline

app = typer.Typer(help="Convierte videos largos en clips verticales 9:16 con subtítulos.", no_args_is_help=True)


def _setup(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "faster_whisper", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _guard(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except ClipForgeError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.secho("Interrumpido. Puedes continuar con `clipforge resume <job_id>`.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(130) from None


ConfigOpt = typer.Option(None, "--config", "-c", help="Ruta a config.yaml")
DataOpt = typer.Option(DATA_DIR, "--data-dir", help="Carpeta de trabajos")
VerboseOpt = typer.Option(False, "--verbose", "-v")


@app.command()
def run(
    source: str = typer.Argument(..., help="URL de YouTube o archivo de video local"),
    clips: Optional[int] = typer.Option(None, "--clips", "-n", help="Número de clips"),
    min_s: Optional[float] = typer.Option(None, "--min", help="Duración mínima (s)"),
    max_s: Optional[float] = typer.Option(None, "--max", help="Duración máxima (s)"),
    faces: bool = typer.Option(True, "--faces/--no-faces", help="Seguir la cara (o recorte centrado)"),
    silences: Optional[bool] = typer.Option(None, "--cut-silences/--keep-silences",
                                            help="Quitar pausas dentro de los clips (por defecto: config.yaml)"),
    layout: Optional[str] = typer.Option(None, "--layout",
                                         help="Diseño: auto | face (cara) | split (webcam + pantalla) | screen"),
    job_id: Optional[str] = typer.Option(None, "--job-id", help="Id del trabajo (por defecto, automático)"),
    config: Optional[Path] = ConfigOpt,
    data_dir: Path = DataOpt,
    verbose: bool = VerboseOpt,
) -> None:
    """Procesa un video completo: descarga → transcripción → selección → clips."""
    _setup(verbose)

    def go() -> None:
        cfg = load_config(config)
        opts = {"clips": clips or cfg.clips, "min": min_s or cfg.min_seconds,
                "max": max_s or cfg.max_seconds, "faces": faces,
                "silences": cfg.silences.enabled if silences is None else silences,
                "layout": layout or cfg.layout.mode}
        if opts["layout"] not in ("auto", "face", "split", "screen"):
            raise ClipForgeError("--layout debe ser auto, face, split o screen.")
        if opts["min"] >= opts["max"]:
            raise ClipForgeError("--min debe ser menor que --max.")
        if not source.startswith(("http://", "https://")) and Path(source).exists():
            src = str(Path(source).resolve())
        else:
            src = source
        job = create_job(src, opts, data_dir, job_id)
        typer.secho(f"Trabajo: {job.id}", fg=typer.colors.CYAN)
        run_pipeline(job, cfg)
        typer.secho(f"Listo. Clips en {job.dir}", fg=typer.colors.GREEN)

    _guard(go)


@app.command()
def resume(
    job_id: str = typer.Argument(..., help="Id del trabajo"),
    from_stage: Optional[str] = typer.Option(None, "--from", help=f"Re-ejecutar desde: {', '.join(STAGES)}"),
    clips: Optional[int] = typer.Option(None, "--clips", "-n"),
    min_s: Optional[float] = typer.Option(None, "--min"),
    max_s: Optional[float] = typer.Option(None, "--max"),
    faces: Optional[bool] = typer.Option(None, "--faces/--no-faces"),
    silences: Optional[bool] = typer.Option(None, "--cut-silences/--keep-silences"),
    layout: Optional[str] = typer.Option(None, "--layout",
                                         help="Diseño: auto | face (cara) | split (webcam + pantalla) | screen"),
    config: Optional[Path] = ConfigOpt,
    data_dir: Path = DataOpt,
    verbose: bool = VerboseOpt,
) -> None:
    """Continúa un trabajo; con --from re-ejecuta desde esa etapa."""
    _setup(verbose)

    def go() -> None:
        job = open_job(job_id, data_dir)
        for key, val in {"clips": clips, "min": min_s, "max": max_s, "faces": faces,
                         "silences": silences, "layout": layout}.items():
            if val is not None:
                job.options[key] = val
        job.save_meta()
        run_pipeline(job, load_config(config), from_stage)
        typer.secho(f"Listo. Clips en {job.dir}", fg=typer.colors.GREEN)

    _guard(go)


@app.command()
def render(
    job_id: str = typer.Argument(..., help="Id del trabajo"),
    clip: int = typer.Option(..., "--clip", help="Número de clip"),
    start: Optional[float] = typer.Option(None, "--start", help="Nuevo inicio (s del video original)"),
    end: Optional[float] = typer.Option(None, "--end", help="Nuevo final (s del video original)"),
    silences: Optional[bool] = typer.Option(None, "--cut-silences/--keep-silences",
                                            help="Quitar o mantener las pausas en este clip"),
    layout: Optional[str] = typer.Option(None, "--layout",
                                         help="Diseño: auto | face (cara) | split (webcam + pantalla) | screen"),
    config: Optional[Path] = ConfigOpt,
    data_dir: Path = DataOpt,
    verbose: bool = VerboseOpt,
) -> None:
    """Ajusta manualmente inicio/fin de un clip y lo vuelve a exportar."""
    _setup(verbose)
    _guard(lambda: rerender_clip(open_job(job_id, data_dir), load_config(config), clip, start, end, silences, layout))


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="0.0.0.0 para abrirla en tu red local"),
    port: int = typer.Option(8000, "--port"),
    config: Optional[Path] = ConfigOpt,
    data_dir: Path = DataOpt,
    verbose: bool = VerboseOpt,
) -> None:
    """Abre la interfaz web en el navegador."""
    _setup(verbose)
    import uvicorn

    from clipforge.web.app import create_app

    typer.secho(f"ClipForge web en http://{host}:{port}", fg=typer.colors.CYAN)
    uvicorn.run(create_app(data_dir, config), host=host, port=port, log_level="warning")


@app.command("jobs")
def list_jobs(data_dir: Path = DataOpt) -> None:
    """Lista los trabajos existentes."""
    if not data_dir.exists():
        typer.echo("No hay trabajos.")
        return
    for d in sorted(p for p in data_dir.iterdir() if (p / "job.json").exists()):
        n = len(list(d.glob("clip_*.mp4"))) - len(list(d.glob("clip_*.part.mp4")))
        typer.echo(f"{d.name}\t{n} clips")


if __name__ == "__main__":
    app()
