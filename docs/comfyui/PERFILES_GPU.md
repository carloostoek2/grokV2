# Perfiles de pesos por GPU

> Implementado el 2026-11-09. El cuaderno de Colab y el bot eligen el set de
> pesos según la tarjeta **real**, no según una lista fija.

## Por qué

Hasta 2026-11-08 el cuaderno descargaba siempre el mismo set (~106 GB) y el bot
usaba siempre las mismas plantillas. En una T4 (14,6 GB) eso no cabe: Krea fp8
son 17,4 GB, Qwen Edit 27,9 GB y Qwen Rapid 26,5 GB. El resultado era OOM o
paginación a RAM.

## Fuente única

`src/grokbot/providers/comfyui/workflows/profiles/{a100,l4,t4}.json`:

| Campo | Contenido |
|---|---|
| `flows` | ids visibles y resolubles en esa tarjeta |
| `default_flow` | fallback si el flujo guardado no existe en el perfil |
| `substitutions` | reemplazo exacto de nombre de peso sobre la plantilla base |
| `weights` | descargas: `path`, `url`, `auth`, `min_size` |
| `symlinks` | enlaces dentro de `models/` |
| `node_patches` | parches idempotentes a custom nodes |

Las plantillas base viven en `templates/` y no se duplican: un perfil solo
declara sustituciones. Si tras sustituir un `UNETLoader` el archivo acaba en
`.gguf`, `profiles.apply_profile()` cambia el nodo a `UnetLoaderGGUF` y quita
`weight_dtype`.

Consumidores del mismo JSON: el resolver del bot, `actualizar_cuaderno.py`
(celda 5) y `descargar_local.py --perfil`.

## Perfiles

| Perfil | Tarjeta | Krea | Qwen Edit | Flujos |
|---|---|---|---|---|
| `a100` | A100 / H100 | bf16 24,48 + 8,27 GB | fp8mixed 19,12 GB | 13 |
| `l4` | L4 24 GB | fp8 12,24 + 4,88 GB | GGUF Q4_K_M 11,96 GB | 12 (sin Rapid AIO) |
| `t4` | T4 16 GB | GGUF Q4_K_M 6,97 GB | no | 4: `grok_style`, `agil_solo`, `agil_nsfw`, `grok_edit` |

`face_detail_impact` («Detalle cara») queda **fuera de todos los perfiles**: la función
está retirada (arquitectura SDXL/Impact antigua) y no se descargan sus pesos. Por eso el
botón «Detalle cara» ya no aparece bajo ningún resultado.

## Detección

- **Cuaderno**: la celda 5 consulta `torch.cuda.get_device_name()` y la VRAM.
  La GPU real manda; `PERFIL_PESOS` (que pasa `arrancar_comfy.sh` según el
  `TIPO` pedido) solo es una pista si no hay GPU detectable. La celda escribe
  `DATOS/.perfil_activo` para que el lanzador sincronice los flujos correctos.
- **Bot**: `ComfyUIProvider.refresh_profile()` consulta `/system_stats` con un
  túnel ya activo (nunca abre uno), mapea GPU → perfil y refresca cada 5 min o
  al abrir la pantalla de configuración. `COMFYUI_PERFIL` permite forzar
  `a100|l4|t4`; `auto` (default) detecta.
- **Resolver**: `get_flow()` devuelve `None` para un flujo fuera del perfil; el
  provider cae al `default_flow` del perfil. El menú de Telegram se filtra con
  `active_flow_ids()`.

## Parche de ComfyUI-GGUF

`ComfyUI-GGUF` tiene una lista hardcodeada `IMG_ARCH_LIST` que no incluía
`krea2`. El perfil `t4` declara `node_patches` y la celda 7 (y
`descargar_local.py`) añaden `"krea2"` de forma idempotente tras clonar el
nodo. Sin ese parche el loader responde
`Unexpected architecture type in GGUF file: 'krea2'`.

## Medición real en T4 (2026-11-09)

| Flujo | Pesos | Resultado | Tiempo |
|---|---|---|---|
| `grok_edit` | Krea GGUF Q4 + TE fp8 | edición correcta, identidad conservada | 257 s en frío / 179 s en caliente |
| `agil_edit_qwen` | Qwen Edit GGUF Q4 + TE fp8 | edición correcta | 775 s (4,6 GB de UNET descargados a RAM) |

Qwen Edit en T4 funciona pero no es práctico; por eso `t4` no lo incluye. En L4
el mismo GGUF no necesita descargar a RAM y debería bajar a ~2–4 min.

## Cómo añadir o cambiar un peso

1. Edita el `weights` del perfil (ruta, URL, auth, mínimo) y, si aplica, la
   `substitutions`.
2. Ejecuta `actualizar_cuaderno.py --listar` y `--archivos-perfil <id>`.
3. Pasa `pytest tests/unit/providers/test_workflow_profiles.py`: el test cruzado
   falla si una plantilla pide un archivo que el perfil no declara.
