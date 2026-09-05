---
name: comfy-add-flow
description: Registrar un flujo nuevo de ComfyUI en grokV2 de forma DIRECTA y ÁGIL — crear `providers/comfyui/workflows/templates/<id>.json` (grafo API-format con modelo/LoRA horneados + clave `_meta`) y añadir `(id, label)` a `domain/user_config.COMFYUI_FLOWS` para que aparezca en el menú `/config` → ComfyUI. Usa esta skill SIEMPRE que el usuario pida agregar/crear/registrar un flujo nuevo o una variante en el menú ComfyUI (p. ej. "agrega un flujo ágil", "otro flujo krea/qwen", "añade al menú de ComfyUI", "un flujo con tal LoRA"), incluso si no la nombra. NO usar para implementar cambios que no sean un flujo (eso va por el pipeline normal / hardener-agile), ni para modificar resolver/provider (infraestructura, no flujos).
---

# Registrar un flujo de ComfyUI en grokV2 (proceso ágil, sin tests ni docs)

Este repo maneja ComfyUI por **flujos**: cada flujo es un workflow API-format que
**hornea** su modelo y su LoRA en los nodos, más una clave `_meta` que declara qué
parchear por request. El `/config` del bot lista los flujos registrados y el usuario
solo elige el flujo (sin tocar modelo/LoRA por separado). El provider los resuelve solo:
agregar un flujo **no toca** `resolver.py` ni `provider.py`.

Este es un **módulo dinámico**: registrar un flujo **no requiere** actualizar docs
(`PRODUCT_STATUS.md` / `AVANCE_VAST_HTTP.md`) ni escribir tests — exención explícita en
`AGENTS.md` (Regla dura 1). Los tests de cardinalidad de flujos los mantiene el dueño.

## Flujo de trabajo

### 1. Entender qué flujo se pide

La receta (modelo base, LoRA, resolución, media_type, con/sin foto) puede venir de:

- Un **export del canvas** de ComfyUI (UI format `{nodes, links}`) → hay que pasarlo a
  **API format** (un nodo por id: `{"id": {"class_type", "inputs"}}`) con conexiones como
  `["<id_origen>", índice]`. El pack `comfy:convert` ayuda a convertir UI↔API.
- Un **payload/script de referencia** (p. ej. `payloads/<modelo>.json` de un repo/setup).
- **Desde cero**: copiar el patrón de un template existente (ver `templates/agil_solo.json`
  para el mínimo txt2img single-pass y `templates/krea2_t2i.json` para doble pasada).

Antes de codificar, deja claras estas preguntas con el usuario:

- ¿`media_type`: `image` o `video`? (video = el provider guarda MP4; hoy no hay pesos de
  video en la box — avísalo si lo piden).
- ¿Solo txt2img, o acepta **foto de referencia** (img2img/i2v)? Si acepta foto →
  `supports_source: true` y el grafo lleva un nodo `LoadImage`.
- ¿Resolución fija (un `EmptyLatentImage` con width/height duros) o **selector** en el
  `/config` (nodo `ResolutionSelector` con widgets elegibles)?
- ¿Qué LoRA(s) y a qué fuerza? (o ninguna).
- ¿Un solo muestreo o pasadas de refino/upscale?

### 2. Nombrar el flujo

- `id`: `snake_case` ASCII (va en callback `cfg:comfyui:flow:<id>` y en el nombre de
  archivo `<id>.json`). Prefijos que agrupan familias funcionan bien (p. ej. `agil_solo`,
  `agil_nsfw`).
- `label`: texto del botón, **copy de usuario en español neutro** (p. ej. "Ágil solo").
  Puede llevar tildes (JSON UTF-8).
- **Verificar que los modelos/LoRAs que vas a hornear existen en la box GPU activa**
  (con el MCP de comfyui, `list_local_models`). Si falta una LoRA/modelo, reportarlo y
  ofrecer descargarlo (Civitai/HF) o pedir el archivo; **no** inventar nombres de archivo.

### 3. Crear el template `providers/comfyui/workflows/templates/<id>.json`

Grafo **API-format** + clave top-level `_meta`. Reglas:

- Los **modelos/LoRA van horneados**: el `UNETLoader`/`CheckpointLoader`/`CLIPLoader`/
  `VAELoader`/`LoraLoader` apuntan a nombres exactos de archivo presentes en la box.
- **Sin `_meta` dentro de los nodos** (el export del canvas trae un `_meta` por nodo con
  el título; elimínalo — el resolver lo descarta igual, pero el archivo debe quedar limpio).
- El nodo **positivo** recibe el prompt del usuario en `positive_node`/`positive_input`.
- Cada **KSampler** con input `seed` debe ir en `seed_nodes` (el resolver randomiza).
- Cada **SaveImage** (o guardado final) va en `save_nodes` (el provider descarga de ahí).
- El grafo **nunca** incluye la clave `_meta` en el enqueue: el resolver la separa.

Contrato de `_meta` (defaults reales de `resolver.py`):

| Clave | Obligatorio | Default | Significado |
|---|---|---|---|
| `id` | sí | — | id del flujo (debe coincidir con el de `COMFYUI_FLOWS`) |
| `name` | no | = `id` | nombre interno del flujo |
| `media_type` | sí | — | `"image"` \| `"video"` |
| `positive_node` | no | `"4"` | nodo cuyo input recibe el prompt |
| `positive_input` | no | `"text"` | input del nodo positivo (`"text"` en CLIPTextEncode; `"prompt"` en Wildcard Processor) |
| `negative_node` | no | `null` | nodo del negativo (solo metadata; **no** se parchea, el texto va en el template) |
| `seed_nodes` | no | `[]` | nodos con input `seed` a randomizar |
| `save_nodes` | no | `[]` | nodos cuya media final se descarga |
| `supports_source` | no | `false` | `true` si el grafo carga foto fuente (`LoadImage`) |
| `timeout` | no | (default 600 s imagen / 1500 s video) | override de segundos para pipelines largos |

Ejemplo mínimo (txt2img krea2 single-pass, flujo real `agil_solo`): un `UNETLoader`
(`krea2_turbo_fp8_scaled`) + `CLIPLoader` (type `krea2`) + `VAELoader` + dos
`CLIPTextEncode` (positivo/negativo) + `EmptyLatentImage` fijo + un `KSampler` (8 pasos,
cfg 1, euler/simple) + `VAEDecode` + `SaveImage`; `_meta` con `positive_node "5"`,
`seed_nodes ["8"]`, `save_nodes ["10"]`, `supports_source false`. Para la variante con
LoRA se inserta un `LoraLoader` entre loaders y sampler y se rewirean `model`/`clip`.

### 4. Registrar el flujo

En `src/grokbot/domain/user_config.py`, añadir la tupla al **final** de `COMFYUI_FLOWS`
(sin cambiar el primer elemento: es el default):

```python
COMFYUI_FLOWS = (
    ("grok_style", "Grok Style"),
    ("donut_face", "Donut Face"),
    ("agil_solo", "Ágil solo"),
    ("agil_nsfw", "Ágil NSFW"),
    ("<id_nuevo>", "<Label nuevo>"),   # ← añadir aquí
)
```

De ahí derivan solos `VALID_COMFYUI_MODELS` (acepta el id) y `COMFYUI_FLOW_LABELS` (el
texto del botón). No hace falta tocar otra cosa del dominio.

### 5. Validación en vivo contra la box (opcional pero recomendada)

Para confirmar que el template corre de verdad (wiring + modelos presentes) sin pasar
por el bot: render del grafo desde el template y enqueue al ComfyUI del box por el túnel
local HTTP activo (p. ej. `http://127.0.0.1:8188` — verifícalo con `curl /system_stats`).

Desde la raíz del repo, con el `.venv`:

```python
# render() parchea el prompt y randomiza seeds; el grafo va SIN _meta
from grokbot.providers.comfyui.workflows.resolver import get_flow, render
import json, time, urllib.request

flow = get_flow("<id_nuevo>")                     # assert flow is not None
graph = render(flow, "retrato editorial de prueba", lambda: 1788600001)
req = urllib.request.Request("http://127.0.0.1:8188/prompt",
        data=json.dumps({"prompt": graph, "client_id": "validar_flujo"}).encode(),
        headers={"Content-Type": "application/json"})
pid = json.loads(urllib.request.urlopen(req, timeout=600).read())["prompt_id"]
# poll GET /history/<pid> hasta que el nodo save_nodes tenga output; confirmar PNG/MP4
```

Si el enqueue falla por un nodo/modelo, diagnosticar con el historial de errores o el
MCP (`get_history action:"diagnose"`). Esto detecta LoRAs mal nombradas, conexiones
rotas o una resolución inválida antes de declarar el flujo listo.

### 6. Cierre

- **No escribir tests ni actualizar docs** (módulo dinámico; exención en `AGENTS.md`
  Regla dura 1). Los tests lo hace el dueño.
- Advertir que al sumar un flujo quedan **rojos** (hasta que el dueño los actualice) dos
  tests de cardinalidad si existen: `tests/unit/providers/test_workflow_resolver.py`
  (`test_flows_returns_registered_flows`, conjunto de ids) y
  `tests/unit/telegram/test_keyboards.py` (lista de callbacks del teclado ComfyUI).
- Recordar que el cambio aplica al bot tras **reiniciar `grok-bot.service`** — lo reinicia
  el dueño; no reiniciar servicios por tu cuenta.
- **No leer `data/` ni `tmp/`** (datos privados del usuario; Regla dura 2 de `AGENTS.md`).
- Commit en español y **sin** trailer `Co-Authored-By` (convención del repo).

## Notas

- Si un flujo nuevo es **variante** de otro (misma base, otra LoRA/resolución), lo más
  ágil es copiar el template base y solo cambiar el nodo `LoraLoader`/los widgets — no
  reinventar el grafo.
- Los exports del canvas con nodos "Anything Everywhere"/"Everywhere" **no materializan
  sus conexiones en modo API**: hay que cablear explícitos los inputs requeridos (caso
  `donut_face`). Si el flujo viene de ese tipo de export, revisa que cada input conectado
  por un nodo virtual quede cableado a mano.
- Rutas de referencia: `src/grokbot/providers/comfyui/workflows/resolver.py` (contrato y
  `render`), `templates/` (ejemplos), `docs/comfyui/AVANCE_VAST_HTTP.md` §5 (receta
  canónica del contrato).
