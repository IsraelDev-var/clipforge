"""Publicación en YouTube Shorts con la API oficial (YouTube Data API v3 + OAuth)."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from clipforge import ClipForgeError
from clipforge.config import Config, YouTubeConfig
from clipforge.job import Job

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # solo para mostrar qué canal está conectado
]
PRIVACY = ("private", "unlisted", "public")
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX_CHARS = 450
RETRY_STATUS = (500, 502, 503, 504)
SETUP_HELP = (
    "Para conectar YouTube necesitas un cliente OAuth propio (una sola vez):\n"
    "  1. https://console.cloud.google.com → crea un proyecto y activa «YouTube Data API v3».\n"
    "  2. Pantalla de consentimiento OAuth: tipo «Externo», añade tu cuenta como usuario de prueba.\n"
    "  3. Credenciales → Crear ID de cliente OAuth → «App de escritorio» → descarga el JSON.\n"
    "  4. Guárdalo como {path} y ejecuta `clipforge youtube login`."
)


# ---------- Metadatos (lógica pura) ----------

def _clean(text: str) -> str:
    """YouTube rechaza `<` y `>` en título y descripción."""
    return re.sub(r"[<>]", "", text or "").strip()


def video_id_from_url(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def source_link(source: str | None, start: float) -> str | None:
    """Enlace al momento exacto del directo original (si vino de YouTube)."""
    vid = video_id_from_url(source or "")
    return f"https://www.youtube.com/watch?v={vid}&t={int(start)}s" if vid else None


def build_metadata(clip: dict, source: str | None, yc: YouTubeConfig, title: str | None = None,
                   description: str | None = None) -> dict:
    """snippet de YouTube a partir del clip (o de los textos editados en la web)."""
    t = _clean(title if title is not None else clip.get("title", "")) or f"Clip {clip['n']}"
    has_tag = "#shorts" in t.lower()
    if yc.add_shorts_tag and not has_tag and len(t) + 8 <= TITLE_MAX:
        t, has_tag = f"{t} #Shorts", True
    t = t[:TITLE_MAX].rstrip()

    tags = [h.lstrip("#") for h in clip.get("hashtags", []) if h.lstrip("#")]
    if description is not None:
        desc = _clean(description)
    else:
        parts = [_clean(clip.get("description", ""))]
        hashtags = " ".join(f"#{x}" for x in tags)
        if yc.add_shorts_tag and not has_tag:
            hashtags = ("#Shorts " + hashtags).strip()
        parts.append(hashtags)
        link = source_link(source, clip.get("start", 0))
        if yc.footer and link:
            parts.append(_clean(yc.footer.format(source_url=link)))
        desc = "\n\n".join(p for p in parts if p)
    desc = desc[:DESCRIPTION_MAX]

    kept, total = [], 0
    for tag in tags:
        if total + len(tag) + 1 > TAGS_MAX_CHARS:
            break
        kept.append(tag)
        total += len(tag) + 1
    snippet = {"title": t, "description": desc, "tags": kept, "categoryId": str(yc.category_id)}
    if yc.default_language:
        snippet["defaultLanguage"] = yc.default_language
        snippet["defaultAudioLanguage"] = yc.default_language
    return snippet


def build_status(privacy: str, publish_at: datetime | None, now: datetime | None = None) -> dict:
    """status de YouTube. Programar exige subir como privado con fecha futura."""
    if privacy not in PRIVACY:
        raise ClipForgeError(f"Privacidad inválida «{privacy}». Opciones: {', '.join(PRIVACY)}.")
    status: dict[str, Any] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at is not None:
        now = now or datetime.now(timezone.utc)
        if publish_at <= now + timedelta(minutes=5):
            raise ClipForgeError("La fecha de publicación programada debe ser al menos 5 minutos en el futuro.")
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return status


def parse_when(text: str) -> datetime:
    """Fecha ISO. Sin zona horaria se interpreta como hora local de este equipo."""
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError as exc:
        raise ClipForgeError(f"Fecha inválida «{text}». Usa por ejemplo 2026-12-01 18:00.") from exc
    return dt.astimezone() if dt.tzinfo is None else dt


def schedule_times(first: datetime, every_hours: float, n: int) -> list[datetime]:
    return [first + timedelta(hours=every_hours * i) for i in range(n)]


# ---------- Credenciales ----------

def _paths(yc: YouTubeConfig) -> tuple[Path, Path]:
    return Path(yc.client_secrets).expanduser(), Path(yc.token).expanduser()


def _save_token(creds, token: Path) -> None:
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token, 0o600)  # el token da acceso a tu canal
    except OSError:
        pass


def load_credentials(yc: YouTubeConfig):
    """Credenciales guardadas (renovándolas si han caducado) o None si no hay sesión."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _, token = _paths(yc)
    if not token.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            return None
        try:
            creds.refresh(Request())
        except RefreshError:
            log.warning("La sesión de YouTube caducó o se revocó; vuelve a ejecutar `clipforge youtube login`.")
            return None
        _save_token(creds, token)
    return creds


def make_service(creds):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_info(service) -> dict:
    items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        return {"id": None, "title": "(la cuenta no tiene canal de YouTube)"}
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def login(yc: YouTubeConfig) -> dict:
    """Abre el navegador para autorizar ClipForge y guarda el token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets, token = _paths(yc)
    if not secrets.exists():
        raise ClipForgeError(f"No existe {secrets}.\n" + SETUP_HELP.format(path=secrets))
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(
        port=0, prompt="consent",
        authorization_prompt_message="Abre este enlace para autorizar ClipForge en YouTube:\n{url}",
        success_message="ClipForge ya está conectado a YouTube. Puedes cerrar esta pestaña.",
    )
    _save_token(creds, token)
    return channel_info(make_service(creds))


def logout(yc: YouTubeConfig) -> bool:
    _, token = _paths(yc)
    if token.exists():
        token.unlink()
        return True
    return False


def connection_status(yc: YouTubeConfig) -> dict:
    secrets, token = _paths(yc)
    out: dict[str, Any] = {"connected": False, "channel": None, "has_client_secrets": secrets.exists(),
                           "client_secrets": str(secrets), "error": None}
    try:
        creds = load_credentials(yc)
        if creds:
            out["channel"] = channel_info(make_service(creds))
            out["connected"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# ---------- Subida ----------

def _http_error_message(exc) -> str:
    status = getattr(exc.resp, "status", 0)
    text = str(exc)
    if status == 401:
        return "La sesión de YouTube no es válida. Ejecuta `clipforge youtube login`."
    if status == 403 and ("quotaExceeded" in text or "dailyLimitExceeded" in text):
        return "Se agotó la cuota diaria de la API de YouTube. Vuelve a intentarlo mañana."
    if status in (400, 403) and "uploadLimitExceeded" in text:
        return "YouTube no permite más subidas hoy en este canal (límite de subidas)."
    if status == 403:
        return f"YouTube rechazó la subida (403): {text}"
    return f"Error de la API de YouTube ({status}): {text}"


def upload(service, path: Path, body: dict, progress: Callable[[float], None] | None = None,
           max_retries: int = 5, sleep: Callable[[float], None] = time.sleep) -> str:
    """Subida reanudable por partes, con reintentos en errores temporales. Devuelve el id del video."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response, retries = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and progress:
                progress(status.progress())
            retries = 0
        except HttpError as exc:
            if exc.resp.status not in RETRY_STATUS or retries >= max_retries:
                raise ClipForgeError(_http_error_message(exc)) from exc
            retries += 1
            sleep(min(60, 2 ** retries))
        except (ConnectionError, TimeoutError, OSError) as exc:
            if retries >= max_retries:
                raise ClipForgeError(f"Falló la conexión con YouTube: {exc}") from exc
            retries += 1
            sleep(min(60, 2 ** retries))
    if "id" not in response:
        raise ClipForgeError(f"Respuesta inesperada de YouTube: {response}")
    if progress:
        progress(1.0)
    return response["id"]


def publish_clip(job: Job, cfg: Config, n: int, privacy: str | None = None, publish_at: datetime | None = None,
                 title: str | None = None, description: str | None = None, force: bool = False,
                 service=None, progress: Callable[[float], None] | None = None) -> dict:
    """Sube el clip `n` y guarda el resultado en highlights.json / clips.json."""
    from clipforge.stages import metadata
    from clipforge.stages.render import clip_name

    yc = cfg.youtube
    data = job.load_json("highlights.json")
    clip = next((c for c in data["clips"] if c["n"] == n), None)
    if clip is None:
        raise ClipForgeError(f"El clip {n} no existe.")
    path = job.path(clip_name(n))
    if not path.exists():
        raise ClipForgeError(f"El clip {n} todavía no está renderizado.")
    prev = clip.get("youtube")
    if prev and not force:
        raise ClipForgeError(f"El clip {n} ya se subió ({prev['url']}). Usa --force para subirlo otra vez.")

    if service is None:
        creds = load_credentials(yc)
        if creds is None:
            raise ClipForgeError("YouTube no está conectado. Ejecuta `clipforge youtube login`.")
        service = make_service(creds)

    body = {"snippet": build_metadata(clip, job.meta.get("source"), yc, title, description),
            "status": build_status(privacy or yc.privacy, publish_at)}
    log.info("Subiendo clip %d a YouTube: «%s» (%s%s)…", n, body["snippet"]["title"], body["status"]["privacyStatus"],
             f", programado {body['status']['publishAt']}" if publish_at else "")
    video_id = upload(service, path, body, progress)
    result = {"id": video_id, "url": f"https://youtube.com/shorts/{video_id}",
              "privacy": body["status"]["privacyStatus"], "publish_at": body["status"].get("publishAt"),
              "title": body["snippet"]["title"], "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    data = job.load_json("highlights.json")  # relee: puede haber cambiado durante la subida
    clip = next(c for c in data["clips"] if c["n"] == n)
    clip["youtube"] = result
    job.save_json("highlights.json", data)
    metadata.run_stage(job, cfg)
    log.info("Clip %d publicado: %s", n, result["url"])
    return result
