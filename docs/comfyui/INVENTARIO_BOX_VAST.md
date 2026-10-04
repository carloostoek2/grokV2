# Inventario de ComfyUI en el box Vast — configuración, pesos y flujos

> **Alcance.** Este documento describe lo **constante**: la configuración de ComfyUI, las
> **versiones del software** que se usan, los pesos instalados y los flujos registrados.
>
> **Lo que depende de la máquina concreta NO se documenta aquí a propósito** — IP y puerto SSH,
> GPU, driver, RAM, disco, SO/kernel, puertos del portal. Una instancia recreada cambia todo eso
> (o parte), así que fijarlo en un doc induce a error. Esas coordenadas viven en el `.env`
> (`COMFYUI_HOST` / `COMFYUI_PORT`) y rotan al recrear la instancia.
>
> Corolario: la instancia puede recrearse; **nada de esto sobrevive a un *recycle*/*destroy***.
> Este doc es la receta de lo que hay que volver a dejar igual.
>
> Para el diseño/integración del provider ver [AVANCE_VAST_HTTP.md](./AVANCE_VAST_HTTP.md) y
> [REMOTE_API_WORKFLOWS.md](./REMOTE_API_WORKFLOWS.md).

## 1. Conexión del bot (contrato)

El bot **no** habla con ComfyUI por HTTP público: abre un **túnel SSH local-forward** y entra
por loopback. Las claves (en `.env`):

| Clave | Valor | Nota |
|---|---|---|
| `COMFYUI_HOST` / `COMFYUI_PORT` | *variable* | SSH al box. **Rota por instancia.** Host vacío = provider deshabilitado |
| `COMFYUI_REMOTE_PORT` | `18188` | Puerto HTTP de ComfyUI **dentro** del box |
| `COMFYUI_TUNNEL_LOCAL_PORT` | `18188` | Puerto local a bindear (`0` = efímero) |
| `COMFYUI_WORKFLOW_SOURCE` | `remote` | `remote` (SSH `cat` desde el box) \| `embed` (templates del repo) |
| `COMFYUI_WORKFLOWS_DIR` | `/workspace/ComfyUI/user/default/api_workflows` | Dir de los graphs API-format en el box |
| `COMFYUI_WORKFLOW_CACHE_TTL` | `45` | Segundos de caché del fetch remoto |

ComfyUI escucha **solo en loopback** (`127.0.0.1:18188`). El puerto `8188` histórico ya no existe
(el `8188` público era un proxy Caddy).

## 2. ComfyUI

| | |
|---|---|
| ComfyUI | **0.38.0** |
| Frontend | `comfyui_frontend_package` 1.53.10 |
| comfy-cli | 1.22.0 |
| ComfyUI-Manager | commit `855a0f50` (`3.42-92-g855a0f50`) |
| Clases de nodo cargadas | ~1000 |

**Cómo se levanta** — vía supervisor (`/etc/supervisor/conf.d/comfyui.conf` →
`/opt/supervisor-scripts/comfyui.sh`):

```
COMFYUI_ARGS = --disable-auto-launch --enable-cors-header --port 18188
autostart    = true
autorestart  = true
```

- Corre con `LD_PRELOAD=libtcmalloc_minimal.so.4`.
- Log: `/var/log/portal/comfyui.log`.
- **En cada arranque** el script corre `uv pip install -r /workspace/ComfyUI/requirements.txt`
  (salvo que exista `/.provisioning`). Ese archivo lista `torch` / `torchvision` **sin pin**, así
  que uv las ve satisfechas y **no pisa** la versión de CUDA instalada a mano.
- No hay `extra_model_paths.yaml`.

> Tras instalar un custom node hay que **reiniciar ComfyUI** para que lo cargue.

## 3. Stack de software (versiones — esto sí es constante)

| Paquete | Versión |
|---|---|
| Python | 3.12 |
| **torch** | **2.11.0+cu130** |
| torchvision / torchaudio | 0.26.0+cu130 / 2.11.0+cu130 |
| Runtime CUDA (torch) | **13.0** |
| triton | 3.6.0 |
| sageattention | 1.0.6 (JIT; sin `.so` propios) |
| transformers | 5.18.0 |
| numpy / scipy | 2.5.2 / 1.18.1 |
| safetensors / einops | 0.8.0 / 0.8.2 |
| aiohttp | 3.14.3 |
| pillow | 12.3.0 |

Libs NVIDIA que acompañan al wheel cu130 (15 paquetes, ~2.7 GB): `nvidia-cublas 13.1.0.3`,
`nvidia-cuda-runtime 13.0.96`, `nvidia-cuda-cupti 13.0.85`, `nvidia-cuda-nvrtc 13.0.88`,
`nvidia-cudnn-cu13 9.19.0.56`, `nvidia-cufft 12.0.0.61`, `nvidia-cufile 1.15.1.6`,
`nvidia-curand 10.4.0.35`, `nvidia-cusolver 12.0.4.66`, `nvidia-cusparse 12.6.3.3`,
`nvidia-cusparselt-cu13 0.8.0`, `nvidia-nccl-cu13 2.28.9`, `nvidia-nvjitlink 13.0.88`,
`nvidia-nvshmem-cu13 3.4.5`, `nvidia-nvtx 13.0.85`.

Las `nvidia-*-cu12` heredadas de cu128 **se purgaron** (liberaron ~2.3 GB). Al hacerlo se
descubrió una trampa que está documentada en §7.6 — **leerla antes de repetir la purga**.

Verificación de que el stack quedó completo (debe dar **0**):

```bash
ldd /venv/main/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so | grep -c "not found"
```

**Requisitos de plataforma que impone este stack** (a verificar en cualquier instancia nueva):

- GPU **Blackwell `sm_120`** (RTX 5090 o superior) — los wheels `cu128`/`cu130` son el mínimo,
  CUDA ≥ 12.8 es obligatorio para `sm_120`.
- **Driver NVIDIA ≥ 580.65** para el runtime CUDA 13.0.
- El wheel es `cp312` → Python 3.12.
- Backend `comfy-kitchen` 0.2.37: expone `quantize_nvfp4` / `dequantize_nvfp4` / `scaled_mm_nvfp4`,
  que es lo que aprovecha el UNET nvfp4 de Moody.

**Rollback a cu128** (por si una actualización futura rompe custom nodes):

```bash
source /venv/main/bin/activate
uv pip install --no-cache-dir \
  torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128
```

## 4. Custom nodes

| Pack | Commit | Aporta |
|---|---|---|
| ComfyUI-GGUF | `6ea2651` | carga UNET `.gguf` |
| ComfyUI-Manager | `855a0f50` (`3.42-92`) | gestión de packs |
| ComfyUI-Workflow-Models-Downloader | `c3ef4db` (`v1.8.0-7`) | descarga de pesos desde la UI |
| rgthree-comfy | (sin `.git`) | utilidades de canvas/nodos |
| **ComfyUI-Krea2-NAG** | `0afb38d` (`v1.0.2`) | `Krea2NormalizedAttentionGuidance`, `Krea2EditNormalizedAttentionGuidance` |
| **comfyui-krea2edit** | `86f886d` (`v1.2.5`) | `Krea2EditModelPatch`, `Krea2EditGroundedEncode` |

Los dos últimos se instalaron el 2026-10-03 (clone + restart). **No tienen dependencias Python**
(`dependencies = []` en ambos `pyproject.toml`) y sus imports resuelven contra ComfyUI 0.38.0.
`comfyui-krea2edit` va en **v1.2.5**, que es el último tag publicado y la versión que
`ComfyUI-Krea2-NAG` exige como compañera (≥ v1.2.5).

Nodos que habilitan (4 clases nuevas, 1000 → 1004):

- `Krea2EditModelPatch` — parchea el forward del modelo para anteponer el latente de la imagen
  fuente como tokens limpios (RoPE frame 1). Entradas: `model`, `source_latent`, `vae` +
  `source_image`, `target_latent`, `fit_mode`, `ref_boost`.
- `Krea2EditGroundedEncode` — encode de instrucción *con* la imagen: el text encoder ve la imagen
  mientras lee la instrucción. Con un `CLIPTextEncode` normal "el modelo nunca ve la imagen
  semánticamente y la calidad cae en picada" (README).
- `Krea2NormalizedAttentionGuidance` — NAG: guía negativa universal en el espacio de atención.
- `Krea2EditNormalizedAttentionGuidance` — la versión combinada (en vez de apilar dos parches).

**Restricciones que impone el README de krea2edit** (importantes para cualquier flujo nuevo):
Turbo = 8 pasos, CFG 1 (camino rápido); generar **≤ 2 MP**; las *remociones* necesitan el modelo
**Raw** a CFG 3, ~20 pasos; y "medir sobre 20+ pasos, no sobre 1".

`ResolutionSelector` **no** es un custom node: viene del core de ComfyUI
(`comfy_extras.nodes_resolution`).

## 5. Pesos instalados (~103 GB, bajo `/workspace/ComfyUI/models`)

| Categoría | Archivo | Tamaño |
|---|---|---|
| `checkpoints/` | `Qwen-Rapid-AIO-NSFW-v23.safetensors` | 26.48 GB |
| `diffusion_models/` | `krea2SATDirtyRealism_uncut_fp8.safetensors` | **12.24 GB** |
| | `krea2_turbo_fp8_scaled.safetensors` | 12.24 GB |
| | `qwen-image-edit-2511-Q4_K_M.gguf` | 12.34 GB |
| | `Moody-Krea-Mix-v4.1G_00001__clean_nvfp4.safetensors` | 8.20 GB |
| | `qwen_image_2.1_int8_convrot.safetensors` | 6.76 GB |
| `text_encoders/` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | 8.74 GB |
| | `qwen3vl_8b_int8_convrot.safetensors` | 8.71 GB |
| | `qwen3vl_4b_fp8_scaled.safetensors` | 4.88 GB |
| `loras/` | `krea2/krea2_identity_edit_v1_2.safetensors` | **1.83 GB** |
| | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | 0.79 GB |
| | `Krea2NSFWV4.safetensors` | 0.43 GB |
| | `grokstyle_krea2_v2.safetensors` | 0.21 GB |
| `vae/` | `qwen_image_2.1_vae_bf16.safetensors` | 0.63 GB |
| | `qwen_image_vae.safetensors` | 0.24 GB |
| | `qwen-image/qwen_image_vae.safetensors` | *(symlink relativo al anterior — lo pide `donut_face`)* |
| | `Wan2_1_VAE_fp32.safetensors` | 0.24 GB |
| | `wan_2.1_vae.safetensors` | *(mismo inodo — hardlink del anterior)* |

Procedencias: los base Krea/Qwen salen de `huggingface.co/Comfy-Org/Krea-2`;
`Moody-Krea-Mix-…nvfp4` de `catlover1937/moody-krea-mix`; `grokstyle_krea2_v2` es la LoRA
**Civitai 2891415** ("Grok Style for Krea 2", **requiere token**).

Renames del owner que hay que mapear al reinstalar: `Krea2NSFWV4.safetensors` ← único equivalente
público `KNP_000003000.safetensors`; `Wan2_1_VAE_fp32.safetensors` ← hardlink de
`wan_2.1_vae.safetensors` de `Comfy-Org/Wan_2.1_ComfyUI_repackaged`.

## 6. Flujos (12 registrados en `COMFYUI_FLOWS`)

Los graphs viven en `/workspace/ComfyUI/user/default/api_workflows/{id}.json` (fuente de verdad en
runtime); el fallback embebido está en `src/grokbot/providers/comfyui/workflows/templates/`.

| id | Nombre UI | Media | Foto | Modelo principal | Sampler / steps / cfg | Resolución | Estado |
|---|---|---|---|---|---|---|---|
| `grok_style` | Grok Style | image | — | `krea2_turbo_fp8_scaled` + LoRA `grokstyle_krea2_v2` **0.6/0.6** + VAE `qwen_image_vae.safetensors` (antes `Wan2_1_VAE_fp32`; solo en el box, no en git) | euler/simple · 3 · 1 | 2:3 @ 1.2 MP | ✅ |
| `agil_solo` | Ágil solo | image | — | `krea2_turbo_fp8_scaled` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_nsfw` | Ágil NSFW | image | — | `krea2_turbo_fp8_scaled` + LoRA `Krea2NSFWV4` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_moody` | Ágil Moody | image | — | `Moody-Krea-Mix-…nvfp4` | euler_ancestral/beta · 8 · 1 | 9:16 @ 1.4 MP → 896×1600 | ✅ |
| `agil_edit_qwen` | Ágil Edit | image | ✔ | `qwen-image-edit-2511-Q4_K_M.gguf` + Lightning `strength_model` **0** (nodo conservado; embed, no en Vast) | euler/simple · 20 · 4.0 · denoise 1.0 | según foto | ✅ |
| `agil_edit_nsfw` | Ágil Edit NSFW | image | ✔ | `Qwen-Rapid-AIO-NSFW-v23` (checkpoint) | euler/simple · 4 · 1 | según foto | ✅ |
| `grok_edit` | Grok Style Edit | image | ✔ | `krea2_turbo_fp8_scaled` + identidad **1.0** y `grokstyle_krea2_v2` `strength_model` **0.6** + VAE `qwen_image_vae` | euler/simple · 10 · 1 | 9:16 @ 1.0 MP | ✅ |
| `dirty_realism` | Dirty Realism | image | — | `krea2SATDirtyRealism_uncut_fp8` (el checkpoint **es** el look, sin LoRA) | euler/simple · 8 · 1 · denoise 1 | 9:16 @ 1.0 MP | ✅ |
| `dirty_edit` | Dirty Realism Edit | image | ✔ | `krea2SATDirtyRealism_uncut_fp8` + LoRA `krea2_identity_edit_v1_2` **1.0** | euler/simple · 8 · 1 | 9:16 @ 1.0 MP | ✅ |
| `qwen21_t2i` | Qwen 2.1 | image | — | `qwen_image_2.1_int8_convrot` | euler/simple · 25 · 1 | 2:3 @ 2.0 MP | ✅ |
| `donut_face` | Donut Face | image | — | `krea2_turbo_fp8_scaled` | — | 9:16 @ 1 MP | ❌ roto |
| `wan_i2v` | Wan I2V | video | ✔ | `wan2.2_ti2v_5B_fp16` | uni_pc/simple · 20 · 5 | — | ❌ roto |

Componentes compartidos: los flujos Krea2 (`grok_style`, `agil_solo`, `agil_nsfw`, `agil_moody`,
`donut_face`) usan `qwen3vl_4b_fp8_scaled` como text encoder y `qwen_image_vae` como VAE.
Excepciones: `qwen21_t2i` usa los `_int8_convrot` + `qwen_image_2.1_vae_bf16`; `agil_edit_qwen`
usa `qwen_2.5_vl_7b_fp8_scaled`; `agil_edit_nsfw` usa un **checkpoint todo-en-uno** sin encoder
ni VAE sueltos; `wan_i2v` usa `umt5_xxl` (faltante).

### `agil_edit_qwen` — Ágil Edit (embed, `b54a103`)

No está en Vast: el resolver cae al template embebido
`src/grokbot/providers/comfyui/workflows/templates/agil_edit_qwen.json` (commit `b54a103`).

- UNET sigue `qwen-image-edit-2511-Q4_K_M.gguf`.
- LoRA Lightning: el nodo se conserva, `strength_model` **0**.
- KSampler: steps **20**, cfg **4.0**, euler, simple, denoise **1.0**.
- `CFGNorm` nodo 16, strength **1**, después de la LoRA.
- `ModelSamplingAuraFlow` shift **3.1**.

### `grok_style` — Grok Style (solo en el box)

Graph en `/workspace/ComfyUI/user/default/api_workflows/grok_style.json`. **No está en git.**

- LoRA `grokstyle_krea2_v2`: `strength_model` **0.6** y `strength_clip` **0.6**.
- VAE `qwen_image_vae.safetensors` (antes `Wan2_1_VAE_fp32`).

### `grok_edit` — particularidades (medidas en vivo el 2026-10-03)

Es el único flujo que **edita sobre Krea 2**, y tiene dos diferencias que importan:

1. **El prompt es una INSTRUCCIÓN, no una descripción.** Va a un `Krea2EditGroundedEncode`
   (input `prompt`), no a un `CLIPTextEncode`: el text encoder *ve* la imagen mientras lee la
   instrucción. Escribir "mujer en una playa" en vez de "cambia el fondo a una playa al
   atardecer" no da el resultado esperado. Las instrucciones conviene darlas en inglés.
2. **Necesita DOS LoRAs apiladas**, y esto se verificó empíricamente.
   En vivo (`grok_edit.json`): la de estilo va a `strength_model` **0.6**; la de identidad
   sigue en **1.0**; el VAE ya es `qwen_image_vae`.
   - `krea2/krea2_identity_edit_v1_2.safetensors` — es la que **habilita editar**. Sin ella el
     modelo ignora la instrucción y además produce artefactos.
   - `grokstyle_krea2_v2.safetensors` — aporta el look Grok Style.

Medición (misma foto, misma instrucción "Change her outfit to a red raincoat."):

| LoRAs | ¿Cumple la instrucción? | Calidad |
|---|---|---|
| solo `grokstyle` | **No** (mantiene la ropa original) | artefactos (motas) |
| solo `identity_edit` | Sí | limpia |
| `identity_edit` + `grokstyle` | Sí | limpia (+ gotas de lluvia coherentes) |

Costo: ~12–16 s con los modelos en caché, ~40 s en frío. Turbo, 10 pasos, CFG 1, target 9:16
@ 1 MP. Las *remociones* ("borra el fondo") requieren el modelo **Raw** a CFG 3 ~20 pasos, que
**no** está instalado — con Turbo no son fiables.

### `dirty_realism` / `dirty_edit` — el checkpoint Dirty (medido 2026-10-03)

- **En vivo**: `dirty_realism.json` — KSampler steps **8** (antes 20), cfg **1**, euler, simple,
  denoise **1**. Checkpoint sigue `krea2SATDirtyRealism_uncut_fp8`. `dirty_edit.json` — steps
  **8** (antes 10); LoRA de identidad sigue en **1.0**.
- **Origen**: Civitai **2796522** / modelVersion **3372523** ("Krea2-SAT-DirtyUncut", autor
  Sateluco), fp8 de 12.24 GB. **La descarga exige token de Civitai** (401 sin él); la metadata
  de la API es pública y no lo necesita.
- Es un **checkpoint (finetune), no una LoRA**: *reemplaza* el UNET, no se apila sobre él.
- **Drop-in verificado**: 942 tensores, todos bajo `model.diffusion_model.*` con tensores
  `.weight_scale` (fp8 escalado) y **sin** VAE ni CLIP embebidos → carga en el mismo
  `UNETLoader`. (El `krea2_turbo_fp8_scaled` de Comfy-Org trae los nombres **sin** ese prefijo;
  ComfyUI acepta ambas convenciones.)
- **Tolerante al muestreo**: probado a 8, 10 y 20 pasos con CFG 1, y a 20 pasos con CFG 3 — los
  cuatro salen bien. A diferencia de lo que el README de krea2edit dice del modelo *Raw*, **este
  funciona a CFG 1**, así que el camino rápido está disponible.
- **La LoRA de identidad transfiere al finetune**: verificado que `krea2_identity_edit_v1_2`
  sigue cumpliendo la instrucción sobre esta base (impermeable rojo, identidad intacta). Por eso
  `dirty_edit` es viable.
- **Ninguno de los dos lleva LoRA de estilo**: el look lo aporta el checkpoint; apilar
  `grokstyle` competiría con él.
- **Alternativa medida**: CFG 3 + 20 pasos da algo más de detalle fotográfico a ~3× el tiempo
  (20 s vs 12 s en txt2img; 44 s vs 16 s en edición). Es cambiar `steps`/`cfg` en el template.

### Flujos rotos — por qué

**`donut_face`** — le faltan **16 clases de nodo**. Mapeadas a su pack con la DB de
ComfyUI-Manager (`extension-node-map.json`):

| Nodo(s) | Pack |
|---|---|
| `SAMLoader` | `ComfyUI-Impact-Pack` |
| `UltralyticsDetectorProvider` | `ComfyUI-Impact-Subpack` |
| `Anything Everywhere` | `cg-use-everywhere` |
| `CR Text Concatenate` | `ComfyUI_Comfyroll_CustomNodes` |
| `Image Save` | `was-node-suite-comfyui` |
| `Seed String`, `Wildcard Processor` | `mikey_nodes` |
| `SeedGenerator` | `RES4LYF` |
| `BlehSetSamplerPreset` | `ComfyUI-bleh` |
| `DF_Text`, `DonutApplyLoRAStack`, `DonutFaceDetailer`, `DonutKrea2FusionControl`, `DonutLoRAStack`, `DonutSampler`, `DonutTiledUpscale` | **no están en la DB del Manager** |

Esos 7 `Donut*` + `DF_Text` son el **pack privado del owner**: no está en el registry de ComfyUI
ni en GitHub → **no es restaurable desde cero**. Los otros 8 sí son instalables (son 7 packs
públicos).

> `Krea2NormalizedAttentionGuidance` **ya no falta**: lo aporta `ComfyUI-Krea2-NAG`, instalado el
> 2026-10-03 (§4). La corrección de esa creencia previa es lo que bajó el conteo de 17 a 16.

De los 2 pesos que faltaban, **ninguno falta ya** (2026-10-03):
`loras/krea2/krea2_identity_edit_v1_2.safetensors` se descargó (§5) y
`vae/qwen-image/qwen_image_vae.safetensors` se resolvió con un **symlink relativo** a
`vae/qwen_image_vae.safetensors` (el VAE ya estaba; el grafo lo buscaba en la subcarpeta).
Verificado: ComfyUI lo lista en `VAELoader`.

Con eso, `donut_face` queda bloqueado **solo** por los 16 nodos — 9 de ellos instalables desde
7 packs públicos, y 7 + `DF_Text` del pack privado del owner.

**`wan_i2v`** — le faltan 3 pesos: `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`,
`text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`, `vae/wan2.2_vae.safetensors`.

> El bot **no falla ruidosamente** por esto: si no puede cargar el workflow remoto cae al fallback
> embebido y, si el grafo es inválido, el error solo aparece al encolar en ComfyUI.

### Flujos que exigen foto (`requires_source`)

Los 4 flujos con `supports_source: true` declaran además **`requires_source: true`** en su `_meta`
(`agil_edit_qwen`, `agil_edit_nsfw`, `grok_edit`, `wan_i2v`). El provider **rechaza** el request
con un mensaje claro si llega sin foto.

Sin esa marca el grafo correría con el **placeholder horneado** — los cuatro apuntan a
`example.png`, que existe en el `input/` del box — y devolvería un resultado incorrecto **en
silencio**. Es un flag independiente de `supports_source` a propósito: un flujo futuro podría
aceptar foto *opcionalmente* sin exigirla.

> Al cambiar cualquiera de estos dos flags hay que **desplegar el `_meta` al box**: en runtime el
> flujo se lee del `api_workflows/` remoto, no del template del repo.

## 7. Gotchas operativos

1. **Los `api_workflows` del box se nombran por `_meta.id`, no por el nombre del template del
   repo.** Ejemplo vivo: `templates/krea2_t2i.json` tiene `_meta.id = grok_style` → en el box es
   `grok_style.json`. Si el nombre no coincide, el bot loguea `SSH fetch workflow … rc=1` y **cae
   al fallback embed sin fallar** (parece funcionar y en realidad ignora el box). El journal sano
   dice `Loaded ComfyUI workflow <id> from remote`.
2. **`/object_info/<nodo>` devuelve HTTP 200 con `{}`** para un nodo inexistente. Para saber si un
   nodo existe de verdad hay que mirar si el JSON tiene contenido (o el listado completo).
3. El `_meta` (top-level y por nodo) **nunca** viaja a ComfyUI: `resolver.py` lo separa antes de
   encolar. Enviarlo crudo da `missing_node_type: Node 'ID #_meta' has no class_type`.
4. En SSH no interactivo `python`/`pip` no existen: hay que `source /venv/main/bin/activate` o usar
   `/venv/main/bin/python`.
5. Tras instalar un custom node, **reiniciar ComfyUI** (§2).
6. **Nunca purgar `nvidia-*-cu12` a ciegas (ni desinstalar paquetes `nvidia-*` en general).**
   Varios paquetes cu12 y cu13 comparten **la misma ruta de instalación** en el namespace
   `nvidia/` y solo se diferencian por la metadata: `nvidia-cusparselt-cu12` y `-cu13` instalan
   ambos en `nvidia/cusparselt/lib/`; ídem `nvidia-nccl-*` (`nvidia/nccl/`) y `nvidia-nvshmem-*`
   (`nvidia/nvshmem/`). Al desinstalar la cu12, uv **borra ficheros que también pertenecen a la
   cu13** y reescribe su `RECORD`, dejando el paquete cu13 "instalado" según pip pero **sin sus
   `.so`**. Síntoma: `ImportError: libcusparseLt.so.0: cannot open shared object file` al importar
   torch (ComfyUI queda en `FATAL`). `uv pip check` **no lo detecta** (valida metadata, no
   enlazado dinámico).

   Afectados en la purga del 2026-10-03 y reparados con
   `uv pip install --no-cache-dir --reinstall nvidia-cudnn-cu13 nvidia-cusparselt-cu13 nvidia-nccl-cu13 nvidia-nvshmem-cu13 --index-url https://download.pytorch.org/whl/cu130`:
   `cudnn`, `cusparselt`, `nccl`, `nvshmem`.

   Para auditar qué paquetes `nvidia_*` perdieron ficheros, comparar su `RECORD` con el disco:

   ```bash
   /venv/main/bin/python - <<'PY'
   import glob, os
   SP = "/venv/main/lib/python3.12/site-packages"
   for rec in sorted(glob.glob(SP + "/nvidia_*.dist-info/RECORD")):
       name = os.path.basename(os.path.dirname(rec)).replace(".dist-info", "")
       miss = [l.split(",")[0] for l in open(rec)
               if l.split(",")[0].startswith("nvidia/")
               and not os.path.exists(os.path.join(SP, l.split(",")[0].strip()))]
       if miss: print("FALTAN:", name, len(miss))
   PY
   ```

   Regla práctica: si hay que tocar paquetes `nvidia-*`, **reinstalar explícitamente** el
   conjunto cu13 en la misma operación y verificar con el `ldd` de §3 antes de dar por bueno el
   cambio.

## 8. Verificación — qué comprobar exactamente

### 8.1 Cambios que tocan el **bot** (provider, resolver, `_meta` de un flujo)

El bot corre en la máquina del repo como **servicio de usuario systemd** (`grok-bot.service`,
gestionado por el shell `grokbot`). **No recarga código solo: hay que reiniciarlo.**

```bash
grokbot status          # estado + pid
grokbot restart         # ← obligatorio tras cambiar código del bot
grokbot logs            # journal en vivo (ctrl-c para salir)
```

Puntos a verificar, en orden:

1. **Suite completa en verde** (no solo el módulo tocado — hay convenciones fijadas en tests
   ajenos): `.venv/bin/pytest -q`.
2. **Si cambió algún `_meta`: desplegar el JSON al box.** En runtime el flujo se lee del
   `api_workflows/` remoto, no del template del repo — si no se despliega, el cambio **no
   aplica** (y no falla: usa el valor viejo).
   ```bash
   scp -P <COMFYUI_PORT> templates/<id>.json \
       root@<COMFYUI_HOST>:/workspace/ComfyUI/user/default/api_workflows/<id>.json
   ```
   El archivo remoto debe llamarse por `_meta.id`, **no** por el nombre del template (§7.1).
3. **Reiniciar el bot** (`grokbot restart`) y confirmar en el journal que el flujo se carga
   desde el box — la línea sana es `Loaded ComfyUI workflow <id> from remote`. Si dice `rc=1`
   cayó al fallback embebido **sin fallar** (§7.1).
4. **Menú**: `/config` → ComfyUI debe listar el flujo por su nombre.
5. **Generación real**, no solo tests: mandar un prompt (y una foto, si es flujo de edición).

### 8.2 Cambios que tocan el **box** (paquetes, torch, custom nodes)

1. **Librerías dinámicas completas** — este chequeo es el que detecta purgas mal hechas (§7.6);
   `uv pip check` **no** sirve para esto:
   ```bash
   ldd /venv/main/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so | grep -c "not found"   # debe dar 0
   ```
2. **torch vivo**, no solo importable:
   ```bash
   /venv/main/bin/python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_capability())"
   ```
3. **ComfyUI arriba y con la versión esperada**:
   ```bash
   supervisorctl status comfyui
   curl -s http://127.0.0.1:18188/system_stats | /venv/main/bin/python -c "import sys,json;print(json.load(sys.stdin)['system']['pytorch_version'])"
   ```
4. **Sin imports fallidos** de custom nodes en el log:
   ```bash
   tail -200 /var/log/portal/comfyui.log | grep -iE "IMPORT FAILED|Failed to import|Traceback"
   ```
5. **Smoke real de un flujo** (§8.3) — un `system_stats` 200 no prueba que se pueda generar.

> Tras instalar o actualizar un **custom node** hay que **reiniciar ComfyUI** para que lo
> cargue, y después revalidar los flujos (§8.3).

### 8.3 Pesos y nodos de los flujos

Comprueba, por flujo, qué clases de nodo y qué pesos faltan realmente. **Ojo con la trampa
§7.2**: `/object_info/<nodo>` responde 200 con `{}` para un nodo inexistente, así que este
script compara contra el listado completo, no contra el HTTP status.

```bash
SSH="ssh -p <COMFYUI_PORT> root@<COMFYUI_HOST>"
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
    if miss_n or miss_m:
        print(f"{meta.get('id'):16} nodos_faltan={len(miss_n)} pesos_faltan={len(miss_m)} {miss_m}")
print("(los no listados están completos)")
PY
```

### 8.4 El guard de edición (`requires_source`)

Un flujo de edición debe **rechazar** el request sin foto; uno de txt2img debe **aceptarlo**.

- Cubierto por tests: `test_edit_flow_without_source_photo_raises` y
  `test_t2i_flow_without_source_photo_is_allowed` (provider) + el flag en el resolver.
- En vivo (bot): seleccionar un flujo de edición y mandar **solo texto** → debe responder
  *"Este flujo edita una foto: envía la imagen con un caption…"*, **nunca** devolver una imagen.
  Si devuelve una imagen, el `_meta` desplegado en el box no tiene `requires_source` (§8.1.2).
- El flag correcto se puede comprobar sin generar nada:
  ```bash
  $SSH '/venv/main/bin/python -c "
  import json,glob
  for f in sorted(glob.glob(\"/workspace/ComfyUI/user/default/api_workflows/*.json\")):
      m=json.load(open(f)).get(\"_meta\",{})
      print(\"  %-16s supports=%-5s requires=%s\" % (m.get(\"id\"),m.get(\"supports_source\"),m.get(\"requires_source\")))"'
  ```

## 9. Recrear el box desde cero (`scripts/provision_vast_box.sh`)

Script idempotente que corre **desde el repo** (necesita los templates y el `.env`) y maneja el
box por SSH. Volver a correrlo es un no-op, así que también sirve como reporte de estado.

```bash
scripts/provision_vast_box.sh                     # todo
scripts/provision_vast_box.sh --only verify       # solo reporta, no cambia nada
scripts/provision_vast_box.sh --skip models       # sin las descargas grandes
scripts/provision_vast_box.sh --only env,links

CIVITAI_TOKEN=... scripts/provision_vast_box.sh   # requerido para las descargas de Civitai
```

Fases: `preflight env nodes models links workflows restart verify`.

**Automatizado** (con origen verificado; los secretos viajan en un archivo 0600, nunca en `argv`):

| Fase | Qué hace |
|---|---|
| `preflight` | SO, GPU, driver (avisa si < 580.65), disco, presencia de ComfyUI y del venv |
| `env` | Instala/verifica el stack torch **2.11.0+cu130** y chequea `ldd` sin faltantes |
| `nodes` | Clona `ComfyUI-Krea2-NAG` (0afb38d) y `comfyui-krea2edit` (86f886d) |
| `models` | 8 pesos: 3 de `Comfy-Org/Krea-2`, Moody (`catlover1937`), la LoRA de identidad (`conradlocke`), el VAE de Wan (`Comfy-Org/Wan_2.1_ComfyUI_repackaged`) y 2 de **Civitai** (grokstyle 3278913, DirtyRealism 3372523) |
| `links` | Symlink `vae/qwen-image/` y hardlink `Wan2_1_VAE_fp32` |
| `workflows` | Despliega los 12 templates a `api_workflows/`, **nombrados por `_meta.id`** (§7.1) |
| `restart` | Reinicia ComfyUI por supervisor y espera el 200 |
| `verify` | Los chequeos de §8 + el estado de cada flujo + los pesos manuales |

**Manual (no automatizable hoy):**

1. **La imagen base del box.** El script **asume** una instancia Vast con la imagen de ComfyUI: el
   árbol `/workspace/ComfyUI`, el venv `/venv/main`, el servicio supervisor `comfyui` en el puerto
   18188, el portal, `comfy-cli` y los packs que trae la imagen (Manager, GGUF, rgdownloader,
   rgthree). Nada de eso lo crea este script.
2. **8 pesos sin origen verificado** (el script los reporta al final como `FALTA`/`ok`):
   `qwen-image-edit-2511-Q4_K_M.gguf`, `qwen_image_2.1_int8_convrot`, `qwen3vl_8b_int8_convrot`,
   `qwen_2.5_vl_7b_fp8_scaled`, `qwen_image_2.1_vae_bf16`, `Qwen-Rapid-AIO-NSFW-v23`,
   `Qwen-Image-Edit-2511-Lightning-4steps`, `Krea2NSFWV4`. Son los que alimentan los flujos
   Ágil Edit / Ágil Edit NSFW / Qwen 2.1. **Si alguien los re-descarga, anotar la URL en la
   tabla de §5 y agregarlos al manifiesto del script.**
3. **Los secretos**: `CIVITAI_TOKEN` (las descargas de Civitai dan 401 sin él) y `HF_TOKEN`
   (solo si algún repo de HF pasa a estar gated).
4. **Reiniciar el bot**: vive en la máquina del repo, no en el box → `grokbot restart` (§8.1).
5. **La decisión de configuración**: qué flujos quedan habilitados, con qué pasos/CFG y con qué
   resolución. El script despliega lo que haya en `templates/`; no juzga si está bien.

> El script **no** reinstala paquetes `nvidia-*` ni purga nada: la trampa de §7.6 (colisión de
> rutas cu12/cu13) sigue aplicando si alguien lo hace a mano.

## 10. Cómo refrescar este inventario

```bash
SSH="ssh -p <COMFYUI_PORT> root@<COMFYUI_HOST>"   # coordenadas del .env

# Versiones del stack
$SSH 'source /venv/main/bin/activate; python -V; pip list | grep -iE "^torch|^triton|^sageattention"'

# Custom nodes + commits
$SSH 'cd /workspace/ComfyUI/custom_nodes && for d in */; do d=${d%/};
      [ -d "$d/.git" ] && echo "$d $(git -C $d rev-parse --short HEAD)" || echo "$d (sin git)"; done'

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
