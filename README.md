# ClipForge

Convierte un video largo (VOD de un directo de YouTube o archivo local) en clips verticales 9:16 con subtítulos estilo Shorts, más un `clips.json` con título, descripción y hashtags.

```
ingest → audio → transcribe → select → reframe → captions → render → metadata
```

Cada etapa guarda su resultado en `data/<job_id>/`. Si algo falla, `resume` continúa sin repetir lo ya hecho.

## Instalación

Necesitas Python 3.11+ y `ffmpeg` en el PATH. Una GPU NVIDIA es opcional (acelera Whisper).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # y pon tu ANTHROPIC_API_KEY
```

**Fuente (opcional):** descarga Montserrat ExtraBold (Google Fonts) y copia el `.ttf` en `fonts/`. Si no la pones, libass usa una fuente del sistema.

## Uso

```bash
clipforge run "https://www.youtube.com/watch?v=XXXX" --clips 8 --min 25 --max 60
clipforge run ./mi_directo.mp4 --clips 5 --no-faces
clipforge resume <job_id> --from select        # re-ejecuta desde una etapa
clipforge render <job_id> --clip 3 --start 120.5 --end 165.0   # ajuste manual
clipforge jobs                                 # lista trabajos
```

Etapas para `--from`: `ingest, audio, transcribe, select, reframe, captions, render, metadata`.

Salida en `data/<job_id>/`: `clip_<n>.mp4` (1080×1920) y `clips.json`.

## Configuración

`config.yaml` define el modelo de Whisper, el modelo de Claude, las duraciones, el estilo de los subtítulos y el suavizado del encuadre. Los flags de la CLI tienen prioridad. La clave de API solo se lee de `.env` o del entorno.

En CPU, `whisper_model: small` es bastante más rápido que `medium`, a cambio de algo de precisión.

## Notas de diseño

- **Selección:** la transcripción se divide en bloques de 10 min con 1 min de solapamiento. Claude propone candidatos por bloque con salida JSON estructurada, que se valida con pydantic (hasta 2 reintentos). Después se ajustan los tiempos a límites de palabra, se descartan los clips fuera de rango, se quitan solapes (gana el mayor score) y una pasada final elige los N mejores.
- **Reframe:** detecta la cara a 5 fps con MediaPipe y, si no la encuentra (por ejemplo, una webcam pequeña), usa el detector Haar de OpenCV. Aplica una zona muerta y una media móvil exponencial en ambos sentidos, sin retardo. El recorte se mueve con `sendcmd` de ffmpeg, interpolado fotograma a fotograma.
- **Subtítulos:** `.ass` con un evento por palabra; la palabra activa se resalta en color y los tiempos van con precisión de centésimas.
- **Render:** una sola llamada a ffmpeg por clip (`libx264 veryfast crf 20`, `aac 160k`, `+faststart`); `loudnorm` es opcional.

## Tests

```bash
pytest
```
