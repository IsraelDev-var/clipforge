"""Carga de configuración (config.yaml) y secretos (.env)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from clipforge import ClipForgeError


class CaptionsConfig(BaseModel):
    font: str = "Montserrat ExtraBold"
    fonts_dir: str | None = "fonts"
    size: int = 80
    primary_color: str = "&H00FFFFFF"
    highlight_color: str = "&H0000FFFF"
    outline_color: str = "&H00000000"
    outline: int = 5
    shadow: int = 2
    words_per_chunk: int = 3
    max_chars: int = 20
    uppercase: bool = True
    margin_v: int = 620


class ReframeConfig(BaseModel):
    sample_fps: float = 5
    smoothing: float = 0.15
    dead_zone: float = 0.06


class LayoutConfig(BaseModel):
    mode: str = "auto"            # auto | face | split | screen
    small_face: float = 0.06      # cara más estrecha que esta fracción del ancho → webcam superpuesta
    min_face_frac: float = 0.3    # fracción mínima de muestras con cara para considerar que hay cámara
    flat_screen: float = 0.45     # fracción de zonas planas a partir de la cual es una pantalla
    camera_ratio: float = 0.4     # alto del panel de cámara en el diseño dividido
    camera_zoom: float = 3.2      # ancho del recorte de cámara = N × ancho de la cara
    screen_height: float = 1.0    # alto del recorte de pantalla / alto del video (menos = más zoom)
    analysis_samples: int = 24
    captions_split_y: float = 0.40   # centro de los subtítulos (fracción del alto) en diseño dividido
    captions_screen_y: float = 0.87  # … y en «solo pantalla»


class YouTubeConfig(BaseModel):
    client_secrets: str = "client_secret.json"            # descargado de Google Cloud (no se sube a git)
    token: str = "~/.config/clipforge/youtube_token.json"  # se crea al hacer login
    privacy: str = "private"                              # private | unlisted | public
    category_id: str = "28"                               # 28 = Ciencia y tecnología, 27 = Educación
    default_language: str | None = "es"
    add_shorts_tag: bool = True
    footer: str | None = "Directo completo: {source_url}"


class SilencesConfig(BaseModel):
    enabled: bool = True
    max_gap: float = 0.5      # pausas más largas que esto se recortan
    pad: float = 0.12         # margen que se deja a cada lado del corte
    min_segment: float = 0.2  # tramos más cortos se unen al anterior


class Config(BaseModel):
    whisper_model: str = "medium"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    language: str = "es"
    initial_prompt: str | None = None
    clips: int = 6
    min_seconds: float = 25
    max_seconds: float = 60
    llm_model: str = "claude-sonnet-5-5"
    chunk_minutes: float = 10
    chunk_overlap_minutes: float = 1
    output_resolution: tuple[int, int] = (1080, 1920)
    loudnorm: bool = False
    captions: CaptionsConfig = Field(default_factory=CaptionsConfig)
    reframe: ReframeConfig = Field(default_factory=ReframeConfig)
    silences: SilencesConfig = Field(default_factory=SilencesConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


class Secrets(BaseSettings):
    """Claves leídas solo desde el entorno o `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    anthropic_api_key: str | None = None


def load_config(path: Path | None = None) -> Config:
    """Lee `config.yaml` (o el indicado); si no existe usa los valores por defecto."""
    path = path or Path("config.yaml")
    if not path.exists():
        return Config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Config.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ClipForgeError(f"config.yaml inválido ({path}): {exc}") from exc
