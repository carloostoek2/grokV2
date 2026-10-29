# Inventario de ComfyUI en el box actual — Google Colab + A100 80 GB

> **Estado:** 2026-10-25. El bot **ya no corre contra Vast**: desde el 2026-10-24 usa una
> sesión de **Google Colab** con **A100-SXM4-80GB**. Este documento es la foto del runtime
> actual. La receta histórica del box Vast sigue en
> [INVENTARIO_BOX_VAST.md](./INVENTARIO_BOX_VAST.md) y **no** describe el entorno actual.

## 1. Conexión del bot (contrato)

El bot no expone ComfyUI a Internet: abre un **túnel SSH local-forward** y entra por
loopback.

| Clave (.env) | Valor actual | Nota |
|---|---|---|
| `COMFYUI_HOST` | `colab-comfy` | Alias de `~/.ssh/config`; host vacío = provider deshabilitado |
| `COMFYUI_PORT` | `22` | Puerto SSH del alias |
| `COMFYUI_REMOTE_PORT` | `8188` | ComfyUI escucha en `127.0.0.1:8188` dentro de la VM |
| `COMFYUI_TUNNEL_LOCAL_PORT` | `0` | Puerto local efímero; el bot publica `127.0.0.1:<libre>` |
| `COMFYUI_WORKFLOW_SOURCE` | `embed` | **Fuente de verdad: `workflows/templates/` del repo** |
| `COMFYUI_WORKFLOWS_DIR` | `/content/drive/MyDrive/ComfyUI/user/default/api_workflows` | Solo para la UI de ComfyUI; el bot no lo lee en `embed` |
| `COMFYUI_WORKFLOW_CACHE_TTL` | `45` | Segundos |

El alias SSH está en `~/.ssh/config`:

```
Host colab-comfy
    HostName colab-runtime
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ProxyCommand /home/ubuntu/.local/bin/colab --config /home/ubuntu/comfy-colab/sessions.json \
        ssh --proxy-mode -s comfy -i ~/.ssh/id_ed25519
    ServerAliveInterval 25
    ServerAliveCountMax 3
```

El túnel vivo observado el 2026-10-25:

```
ssh -N -L 127.0.0.1:8188:127.0.0.1:8188 \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    root@colab-comfy
```

- ComfyUI escucha **solo en loopback** (`--listen 127.0.0.1 --port 8188`).
- El puerto local `8188` de esta máquina queda ocupado por el túnel. Si se cae, el bot
  reintenta; el helper `~/bin/colab-comfy tunnel` lo vuelve a abrir (ver §7).
- El provider del bot usa **modo directo** cuando `127.0.0.1:8188` ya está abierto
  (`ComfyUI port 8188 is already open locally; using direct connection`): no lanza otro
  SSH. Con el túnel vivo, el alias CLI roto **no** bloquea al bot; solo afecta a
  reconectar si el túnel se cae.

## 2. ComfyUI

| | |
|---|---|
| ComfyUI | **0.42.0** |
| Frontend | `comfyui-frontend-package` 1.57.1 |
| Python | 3.13.16 |
| PyTorch | **2.11.0+cu130** |
| `comfy-kitchen` | 0.2.40 |
| GPU | **NVIDIA A100-SXM4-80GB** (VRAM total ~85.09 GB; ~50.4 GB libres con modelos cargados) |
| Shape | High-RAM (~179 GB de RAM) |

Arranque observado (`/system_stats.argv`):

```
main.py --listen 127.0.0.1 --port 8188 --enable-compress-response-body \
  --base-directory /content/drive/MyDrive/ComfyUI \
  --temp-directory /content/comfy_local \
  --database-url sqlite:////content/comfy_local/comfyui.db
```

- **Base de datos, modelos y workflows viven en Google Drive** (`/content/drive/MyDrive/ComfyUI`).
- Temporales/DB de la sesión viven en `/content/comfy_local` (se pierden al recrear la VM).
- No hay `--use-sage-attention` en el arranque actual; si se prueba, hacer A/B de calidad.

## 3. Stack de software

| Paquete | Versión |
|---|---|
| ComfyUI | 0.42.0 |
| Python | 3.13.16 |
| torch / torchvision / torchaudio | 2.11.0+cu130 / 0.26.0+cu130 / 2.11.0+cu130 |
| Runtime CUDA (torch) | 13.0 |
| `comfy-kitchen` | 0.2.40 |
| `comfyui-frontend-package` | 1.57.1 |
| `comfyui-workflow-templates` | 0.11.96 |

Verificación rápida:

```bash
curl -s http://127.0.0.1:8188/system_stats | python3 -m json.tool
```

## 4. Custom nodes

El cuaderno `~/comfy-colab/Untitled0.ipynb` instala en Drive:

| Pack | Aporta |
|---|---|
| `ComfyUI-Manager` | Gestión de nodos |
| `ComfyUI-GGUF` | UNET/CLIP `.gguf` |
| `ComfyUI-Krea2-NAG` (`0afb38d`) | `Krea2NormalizedAttentionGuidance`, `Krea2EditNormalizedAttentionGuidance` |
| `comfyui-krea2edit` (`86f886d`) | `Krea2EditModelPatch`, `Krea2EditGroundedEncode` |
| `rgthree-comfy` | Utilidades de canvas |

Verificado por API el 2026-10-25: las 4 clases de Krea2/NAG, `SeedNode`,
`ResolutionSelector`, `ConditioningConcat`, `EmptySD3LatentImage`, `ImageScaleBy`,
`VAEEncode`/`VAEDecode`, `UnetLoaderGGUF` y `CheckpointLoaderSimple` están cargadas.

## 5. Pesos instalados (Google Drive)

Listado real (`GET /models/...`), 2026-10-25:

| Carpeta | Archivos |
|---|---|
| `diffusion_models/` | `Moody-Krea-Mix-v4.1MM_00001__clean.safetensors`, `Moody-Krea-Mix-v7_00002__clean_fp8.safetensors`, `krea2_turbo_bf16.safetensors`, `z_image_turbo_bf16.safetensors`, `krea2SATDirtyRealism_uncut_fp8.safetensors` |
| `text_encoders/` | `qwen3vl_4b_bf16.safetensors`, `qwen3vl_4b_fp8_scaled.safetensors`, `qwen_3_4b.safetensors` |
| `vae/` | `qwen_image_vae.safetensors`, `Wan2_1_VAE_fp32.safetensors`, `ae.safetensors` |
| `loras/` | `grokstyle_krea2_v2.safetensors`, `Krea2NSFWV4.safetensors`, `Krea2-realism-V2.safetensors`, `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`, `krea2/krea2_identity_edit_v1_2.safetensors` |

**Instalados el 2026-10-25** (descargados en la VM con
`comfyui-workflow-models-downloader`; los 7 flujos HQ corren):

- `diffusion_models/krea2SATDirtyRealism_uncut_fp8.safetensors` — checkpoint Dirty
  (Civitai 2796522 / version 3372523; 13.14 GB).
- `loras/krea2/krea2_identity_edit_v1_2.safetensors` — LoRA que **habilita la edición**
  sobre Krea 2 (Hugging Face `conradlocke/krea2-identity-edit`; 1.83 GB).

## 6. Flujos: bloque HQ (menú activo)

El `COMFYUI_FLOWS` registra 26 ids; el menú de Telegram oculta 10 y muestra **16**
(9 base + 7 `*_hq`). El tuning **candid** del 2026-10-25 vive en
`src/grokbot/providers/comfyui/workflows/templates/*_hq.json` y, como el bot está en
`COMFYUI_WORKFLOW_SOURCE=embed`, **aplica sin desplegar nada al box**. El resolver cachea
los flujos embebidos por proceso (`_flows_cached`): tras editar un template hay que
**reiniciar el bot** para que recargue.

| Flujo | Modelo / LoRA | Muestreo | Resolución | Corre en Colab |
|---|---|---|---|---|
| `grok_style_hq` | Krea2 Turbo bf16 + `grokstyle_krea2_v2` 0.5/0.5 | euler/simple · 10 pasos · cfg 1 + refine 6 pasos denoise 0.25 | 2:3 @ 1.2 MP → refine 1.25× (~1.9 MP) → salida 1.1× | ✅ |
| `agil_solo_hq` | Krea2 Turbo bf16, sin LoRA | euler/simple · 8 pasos · cfg 1 | 1024×1792 (1.83 MP) | ✅ |
| `agil_nsfw_hq` | Krea2 Turbo bf16 + `Krea2NSFWV4` 0.8/0.8 | euler/simple · 8 pasos · cfg 1 | 1024×1792 (1.83 MP) | ✅ |
| `agil_moody_hq` | Moody v7 fp8 + `Krea2-realism-V2` 1.0/1.0 + `Krea2NSFWV4` 1.0 (model-only) | er_sde/simple · 8 pasos · cfg 1 | 9:16 @ 2.0 MP | ✅ (excepción estilística) |
| `dirty_realism_hq` | DirtyUncut fp8, sin LoRA | euler/simple · 10 pasos · cfg 1.0 | 9:16 @ 1.5 MP | ✅ |
| `dirty_edit_hq` | DirtyUncut fp8 + `krea2_identity_edit_v1_2` 1.0 | euler/simple · 12 pasos · cfg 2.0 | 9:16 @ 1.5 MP | ✅ |
| `grok_edit_hq` | Krea2 Turbo bf16 + identity 1.0 + `grokstyle` 0.5 | euler/simple · 10 pasos · cfg 1 | 9:16 @ 1.5 MP | ✅ |

Notas de diseño:

- **`grok_style_hq`** usa un `SeedNode` compartido (nodo `200`) por los dos KSamplers, así
  el resultado de dos pasadas es reproducible. Antes cada pasada recibía una seed aleatoria
  distinta.
- **Dirty HQ** lleva el negativo de piel en el nodo 5
  (`airbrushed skin, plastic skin, waxy skin, beauty filter, retouched, studio lighting,
  glossy skin, cgi, …`). Con `cfg 2.0` el negativo sí actúa.
- En los flujos Turbo a **cfg 1** el negative prompt **no tiene efecto**; la textura de piel
  se pide en positivo (`visible pores, natural skin shine, no retouching`).
- `dirty_edit_hq` usa el mismo negativo en el `Krea2EditGroundedEncode` de la rama negativa
  (nodo 9). Es un A/B pendiente: si la edición se resiste, dejar ese prompt vacío.
- `agil_moody_hq` se resincronizó el 2026-10-25 con el grafo que el dueño corre en el
  navegador: Moody v7 fp8 + `Krea2-realism-V2` 1.0/1.0 + `Krea2NSFWV4` 1.0, `er_sde`/simple
  a 8 pasos, y se subió la resolución del navegador (896×1600) a 9:16 @ 2.0 MP. Es el flujo
  "moody/cine", no el candid.

### 6.1 Smoke test (2026-10-25)

Ejecutado por la API contra el túnel vivo, con prompt candid y seed fijo:

| Flujo | Resultado | Tiempo | Output |
|---|---|---|---|
| `grok_style_hq` | ✅ `success` | 33.7 s | `grokbot/comfyui_00107_.png` |
| `agil_solo_hq` | ✅ `success` | 12.5 s | `grokbot/comfyui_00108_.png` |
| `agil_nsfw_hq` | ✅ `success` | 12.4 s | `grokbot/comfyui_00109_.png` |
| `agil_moody_hq` | ✅ `success` | 29.8 s | `grokbot/comfyui_00110_.png` |
| `dirty_realism_hq` | ✅ `success` | 93.4 s (frío) / 23.5 s (caliente) | `grokbot/dirty_realism_00003_.png` / `00004` |
| `grok_edit_hq` | ✅ `success` | 54.7 s (frío) / 30.1 s (caliente) | `grokbot/comfyui_00114_.png` / `00115` |
| `dirty_edit_hq` | ✅ `success` | 67.3 s (frío) / 52.9 s (caliente) | `grokbot/dirty_edit_00001_.png` / `00002` |

Los tiempos "frío" incluyen la carga de 13.14 GB del checkpoint Dirty desde Drive; los
"caliente" son una segunda ejecución con seed nueva. Los PNG quedaron en el `output/` de
Drive (`grokbot/`); se pueden borrar desde la UI o Drive. Los dos flujos de edición se
probaron subiendo `grok_style_hq` como `source.png` y la instrucción
"Change her t-shirt to a red raincoat.".

La tabla de arriba es del baseline A. El default elegido el 2026-10-25 es **B**: smoke
con seed 88888: `dirty_realism_hq` 19.8 s (10/cfg 1.0) y `grok_style_hq` 33.7 s
(refine 0.25).

### 6.2 A/B candid (2026-10-25, seed 77777)

Mismo prompt, seed fijo y resolución comparable. Salidas en `/tmp/ab/` (local) y
`grokbot/ab_*` (Drive); contact sheets al 100% en `/tmp/ab/*_ab.png`.

- **Dirty:** A (12 pasos/cfg 2.0) 23.6 s; B (10/cfg 1.0) 11.1 s; C (20/cfg 3.0) 37.4 s.
  A y B quedan casi iguales en textura (high-pass medio/RMS 4.89/7.99 vs 4.92/7.91);
  B es más suave y ~2× más rápido; C sube contraste/rojez y high-pass (5.46/9.35) sin
  verse más "casero". **Default: B (elegido 2026-10-25 por textura más suave); A queda como referencia.**
- **Grok Style:** A (refine 0.18), B (0.25), C (sin refine). A/B casi idénticos
  (1.51/2.41); C es visiblemente más liso (1.32/2.22) y más "beauty". **Default: B (0.25,
  elegido 2026-10-25 por el suavizado mínimo); A queda como referencia.** Los tiempos de Grok no son comparables
  entre sí: ComfyUI reutilizó la primera pasada cacheada; la comparación de calidad sí
  es válida.
- Un solo seed/prompt: repetir con 2-3 seeds antes de cambiar el default.

## 7. Gotchas operativos

### 7.1 El alias `comfy` no está registrado en la CLI

El 2026-10-25, `colab --config /home/ubuntu/comfy-colab/sessions.json sessions` muestra la
sesión como `[?] gpu-a100-hm-euw4a0-7eczkl7gsedr` y `-s comfy` responde **"Session not
found"**. El keep-alive daemon ya no está vivo y no hay socket ControlMaster. El túnel
actual es un `ssh` suelto que sigue funcionando; **si se cae, `colab-comfy tunnel` no lo
podrá reconectar** hasta que la sesión vuelva a registrarse.

Opciones (elegir una, no editar `sessions.json` a mano):

1. Reiniciar el stack con `colab-comfy A100 --high-mem` (crea una sesión nueva y registra
   el alias; los modelos viven en Drive, así que no se pierden).
2. Investigar el estado de la CLI con `colab --config ... log` antes de recrear.
3. Mientras tanto, usar la API por el túnel vivo y evitar cerrarlo.
4. **Reutilizar la sesión sin recrearla** (lo que funcionó el 2026-10-25): reconstruir el
   `SessionState` desde la API en un config temporal y usar `colab exec` con ese config:

   ```python
   from colab_cli.common import state
   from colab_cli.state import SessionState, StateStore

   a = state.client.list_assignments()[0]
   s = SessionState(
       name="comfy",
       token=a.runtime_proxy_info.token,
       url=a.runtime_proxy_info.url,
       endpoint=a.endpoint,
       variant=a.variant.name,
       accelerator=a.accelerator.value,
       machine_shape=a.machine_shape.name,
   )
   StateStore("/tmp/colab_attach.json").add(s)
   ```

   Luego `colab --config /tmp/colab_attach.json exec -s comfy -f script.py`. El token de
   `runtime_proxy_info` dura ~1 h; si expira, repetir el paso. No toca el túnel vivo.

### 7.2 `colab ssh` directo devuelve 404

Intentar un segundo `ssh colab-comfy` puede responder
`[colab] Endpoint not found (HTTP 404): this runtime does not expose the /colab/ssh
endpoint`. El túnel HTTP existente sigue sano. No cerrar el túnel vivo para "probar" SSH.

### 7.3 El token de Civitai está hardcodeado en el cuaderno

`~/comfy-colab/Untitled0.ipynb` contiene un `CIVITAI_TOKEN` como valor por defecto en una
celda. El owner lo considera descartable (solo descargas, sin crédito), pero conviene
sacarlo del notebook y leerlo de `~/comfy-colab/secrets.env` (`CIVITAI_TOKEN`) para no
filtrarlo en capturas o commits. No pegar el token en docs, logs ni commits.

### 7.4 Descargar/reinstalar los pesos del bloque HQ

**Ejecutado el 2026-10-25** para instalar el checkpoint Dirty y la LoRA de identidad; se
conserva para reinstalar tras recrear la VM. La vía más simple es el nodo
**comfyui-workflow-models-downloader**, que ya
tiene `civitai_api_key` y `huggingface_token` guardados en su `settings.json`. Encolar por
HTTP a través del túnel (no hay que pasar tokens a mano):

```bash
curl -s -X POST http://127.0.0.1:8188/workflow-models/download-url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://civitai.com/api/download/models/3372523?type=Model&format=SafeTensor",
       "filename":"krea2SATDirtyRealism_uncut_fp8.safetensors",
       "directory":"diffusion_models"}'

curl -s -X POST http://127.0.0.1:8188/workflow-models/download-url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://huggingface.co/conradlocke/krea2-identity-edit/resolve/main/krea2_identity_edit_v1_2.safetensors",
       "filename":"krea2_identity_edit_v1_2.safetensors",
       "directory":"loras/krea2"}'
```

Seguimiento:

```bash
curl -s http://127.0.0.1:8188/workflow-models/progress
curl -s http://127.0.0.1:8188/workflow-models/download-history
```

También se puede usar la celda del cuaderno (con `CIVITAI_TOKEN` en el entorno). Al
terminar, **reiniciar ComfyUI** (celda 9 del cuaderno, o `pkill -f main.py` y relanzar el
mismo `main.py` con `--base-directory /content/drive/MyDrive/ComfyUI`) y validar:

```bash
curl -s http://127.0.0.1:8188/models/diffusion_models
curl -s http://127.0.0.1:8188/models/loras
```

### 7.5 La UI y el bot no comparten los grafos

- El bot usa `templates/` del repo (`embed`).
- La UI de ComfyUI lee `/content/drive/MyDrive/ComfyUI/user/default/api_workflows`.
- `~/comfy-colab/actualizar_cuaderno.py` solo sincroniza **9 flujos base**, no los `*_hq`.
  Para probar los HQ en la UI hay que copiarlos a `api_workflows/` o extender ese script.

## 8. Verificación (qué correr)

```bash
# Túnel + GPU + ComfyUI
~/bin/colab-comfy status
ps -eo pid,etime,args | grep -E 'ssh .*8188|colab.*keep-alive' | grep -v grep
curl -s http://127.0.0.1:8188/system_stats | python3 -m json.tool

# Pesos realmente visibles para los loaders
curl -s http://127.0.0.1:8188/object_info/UNETLoader
curl -s http://127.0.0.1:8188/object_info/CLIPLoader
curl -s http://127.0.0.1:8188/object_info/VAELoader
curl -s http://127.0.0.1:8188/object_info/LoraLoader

# Nodos que exigen los flujos HQ
for n in Krea2EditModelPatch Krea2EditGroundedEncode SeedNode ResolutionSelector; do
  curl -s "http://127.0.0.1:8188/object_info/$n" | grep -q "\"$n\"" \
    && echo "$n OK" || echo "$n MISSING"
done
```

## 9. Histórico

- **Vast**: la receta anterior (puerto `18188`, `/workspace/ComfyUI`, pesos fp8, script
  `scripts/provision_vast_box.sh`) sigue en
  [INVENTARIO_BOX_VAST.md](./INVENTARIO_BOX_VAST.md). No usarla para diagnosticar el
  runtime actual.
- **Diseño del slice** (transporte, cliente, resolver, provider):
  [AVANCE_VAST_HTTP.md](./AVANCE_VAST_HTTP.md).
