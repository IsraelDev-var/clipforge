# ClipForge

Convierte un video largo (VOD de un directo de YouTube o archivo local) en clips verticales 9:16 con subtítulos estilo Shorts, más un `clips.json` con título, descripción y hashtags.

```
ingest → audio → transcribe → select → reframe → captions → render → metadata
```

Cada etapa guarda su resultado en `data/<job_id>/`. Si algo falla, `resume` continúa sin repetir lo ya hecho.

> Guía paso a paso para Windows (instalación, YouTube, problemas frecuentes): [docs/GUIA_LOCAL.md](docs/GUIA_LOCAL.md). Detalles técnicos y de arquitectura: [CLAUDE.md](CLAUDE.md).

## Instalación

Necesitas Python 3.11+ y `ffmpeg` en el PATH. Una GPU NVIDIA es opcional (acelera mucho Whisper): instala también `pip install -e ".[gpu]"`, que trae las librerías CUDA (cuBLAS y cuDNN). Si faltan, la transcripción pasa sola a la CPU.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # y pon la clave del proveedor de IA que uses
```

### IA para elegir los clips

Se elige con `llm_provider` en `config.yaml`:

| Proveedor | Coste | Qué necesitas |
|---|---|---|
| `gemini` (por defecto) | Gratis, con límite diario | `GEMINI_API_KEY` de https://aistudio.google.com/apikey (sin tarjeta) |
| `ollama` | Gratis, local y sin internet | [Ollama](https://ollama.com) instalado y `ollama pull qwen2.5:7b` |
| `claude` | De pago | `ANTHROPIC_API_KEY` |
| `openai` | Depende | Cualquier API compatible con OpenAI: `llm_base_url` + `OPENAI_API_KEY` |

Con `llm_model` cambias el modelo del proveedor. Si el nivel gratuito de Gemini se agota, espera y continúa con `clipforge resume <job_id> --from select`.

**Fuente (opcional):** descarga Montserrat ExtraBold (Google Fonts) y copia el `.ttf` en `fonts/`. Si no la pones, libass usa una fuente del sistema.

## Uso

```bash
clipforge run "https://www.youtube.com/watch?v=XXXX" --clips 8 --min 25 --max 60
clipforge run ./mi_directo.mp4 --clips 5 --no-faces --keep-silences
clipforge run ./directo_con_pantalla.mp4 --layout split
clipforge resume <job_id> --from select        # re-ejecuta desde una etapa
clipforge render <job_id> --clip 3 --start 120.5 --end 165.0   # ajuste manual
clipforge jobs                                 # lista trabajos
```

### Interfaz web

```bash
clipforge web                    # http://127.0.0.1:8000
clipforge web --host 0.0.0.0     # accesible desde otros dispositivos de tu red
```

Pega un enlace o sube un archivo y verás el progreso por etapas. Cuando termine, cada clip aparece con su video, puntuación y transcripción. Desde ahí puedes editar el título, la descripción y los hashtags, copiarlos, ajustar el inicio y el fin sobre el video original y re-exportar. Puedes descargar cada MP4 o todos en un `.zip`. Los trabajos se procesan de uno en uno, en cola. La interfaz no tiene autenticación, así que no la expongas a internet.

Etapas para `--from`: `ingest, audio, transcribe, select, reframe, captions, render, metadata`.

Salida en `data/<job_id>/`: `clip_<n>.mp4` (1080×1920) y `clips.json`.

## Publicar en YouTube Shorts

Se usa la API oficial (YouTube Data API v3) con tu propio cliente OAuth. Se configura una sola vez:

1. En https://console.cloud.google.com crea un proyecto y activa **YouTube Data API v3**.
2. En **Pantalla de consentimiento OAuth** elige tipo «Externo» y añade tu cuenta de Google como usuario de prueba.
3. En **Credenciales** pulsa «Crear ID de cliente OAuth», elige «App de escritorio» y descarga el JSON.
4. Guárdalo como `client_secret.json` en la carpeta del proyecto. Está en `.gitignore`, no lo subas.
5. Ejecuta `clipforge youtube login`. Se abre el navegador; autoriza tu canal. El token queda en `~/.config/clipforge/youtube_token.json`.

```bash
clipforge youtube status                                  # qué canal está conectado
clipforge publish <job_id>                                # sube todos los clips pendientes (privados por defecto)
clipforge publish <job_id> --clip 3 --privacy unlisted
clipforge publish <job_id> --schedule "2026-12-01 18:00" --every 24   # uno al día, los más virales primero
clipforge youtube logout
```

En la web, cada clip tiene el botón «Publicar en YouTube». Desde ahí editas título y descripción, eliges la visibilidad o una fecha programada y ves el progreso de la subida.

**Qué sube:**
- **Título:** el del clip, con `#Shorts` añadido (o en la descripción si no cabe en 100 caracteres).
- **Descripción:** la del clip, más los hashtags y el enlace al minuto exacto del directo original, si vino de YouTube.
- **Etiquetas:** los hashtags del clip.
- **Visibilidad:** privada por defecto. Al programar, el video se sube como privado y YouTube lo publica a la hora indicada.

El resultado (id, enlace, visibilidad, fecha) se guarda en `clips.json`, y no se sube dos veces el mismo clip salvo con `--force`.

**Ten en cuenta (compruébalo en la documentación de Google, porque cambia):**
- **Apps sin verificar:** los proyectos de API nuevos que no han pasado la auditoría de Google pueden ver sus subidas restringidas a privado. Para publicar en público desde la API hay que solicitar esa auditoría. Mientras tanto, sube como privado y cambia la visibilidad desde YouTube Studio.
- **Cuota:** la cuota diaria por defecto de la API solo da para unas pocas subidas al día. Si se agota, ClipForge lo indica y basta con reintentar al día siguiente.

## Configuración

`config.yaml` define el modelo de Whisper, el proveedor y modelo de IA, las duraciones, el estilo de los subtítulos y el suavizado del encuadre. Los flags de la CLI tienen prioridad. Las claves de API solo se leen de `.env` o del entorno.

En CPU, `whisper_model: small` es bastante más rápido que `medium`, a cambio de algo de precisión.

## Notas de diseño

- **Selección:** la transcripción se divide en bloques de 10 min con 1 min de solapamiento. El LLM propone candidatos por bloque con salida JSON estructurada, que se valida con pydantic (hasta 2 reintentos). Después se ajustan los tiempos a límites de palabra, se descartan los clips fuera de rango, se quitan solapes (gana el mayor score) y una pasada final elige los N mejores.
- **Diseño por clip:** se analizan unas 24 muestras de cada clip y se elige automáticamente entre tres diseños:
  - **cara:** recorte 9:16 que sigue al presentador;
  - **dividido:** la cara es pequeña (menos del 6 % del ancho, típico de una webcam superpuesta) y la imagen tiene muchas zonas de color plano (editor o pantalla). Queda la cámara arriba (40 %) y la pantalla abajo (60 %), con los subtítulos sobre la línea divisoria;
  - **solo pantalla:** no hay cara y sí pantalla. El código va centrado sobre un fondo desenfocado y los subtítulos debajo.

  La zona de pantalla es una ventana fija, para que el código se lea, colocada donde hay más actividad (escritura, scroll) y más texto, y evitando la webcam. Para webcams pequeñas se busca la cara a 1280 px con OpenCV. Se puede forzar el diseño con `--layout auto|face|split|screen` en `run`, `resume` o `render`, o por clip desde la web. Los umbrales están en `layout` dentro de `config.yaml`; se calibraron con videos sintéticos, así que conviene revisarlos con directos reales. `crop_track_<n>.json` guarda las métricas medidas.
- **Eliminación de silencios:** dentro de cada clip se recortan las pausas de más de 0,5 s entre palabras, además del silencio inicial y final, dejando 0,12 s de margen para que el corte suene natural. Todo se hace en el mismo pase de ffmpeg (`select`/`aselect`): el encuadre usa los tiempos originales y los subtítulos y la viralidad, los ya recortados. Está activada por defecto (`silences` en `config.yaml`). Se desactiva con `--keep-silences` en `run`, `resume` o `render`, o por clip desde la web. La selección usa la duración original, así que un clip puede quedar algo por debajo de `--min` tras recortar.
- **Viralidad (0–100):** el 70 % sale de la IA, que valora del 1 al 10 el gancho, si el clip se entiende solo, su valor, la emoción y si invita a compartir. Lo hace en la misma llamada de selección, sin coste extra. El 30 % sale de señales medidas en el propio clip: tiempo hasta la primera palabra, palabras por segundo, porcentaje de silencios, cierre en fin de frase, duración y variación del volumen. Una pregunta en el gancho suma 3 puntos. Cada clip recibe además consejos concretos, y la puntuación se recalcula al re-exportar. Está en `clips.json` y en la web, donde puedes ordenar por viralidad. Recalcular: `clipforge resume <job_id> --from virality`.
- **Reframe:** detecta la cara a 5 fps con MediaPipe y, si no la encuentra (por ejemplo, una webcam pequeña), usa el detector Haar de OpenCV. Aplica una zona muerta y una media móvil exponencial en ambos sentidos, sin retardo. El recorte se mueve con `sendcmd` de ffmpeg, interpolado fotograma a fotograma.
- **Subtítulos:** `.ass` con un evento por palabra; la palabra activa se resalta en color y los tiempos van con precisión de centésimas.
- **Render:** una sola llamada a ffmpeg por clip (`libx264 veryfast crf 20`, `aac 160k`, `+faststart`); `loudnorm` es opcional.

## Tests

```bash
pytest
```
