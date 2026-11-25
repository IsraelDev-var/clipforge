# Guía local de ClipForge (Windows)

Todo lo necesario para instalar, configurar y usar ClipForge en tu PC. La parte técnica para Claude Code está en [`../CLAUDE.md`](../CLAUDE.md).

## 1. Requisitos

| Necesitas | Cómo comprobarlo | Cómo instalarlo |
|---|---|---|
| Python 3.11 o superior | `python --version` | https://www.python.org/downloads/ (marca «Add to PATH») |
| Git | `git --version` | `winget install Git.Git` |
| ffmpeg | `ffmpeg -version` | `winget install ffmpeg` y abre una terminal nueva |
| Clave de API de Anthropic | — | https://console.anthropic.com → API Keys |
| GPU NVIDIA (opcional) | `nvidia-smi` | Acelera mucho la transcripción |

## 2. Instalación

En PowerShell:

```powershell
git clone https://github.com/IsraelDev-var/clipforge
cd clipforge
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
notepad .env          # pon tu clave: ANTHROPIC_API_KEY=sk-ant-...
pytest                # deben pasar todos los tests
```

Cada vez que abras una terminal nueva, entra en la carpeta y activa el entorno con `.venv\Scripts\activate`.

Para actualizar a la última versión:

```powershell
git pull
pip install -e ".[dev]"
```

**Fuente de los subtítulos (opcional):** descarga Montserrat ExtraBold de Google Fonts y copia el `.ttf` en `fonts\`. Sin ella se usa una fuente del sistema.

## 3. Conectar YouTube (una vez)

Ya hecho en Google Cloud:
- Proyecto «Clipper» con **YouTube Data API v3** activada.
- Pantalla de consentimiento externa, en modo Prueba, con tu cuenta como usuario de prueba.
- Cliente OAuth **«Clipper Desktop»** (App de escritorio).

Falta en tu PC:

1. Copia el JSON que descargaste al crear el cliente (`client_secret_….json`) a la carpeta `clipforge` y renómbralo a **`client_secret.json`**. No lo compartas: está en `.gitignore` y no se sube a GitHub.
   - Si perdiste el JSON, en Google Cloud → Clientes → «Clipper Desktop» crea un secreto nuevo y descárgalo.
2. Inicia sesión:
   ```powershell
   clipforge youtube login
   ```
   Se abre el navegador. Entra con la cuenta del canal, pulsa **Continuar** en el aviso de «app no verificada» y autoriza.
3. Comprueba que muestra tu canal:
   ```powershell
   clipforge youtube status
   ```

**Ten en cuenta:**
- **Videos privados:** mientras Google no audite el proyecto, los videos subidos por la API quedan privados. Cámbialos a público desde YouTube Studio.
- **Sesión de 7 días:** en modo Prueba, la sesión caduca a los 7 días. Si ves «sesión caducada», repite `clipforge youtube login`. Para evitarlo, en Google Cloud → Google Auth Platform → Público, pulsa «Publicar app» (pasa a «En producción»).
- **Cuota diaria:** la API permite pocas subidas al día. Si se agota, prueba al día siguiente.

## 4. Uso

### Interfaz web (recomendada)

```powershell
clipforge web
```

Abre http://127.0.0.1:8000. Desde ahí puedes:
1. Pegar un enlace de YouTube (de un directo ya terminado) o subir un video. Elegir número de clips, duración, diseño y si se quitan los silencios.
2. Ver el progreso por etapas. La primera vez se descarga el modelo de Whisper (≈ 1,5 GB), así que tarda más.
3. Revisar cada clip: video, nota de viralidad con consejos y transcripción.
4. Editar título, descripción y hashtags. Con «Ajustar inicio/fin» cambias el rango, el diseño o los silencios y vuelves a exportar.
5. Descargar el MP4 o todos en un zip, o pulsar **«Publicar en YouTube»**.

La web no tiene contraseña. Úsala solo en tu PC; no la abras a internet.

### Línea de comandos

```powershell
clipforge run "https://www.youtube.com/watch?v=XXXX" --clips 8 --min 25 --max 60
clipforge run .\mi_directo.mp4 --layout split          # forzar webcam + pantalla
clipforge resume <job_id> --from select                 # volver a elegir clips
clipforge render <job_id> --clip 3 --start 120.5 --end 165.0
clipforge publish <job_id> --schedule "2026-12-01 18:00" --every 24   # uno al día, los más virales primero
clipforge jobs
```

Los resultados quedan en `data\<job_id>\`: `clip_N.mp4` y `clips.json`, con títulos, descripciones, hashtags, viralidad y enlaces de YouTube.

## 5. Ajustes útiles (`config.yaml`)

| Quieres… | Cambia |
|---|---|
| Transcribir más rápido en CPU | `whisper_model: small` |
| Mejor transcripción (con GPU) | `whisper_model: large-v3` |
| Otro modelo de Claude | `llm_model` |
| Subtítulos más grandes o de otro color | sección `captions` (`size`, `highlight_color` en formato ASS BGR) |
| Cortar más o menos silencio | `silences.max_gap` (pausa mínima que se corta) y `silences.pad` |
| El diseño dividido no se activa con tu webcam | sube `layout.small_face`; mira `metrics` en `data\<job>\crop_track_N.json` |
| Más zoom en el código | baja `layout.screen_height` (p. ej. `0.8`) |
| Más o menos zoom en la webcam | `layout.camera_zoom` |
| Visibilidad por defecto al subir | `youtube.privacy` |

## 6. Problemas frecuentes

| Mensaje o síntoma | Solución |
|---|---|
| `No se encontró ffmpeg en el PATH` | `winget install ffmpeg` y abre una terminal nueva |
| `Clave de API de Anthropic inválida o ausente` | Revisa `ANTHROPIC_API_KEY` en `.env` (en la carpeta desde la que ejecutas) |
| `No se pudo cargar el modelo Whisper` | Necesita internet la primera vez (huggingface.co). Prueba `whisper_model: small` |
| `El directo todavía está en vivo` | Espera a que termine y YouTube genere el VOD |
| Falla la descarga de YouTube | `pip install -U yt-dlp`, o descarga el video y súbelo como archivo |
| Transcripción muy lenta | Sin GPU es normal; usa `whisper_model: small` |
| `YouTube no está conectado` o `sesión caducada` | `clipforge youtube login` |
| `Se agotó la cuota diaria de la API de YouTube` | Espera al día siguiente |
| Error de autorización justo después de configurar Google Cloud | Los cambios tardan de minutos a horas en aplicarse; espera |
| El servidor web se cerró a mitad de un proceso | El trabajo aparece con error; pulsa «Reanudar» |
| La cara no se encuadra bien | Prueba `--layout face`, `--no-faces` (centrado) o ajusta la sección `reframe` |

## 7. Trabajar con Claude Code en local

Abre Claude Code dentro de la carpeta `clipforge`. Leerá `CLAUDE.md` automáticamente y sabrá la arquitectura, los formatos, las decisiones tomadas y lo que falta por hacer. Un buen primer encargo es:

> Procesa `<mi_directo.mp4>` con `clipforge run`, revisa los clips y `clips.json`, y propón ajustes al prompt de selección y a los umbrales de `config.yaml`.
