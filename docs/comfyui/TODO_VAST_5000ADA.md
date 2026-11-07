# TODO — Migración a Vast (RTX 5000 Ada, 32 GB)

> Estado: 2026-11-07. **Decisión revisada por el owner:** *todos los flujos van
> en fp8* y todos los que usan el mismo modelo apuntan al **mismo archivo**.
> Se descarta la restauración a bf16/fp16.

## Ejecutado en esta sesión

- [x] **Higiene y precisión**: los 21 templates activos usan fp8. El set Krea
      quedó unificado en `krea2_turbo_fp8_scaled` + `qwen3vl_4b_fp8_scaled` +
      `qwen_image_vae`, incluidos los `*_hq` y `grok_edit`.
- [x] **`agil_moody` / `agil_moody_hq`** conservan su UNET Moody propio y
      comparten el TE Krea.
- [x] **Dirty\* retirados temporalmente**: `dirty_realism`, `dirty_edit`,
      `dirty_realism_hq`, `dirty_edit_hq` y `ohwx_dirty_edit` fuera de
      `COMFYUI_FLOWS`; templates movidos a `workflows/templates/_disabled/`.
	  Una config guardada con esos ids cae al flujo default (`grok_style`).
- [x] **Tests actualizados**: contrato de archivo compartido en
      `test_workflow_resolver.py`, menú y cadenas sin Dirty*.
- [x] **Suite completa en verde**: 862 passed.
- [x] **Script de descargas** (`/home/ubuntu/comfy-colab/descargar_local.py`):
      carga automática de `secrets.env` (tokens de Civitai/HF, sin hardcodear),
      `--secrets PATH`, checkpoint Dirty fuera de la descarga y de los symlinks.
- [x] **Documentación**: `PRODUCT_STATUS.md` §2, `AVANCE_VAST_HTTP.md`,
      `DESCARGAS_LOCALES.md` y auditoría estática en
      `AUDITORIA_FLUJOS_5000ADA.md`.

## Pendiente (requiere el box)

- [ ] Renta la instancia (32 GB VRAM, ≥64 GB RAM, ≥120 GB disco) y documenta la
      ficha real en `INVENTARIO_BOX_VAST.md` o en un inventario nuevo.
- [ ] Añade al descargador los assets que faltan para el menú:
      `Moody-Krea-Mix-v7` + `Krea2-realism-V2` (`agil_moody_hq`) y los pesos de
      `face_detail_impact` (botón «Detalle cara»).
- [ ] Ejecuta `descargar_local.py --listar` y la descarga completa.
- [ ] Instala/verifica los custom nodes y sus commits.
- [ ] Genera una imagen por flujo habilitado, en frío y en caliente; registra
      VRAM pico, offload y tiempo.
- [ ] Ajusta `_meta.timeout` con lo medido.
- [ ] Verifica end-to-end desde Telegram (`COMFYUI_WORKFLOW_SOURCE` y túnel SSH).

## Validation of done

- [x] Un solo archivo por peso en todos los flujos Krea.
- [x] Sin Dirty* en catálogo, menú, cadenas ni descargas.
- [x] Descargas con tokens desde `secrets.env`.
- [x] Suite completa en verde (862).
- [ ] Generación real verificada en la RTX 5000 Ada (pendiente de renta).
