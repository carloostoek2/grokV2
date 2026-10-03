# Inventario del box Vast (ComfyUI) — estado exacto

> **Foto del 2026-10-03.** Esto es un *inventario* (qué hay hoy), no un contrato ni un
> runbook. Para el diseño/integración ver [AVANCE_VAST_HTTP.md](./AVANCE_VAST_HTTP.md) y
> [REMOTE_API_WORKFLOWS.md](./REMOTE_API_WORKFLOWS.md).
>
> ⚠️ El box **no tiene volumen persistente**: un *recycle*/*destroy* borra ComfyUI, los
> pesos y los custom nodes. Solo sobrevive *stop/start*. Ver §9.

## 1. Acceso

| Qué | Valor |
|---|---|
| SSH | `ssh -p 44411 root@180.189.55.43` (coordenadas en `.env`: `COMFYUI_HOST` / `COMFYUI_PORT`) |
| ComfyUI HTTP | loopback del box `127.0.0.1:18188` (`COMFYUI_REMOTE_PORT`); el bot entra por **túnel SSH local-forward**, no por HTTP público |
| UI web | portal del box: `external_port 10100 → internal_port 18188`, `open_path /` |
| Log de ComfyUI | `/var/log/portal/comfyui.log` |
| Instalación | `/workspace/ComfyUI` |

Las coordenadas SSH rotan al reiniciar la instancia. Si el bot no llega al box, probar el SSH
antes de culpar al provider.

## 2. Hardware

| | |
|---|---|
| GPU | **NVIDIA GeForce RTX 5090** — 32 607 MiB (~31.8 GB), compute capability **12.0** (Blackwell `sm_120`) |
| Driver | **580.82.07** (mínimo para CUDA 13.0: 580.65) |
| CPU / RAM | 16 vCPU / 30 GB |
| Disco | 251 GB totales, 100 GB usados, **152 GB libres** (`overlay`, incluye `/workspace`) |

## 3. Sistema y runtime

| | |
|---|---|
| SO | Ubuntu 24.04.4 LTS (Noble Numbat), kernel 6.17.0-19-generic |
| CUDA toolkit del sistema (`nvcc`) | 12.8.93 — **no** es el que usa ComfyUI (los wheels traen su propio runtime) |
| Python | 3.12.14 |
| venv | **`/venv/main`** (no hay `.venv` dentro de ComfyUI) |

### Stack de cómputo (lo que acabamos de actualizar)

| Paquete | Versión |
|---|---|
| **torch** | **2.11.0+cu130** |
| torchvision | 0.26.0+cu130 |
| torchaudio | 2.11.0+cu130 |
| Runtime CUDA (torch) | **13.0** |
| triton | 3.6.0 |
| sageattention | 1.0.6 (JIT; sin `.so` propios) |
| numpy / scipy | 2.5.2 / 1.18.1 |
| transformers | 5.18.0 |
| safetensors / einops | 0.8.0 / 0.8.2 |
| aiohttp | 3.14.3 |
| pillow | 12.3.0 |

Libs NVIDIA del wheel: `nvidia-cudnn-cu13 9.19.0.56`, `nvidia-cusparselt-cu13 0.8.0`,
`nvidia-nccl-cu13 2.28.9`, `nvidia-nvshmem-cu13 3.4.5`, `nvidia-nvjitlink 13.0.88`,
`nvidia-nvtx 13.0.85`.

> **Residuo:** quedan ~4.5 GB de paquetes `nvidia-*-cu12` huérfanos (nada los enlaza:
> `libtorch_cuda.so` no referencia cu12 y sageattention no tiene binarios propios). Se pueden
> purgar sin riesgo aparente, pero **no se hizo**.

### ComfyUI

| | |
|---|---|
| ComfyUI | **0.38.0** |
| Frontend | `comfyui_frontend_package` 1.53.10 |
| comfy-cli | 1.22.0 |
| ComfyUI-Manager | commit `855a0f50` (`3.42-92-g855a0f50`) |
| Backend `comfy-kitchen` | 0.2.37 — reporta disponible `quantize_nvfp4` / `dequantize_nvfp4` / `scaled_mm_nvfp4` (lo que usa el UNET nvfp4 de Moody) |
| Clases de nodo cargadas | 1000 |

## 4. Servicio

Supervisor (`/etc/supervisor/conf.d/comfyui.conf`), script `/opt/supervisor-scripts/comfyui.sh`:

```
command      = /opt/supervisor-scripts/comfyui.sh
COMFYUI_ARGS = --disable-auto-launch --enable-cors-header --port 18188
autostart    = true
autorestart  = true
```

- Escucha **solo en loopback** (`127.0.0.1:18188`); el `8188` histórico ya no existe.
- Arranca con `LD_PRELOAD=libtcmalloc_minimal.so.4`.
- **En cada arranque** el script corre `uv pip install -r /workspace/ComfyUI/requirements.txt`
  (salvo que exista `/.provisioning`, que no existe). Ese archivo lista `torch` / `torchvision`
  **sin pin**, así que uv las ve satisfechas y **no pisa** la versión CUDA instalada a mano.
- No hay `extra_model_paths.yaml`.

## 5. Custom nodes

| Pack | Commit |
|---|---|
| ComfyUI-GGUF | `6ea2651` |
| ComfyUI-Manager | `855a0f50` |
| ComfyUI-Workflow-Models-Downloader | `c3ef4db` (`v1.8.0-7-gc3ef4db`) |
| rgthree-comfy | (sin `.git`) |

Eso es **todo**. En particular **no está** el pack privado que provee los nodos `Donut*` — ver §7.

## 6. Pesos instalados (~91 GB)

| Categoría | Archivo | Tamaño |
|---|---|---|
| `checkpoints/` | `Qwen-Rapid-AIO-NSFW-v23.safetensors` | 26.48 GB |
| `diffusion_models/` | `krea2_turbo_fp8_scaled.safetensors` | 12.24 GB |
| | `qwen-image-edit-2511-Q4_K_M.gguf` | 12.34 GB |
| | `Moody-Krea-Mix-v4.1G_00001__clean_nvfp4.safetensors` | 8.20 GB |
| | `qwen_image_2.1_int8_convrot.safetensors` | 6.76 GB |
| `text_encoders/` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | 8.74 GB |
| | `qwen3vl_8b_int8_convrot.safetensors` | 8.71 GB |
| | `qwen3vl_4b_fp8_scaled.safetensors` | 4.88 GB |
| `loras/` | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | 0.79 GB |
| | `Krea2NSFWV4.safetensors` | 0.43 GB |
| | `grokstyle_krea2_v2.safetensors` | 0.21 GB |
| `vae/` | `qwen_image_2.1_vae_bf16.safetensors` | 0.63 GB |
| | `qwen_image_vae.safetensors` | 0.24 GB |
| | `Wan2_1_VAE_fp32.safetensors` | 0.24 GB |
| | `wan_2.1_vae.safetensors` | *(mismo inodo — hardlink del anterior)* |

Procedencias conocidas: los tres Krea/Qwen base salen de `huggingface.co/Comfy-Org/Krea-2`;
`Moody-Krea-Mix-…nvfp4` de `catlover1937/moody-krea-mix`; `grokstyle_krea2_v2` es la LoRA
**Civitai 2891415** ("Grok Style for Krea 2", requiere token).

## 7. Flujos (9 registrados en `COMFYUI_FLOWS`)

Los graph viven en `/workspace/ComfyUI/user/default/api_workflows/{id}.json` (fuente de verdad
en runtime) y su fallback embebido en `src/grokbot/providers/comfyui/workflows/templates/`.

| id | Nombre UI | Media | Foto | Modelo principal | Sampler / steps / cfg | Resolución | Estado |
|---|---|---|---|---|---|---|---|
| `grok_style` | Grok Style | image | — | `krea2_turbo_fp8_scaled` + LoRA `grokstyle_krea2_v2` + VAE **Wan** | euler/simple · 3 · 1 | 2:3 @ 1.2 MP | ✅ |
| `agil_solo` | Ágil solo | image | — | `krea2_turbo_fp8_scaled` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_nsfw` | Ágil NSFW | image | — | `krea2_turbo_fp8_scaled` + LoRA `Krea2NSFWV4` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_moody` | Ágil Moody | image | — | `Moody-Krea-Mix-…nvfp4` | euler_ancestral/beta · 8 · 1 | **9:16 @ 1.4 MP** → 896×1600 | ✅ |
| `agil_edit_qwen` | Ágil Edit | image | ✔ | `qwen-image-edit-2511-Q4_K_M.gguf` + LoRA `…Lightning-4steps` | euler/simple · 4 · 1 | según foto | ✅ |
| `agil_edit_nsfw` | Ágil Edit NSFW | image | ✔ | `Qwen-Rapid-AIO-NSFW-v23` (checkpoint) | euler/simple · 4 · 1 | según foto | ✅ |
| `qwen21_t2i` | Qwen 2.1 | image | — | `qwen_image_2.1_int8_convrot` | euler/simple · 25 · 1 | 2:3 @ 2.0 MP | ✅ |
| `donut_face` | Donut Face | image | — | `krea2_turbo_fp8_scaled` | — | 9:16 @ 1 MP | ❌ roto |
| `wan_i2v` | Wan I2V | video | ✔ | `wan2.2_ti2v_5B_fp16` | uni_pc/simple · 20 · 5 | — | ❌ roto |

Sobre los componentes compartidos: los flujos Krea2 (`grok_style`, `agil_solo`, `agil_nsfw`,
`agil_moody`, `donut_face`) usan `qwen3vl_4b_fp8_scaled` como text encoder y `qwen_image_vae`
como VAE. Las excepciones: `qwen21_t2i` usa los `_int8_convrot` + `qwen_image_2.1_vae_bf16`;
`agil_edit_qwen` usa `qwen_2.5_vl_7b_fp8_scaled`; `agil_edit_nsfw` usa un **checkpoint
todo-en-uno** (`Qwen-Rapid-AIO-NSFW-v23`) sin encoder ni VAE sueltos; `wan_i2v` usa `umt5_xxl`
(faltante).

### Flujos rotos — por qué

**`donut_face`** — le faltan **17 clases de nodo** que no existen en el box:

`Anything Everywhere`, `BlehSetSamplerPreset`, `CR Text Concatenate`, `DF_Text`,
`DonutApplyLoRAStack`, `DonutFaceDetailer`, `DonutKrea2FusionControl`, `DonutLoRAStack`,
`DonutSampler`, `DonutTiledUpscale`, `Image Save`, `Krea2NormalizedAttentionGuidance`,
`SAMLoader`, `Seed String`, `SeedGenerator`, `UltralyticsDetectorProvider`, `Wildcard Processor`.

El grupo `Donut*` es un **pack privado del owner** que no está en el registry de ComfyUI ni en
GitHub → **no es restaurable desde cero**. Además faltan 2 pesos:
`loras/krea2/krea2_identity_edit_v1_2.safetensors`, `vae/qwen-image/qwen_image_vae.safetensors`
(nota: ese VAE está instalado en la raíz de `vae/`, pero el flujo lo busca en la subcarpeta
`qwen-image/`).

**`wan_i2v`** — le faltan 3 pesos: `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`,
`text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`, `vae/wan2.2_vae.safetensors`.

> El bot **no falla ruidosamente** por esto: al no poder cargar el workflow remoto cae al
> fallback embebido y, si el grafo es inválido, el error aparece recién al encolar en ComfyUI.

## 8. Gotchas operativos

1. **`python`/`pip` no existen en SSH no interactivo.** Hay que `source /venv/main/bin/activate`
   o usar `/venv/main/bin/python` directo.
2. **Los `api_workflows` del box se nombran por `_meta.id`, no por el nombre del template del
   repo.** Ejemplo vivo: el template `templates/krea2_t2i.json` tiene `_meta.id = grok_style`
   → en el box es `grok_style.json`. Si el nombre no coincide, el bot loguea
   `SSH fetch workflow … rc=1` y **cae al fallback embed sin fallar** (parece funcionar y en
   realidad ignora el box). El journal sano dice
   `Loaded ComfyUI workflow <id> from remote`.
3. **`/object_info/<nodo>` devuelve HTTP 200 con `{}`** para un nodo inexistente. Para saber si
   un nodo existe de verdad hay que mirar si el JSON tiene contenido (o el listado completo).
4. Tras instalar un custom node hay que **reiniciar ComfyUI** para que lo cargue.
5. El `_meta` (top-level y por nodo) **nunca** viaja a ComfyUI: `resolver.py` lo separa antes de
   encolar. Enviarlo crudo da `missing_node_type: Node 'ID #_meta' has no class_type`.
6. **Sin volumen persistente.** Ver §9.

## 9. Persistencia

`workspace_is_volume: false`. *Stop/start* conserva el disco; *recycle*/*destroy* lo borra todo
(ComfyUI, pesos, custom nodes, torch cu130). Si el owner quiere que aguante, hay que montar un
volumen.

**Rollback de torch a cu128** (freeze completo en `/workspace/torch-cu128-freeze.txt`):

```bash
source /venv/main/bin/activate
uv pip install --no-cache-dir \
  torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128
```

## 10. Cómo refrescar este inventario

```bash
SSH="ssh -p 44411 root@180.189.55.43"

# Entorno
$SSH 'source /venv/main/bin/activate; python -V; pip list | grep -iE "^torch|^triton|^sageattention"'
$SSH 'nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader'

# Pesos
$SSH 'cd /workspace/ComfyUI/models && find . -type f \( -name "*.safetensors" -o -name "*.gguf" \) \
      -printf "%s\t%p\n" | sort -k2 | awk -F"\t" "{printf \"%.2f GB\t%s\n\", \$1/1073741824, \$2}"'

# Flujos: valida cada graph contra los nodos y pesos realmente presentes
$SSH '/venv/main/bin/python -' <<'PY'
import json, urllib.request, os, glob
oi = json.load(urllib.request.urlopen("http://127.0.0.1:18188/object_info", timeout=60))
KEYS = {"unet_name":"diffusion_models","ckpt_name":"checkpoints","clip_name":"text_encoders",
        "vae_name":"vae","lora_name":"loras"}
M = "/workspace/ComfyUI/models"
for f in sorted(glob.glob("/workspace/ComfyUI/user/default/api_workflows/*.json")):
    wf = json.load(open(f)); meta = wf.pop("_meta", {})
    miss_n = sorted({n["class_type"] for n in wf.values()
                     if isinstance(n, dict) and n.get("class_type") not in oi})
    miss_m = sorted({f"{KEYS[k]}={v}" for n in wf.values() if isinstance(n, dict)
                     for k, v in (n.get("inputs") or {}).items()
                     if k in KEYS and isinstance(v, str)
                     and not os.path.exists(os.path.join(M, KEYS[k], v))})
    print(f"{meta.get('id'):16} nodos_faltan={miss_n or '-'} pesos_faltan={miss_m or '-'}")
PY
```
