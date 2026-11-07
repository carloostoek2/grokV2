# Auditoría de flujos para Vast — RTX 5000 Ada (32 GB)

> Fecha: 2026-11-07. Auditoría **estática** (templates + `descargar_local.py`).
> Las mediciones en vivo del box quedan pendientes de la renta; se marcan como
> estimaciones. Objetivo: saber qué debe tener la instancia antes de alquilarla.

## Conclusión rápida

- **La VRAM de 32 GB alcanza para todos los flujos del menú**, con dos matices:
  - `agil_edit_qwen` / `agil_edit_hq` rozan el límite (~29 GB de pesos). En la
    práctica ComfyUI hará *offload* parcial; funcionan, pero más lentos que en la
    A100.
  - `agil_edit_hq` añade SeedVR2 al final. Si ComfyUI mantiene cargado el modelo
    de edición durante el upscale, el pico teórico supera los 32 GB → offload
    obligatorio.
- **Disco**: los mínimos declarados en `descargar_local.py` suman **>65 GB**;
  planificar **≥120 GB** para pesos + salidas + caché.
- **RAM del host**: ≥64 GB recomendable (el offload de ComfyUI usa RAM del
  sistema; con menos, el *fallback* es disco y se vuelve muy lento).
- **Bloqueante de assets**: `descargar_local.py` no descarga todavía
  `Moody-Krea-Mix-v7`, `Krea2-realism-V2` (los usa `agil_moody_hq`) ni los pesos
  de `face_detail_impact` (botón «Detalle cara»). Ver §3.

## 1. Flujos activos y pesos

Tamaños: los medidos en `_meta.source` cuando existen; si no, el mínimo
declarado por el descargador (estimación conservadora).

| flujo | template | UNET / checkpoint | TE | VAE | extras | VRAM estimada | ¿32 GB? |
|---|---|---|---|---|---|---|---|
| `grok_style` | `krea2_t2i.json` | `krea2_turbo_fp8_scaled` 12,24 GB | `qwen3vl_4b_fp8_scaled` 4,88 GB | `qwen_image_vae` 0,25 GB | LoRA `grokstyle_krea2_v2` | ~18 GB | ✅ |
| `agil_solo` | `agil_solo.json` | ídem | ídem | ídem | — | ~17,4 GB | ✅ |
| `agil_nsfw` | `agil_nsfw.json` | ídem | ídem | ídem | LoRA `Krea2NSFWV4` | ~18 GB | ✅ |
| `grok_edit` | `grok_edit.json` | ídem | ídem | ídem | `krea2_identity_edit_v1_2` + `grokstyle` | ~19,5 GB | ✅ |
| `agil_moody` | `agil_moody.json` | `Moody-Krea-Mix-v4.1G` nvfp4 | `qwen3vl_4b_fp8_scaled` | `qwen_image_vae` | — | ~12-18 GB | ✅ |
| `agil_moody_hq` | `agil_moody_hq.json` | `Moody-Krea-Mix-v7` fp8 | ídem | ídem | `Krea2-realism-V2` + `Krea2NSFWV4` | ~19 GB | ✅ (faltan assets) |
| `agil_edit_qwen` | `agil_edit_qwen.json` | `qwen_image_edit_2511_fp8mixed` 20 GB | `qwen_2.5_vl_7b_fp8_scaled` 8 GB | `qwen_image_vae` | Lightning bf16 | ~29 GB | ⚠️ justo |
| `agil_edit_hq` | `agil_edit_hq.json` | ídem | ídem | ídem | ídem + SeedVR2 8 GB | ~29 GB, pico ~37 GB | ⚠️ offload |
| `agil_edit_nsfw` | `agil_edit_nsfw.json` | `Qwen-Rapid-AIO-NSFW-v23` 25 GB (checkpoint) | — | — | — | ~25 GB | ✅ |
| `grok_style_hq` | `grok_style_hq.json` | `krea2_turbo_fp8_scaled` | `qwen3vl_4b_fp8_scaled` | `qwen_image_vae` | `grokstyle` | ~18 GB | ✅ |
| `agil_solo_hq` | `agil_solo_hq.json` | ídem | ídem | ídem | — | ~17,4 GB | ✅ |
| `agil_nsfw_hq` | `agil_nsfw_hq.json` | ídem | ídem | ídem | `Krea2NSFWV4` | ~18 GB | ✅ |
| `grok_edit_hq` | `grok_edit_hq.json` | ídem | ídem | ídem | `krea2_identity_edit_v1_2` + `grokstyle` | ~19,5 GB | ✅ |

Flujos registrados pero **ocultos** en `/config` (no entran a la decisión de
renta, pero siguen siendo ids válidos): `donut_face`, `wan_i2v`, `qwen21_t2i`,
`flux1_dev_t2i`, `minimax_h3_i2v` (sin template), `face_detail_impact`,
`instantid_sdxl`, `ohwx_krea2`, `ohwx_edit`.

## 2. Un solo archivo por peso

Set canónico Krea (compartido por 13 flujos):

```
diffusion_models/krea2_turbo_fp8_scaled.safetensors
text_encoders/qwen3vl_4b_fp8_scaled.safetensors
vae/qwen_image_vae.safetensors
```

- `agil_moody` / `agil_moody_hq` usan su propio UNET Moody y **comparten el TE**
  con el set Krea.
- `agil_edit_qwen` / `agil_edit_hq` usan la familia Qwen-Image-Edit (fp8mixed).
- `agil_edit_nsfw` usa el checkpoint Qwen Rapid AIO (sin UNET/TE/VAE separados).
- Alias del box: `models/unet/*` y `models/clip/*` son symlinks a
  `diffusion_models/` y `text_encoders/`; no duplicar archivos.
- Test que fija el contrato:
  `tests/unit/providers/test_workflow_resolver.py::test_krea2_model_files_are_shared_fp8_across_flows`.

## 3. Assets que faltan en `descargar_local.py`

| flujo | falta | impacto |
|---|---|---|
| `agil_moody_hq` | `Moody-Krea-Mix-v7_00002__clean_fp8`, `Krea2-realism-V2` | flujo visible del menú fallaría |
| `face_detail_impact` | `sd_xl_base_1.0`, `4x-UltraSharp`, `face_yolov8m`, `sam_vit_b` | botón «Detalle cara» falla |
| `ohwx_krea2` / `ohwx_edit` | `ohwx_krea2` (LoRA) | flujos ocultos, no bloquean |
| `donut_face` | `4x_NickelbackFS`, `sam_vit_b`, `face_yolov8m`, `realism_engine_krea2_v1`, `Krea2_NSFW_Aesthetics_V1` | flujo oculto |
| `flux1_dev_t2i`, `qwen21_t2i`, `wan_i2v`, `instantid_sdxl` | pesos propios | legacy/ocultos |

Recomendación: añadir los dos primeros bloques al descargador antes de rentar;
el resto puede quedar fuera mientras no se usen.

## 4. Nodos personalizados requeridos

Fijados por `descargar_local.py`:

- `ComfyUI-Manager`
- `ComfyUI-GGUF`
- `ComfyUI-Krea2-NAG` @ `0afb38d`
- `comfyui-krea2edit` @ `86f886d` (necesario para `grok_edit`, `ohwx_edit`,
  `grok_edit_hq`)
- `rgthree-comfy`
- `seedvr2_videoupscaler` (necesario para `agil_edit_hq`)

Extras instalables si se usan los flujos ocultos: Impact Pack, Impact Subpack,
Essentials, KJNodes.

## 5. Timeouts actuales (`_meta.timeout`)

| flujo | timeout | nota |
|---|---|---|
| `agil_edit_qwen` | 900 s | cold load ~8 min en A100; vigilar en 32 GB |
| `agil_edit_hq` | 1200 s | + etapa SeedVR2 |
| `agil_edit_nsfw` | 900 s | checkpoint de 25 GB |
| `donut_face` | 2400 s | pipeline largo |
| `face_detail_impact` | 600 s | |
| `instantid_sdxl` | 420 s | |
| `wan_i2v` | 1800 s | |
| resto | default del provider | |

Ajustar con lo medido en el box; el offload de 32 GB puede acercar el cold
start al límite de `agil_edit_qwen`.

## 6. Pendiente de medición en vivo

- [ ] VRAM pico por flujo (frío y caliente), con y sin offload.
- [ ] Tiempo por flujo en la 5000 Ada vs. A100.
- [ ] Confirmar que `agil_edit_hq` completa SeedVR2 sin OOM.
- [ ] Verificar `/object_info` tras instalar los nodos.
- [ ] Comprobar que cambiar de flujo Krea no recarga el UNET/TE.
