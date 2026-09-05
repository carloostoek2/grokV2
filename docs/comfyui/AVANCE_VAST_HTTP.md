# ComfyUI en grokV2 — integración por API HTTP/WS (avance y estado)

Fecha: 2026-09-05 · Slice `aa65651` → `e5ead0e` (rama `master`).
Sigue a la investigación `API_COMFYUI.md` y documenta **cómo quedó implementado** el
flujo, el estado del proyecto y lo que falta. Los commits previos del slice llevan
co-autoría; los siguientes **no** (decisión del owner).

## 1. Decisión de la integración

grokbot ya no depende de scripts dentro de la GPU (`/workspace/gen_comfy.py` vía
SSH/scp). El provider ComfyUI conduce la **API nativa de ComfyUI (REST + WebSocket)**
a través de un **túnel SSH local-forward** al box Vast:

- **Refine (2-stage) queda fuera de esta integración**: no se eliminó del repo, pero
  no se toma en cuenta. El nuevo provider **no emite `meta["comfyui_remotes"]`**, y ese
  key es el disparo de `ResolveRefineUseCase.offer(...)` → ninguna generación entra a la
  confirmación de refine (el botón "Refinar" no aparece). El toggle de `/config` queda
  inerte.
- Alcance v1: **imagen** (`krea2`/`none`, txt2img) validado en vivo. Video y variantes
  con foto quedan pendientes de plantillas/pesos (ver §6).

## 2. Cómo está implementado el flujo

```
Telegram handler / use case (application)
   └─ registry.resolve_image(cfg) ──► ComfyUIProvider.generate(request[, source_image])
                                          │ 1. lookup(model, lora, media) → TemplateSpec
                                          │ 2. transport.ensure() → base_url (túnel ssh -N -L)
                                          │ 3. ComfyApiClient(base_url)
                                          │ 4. render(spec, prompt, seeds) → grafo API-format
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
| `workflows/resolver.py` | Registro `(model, lora, media_type)` → `TemplateSpec` (grafo + nodos prompt/seed/save + `supports_source`). `render()` copia el grafo y parchea prompt + seeds. |
| `workflows/templates/*.json` | Grafos API-format versionados (hoy `krea2_t2i.json`). |
| `provider.py` | `ComfyUIProvider` (implements `ImageProvider`/`VideoProvider`): valida model/lora (M1), preconditions (foto para video), resuelve plantilla, corre y descarga a `tmp/comfyui`, normaliza a `GenerationResult`. |

Detalles del flujo por generación:

1. `_model_lora(request)` valida `model`/`lora` contra el catálogo (M1: nunca se
   interpolan valores crudos a ningún shell; solo se parchean inputs del JSON).
2. `lookup(cm, cl, media)` → plantilla; si no hay, el request **no** es soportado
   (`supports()==False` → mensaje user-safe "el modelo configurado no puede generar…")
   o `ProviderInputError` directo.
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

Con el grafo real de krea2 (txt2img, 2 pasadas + upscale + `SaveImage`):

- Generación real contra el box por el flujo HTTP: **imagen krea2 PNG generada y
  descargada** (2.4 MB, magic PNG verificado).
- `media_type=image`, `file_path` local, `meta = {"file_paths", "download_allowlist"}` →
  **refine no se dispara**.
- Suite completa **653 passed** (0-red/0-mock); tests nuevos de transporte, cliente,
  resolver y provider (fakes).

## 4. Estado del proyecto (resumen)

- El camino SSH (`ssh_client.py`, `gen_comfy.py`) se eliminó del árbol; exports y
  `main.py` apuntan al provider HTTP.
- El bot sigue operando igual en Telegram para lo que ya corría; la única diferencia
  visible: los resultados ComfyUI **no** ofrecen refine.
- `refine` queda dormido (código legacy intacto, sin `comfyui_remotes`).

## 5. Cómo añadir una nueva familia/plantilla

Patrón (mismo slice, siguiente commit):

1. Exportar el workflow desde el canvas de ComfyUI (formato UI o API). Colocarlo como
   grafo API-format en `workflows/templates/<clave>.json` (quitar `_meta`, prompts
   ejemplo, prefixes; seeds a 0).
2. Registrar en `workflows/resolver.py`:
   `(model, lora, MediaType.X.value) → {"file", "positive_node", "seed_nodes",
   "save_nodes", "supports_source": bool}`.
3. Si la plantilla admite foto (`LoadImage`), subir con `upload_image` (nombre único por
   job) y apuntar el nodo; marcar `supports_source=True`.
4. Probar offline (tests con fakes) y validar en vivo una generación; actualizar
   catálogo/UI solo cuando haya plantilla.

## 6. Pendientes / siguientes pasos

| Pendiente | Detalle |
|---|---|
| Video (`wan_i2v` / `minimax_i2v`) | El box tiene los nodos (Wan i2v, MiniMax H3) pero **no los pesos** (solo VAE Wan). Confirmar/instalar pesos y receta → plantilla API-format + `SaveVideo` → validar. |
| Variantes con foto (img2img / i2v) | `supports_source` + `upload_image`; requiere los grafos con `LoadImage`. |
| Catálogo v1 (recorte UI) | Solo cuando haya plantillas: reducir modelos/loras ofrecidos a los soportados. |
| Smoke Telegram | Recorrido manual en vivo del flujo ComfyUI imagen (sin refine). |
| Hosted API nodes | El box expone también nodos de API alojada (Krea2ImageNode, QwenImage…); fuera de alcance si se decide difusión local. |
