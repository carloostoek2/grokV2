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
| `min_vram_gb` | opcional: `flow_id` → VRAM mínima (GB) para ofrecer ese flujo dentro del perfil |

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
| `l4` | L4 24 GB (y cualquier tarjeta de 20–39 GB) | fp8 12,24 + 4,88 GB | GGUF Q4_K_M 11,96 GB | 12 con menos de 28 GB de VRAM; 13 con 28 GB o más (`agil_edit_nsfw`) |
| `t4` | T4 16 GB | GGUF Q4_K_M 6,97 GB | no | 4: `grok_style`, `agil_solo`, `agil_nsfw`, `grok_edit` |

`face_detail_impact` («Detalle cara») queda **fuera de todos los perfiles**: la función
está retirada (arquitectura SDXL/Impact antigua) y no se descargan sus pesos. Por eso el
botón «Detalle cara» ya no aparece bajo ningún resultado.

Desde el 2026-11-11 (`237b0fc`), `l4` incluye `agil_edit_nsfw` («Ágil Edit NSFW») y
descarga su checkpoint `checkpoints/Qwen-Rapid-AIO-NSFW-v23.safetensors`. Ese archivo
ocupa **como mínimo 25 GB de disco** (`min_size` del manifiesto es el tamaño del
archivo descargado, no la VRAM). Medido en Vast: 40 s en frío, 15 s en caliente y
**27,6 GB de VRAM**, más de los 24 GB de una L4.

Como `profile_for_device()` asigna `l4` a toda tarjeta de 20–39 GB, el flujo queda
condicionado por VRAM: `l4.json` declara `"min_vram_gb": {"agil_edit_nsfw": 28}`. El
bot solo lo ofrece (menú, botón «Editar» y resolver) si `/system_stats` reporta 28 GB
o más; en una L4 de 24 GB, o con VRAM desconocida (por ejemplo `COMFYUI_PERFIL=l4`
forzado), queda oculto y un ajuste guardado con ese flujo cae al `default_flow`. El
checkpoint se sigue descargando en todo `l4`, así que una L4 necesita el disco aunque
no use el flujo.

**Pendiente**: medir `agil_edit_nsfw` en una L4 real de 24 GB. Si cabe (por ejemplo,
con descarga parcial a RAM y un tiempo aceptable), baja el valor de `min_vram_gb` a
la VRAM que reporta la L4 (~22 GB) o quita la entrada para ofrecerlo en todo `l4`, y
actualiza el conteo fijado en `tests/unit/providers/test_workflow_profiles.py`.

## Detección

- **Cuaderno**: la celda 5 consulta `torch.cuda.get_device_name()` y la VRAM.
  La GPU real manda; `PERFIL_PESOS` (que pasa `arrancar_comfy.sh` según el
  `TIPO` pedido) solo es una pista si no hay GPU detectable. La celda escribe
  `DATOS/.perfil_activo` para que el lanzador sincronice los flujos correctos.
- **Bot**: `ComfyUIProvider.refresh_profile()` consulta `/system_stats` con un
  túnel ya activo (nunca abre uno), mapea GPU → perfil y refresca cada 5 min o
  al abrir la pantalla de configuración. `COMFYUI_PERFIL` permite forzar
  `a100|l4|t4`; `auto` (default) detecta. Con la VRAM detectada se quitan del
  perfil los flujos cuyo `min_vram_gb` no alcanza; con un perfil forzado la VRAM
  es desconocida y esos flujos se ocultan.
- **Resolver**: `get_flow()` devuelve `None` para un flujo fuera del perfil; el
  provider cae al `default_flow` del perfil. El menú de Telegram se filtra con
  `active_flow_ids()`.

## Arranque en dos fases (CPU → GPU)

Desde 2026-11-10, `arrancar_comfy.sh` separa la preparación de la ejecución:

1. **Fase CPU** (`<sesión>-prep`): runtime sin GPU. Monta Drive y ejecuta
   `preparar_modelos.py` — el mismo código de la celda 5 con
   `MODO_PREPARACION=1`, así que el perfil sale de `PERFIL_PESOS` y no importa
   `torch`. Si no falta ningún peso **no instala `aria2`**; si falta alguno, lo
   instala y descarga solo esos. La descarga ocurre con CPU, sin unidades GPU.
2. **Fase GPU**: solo si la fase CPU imprimió `PREPARACION_COMPLETA` se crea la
   sesión de la tarjeta y se ejecuta el cuaderno. Si quedó
   `PREPARACION_INCOMPLETA` (Drive, token de Civitai, descarga fallida) el
   lanzador aborta antes de gastar GPU; `--sin-preparar` fuerza el arranque
   antiguo.

La celda 5 sigue verificando los pesos en la GPU como red de seguridad (si
todo está presente son solo `os.path.getsize`, segundos). Con la sesión GPU ya
activa no se repite la fase CPU; `--preparar` la reinicia para re-verificar.

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
