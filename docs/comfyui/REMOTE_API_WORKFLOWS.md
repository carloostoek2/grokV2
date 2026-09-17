# Remote API workflows (Vast source of truth)

Stock ComfyUI `POST /prompt` **always** needs an API-format graph in the body.
There is no “run workflow by name” endpoint. grokV2 therefore stores API-format
JSON files **on the Vast box** and fetches them at runtime.

## Layout on Vast

```
/workspace/ComfyUI/user/default/api_workflows/
  agil_moody.json
  agil_solo.json
  agil_nsfw.json
  donut_face.json
  grok_style.json
```

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
