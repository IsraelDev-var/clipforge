# ClipForge — guía para Claude Code

Herramienta propia que convierte un video largo (VOD de un directo de YouTube o archivo local) en clips verticales 9:16 con subtítulos, listos para Shorts, TikTok o Reels, y opcionalmente los publica en YouTube Shorts. El uso previsto es el contenido propio del canal: directos de programación en español.

Responde y escribe en **español**: mensajes de error, logs, textos de la web y docstrings. Los nombres de código van en inglés.

## Estado actual

- **v1 y v2 completas:**
  - CLI, interfaz web, puntuación de viralidad y eliminación de silencios.
  - Diseño dividido cuando se comparte pantalla.
  - Publicación en YouTube Shorts.
- **Sin probar con datos reales** (se desarrolló en un contenedor sin acceso a YouTube ni a Hugging Face, y sin clave de Anthropic):
  - Descarga con yt-dlp.
  - Transcripción con faster-whisper.
  - Selección con la API de Claude (solo se probó con un LLM simulado).
  - Subida real a YouTube (solo se probó con un servicio simulado).
- **Pendiente de calibrar con directos reales:**
  - El prompt de selección (`clipforge/prompts/select_system.txt`).
  - Los pesos de la viralidad (`stages/virality.py`).
  - Los umbrales de diseño (sección `layout` de `config.yaml`).
- **Siguiente paso recomendado:** procesar un directo real, revisar los clips y ajustar lo anterior. Cada `crop_track_<n>.json` guarda las métricas del diseño, y `highlights.json` guarda el desglose de la viralidad.

## Comandos

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                                              # 48 tests, < 1 s; no necesitan red ni ffmpeg
clipforge web                                       # interfaz en http://127.0.0.1:8000
clipforge run <url|archivo> [--clips N --min S --max S --layout auto|face|split|screen --keep-silences --no-faces]
clipforge resume <job_id> [--from <etapa>]
clipforge render <job_id> --clip N [--start S --end S --layout X --keep-silences]
clipforge publish <job_id> [--clip N] [--privacy private|unlisted|public] [--schedule "AAAA-MM-DD HH:MM" --every 24] [--force]
clipforge youtube login | status | logout
clipforge jobs
```

**Requisitos:**
- **Obligatorios:** Python ≥ 3.11 y `ffmpeg`/`ffprobe` en el PATH.
- **Opcional:** GPU NVIDIA; acelera Whisper.
- **Primera transcripción:** descarga el modelo de Whisper desde huggingface.co (`medium` ≈ 1,5 GB).
- **Primer encuadre:** descarga el modelo de caras de MediaPipe desde storage.googleapis.com a `~/.cache/clipforge/`.

## Arquitectura

Cada trabajo es una carpeta `data/<job_id>/` (ignorada en git). El pipeline (`clipforge/pipeline.py`, dict `STAGES`) ejecuta estas etapas en orden. Cada una es un módulo de `clipforge/stages/` con `done(job) -> bool` y `run_stage(job, cfg[, only=n])`:

| # | Etapa | Lee | Escribe |
|---|---|---|---|
| 1 | `ingest` | URL o archivo | `source.mp4` (yt-dlp ≤ 1080p; archivo .mp4 → enlace duro; otros → remux) y `job.json["video"]` |
| 2 | `audio` | `source.mp4` | `audio.wav` (mono, 16 kHz) |
| 3 | `transcribe` | `audio.wav` | `transcript.json` (faster-whisper, `word_timestamps`, `vad_filter`, `initial_prompt` con vocabulario técnico) |
| 4 | `select` | `transcript.json` | `highlights.json` (Claude por bloques de 10 min con 1 min de solape, más una pasada final de ranking) |
| 5 | `silences` | `highlights.json` | `clip.segments` y `clip.edited_duration` |
| 6 | `virality` | `highlights.json`, `audio.wav` | `clip.virality` y `clip.virality_llm` |
| 7 | `reframe` | `source.mp4` | `crop_track_<n>.json` (diseño y recortes) |
| 8 | `captions` | `transcript.json`, `crop_track_<n>.json` | `subs_<n>.ass` |
| 9 | `render` | todo lo anterior | `clip_<n>.mp4` (1080×1920), más `crop_cmds_<n>.txt` |
| 10 | `metadata` | `highlights.json` | `clips.json` |

- **Reanudación:** `run_pipeline` salta las etapas con `done()` verdadero. `--from X` fuerza X y todas las siguientes. Si `select` se vuelve a ejecutar, se fuerza todo desde `silences`.
- **Re-exportar un clip:** `rerender_clip` (ajuste manual de inicio/fin, diseño o silencios) rehace `silences → virality → reframe → captions → render` solo para ese clip y luego `metadata`.
- **Estado para la web:** está en `job.json["status"]` (`queued|running|done|error`, etapa y mensaje), y se escribe con `job.set_status()`. Los JSON se guardan de forma atómica (`.tmp` + `replace`).
- **Errores esperados:** se lanzan como `ClipForgeError` con mensaje en español. La CLI los muestra en rojo y la web los guarda en el estado.

### Módulos

- **`cli.py`:** Typer. Los comandos de arriba; `youtube` es un sub-Typer.
- **`config.py`:** modelos pydantic (`Config`, `CaptionsConfig`, `ReframeConfig`, `LayoutConfig`, `SilencesConfig`, `YouTubeConfig`) más `Secrets`, que lee `ANTHROPIC_API_KEY` de `.env`. `load_config()` lee `config.yaml` del directorio actual; si no existe, usa los valores por defecto.
- **`job.py`:** `Job` (rutas, JSON, estado y opciones del trabajo), `create_job` y `open_job`.
- **`stages/layout.py`:** lógica pura de diseño (métricas, `decide`, cajas de cámara y pantalla, mapa de actividad).
- **`publish/youtube.py`:** OAuth, metadatos, subida reanudable y `publish_clip`.
- **`web/app.py`:**
  - FastAPI con `create_app(data_dir, config_path)`.
  - `Worker` es una cola de un solo hilo: un trabajo pesado a la vez y nunca dos tareas sobre el mismo job.
  - Al arrancar, marca como error los trabajos que quedaron en `running`/`queued`.
- **`web/static/`:** HTML, CSS y JS sin dependencias ni compilación. Rutas por hash (`#/`, `#/job/<id>`) y sondeo cada 2 s mientras hay proceso. Las tarjetas solo se reconstruyen si cambia su firma (`data-sig`), para no reiniciar los videos ni borrar lo que se está escribiendo.
- **`utils/ffmpeg.py`:** `run()` muestra el final de stderr si falla; también `probe()` y `require_ffmpeg()`.
- **`utils/timing.py`:** formato de tiempo ASS, ajuste a límites de palabra y `flatten_words`.

### API web

| Método y ruta | Uso |
|---|---|
| `GET /api/config` | Valores por defecto del formulario |
| `GET /api/jobs` | Lista de trabajos |
| `POST /api/jobs` | Nuevo trabajo (form: `url` o `file`, `clips`, `min_s`, `max_s`, `faces`, `silences`, `layout`) |
| `GET /api/jobs/{id}` | Detalle, clips con `text`, `layout_used`, `version` |
| `PATCH /api/jobs/{id}/clips/{n}` | Editar título, descripción o hashtags |
| `POST /api/jobs/{id}/clips/{n}/render` | Re-exportar (`start`, `end`, `cut_silences`, `layout`) |
| `POST /api/jobs/{id}/resume` | Reanudar (`from_stage`) |
| `DELETE /api/jobs/{id}` | Borrar el trabajo |
| `GET /api/jobs/{id}/files/{name}` | Archivos permitidos: `source.mp4`, `clip_N.mp4`, `clips.json` |
| `GET /api/jobs/{id}/zip` | Todos los clips en un zip |
| `GET /api/youtube/status` | Canal conectado (caché de 120 s; `?refresh=true` la ignora) |
| `GET /api/jobs/{id}/clips/{n}/youtube` | Vista previa de los metadatos de YouTube |
| `POST /api/jobs/{id}/clips/{n}/publish` | Publicar (`title`, `description`, `privacy`, `publish_at` ISO, `force`) |

La web **no tiene autenticación**: úsala solo en local (por defecto escucha en 127.0.0.1).

## Formatos de datos

**`transcript.json`:**
```json
{"language": "es", "duration": 5423.1,
 "segments": [{"id": 0, "start": 12.3, "end": 17.8, "text": "…", "words": [{"w": "Hoy", "s": 12.3, "e": 12.5}]}]}
```

**`highlights.json` → `clips[]`:** cada clip se va enriqueciendo etapa a etapa.
- **`select`:** `n` (empieza en 1, ordenado por inicio), `start` y `end` (segundos del video original, ajustados a límites de palabra), `duration`, `score` (1–100), `hook`, `title` (≤ 60), `description`, `hashtags` (con `#`) y `reason`. También `virality` con los criterios de la IA de 1 a 10 (`hook`, `standalone`, `value`, `emotion`, `shareability`).
- **`silences`:** `segments` (lista de `[ini, fin]` relativos al clip, o `null` si no se recortan silencios) y `edited_duration`.
- **`virality`:**
  - Mueve los criterios de la IA a `virality_llm`.
  - `virality` pasa a ser `{score, grade (alto/medio/bajo), llm, llm_score, signals_score, signals, subscores, tips[]}`.
- **Ajustes por clip** (web o CLI): `cut_silences` (bool) y `layout` (`face|split|screen`, o ausente = automático).
- **Tras publicar:** `youtube` = `{id, url, privacy, publish_at, title, uploaded_at}`.

**`crop_track_<n>.json`:** campo `mode`:
- `face` o `center`: `{width, height, y, points: [{t, x}]}`, con `t` relativo al clip original.
- `split`: `{camera: {x, y, w, h}, screen: {x, y, w, h}}`.
- `screen`: `{screen: {…}}`.
- Además, siempre: `src_width`, `src_height`, `requested` y `metrics` (`face_frac`, `face_width`, `flat`).

**`clips.json`:** salida final para publicar. Incluye duración final y original, segundos recortados, viralidad con desglose y consejos, textos y datos de YouTube.

**`job.json`:** `id`, `source`, `title` (nombre del archivo subido), `options` (`clips`, `min`, `max`, `faces`, `silences`, `layout`), `video` (`duration`, `width`, `height`, `fps`) y `status`.

## Detalles delicados (leer antes de tocar)

- **Tiempos en el render:**
  - ffmpeg se lanza con `-ss inicio -t duración -i source.mp4` y `cwd=job.dir`. Las rutas de los filtros son relativas para no tener que escaparlas, sobre todo en Windows.
  - Dentro de los filtros, `t` empieza en 0 en el inicio del clip.
- **Orden del grafo `face`:** `sendcmd` → `crop` (sigue la cara con los tiempos ORIGINALES) → `select`/`setpts` (quita silencios) → `scale` → `ass`.
  - Los subtítulos (`.ass`) ya vienen con los tiempos RECORTADOS (`silences.remap_words`).
  - Si cambias el orden, se desincroniza.
- **Diseños `split` y `screen`:** se quitan los silencios primero (`select`), luego `split=2` y los recortes estáticos; `vstack` u `overlay` sobre fondo desenfocado.
- **Audio:** `-af aselect=…,asetpts=N/SR/TB` con la misma expresión que el video.
- **Comillas en filtros:** las expresiones `between(t,a,b)` contienen comas y van entre comillas simples dentro del filtergraph (se pasan como lista a `subprocess`, sin shell).
- **MediaPipe 1.x:** ya no tiene `mp.solutions`; se usa la Tasks API (`FaceDetector`) con el modelo `blaze_face_short_range`.
  - Ese modelo solo detecta caras grandes. Para webcams pequeñas hay una segunda pasada con Haar de OpenCV a 1280 px (`reframe.analyze`).
  - En Linux sin `libEGL` MediaPipe falla y se usa solo Haar; esto ya está gestionado.
- **Detección de pantalla:** usa la fracción de bloques de 8×8 px de color totalmente plano (`layout.flat_fraction`). La densidad de bordes NO servía para distinguir pantalla de cámara (se midió).
- **Silencios:** los tramos aislados más cortos que `min_segment` se **descartan**. Unirlos al tramo anterior reintroduciría el silencio que los separa (hay un test que lo cubre).
- **Selección:**
  - La IA elige los clips con su duración ORIGINAL; tras recortar silencios un clip puede quedar por debajo de `--min`.
  - El post-procesado (ajuste a palabras, filtro de duración, quitar solapes) está en `select.postprocess` y tiene tests.
- **API de Claude** (`select.ClaudeLLM`):
  - Modelo en `config.yaml` (`llm_model: claude-sonnet-5-5`, elegido por el usuario).
  - Usa `client.beta.messages.create` con salida JSON estructurada (`output_config.format` con `json_schema`) y `betas=["server-side-fallback-2026-07-01"]` + `fallbacks="default"`: si el modelo rechaza un bloque, la API reintenta con otro modelo.
  - Valida con pydantic y reintenta hasta 2 veces si el JSON es inválido.
  - Los tests usan un LLM falso (protocolo `LLM.complete`).
- **Nunca** pongas claves en el código. `ANTHROPIC_API_KEY` va en `.env`; `client_secret*.json` y `.env` están en `.gitignore`.

## YouTube

- **Cliente OAuth:** proyecto de Google Cloud «Clipper», con YouTube Data API v3 activada y un cliente OAuth «App de escritorio». La pantalla de consentimiento es «Externa» en modo Prueba, con la cuenta del canal como usuario de prueba.
- **Archivos:**
  - `client_secret.json` va en la raíz del proyecto (no está en git).
  - El token se guarda en `~/.config/clipforge/youtube_token.json` con permisos 600.
- **Permisos:** `youtube.upload` y `youtube.readonly` (este último solo para mostrar el canal conectado).
- **Limitaciones conocidas:**
  - Mientras el proyecto no pase la auditoría de Google, los videos subidos por la API quedan **privados**. ClipForge sube en privado por defecto.
  - En modo Prueba el refresh token **caduca a los 7 días**: hay que repetir `clipforge youtube login`, o pasar la app a «En producción» en Google Cloud.
  - La cuota diaria de la API da para pocas subidas al día; el error de cuota se muestra claro.
  - Programar (`publish_at`) obliga a subir como privado; YouTube lo publica a esa hora.
- **Si algún día se aloja la web en un servidor:** habría que cambiar a un cliente OAuth de tipo «Aplicación web» con un callback en la propia app. Ahora el login usa `InstalledAppFlow.run_local_server`.

## Convenciones

- Python ≥ 3.11, tipado, docstrings breves en español y funciones puras separadas de la entrada/salida para poder probarlas.
- Tests en `tests/` (pytest): selección, subtítulos, silencios, viralidad, diseño, web y YouTube, con todo lo externo simulado. Añade tests a la lógica nueva.
- La web no debe depender de librerías de frontend. Cualquier texto que venga del LLM se inserta con `textContent` (helper `h()`), nunca con `innerHTML`.
- Las opciones nuevas se añaden en tres sitios: `config.py` (valor por defecto), `config.yaml` (comentado) y, si procede, flag de CLI y control en la web.
- Ramas: trabajo en una rama y luego fast-forward a `main`.

## Ideas abiertas

- Calibrar los umbrales con directos reales y añadir el `.ass` y un clip corto de prueba a `tests/fixtures/`.
- Ajustar los pesos de la viralidad comparándolos con las métricas reales de los Shorts publicados.
- Ajuste fino del recorte de la webcam en el diseño dividido (detectar el rectángulo de la superposición en lugar de usar `camera_zoom`).
- Publicación en lote desde la web («programar todos, uno al día»); hoy solo existe en la CLI.
