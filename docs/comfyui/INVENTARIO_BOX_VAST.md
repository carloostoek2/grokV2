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

## 5. Pesos instalados (~91 GB, bajo `/workspace/ComfyUI/models`)

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

## 6. Flujos (10 registrados en `COMFYUI_FLOWS`)

Los graphs viven en `/workspace/ComfyUI/user/default/api_workflows/{id}.json` (fuente de verdad en
runtime); el fallback embebido está en `src/grokbot/providers/comfyui/workflows/templates/`.

| id | Nombre UI | Media | Foto | Modelo principal | Sampler / steps / cfg | Resolución | Estado |
|---|---|---|---|---|---|---|---|
| `grok_style` | Grok Style | image | — | `krea2_turbo_fp8_scaled` + LoRA `grokstyle_krea2_v2` + VAE **Wan** | euler/simple · 3 · 1 | 2:3 @ 1.2 MP | ✅ |
| `agil_solo` | Ágil solo | image | — | `krea2_turbo_fp8_scaled` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_nsfw` | Ágil NSFW | image | — | `krea2_turbo_fp8_scaled` + LoRA `Krea2NSFWV4` | euler/simple · 8 · 1 | fija 896×1600 (1.43 MP) | ✅ |
| `agil_moody` | Ágil Moody | image | — | `Moody-Krea-Mix-…nvfp4` | euler_ancestral/beta · 8 · 1 | 9:16 @ 1.4 MP → 896×1600 | ✅ |
| `agil_edit_qwen` | Ágil Edit | image | ✔ | `qwen-image-edit-2511-Q4_K_M.gguf` + LoRA `…Lightning-4steps` | euler/simple · 4 · 1 | según foto | ✅ |
| `agil_edit_nsfw` | Ágil Edit NSFW | image | ✔ | `Qwen-Rapid-AIO-NSFW-v23` (checkpoint) | euler/simple · 4 · 1 | según foto | ✅ |
| `grok_edit` | Grok Style Edit | image | ✔ | `krea2_turbo_fp8_scaled` + LoRAs `krea2_identity_edit_v1_2` **y** `grokstyle_krea2_v2` | euler/simple · 10 · 1 | 9:16 @ 1.0 MP | ✅ |
| `qwen21_t2i` | Qwen 2.1 | image | — | `qwen_image_2.1_int8_convrot` | euler/simple · 25 · 1 | 2:3 @ 2.0 MP | ✅ |
| `donut_face` | Donut Face | image | — | `krea2_turbo_fp8_scaled` | — | 9:16 @ 1 MP | ❌ roto |
| `wan_i2v` | Wan I2V | video | ✔ | `wan2.2_ti2v_5B_fp16` | uni_pc/simple · 20 · 5 | — | ❌ roto |

Componentes compartidos: los flujos Krea2 (`grok_style`, `agil_solo`, `agil_nsfw`, `agil_moody`,
`donut_face`) usan `qwen3vl_4b_fp8_scaled` como text encoder y `qwen_image_vae` como VAE.
Excepciones: `qwen21_t2i` usa los `_int8_convrot` + `qwen_image_2.1_vae_bf16`; `agil_edit_qwen`
usa `qwen_2.5_vl_7b_fp8_scaled`; `agil_edit_nsfw` usa un **checkpoint todo-en-uno** sin encoder
ni VAE sueltos; `wan_i2v` usa `umt5_xxl` (faltante).

### `grok_edit` — particularidades (medidas en vivo el 2026-10-03)

Es el único flujo que **edita sobre Krea 2**, y tiene dos diferencias que importan:

1. **El prompt es una INSTRUCCIÓN, no una descripción.** Va a un `Krea2EditGroundedEncode`
   (input `prompt`), no a un `CLIPTextEncode`: el text encoder *ve* la imagen mientras lee la
   instrucción. Escribir "mujer en una playa" en vez de "cambia el fondo a una playa al
   atardecer" no da el resultado esperado. Las instrucciones conviene darlas en inglés.
2. **Necesita DOS LoRAs apiladas**, y esto se verificó empíricamente:
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
> embebido y, si el grafo es inválido, el error recién aparece al encolar en ComfyUI.

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

## 8. Cómo refrescar este inventario

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
