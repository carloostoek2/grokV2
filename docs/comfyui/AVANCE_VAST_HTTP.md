# ComfyUI en grokV2 — integración por API HTTP/WS (avance y estado)

Fecha: 2026-09-05 · Slice `aa65651` → `e5ead0e` (rama `master`).
Sigue a la investigación `API_COMFYUI.md` y documenta **cómo quedó implementado** el
flujo, el estado del proyecto y lo que falta. Los commits previos del slice llevan
co-autoría; los siguientes **no** (decisión del owner).

> **Actualización 2026-09-05 — ComfyUI se maneja por FLUJOS (no por catálogo de
> modelo/LoRA).** El `/config` de ComfyUI ya no lista modelos (qwen, krea2_raw/moody,
> wan_i2v, …), LoRAs ni el toggle Refinar. Ahora lista **flujos por nombre**; hoy hay
> cuatro: **Grok Style** (`workflows/templates/krea2_t2i.json`, que hornea la LoRA
> `grokstyle_krea2_v2`), **Donut Face** (`donut_face.json`, pipeline Krea2 con upscale +
> DonutFaceDetailer, validado en vivo), **Ágil solo** (`agil_solo.json`, krea2 turbo
> single-pass 896×1600 sin LoRA) y **Ágil NSFW** (`agil_nsfw.json`, idem + LoRA
> `Krea2NSFWV4.safetensors` @1.0), ambos validados en vivo. Cada flujo es un workflow API-format con su
> modelo/LoRA horneados + una clave `_meta` (id, name, media_type, nodos prompt/seed/save,
> `positive_input`, `timeout`) que el resolver separa antes de encolar. El provider
> resuelve por `get_flow(id)`; el id vive en `domain/user_config.COMFYUI_FLOWS`. Sesiones
> legacy con modelos del catálogo anterior se auto-normalizan al flujo default.
> **Agregar un flujo = dejar caer `templates/<id>.json` con su `_meta` + registrar
> `(id, name)` en `COMFYUI_FLOWS`** (esta sección actualiza §1-§6).

## 1. Decisión de la integración

grokbot ya no depende de scripts dentro de la GPU (`/workspace/gen_comfy.py` vía
SSH/scp). El provider ComfyUI conduce la **API nativa de ComfyUI (REST + WebSocket)**
a través de un **túnel SSH local-forward** al box Vast:

- **Refine (2-stage) queda fuera de esta integración**: no se eliminó del repo, pero
  no se toma en cuenta. El nuevo provider **no emite `meta["comfyui_remotes"]`**, y ese
  key es el disparo de `ResolveRefineUseCase.offer(...)` → ninguna generación entra a la
  confirmación de refine. El toggle "Refinar" **salió de la UI** del `/config` (ya no se
  ofrece; refine sigue dormido en el código).
- Alcance v1: **imagen** con el flujo **Grok Style** (txt2img) validado en vivo. Video y
  variantes con foto quedan pendientes de plantillas/pesos (ver §6).

## 2. Cómo está implementado el flujo

```
Telegram handler / use case (application)
   └─ registry.resolve_image(cfg) ──► ComfyUIProvider.generate(request[, source_image])
                                          │ 1. get_flow(id) → Flow (workflow horneado + nodos)
                                          │ 2. transport.ensure() → base_url (túnel ssh -N -L)
                                          │ 3. ComfyApiClient(base_url)
                                          │ 4. render(flow, prompt, seeds) → grafo API-format
                                          │ 5. run_workflow(graph)  (POST /prompt + WS + /history)
                                          │ 6. history(prompt_id) → outputs de save_nodes
                                          │ 7. view(...) → bytes → archivo local en tmp/comfyui
                                          ▼
                              GenerationResult(file_path, meta["file_paths"], ...)
                                          ▼
                              ResultSender → foto/video a Telegram (sin refine)
```

Componentes (bajo `src/grokbot/providers/comfyui/`):

| Módulo | Rol |
|---|---|
| `transport.py` | `SshLocalForward`: abre/mantiene `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote>` al box. `ensure()/restart()/close()`; spawner inyectable (tests). No ejecuta comandos remotos. |
| `client.py` | `ComfyApiClient` (aiohttp): `enqueue`, `run_workflow` (fin por WS con match de `prompt_id` y fallback a `/history`; deadline único), `history`, `view` (acepta MP4), `upload_image`, `health`, `object_info`. Errores tipados de `providers/base.py`. Sesión y fuente WS inyectables. |
| `workflows/resolver.py` | Registro de **flujos** por id: `flows()`/`get_flow(id)` → `Flow` (grafo limpio + nodos prompt/seed/save + `supports_source`), descubriendo los `templates/*.json`. `render()` copia el grafo (sin `_meta`) y parchea prompt + seeds. |
| `workflows/templates/*.json` | Workflows API-format versionados, **self-describables** (`_meta`: id, name, media_type, nodos a parchear). Hoy `krea2_t2i.json` = flujo `grok_style`. |
| `provider.py` | `ComfyUIProvider` (implements `ImageProvider`/`VideoProvider`): valida el **id de flujo** (M1), resuelve el flujo, corre y descarga a `tmp/comfyui`, normaliza a `GenerationResult`. |

Detalles del flujo por generación:

1. `_resolve_flow(request)` resuelve el flujo por su id (`get_flow(flow_id)`); un id
   desconocido es `ProviderInputError` (M1: nunca se interpolan valores crudos a ningún
   shell; solo se parchean inputs del JSON).
2. `supports()` = existe el flujo y su `media_type` coincide con el request; si no, el
   request **no** es soportado (`supports()==False` → mensaje user-safe) o
   `ProviderInputError` directo.
3. `transport.ensure()` devuelve `http://127.0.0.1:<puerto_local>` (túnel al
   `COMFYUI_REMOTE_PORT` dentro del box, en el box real **18188**).
4. `render()` copia la plantilla, pone el prompt del usuario en el nodo positivo y
   randomiza los seeds de los nodos KSampler.
5. `run_workflow()` encola con `client_id`; escucha `/ws` hasta `executing node=None`
   (filtra binarios/previews); si el WS no termina cae a polling de `/history`; al final
   verifica `status_str == "success"`.
6. `history()` → de los `save_nodes` (p. ej. `19`) junta `images`/`gifs`/`videos` →
   refs `{filename, subfolder, type}`.
7. `view(...)` descarga cada media a `tmp/comfyui/comfyui_<ts>_<hex><ext>` (ext
   permitido: png/jpg/webp/mp4/webm/…).
8. Resultado: `GenerationResult(provider="comfyui", file_path=<local>, media_type,
   meta={"file_paths": [...] , "download_allowlist": None})`. **Sin `comfyui_remotes`.**

Settings/env: `COMFYUI_HOST`/`COMFYUI_PORT` = SSH al box (host vacío = deshabilitado);
`COMFYUI_REMOTE_PORT` = puerto HTTP de ComfyUI dentro del box (default 18188);
`COMFYUI_TUNNEL_LOCAL_PORT` = puerto local a bindear (0 = efímero).

## 3. Qué se validó (smoke en vivo)

Con el flujo **Grok Style** (`krea2_t2i.json`, txt2img, 2 pasadas + upscale + `SaveImage`):

- Generación real contra el box por el flujo HTTP: **imagen krea2 PNG generada y
  descargada** (2.4 MB, magic PNG verificado).
- `media_type=image`, `file_path` local, `meta = {"file_paths", "download_allowlist"}` →
  **refine no se dispara**.
- **Flujo 2 — Donut Face (2026-09-05)**: export del canvas (muestreo + 2 pasadas de
  tiled-upscale + DonutFaceDetailer) normalizado a `donut_face.json` y generado en vivo:
  **imagen final real descargada** (~4.8 MB, magic PNG). El export usaba nodos "Anything
  Everywhere" cuyas conexiones **no se materializan en modo API** (inputs requeridos
  `vae`/`bbox_detector`/`model` faltantes) → se cablearon **explícitos** en el template
  (VAE 468 a los decode/upscale/detailer; detector 50 + SAM 52 al FaceDetailer; model 1055
  al DonutKrea2FusionControl). Suite completa **655 passed** (0-red/0-mock).
- **Flujos 3 y 4 — Ágil solo / Ágil NSFW (2026-09-05)**: grafos **krea2 turbo
  fp8_scaled single-pass** a resolución **FIJA 896×1600** (8 pasos, cfg 1, euler/simple,
  1 VAE + 1 KSampler + SaveImage), **sin** ResolutionSelector ni 2.ª pasada; **solo
  txt2img** (`supports_source: false`). `agil_solo.json` (sin LoRA) y `agil_nsfw.json`
  (con la LoRA `Krea2NSFWV4.safetensors` @1.0 horneada en un `LoraLoader`). Ambos
  renderizados desde el template (vía `resolver.render`) y generados en vivo contra el box
  (PNG 896×1600); son ~40% más rápidos que Grok Style/Donut Face. Suite completa
  **659 passed** (0-red/0-mock).

## 4. Estado del proyecto (resumen)

- El camino SSH (`ssh_client.py`, `gen_comfy.py`) se eliminó del árbol; exports y
  `main.py` apuntan al provider HTTP.
- **ComfyUI se configura por flujos** (hoy `grok_style`/Grok Style, `donut_face`/Donut
  Face, `agil_solo`/Ágil solo y `agil_nsfw`/Ágil NSFW): el `/config` lista los flujos por
  nombre; el catálogo legacy de modelo/LoRA (qwen,
  krea2_raw/moody, wan_i2v, LoRAs, refine) salió de la UI y de la validación — las sesiones
  viejas se auto-normalizan al flujo default al cargar.
- El bot sigue operando igual en Telegram para lo que ya corría; la única diferencia
  visible: los resultados ComfyUI **no** ofrecen refine.
- `refine` queda dormido (código legacy intacto, sin `comfyui_remotes`; su toggle ya no
  se ofrece en `/config`).

## 5. Cómo añadir un flujo nuevo

Cada flujo = **un archivo JSON + una entrada de dominio**; nada más (la conexión
directa no se toca). Contrato:

1. Exportar el workflow desde el canvas de ComfyUI (API-format) y dejarlo en
   `workflows/templates/<id>.json` con su modelo/LoRA **horneados** en los nodos y una
   clave `_meta`: id, name, media_type, positive_node, **positive_input** (default
   `"text"`; p. ej. `"prompt"` si el nodo es un Wildcard Processor), negative_node,
   seed_nodes, save_nodes, supports_source y **timeout** (override del default por media).
2. Registrar el flujo en `domain/user_config.COMFYUI_FLOWS`:
   `(("grok_style", "Grok Style"), ("<id>", "<Nombre>"))`. El id entra a
   `VALID_COMFYUI_MODELS` (validación/normalización) y la UI lo lista por `name`.

El resolver descubre el template solo (`flows()`/`get_flow`); `_meta` (top-level y el de
*cada nodo* del export) se descarta antes de encolar (ComfyUI nunca lo ve). Probar offline
(tests con fakes) y validar en vivo una generación. Si el flujo admite foto (`LoadImage`),
declarar `supports_source: true` (el provider sube la foto con `upload_image`).
**Ojo con los exports que usan nodos "Anything Everywhere"** (conexiones virtuales): en
modo API no se materializan → hay que cablear **explícitos** los inputs requeridos que esos
nodos inyectaban (caso `donut_face`: `vae`, `bbox_detector`, `sam_model_opt`, `model`).

## 6. Pendientes / siguientes pasos

| Pendiente | Detalle |
|---|---|
| Más flujos (video `wan_i2v` / `minimax_i2v`) | El box tiene los nodos (Wan i2v, MiniMax H3) pero **no los pesos** (solo VAE Wan). Cuando haya pesos/receta, añadirlos como flujos (contrato §5) → template + `COMFYUI_FLOWS` → validar. |
| Variantes con foto (img2img / i2v) | Como flujo con `supports_source: true` (+ `upload_image`); requiere los grafos con `LoadImage`. |
| Catálogo v1 (recorte UI) | **Hecho (2026-09-05)** — ComfyUI se maneja por flujos; la UI solo ofrece los flujos registrados (**Grok Style**, **Donut Face**, **Ágil solo**, **Ágil NSFW**). |
| Smoke Telegram | Recorrido manual en vivo de los flujos ComfyUI imagen (sin refine) — flujos validados por API directa (Grok Style, Donut Face, Ágil solo y Ágil NSFW generaron imagen real). |
| Hosted API nodes | El box expone también nodos de API alojada (Krea2ImageNode, QwenImage…); fuera de alcance si se decide difusión local. |
