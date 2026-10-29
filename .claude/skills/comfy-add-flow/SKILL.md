---
name: comfy-add-flow
description: Registrar un flujo NUEVO de ComfyUI en grokV2 o REEMPLAZAR el grafo de uno existente a partir de un export API dejado en el repo — escribir `providers/comfyui/workflows/templates/<id>.json` (grafo API-format con modelo/LoRA horneados + clave `_meta`) y, solo si el id es nuevo, añadir `(id, label)` a `domain/user_config.COMFYUI_FLOWS`. Usa esta skill SIEMPRE que el usuario pida agregar/crear/registrar un flujo nuevo, o reemplazar/actualizar/sustituir un flujo existente con un archivo/export nuevo (p. ej. "reemplaza Ágil Edit con este archivo", "agrega un flujo ágil", "otro flujo krea/qwen", "añade al menú de ComfyUI"), incluso si no la nombra. NO usar para implementar cambios que no sean un flujo (eso va por el pipeline normal / hardener-agile), ni para modificar resolver/provider (infraestructura, no flujos).
---

# Registrar o reemplazar un flujo de ComfyUI en grokV2 (proceso ágil, sin tests ni docs)

Este repo maneja ComfyUI por **flujos**: cada flujo es un workflow API-format que
**hornea** su modelo y su LoRA en los nodos, más una clave `_meta` que declara qué
parchear por request. El `/config` del bot lista los flujos registrados y el usuario
solo elige el flujo (sin tocar modelo/LoRA por separado). El provider los resuelve
solo: cambiar un flujo **no toca** `resolver.py` ni `provider.py`.

Este es un **módulo dinámico**: ni agregar ni reemplazar un flujo requiere actualizar
docs (`PRODUCT_STATUS.md` / `AVANCE_VAST_HTTP.md`) — exención explícita en `AGENTS.md`
(Regla dura 1). Los tests de cardinalidad y los que fijan ids los mantiene el dueño,
pero si un test existente fija ids del flujo que reemplazas, **hay que actualizarlo**
(paso A.7).

Hay dos modos. Decide cuál aplica **antes** de tocar nada:

- **Modo A — Reemplazar** un flujo existente (mismo id): el usuario deja un export y
  dice "reemplaza X con este archivo". No se toca `COMFYUI_FLOWS`.
- **Modo B — Agregar** un flujo nuevo: hace falta registrar el id y el label.

Invocación esperada (drop-and-go): el usuario suelta el export en el repo y pide
«usa comfy-add-flow para reemplazar <label> con `<ruta>`»; la skill hace A.1–A.9 sin
que haya que buscar nada más.

## Regla 0 — el repo es la fuente de verdad

Con `COMFYUI_WORKFLOW_SOURCE=embed` (el default de producto en este repo) el bot carga
`templates/<id>.json` **del repo**. Subir, renombrar o respaldar archivos en la box
Colab/Drive (`/content/drive/.../user/default/api_workflows/`) **no cambia nada** en el
bot: esa carpeta solo la lee la UI de ComfyUI.

```bash
grep -E "^COMFYUI_WORKFLOW_SOURCE" .env     # embed → repo; remote → box
grep -E "^COMFYUI_WORKFLOWS_DIR" .env
```

Si alguien dice "lo subí a Colab y sigue igual", esa es la causa — no buscar más.
`remote` hace SSH-cat a la box con cache TTL corto y fallback a embed, pero en Colab
hoy **no sirve**: la única sesión SSH la ocupa el túnel del bot (`colab ssh` responde
429). No cambiar el modo para "arreglar" un flujo.

---

## Modo A — Reemplazar un flujo existente (drop-and-go)

Entrada típica: el usuario deja el archivo en el repo (raíz, `docs/` o `/tmp`) y dice
"reemplaza Ágil Edit con `agil_edit_qwen.json`". El archivo de export **no se
commitea**: solo se copia su grafo al template.

Invariantes al reemplazar:

- El `id` **no cambia** → no se toca `COMFYUI_FLOWS` ni el menú del `/config`.
- Se conservan `name`, `source` (descripción), `timeout` y el resto de metadata del
  template anterior; solo se reescriben los **roles de nodo** (ids nuevos).
- Los ids del export son libres (`"1"`, `"78"`, `"433:111"`…). **No hay que
  renumerar nada**: el `_meta` es lo único acoplado a los ids.

### A.1 Mapear etiqueta → id (si el pedido vino por nombre)

```bash
.venv/bin/python -c "from grokbot.domain.user_config import COMFYUI_FLOWS as F; [print(i,l) for i,l in F]"
```

El template vive en `src/grokbot/providers/comfyui/workflows/templates/<id>.json`.

### A.2 Verificar que el archivo es API-format

Si guardaste desde el canvas con "Save", es **UI-format** (`{"nodes": [...], "links": [...]}`)
y no sirve: hay que re-exportar con **Export (API)**. No hay conversor en el repo.

```bash
.venv/bin/python - "${EXPORT:?ruta del export}" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
nodes={k:v for k,v in d.items() if k!="_meta"}
is_api=bool(nodes) and all(isinstance(v,dict) and "class_type" in v for v in nodes.values())
print(("API" if is_api else "UI"), "| nodos:", len(nodes), "| _meta:", "_meta" in d)
PY
```

Esperado: `API | nodos: N | _meta: False|True`. Si dice `UI`, parar y pedir Export (API).

### A.3 Inventariar nodos, links y widgets

```bash
.venv/bin/python - "${EXPORT:?ruta del export}" <<'PY'
import json,sys
g=json.load(open(sys.argv[1])); g.pop("_meta",None)
for nid,n in sorted(g.items(), key=lambda kv:(len(kv[0]),kv[0])):
    ins=n.get("inputs",{})
    links={k:v[0] for k,v in ins.items() if isinstance(v,list) and len(v)==2 and isinstance(v[0],str)}
    widgets={k:v for k,v in ins.items() if k not in links}
    print(f"{nid:>10} {n['class_type']:<34} links={links} widgets={widgets}")
PY
```

### A.4 Derivar el `_meta` con la tabla de roles (§3)

### A.5 Escribir el template

Reglas del archivo (el resolver las tolera, pero el repo queda limpio):

- `_meta` como **primera** clave del objeto; el grafo copiado tal cual.
- **Sin `_meta` por nodo** (el export trae `title`/`cnr_id`/`ver`; se descartan).
- `LoadImage.image` → `example.png` (placeholder; el provider lo sobrescribe al subir
  la foto real porque `requires_source`/`supports_source` están en `true`).
- `_meta.id` **debe** coincidir con el nombre del archivo y con el id registrado.

Transformación determinista (ajusta `META` antes de correr):

```bash
.venv/bin/python - "${EXPORT:?ruta}" <<'PY'
import json,sys
from pathlib import Path
META = {
  "id": "<id>", "name": "<label>", "media_type": "image",
  "positive_node": "<nodo>", "positive_input": "prompt", "negative_node": "<nodo>",
  "seed_nodes": ["<nodo>"], "save_nodes": ["<nodo>"],
  "supports_source": True, "requires_source": True,
  "source_node": "<nodo>", "source_input": "image", "timeout": 600,
  "source": "<qué cambió y por qué>",
}
raw=json.load(open(sys.argv[1]))
graph={nid:{"inputs":n.get("inputs",{}),"class_type":n["class_type"]}
       for nid,n in raw.items() if isinstance(n,dict) and "class_type" in n}
for n in graph.values():
    # Solo si el bot SIEMPRE sube la foto: el placeholder es irrelevante.
    # Con supports_source sin requires_source el placeholder sí se usa → no tocarlo.
    if n["class_type"]=="LoadImage" and META.get("requires_source"):
        n["inputs"]["image"]="example.png"
dst=Path("src/grokbot/providers/comfyui/workflows/templates")/f'{META["id"]}.json'
dst.write_text(json.dumps({"_meta":META,**graph},indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
print("wrote",dst,len(graph),"nodos")
PY
```

### A.6 Validar sin GPU (§4.1) y contra la box (§4.2)

### A.7 Tests que fijan ids

```bash
grep -rn '"<id>"' tests/            # p. ej. agil_edit_qwen
```

Los tests de `tests/unit/providers/test_workflow_resolver.py` fijan `source_node`,
`positive_input`, etc. Si el re-export cambió ids, actualiza esas assertions al id
nuevo y corre la **suite completa** (no solo el módulo):

```bash
.venv/bin/pytest -q                 # hoy: 828 passed
```

### A.8 Aplicar

El catálogo se cachea en memoria (`lru_cache`), así que el template nuevo no entra
hasta reiniciar. `grok-bot` es un **servicio de usuario**:

```bash
systemctl --user restart grok-bot.service
systemctl --user show grok-bot.service -p ActiveState -p SubState
journalctl --user -u grok-bot.service --since "2 min ago" --no-pager | tail -5
```

Advertir que el reinicio **corta generaciones en curso**. Si el usuario pidió solo
preparar el cambio (no aplicarlo), no reiniciar y decirlo en el reporte.

### A.9 Reportar

Qué flujo, qué cambió de fondo (modelo/LoRA/pasos/switch), `_meta` resultante,
resultado de tests y estado del servicio. Commit en español y **sin** trailer
`Co-Authored-By` (convención del repo), solo si el usuario lo pide.

---

## Modo B — Agregar un flujo nuevo

### B.1 Entender qué flujo se pide

La receta (modelo base, LoRA, resolución, media_type, con/sin foto) puede venir de:

- Un **export API** del canvas (mismo formato que usa el bot).
- Un **export UI** (`{nodes, links}`) → hay que pasarlo a API-format; el pack
  `comfy:convert` del MCP ayuda a convertir UI↔API.
- Un **payload/script de referencia** (p. ej. `payloads/<modelo>.json`).
- **Desde cero**: copiar el patrón de un template existente (`templates/agil_solo.json`
  para el mínimo txt2img single-pass; `templates/krea2_t2i.json` para doble pasada).

Antes de codificar, deja claras estas preguntas con el usuario:

- ¿`media_type`: `image` o `video`?
- ¿Solo txt2img, o acepta **foto de referencia** (img2img/i2v)? Si acepta foto →
  `supports_source: true` y el grafo lleva un nodo `LoadImage`.
- ¿Resolución fija (`EmptyLatentImage` con width/height duros) o **selector** en el
  `/config` (`ResolutionSelector` con widgets elegibles)?
- ¿Qué LoRA(s) y a qué fuerza?
- ¿Un solo muestreo o pasadas de refino/upscale?

### B.2 Nombrar el flujo

- `id`: `snake_case` ASCII (va en callback `cfg:comfyui:flow:<id>` y en el archivo).
  Prefijos que agrupan familias funcionan bien (`agil_solo`, `agil_nsfw`).
- `label`: texto del botón, **copy de usuario en español**, puede llevar tildes.
- **Verificar que los modelos/LoRAs existen en la box activa** (§4.2). Si falta uno,
  reportarlo y ofrecer descargarlo; **no** inventar nombres de archivo.

### B.3 Crear el template

Mismas reglas que A.5. Ejemplo mínimo (txt2img krea2 single-pass, `agil_solo`):
`UNETLoader` + `CLIPLoader` + `VAELoader` + dos `CLIPTextEncode` + `EmptyLatentImage`
fijo + `KSampler` (8 pasos, cfg 1, euler/simple) + `VAEDecode` + `SaveImage`; `_meta`
con `positive_node "5"`, `seed_nodes ["8"]`, `save_nodes ["10"]`,
`supports_source false`. Variante con LoRA: insertar `LoraLoader` entre loaders y
sampler y rewiring `model`/`clip`.

### B.4 Registrar el flujo

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

De ahí derivan solos `VALID_COMFYUI_MODELS` (acepta el id) y `COMFYUI_FLOW_LABELS`
(texto del botón). Si el flujo T2I tiene un hermano de edición, mapearlo también en
`COMFYUI_EDIT_SIBLINGS` (decide el botón "Editar" bajo el resultado).

### B.5 Tests de cardinalidad

Un flujo nuevo deja **rojos** hasta actualizar (los ve el dueño, pero avísalo):
`tests/unit/providers/test_workflow_resolver.py` (`test_flows_returns_registered_flows`,
conjunto de ids) y `tests/unit/telegram/test_keyboards.py` (callbacks del teclado).
Correr la suite completa.

### B.6 Validar y aplicar

§4.1, §4.2 y §A.8.

---

## §3 Determinación del `_meta` (roles por topología)

No adivines ids: derívalos del grafo. El export de ComfyUI a veces usa ids de
subgrafo con dos puntos (`"433:111"`), y un re-export **cambia** todos los ids.

| Clave | Cómo se determina |
|---|---|
| `id` | id existente (reemplazo) o `snake_case` nuevo; debe igualar el nombre de archivo |
| `name` | conservar el del template anterior; para uno nuevo = label |
| `media_type` | `video` si el nodo de guardado es de video (`VHS_VideoCombine`, `SaveVideo`, `SaveWEBM`); si no, `image` |
| `positive_node` | seguir `KSampler.positive` (§3.1) |
| `positive_input` | `prompt` si el nodo lo tiene; si no, `text` |
| `negative_node` | nodo en `KSampler.negative` — **solo metadata**, el provider no lo parchea |
| `seed_nodes` | nodos con input `seed` (§3.2) |
| `save_nodes` | nodos `Save*` (`SaveImage`, `SaveImageAdvanced`, `SaveAnimatedWEBP`…) o `VHS_VideoCombine`; excluir `Preview*` |
| `source_node` | `LoadImage` que alimenta el encode positivo (a través de `FluxKontextImageScale`/`VAEEncode`) |
| `source_input` | input del nodo fuente (`image` en `LoadImage`) |
| `supports_source` | `true` si hay `LoadImage` cableado |
| `requires_source` | `true` si el latent sale de `VAEEncode` de esa imagen (sin ruta txt2img). Si es dudoso, **preguntar** |
| `timeout` | conservar el anterior; si no, omitir (default 600 s imagen / 1500 s video) |
| `source` | texto libre: qué modelo/LoRA/pasos y qué cambió (útil para el próximo reemplazo) |

### §3.1 Encontrar el nodo positivo

1. Localiza el sampler (`KSampler`, `KSamplerAdvanced`, `SamplerCustom*`) en el inventario.
2. Toma el id en su input `positive`.
3. Si ese nodo es de paso — `ReferenceLatent`, `ConditioningCombine`, `ConditioningConcat`,
   `ConditioningSetArea*`, `ControlNetApply*`, `ComfySwitchNode` — sigue **hacia
   arriba** hasta el primer nodo con input de texto (`prompt` > `text` > `string`).
4. Con un `ComfySwitchNode`, mira su input `switch` (un `PrimitiveBoolean`): la rama
   activa está en `on_true`/`on_false`. Las dos ramas suelen ser el mismo text-encode,
   pero si fueran distintas, **preguntar** al usuario cuál recibe el prompt.
5. `negative_node` es el nodo en `KSampler.negative` (aquí suele ser el encode con
   `prompt` vacío). No confundir "texto largo" con "positivo": manda el cableado.

### §3.2 Seeds

Incluye todo nodo con input `seed`; el resolver randomiza **solo** el input llamado
literalmente `seed`. **Limitación conocida**: si el sampler usa `noise_seed`
(`KSamplerAdvanced`, `RandomNoise`), el seed queda fijo entre requests — avisarlo; no
intentar arreglarlo desde el template (requiere tocar `resolver.render`).

---

## §4 Validación

### §4.1 Sin GPU (obligatoria)

```bash
.venv/bin/python - <<'PY'
from grokbot.providers.comfyui.workflows import resolver as R
R.reset_workflow_source()                      # fuerza templates/ del repo
flow=R.get_flow("<id>"); assert flow is not None, "no resuelve"
g=R.render(flow,"prueba",lambda:42)
assert "_meta" not in g, "_meta se coló al grafo"
assert g[flow.positive_node]["inputs"][flow.positive_input]=="prueba"
missing=[(n,i,v[0]) for n,node in g.items() for i,v in node.get("inputs",{}).items()
         if isinstance(v,list) and len(v)==2 and isinstance(v[0],str) and v[0] not in g]
print("origin:",flow.origin,"| positive:",flow.positive_node,"| saves:",flow.save_nodes,
      "| seeds:",flow.seed_nodes,"| source:",flow.source_node,flow.source_input)
print("links colgantes:",missing)
assert not missing
print("OK")
PY
```

Comprobar también: `requires_source` coherente con `source_node`, y `save_nodes` no vacío.

### §4.2 Contra la box (barata, sin encolar)

Por el túnel local (va con `colab ssh`; verificar primero con `curl -s 127.0.0.1:8188/system_stats`):

```bash
ID=<id>
NODES=$(.venv/bin/python - "$ID" <<'PY'
import json,sys
p=f"src/grokbot/providers/comfyui/workflows/templates/{sys.argv[1]}.json"
print(" ".join(sorted({v["class_type"] for k,v in json.load(open(p)).items() if k!="_meta"})))
PY
)
for n in $NODES; do
  printf "%-34s %s\n" "$n" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:8188/object_info/$n")"
done
```

Y los nombres de modelos/LoRA contra los combos de su loader (imprime lo instalado):

```bash
curl -s "http://127.0.0.1:8188/object_info/LoraLoaderModelOnly" \
  | .venv/bin/python -c "import json,sys;print(json.load(sys.stdin)['LoraLoaderModelOnly']['input']['required']['lora_name'][0])"
```

(un `404` en `/object_info` o un nombre ausente = el flujo fallaría en producción;
instalar el nodo/modelo o corregir el nombre).

### §4.3 En vivo (opcional, gasta GPU)

Para confirmar wiring de verdad sin pasar por el bot: `render()` y `POST /prompt` al
ComfyUI del túnel, luego `GET /history/<prompt_id>` hasta que el nodo de
`save_nodes` tenga output; confirmar PNG/MP4. Diagnóstico con el MCP
(`get_history action:"diagnose"`). Esto detecta LoRAs mal nombradas, conexiones rotas
o resoluciones inválidas antes de declarar el flujo listo.

---

## Cierre

- **No escribir tests ni actualizar docs** para el flujo en sí (módulo dinámico;
  exención en `AGENTS.md` Regla dura 1) — pero sí actualizar los tests que fijen ids
  del flujo reemplazado (A.7).
- Reiniciar `grok-bot.service` **solo** cuando el pedido sea aplicar (A.8); comprobar
  estado y log, y avisar que corta generaciones en curso.
- **No leer `data/` ni `tmp/`** (datos privados del usuario; Regla dura 2 de `AGENTS.md`).
- Commit en español, atómico y **sin** trailer `Co-Authored-By` (convención del repo).
- El archivo de export que dejó el usuario no se commitea; solo el template.

## Notas

- Si un flujo nuevo es **variante** de otro (misma base, otra LoRA/resolución), lo más
  ágil es copiar el template base y solo cambiar el `LoraLoader`/los widgets.
- Exportes con "Anything Everywhere" **no materializan sus conexiones en modo API**:
  hay que cablear explícitos los inputs requeridos (caso `donut_face`).
- Antes de diagnosticar "el bot no toma el archivo nuevo", repasa la **Regla 0**: casi
  siempre es que se subió a la box en modo `embed`.
- Rutas de referencia: `src/grokbot/providers/comfyui/workflows/resolver.py` (contrato y
  `render`), `templates/` (ejemplos), `docs/comfyui/AVANCE_VAST_HTTP.md` §5 (receta
  canónica del contrato).
