# API de ComfyUI desde Python — investigación y notas para integrar en grokbot (Telegram)

Fecha: 2026-09-05 · Investigación (sin cambios de código). Solo se considera el
estado de `master` y la documentación pública de ComfyUI.

## 1. Objetivo

Investigar cómo un cliente **Python asíncrono** se comunica con la **API nativa de
ComfyUI** (REST + WebSocket) para luego integrarlo en grokbot por las vías que ya
expone `master` (provider + dominio), sin depender de scripts dentro de la GPU.

## 2. Panorama de la API

ComfyUI es un servidor `aiohttp`/`asyncio`. Por defecto escucha en
`http://127.0.0.1:8188` (headless: `python main.py --disable-auto-launch`; remoto
con `--listen`). **No trae autenticación por defecto.**

Dos superficies complementarias:

- **REST**: encolar workflows, validar nodos, subir imágenes, consultar
  cola/historial, descargar resultados, gestión.
- **WebSocket** (`/ws`): eventos en tiempo real (progreso, nodo en ejecución,
  fin, errores) y previews binarias.

Regla de oro: **los resultados no viajan por WebSocket** (salvo el nodo
`SaveImageWebsocket`); se recuperan por HTTP (`/history` + `/view`). El WS solo
avisa del estado.

## 3. Formato de workflow: UI vs API ("prompt")

- **Formato UI** (`{nodes: [], links: []}`): lo que guarda el canvas del
  navegador. No se envía a la API.
- **Formato API** (a.k.a. "workflow prompt"): objeto plano
  `{"<node_id>": {"class_type": "<Nodo>", "inputs": {...}}}`. Las conexiones se
  expresan como valor de un input con la forma `["<node_origen>", <índice>]`.

Export desde la UI: **Workflow → Export (API format)** / Save (API format). Es el
payload canónico de `POST /prompt`.

Implicación para grokbot: el grafo (qué nodos corren, modelo/LoRA, qué se patchea)
debe vivir **en Python** (plantillas JSON API-format versionadas en el repo + un
mapeo de qué inputs mutar), porque a la API se le manda el workflow completo. Hoy
esa lógica está en un script dentro de la GPU; integrar por API implica traerla al
repo.

## 4. Endpoints REST

Todas las rutas existen también con prefijo `/api` (p. ej. `/api/prompt`); la forma
canónica es sin prefijo.

| Método | Ruta | Qué hace | Notas para el bot |
|---|---|---|---|
| POST | `/prompt` | Validar y **encolar** | Body `{"prompt": <API-format>, "client_id": "<uuid>"}` (+ `prompt_id`, `extra_data` en builds recientes). 200 → `{prompt_id, number}`; 400 → `{error, node_errors}`. **Ojo:** builds recientes pueden devolver `prompt_id` aunque *algunos* nodos de salida fallen la validación → los errores llegan en `node_errors`; hay que tratarlos como fallo |
| GET | `/prompt` | Cola actual (`queue_running`, `queue_pending`) | Útil para "posición X" |
| GET | `/history/{prompt_id}` | Resultado/estado de una ejecución | `outputs` (metadata de media por nodo) + `status.status_str` (`success`/`error`) + `status.completed` |
| POST | `/history` | Limpiar / borrar entradas | Higiene si acumula |
| GET | `/view` | **Descargar** un archivo | Query: `filename`, `subfolder`, `type` (`output`/`input`/`temp`). Sirve imágenes **y** MP4 (`video/mp4`) |
| GET | `/object_info` | Schemas de nodos (también `/object_info/{class}`) | Descubrir inputs y listas de modelos |
| POST | `/upload/image` | Subir imagen de entrada | multipart: `image`, `type=input`, `overwrite`, `subfolder` → `{name, subfolder, type}` |
| POST | `/interrupt` | Interrumpir la ejecución en curso | Cancelar |
| POST | `/queue` | Gestión de cola (limpiar/borrar) | No persistida: un reinicio la vacía |
| GET | `/system_stats` | Versiones, GPU, VRAM | Health-check |
| POST | `/free` | Descargar modelos de VRAM | Útil tras OOM |

`/view` con `type=output` devuelve los bytes (PNG/JPEG/WebP/MP4). Es el
equivalente HTTP del pull de archivos que hoy se hace por scp.

## 5. WebSocket `/ws`

Conexión: `ws://<host>:8188/ws?clientId=<uuid>`. El `client_id` debe **coincidir
con el de `POST /prompt`** para recibir los eventos de ese prompt (el servidor
mantiene un registro client_id → socket). Sin `clientId`, el servidor asigna uno y
lo manda en el primer `status` (`data.sid`). Reconectar con el mismo `client_id`
reanuda la sesión.

Mensaje: JSON `{"type": "...", "data": {...}}`.

| Tipo | Cuándo | `data` relevante |
|---|---|---|
| `status` | Al conectar / cambios de cola | `exec_info.queue_remaining` (y `sid` si no diste clientId) |
| `execution_start` | El prompt arranca | `prompt_id` |
| `execution_cached` | Nodos omitidos por caché | `prompt_id`, `nodes[]` |
| `executing` | Un nodo pasa a ejecutarse | `prompt_id`, `node` (id, o **`None` = fin del prompt**) |
| `progress` | Progreso de un nodo (sampler) | `prompt_id`, `node`, `value`, `max` |
| `executed` | Nodo devuelve update de UI (imágenes guardadas) | `prompt_id`, `node`, `output` (metadata media) |
| `execution_success` | Workflow terminó OK | `prompt_id`, `timestamp` |
| `execution_error` | Error de ejecución | `prompt_id`, `exception_message`, `node_id`, `node_type`, `traceback` |
| `execution_interrupted` | Interrupción | `prompt_id`, `node_id`, `executed[]` |

**Señal de fin**: `executing` con `data.node is None` **y** `data.prompt_id` igual al
tuyo (en multi-client hay que filtrar por `prompt_id`). Alternativa robusta de los
ejemplos oficiales: esperar ese evento y luego leer `/history/{prompt_id}` y
comprobar `status.status_str` (`success`/`error`).

**Frames binarios** = previews de imagen. Primeros 4 bytes = `BinaryEventTypes`
(big-endian; `1` = preview). Un bot que no muestra previews los descarta. Para
streaming, `SaveImageWebsocket` envía las imágenes finales por el WS (8 bytes de
cabecera + bytes), sin disco.

## 6. Flujo canónico en Python

Patrón oficial (stdlib + `websocket-client`; referencia:
`script_examples/websockets_api_example.py` y docs.comfy.org), en 4 pasos:

1. **Encolar** `POST /prompt` con `{"prompt": <API-format>, "client_id", "prompt_id"}`.
2. **Escuchar** `ws://.../ws?clientId=<mismo uuid>` hasta `executing.node is None`
   (matching `prompt_id`); ignorar binarios.
3. **Leer resultado** `GET /history/{prompt_id}` → `outputs[<node>]` con
   `images[]` (y metadata de video VHS) `{filename, subfolder, type}`.
4. **Descargar** `GET /view?filename=...&subfolder=...&type=...` → bytes.

Versión equivalente con **aiohttp** (HTTP **y** WS con la misma `ClientSession`;
encaja en grokbot sin dependencia nueva):

```python
import asyncio, json, uuid
import aiohttp

COMFY = "http://127.0.0.1:8188"

async def run_workflow(workflow: dict) -> list[dict]:
    client_id = str(uuid.uuid4())
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{COMFY}/prompt", json={
            "prompt": workflow, "client_id": client_id,
        }) as r:
            body = await r.json()
        if r.status != 200:                      # 400: node_errors de validación
            raise ComfyValidationError(body)
        prompt_id = body["prompt_id"]

        async with s.ws_connect(
            f"{COMFY}/ws?clientId={client_id}", heartbeat=30,
        ) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue                     # binario = preview
                data = json.loads(msg.data)
                if data["type"] == "execution_error":
                    raise ComfyExecutionError(data["data"])
                if (data["type"] == "executing"
                        and data["data"].get("prompt_id") == prompt_id
                        and data["data"].get("node") is None):
                    break                        # fin del prompt

        async with s.get(f"{COMFY}/history/{prompt_id}") as r:
            hist = await r.json()
        if hist[prompt_id]["status"].get("status_str") != "success":
            raise ComfyExecutionError(hist[prompt_id]["status"])
        return [img for node in hist[prompt_id]["outputs"].values()
                for img in node.get("images", [])]

async def download(media: dict) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{COMFY}/view", params=media) as r:
            return await r.read()                # PNG/JPEG/WebP o MP4
```

Variantes del patrón oficial:

- **Fire-and-forget**: solo `POST /prompt` (encolar y seguir).
- **WS + `/history`**: el de arriba; equilibrio robustez/simplicidad (recomendado).
- **Streaming por WS** con `SaveImageWebsocket`: imágenes por socket; útil para
  preview en vivo, más frágil para persistir.
- **Polling de `/history`** (sin WS): más simple, stateless y testeable; no da
  progreso en vivo ni tiempos por etapa.

### Entrada de imágenes (img2img / i2v / faceswap)

El nodo `LoadImage` lee un archivo del `input/` del servidor. Para mandar una foto
que llega de Telegram: `POST /upload/image` (multipart) y apuntar `LoadImage.image`
al `name` devuelto. Dos jobs simultáneos no deben reescribirse el mismo archivo
antes de leerse → `overwrite` controlado o nombre con hash/`prompt_id`.

### Video (Wan / MiniMax / VHS)

El nodo que guarda video (VHS `VHS_VideoCombine`) escribe `.mp4` (a veces
`-audio.mp4`) en `output/` o `temp/`; la metadata sale en `outputs[<node>]`, y se
baja con `GET /view?...&type=output` → `video/mp4`. El cliente debe aceptar ese
content-type.

## 7. Puntos de diseño a decidir en el plan (desde cero)

El plan de integración desde cero debe fijar, como decisiones explícitas:

1. **Dónde vive la GPU / cómo se alcanza**: ¿mismo box por SSH con túnel
   local-forward al puerto HTTP de ComfyUI, o URL base expuesta? (Afecta settings y
   transporte.)
2. **Alcance de la primera versión**: ¿imagen solamente, o también video
   (`wan_i2v`/`minimax_i2v`) y el refine 2-stage? (En `master` hoy esos caminos van
   por SSH a un script en la GPU.)
3. **Origen del grafo API-format**: traer los workflows al repo como plantillas
   JSON + un mapeo declarativo de qué inputs se patchean (modelo/LoRA → plantilla),
   ya que el armado debe ocurrir en Python.
4. **Detección de fin**: WebSocket (recomendado, con `/history` como fallback) o
   polling simple.
5. **EncaJe con los seams de `master`**: implementar los mismos Protocols
   (`ImageProvider`/`VideoProvider`/… de `providers/base.py`), devolver
   `GenerationResult` con la misma convención local (`file_path` + `meta`), y
   respetar la jerarquía de errores existente.
6. **Testabilidad**: transporte inyectable (estilo runner), `aioresponses` para
   REST y un stub para la fuente de eventos del WS.

## 8. Fuentes

- ComfyUI — Server overview: https://docs.comfy.org/development/comfyui-server/comms_overview
- ComfyUI — API Routes: https://docs.comfy.org/development/comfyui-server/comms_routes
- ComfyUI — Server Messages (WebSocket): https://docs.comfy.org/development/comfyui-server/comms_messages
- ComfyUI — API Examples (Python): https://docs.comfy.org/development/comfyui-server/api-examples
- Ejemplo oficial `websockets_api_example.py`:
  https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py
- DeepWiki — API and Programmatic Usage:
  https://deepwiki.com/Comfy-Org/ComfyUI/7-api-and-programmatic-usage
- Runflow — ComfyUI API: The Complete Developer's Guide (2026):
  https://www.runflow.io/blog/comfyui-api-developer-guide
