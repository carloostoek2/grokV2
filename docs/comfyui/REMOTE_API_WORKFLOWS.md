# Remote API workflows (Vast source of truth)

Stock ComfyUI `POST /prompt` **always** needs an API-format graph in the body.
There is no “run workflow by name” endpoint. grokV2 therefore stores API-format
JSON files **on the Vast box** and fetches them at runtime.

## Layout on Vast

```
/workspace/ComfyUI/user/default/api_workflows/
  agil_edit_nsfw.json   agil_edit_qwen.json   agil_moody.json
  agil_nsfw.json        agil_solo.json        dirty_edit.json
  dirty_realism.json    donut_face.json       grok_edit.json
  grok_style.json       ohwx_dirty_edit.json  ohwx_edit.json
  ohwx_krea2.json       qwen21_t2i.json       wan_i2v.json
```

> Pesos, sampler y cuáles flujos están rotos: [INVENTARIO_BOX_VAST.md](./INVENTARIO_BOX_VAST.md).
> El listado de arriba es el de ese inventario (los 12 previos más los tres Ohwx).
> `donut_face` y `wan_i2v` siguen sin ser ejecutables por nodos o pesos faltantes.
> Los tres Ohwx sí corren, pero solo si está `models/loras/ohwx_krea2.safetensors`
> (Drive, paso manual; el script no la descarga).

| id | Nombre | Foto | LoRA | Trigger |
|---|---|---|---|---|
| `ohwx_krea2` | Ohwx | no | `ohwx_krea2.safetensors` @ 1.0/1.0 (`LoraLoader`) | `ohwx woman` en el prompt |
| `ohwx_edit` | Ohwx Edit | sí | `ohwx_krea2` @ 1.0 (`LoraLoaderModelOnly`) + `grokstyle_krea2_v2` @ 0.6 | `ohwx woman` en la instrucción |
| `ohwx_dirty_edit` | Ohwx Dirty Edit | sí | solo `ohwx_krea2` @ 1.0 sobre el UNET Dirty | `ohwx woman` en la instrucción |

Los JSON salen de `src/grokbot/providers/comfyui/workflows/templates/` y
`scripts/provision_vast_box.sh` (fase `workflows`) los copia a `api_workflows/`
con el nombre de `_meta.id`. La LoRA hay que unirla desde Drive y dejarla en
`/workspace/ComfyUI/models/loras/ohwx_krea2.safetensors` (SHA-256
`d453337ccb17ae1696b763c1315ca606baf3907ffe6b75beb493a327c40e488e`, 457111520 bytes).
`verify` falla si falta, salvo `--skip-ohwx`.

Each file is ComfyUI **API format** (flat `{node_id: {class_type, inputs}}`) plus
a top-level `_meta` block (`id`, `name`, `media_type`, `positive_node`,
`seed_nodes`, `save_nodes`, …). `_meta` is stripped before enqueue.

## How the bot loads them

1. Env `COMFYUI_WORKFLOW_SOURCE=remote` (default) + `COMFYUI_HOST` set.
2. Over the **same SSH credentials** as the Comfy tunnel, the resolver runs
   `ssh … cat -- $COMFYUI_WORKFLOWS_DIR/{flow_id}.json` (not HTTP).
3. Short TTL cache (`COMFYUI_WORKFLOW_CACHE_TTL`, default 45s).
4. If the remote file is missing/invalid → **fallback** to repo
   `src/grokbot/providers/comfyui/workflows/templates/` with a warning log.
5. `render()` still injects only prompt + seeds; then `POST /prompt`.

Set `COMFYUI_WORKFLOW_SOURCE=embed` to force local templates (offline/tests).

## Updating a workflow after editing in the UI

1. Open the graph in ComfyUI, run once (or use **Export (API)**).
2. Ensure the JSON is API-format and includes the bot `_meta` (copy from the
   previous `api_workflows/{flow_id}.json` if Export strips it).
3. Overwrite `/workspace/ComfyUI/user/default/api_workflows/{flow_id}.json` on Vast.
4. Wait for cache TTL (or restart `grok-bot`) and the next generation picks it up.

**Do not** rely on a UI→API converter in the bot — there isn’t one.

## Env keys

| Key | Default | Meaning |
|-----|---------|---------|
| `COMFYUI_WORKFLOW_SOURCE` | `remote` | `remote` \| `embed` |
| `COMFYUI_WORKFLOWS_DIR` | `/workspace/ComfyUI/user/default/api_workflows` | Dir on Vast |
| `COMFYUI_WORKFLOW_CACHE_TTL` | `45` | Seconds |
