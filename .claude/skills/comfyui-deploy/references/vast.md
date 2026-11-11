# Despliegue en vast.ai

> **Estado:** Vast es la receta de respaldo. El runtime diario del bot es Google Colab
> (ver `references/colab.md`). Las coordenadas de una instancia Vast —IP, puerto SSH, GPU,
> driver, disco— **rotan al recrearla** y viven en `.env`, nunca en documentación.
>
> Esta guía conserva las dos vías: provisión rápida sobre una imagen de ComfyUI y
> reconstrucción completa desde el repo.

## 1. Dos caminos

| Camino | Script | Cuándo usarlo |
|---|---|---|
| **Provisión rápida** | `~/comfy-colab/provision_vast.sh` | La instancia ya trae imagen de ComfyUI (`/workspace/ComfyUI`, `/venv/main`, supervisor en 18188); solo faltan modelos, nodos y Manager |
| **Reconstrucción completa** | `/home/ubuntu/repos/grokV2/scripts/provision_vast_box.sh` | Hay que dejar igual el stack de torch, los custom nodes, los pesos y los workflows |

No confundir los descargadores:

| Script | Dónde corre | Qué hace |
|---|---|---|
| `~/comfy-colab/descargar_local.py` | Vast o cualquier ComfyUI existente | Descarga pesos y clona nodos leyendo los perfiles del repo |
| `~/comfy-colab/preparar_modelos.py` | **Solo Colab** (fase CPU) | **Generado** por `actualizar_cuaderno.py`; no usarlo en Vast |

## 2. Camino rápido: `provision_vast.sh`

### 2.1 Requisitos

En la instancia Vast:

- Imagen con ComfyUI: `/workspace/ComfyUI/main.py`, `/venv/main/bin/python`.
- Servicio supervisor `comfyui` escuchando en `127.0.0.1:18188`.
- Acceso SSH como `root` con clave (sin contraseña interactiva).

En la máquina local (`/home/ubuntu/comfy-colab`):

- `descargar_local.py`
- `secrets.env` con `CIVITAI_TOKEN` y `HF_TOKEN` (permisos 600)
- `DESCARGAS_LOCALES.md`
- `sitecustomize.py`

### 2.2 Uso

```bash
cd /home/ubuntu/comfy-colab

# Inventario + copia de archivos + ComfyUI-Manager + lista de descargas
./provision_vast.sh <HOST> <SSH_PORT>

# Además descarga modelos y nodos en segundo plano
./provision_vast.sh <HOST> <SSH_PORT> --download

# Seguir la descarga
ssh -p <SSH_PORT> root@<HOST> 'tail -f /root/descarga.log'
```

### 2.3 Qué hace, paso a paso

1. **Inventario**: `nvidia-smi` (nombre y VRAM), `df -h /workspace`, y verifica que exista
   `/workspace/ComfyUI/main.py`. Si no existe, aborta.
2. **Copia a `/root/`**: `descargar_local.py`, `secrets.env`, `DESCARGAS_LOCALES.md`,
   `sitecustomize.py`.
3. **Shim**: `chmod 600 /root/secrets.env`; copia `sitecustomize.py` al `purelib` del venv
   (`/venv/main/lib/python3.x/site-packages/sitecustomize.py`) y verifica que
   `os.memfd_create` exista. Es necesario para ComfyUI 0.43+ en builds de Python que no
   exponen `memfd_create`.
4. **ComfyUI-Manager**: `pip install -U --pre comfyui-manager` con `/venv/main/bin/pip`.
5. **Habilitar Manager**: añade `--enable-manager` a `COMFYUI_ARGS` en
   `/etc/supervisor/conf.d/comfyui.conf` (y a `/etc/environment` si aplica), luego
   `supervisorctl reread` + `update comfyui`.
6. **Descargas**:
   - Con `--download`: `setsid nohup python3 -u descargar_local.py /workspace/ComfyUI
     --python /venv/main/bin/python > /root/descarga.log 2>&1 &`.
   - Sin `--download`: `descargar_local.py ... --listar` (no descarga).

### 2.4 `descargar_local.py`

Descarga pesos en `ComfyUI/models/` y clona nodos en `ComfyUI/custom_nodes/`. **No**
instala ComfyUI, PyTorch ni arranca el servidor.

```bash
# Listar sin descargar
python3 descargar_local.py /workspace/ComfyUI --listar

# Perfil explícito
python3 descargar_local.py /workspace/ComfyUI --perfil t4

# Perfil detectado con nvidia-smi
python3 descargar_local.py /workspace/ComfyUI --perfil auto

# Python del entorno de ComfyUI (ahí se instalan los requirements de los nodos)
python3 descargar_local.py /workspace/ComfyUI --python /venv/main/bin/python

# Solo modelos o solo nodos
python3 descargar_local.py /workspace/ComfyUI --solo-modelos
python3 descargar_local.py /workspace/ComfyUI --solo-nodos

# Perfiles desde otra copia del repo
python3 descargar_local.py /workspace/ComfyUI --perfiles /ruta/a/profiles.py

# Otro archivo de secretos
python3 descargar_local.py /workspace/ComfyUI --secrets /ruta/secrets.env
```

Comportamiento:

- Lee `profiles.py` del repo grokV2 por defecto; los perfiles son la fuente única de pesos,
  URLs y sustituciones.
- Carga `secrets.env` del mismo directorio si existe; las variables ya exportadas tienen
  prioridad.
- Guarda las descargas con extensión `.download`; si se interrumpe, vuelve a empezar.
- Conserva archivos existentes que alcanzan el tamaño mínimo; no verifica hash.
- No borra modelos.
- Aplica el parche `krea2` a `ComfyUI-GGUF` cuando el perfil es `t4`.

Al terminar una descarga de nodos, **reiniciar ComfyUI** para que los cargue:

```bash
ssh -p <SSH_PORT> root@<HOST> 'supervisorctl restart comfyui'
```

## 3. Camino completo: `scripts/provision_vast_box.sh`

Corre **desde el repo del bot** (necesita `templates/` y `.env`) y maneja el box por SSH.
Es idempotente: volver a correrlo es un no-op y sirve como reporte de estado.

```bash
cd /home/ubuntu/repos/grokV2

scripts/provision_vast_box.sh                     # todo
scripts/provision_vast_box.sh --only verify       # solo reporta, no cambia nada
scripts/provision_vast_box.sh --skip models       # sin descargas grandes
scripts/provision_vast_box.sh --only env,links
scripts/provision_vast_box.sh --host <HOST> --port <PORT>

CIVITAI_TOKEN=... scripts/provision_vast_box.sh   # necesario para Civitai
```

Lee `COMFYUI_HOST` y `COMFYUI_PORT` de `.env` si no se pasan por parámetro.

### 3.1 Fases

| Fase | Qué hace |
|---|---|
| `preflight` | SO, GPU, driver, disco, presencia de ComfyUI y del venv. Avisa si el driver es < 580.65 |
| `env` | Instala/verifica torch `2.11.0+cu130` (torchvision/torchaudio `0.26.0+cu130`) y comprueba `ldd` sin faltantes |
| `nodes` | Clona `ComfyUI-Krea2-NAG` (`0afb38d`) y `comfyui-krea2edit` (`86f886d`) |
| `models` | Descarga los pesos con origen verificado del manifiesto (tokens en un archivo 0600, nunca en `argv`) |
| `links` | Symlink `vae/qwen-image/qwen_image_vae.safetensors` y hardlink `Wan2_1_VAE_fp32` |
| `workflows` | Copia los templates a `api_workflows/`, **nombrados por `_meta.id`** |
| `restart` | `supervisorctl restart comfyui` y espera HTTP 200 en `/system_stats` |
| `verify` | Chequeos de stack, imports, flujos (nodos/pesos faltantes) y pesos manuales |

### 3.2 Qué queda manual

1. **La imagen base del box.** El script asume una instancia Vast con la imagen de ComfyUI:
   `/workspace/ComfyUI`, `/venv/main`, supervisor `comfyui` en 18188, portal, `comfy-cli` y
   los packs base (Manager, GGUF, rgthree, ...). Nada de eso lo crea el script.
2. **Pesos sin origen verificado.** El manifiesto solo descarga los que tienen URL
   comprobada; el resto se reporta como `FALTA`/`ok`. Lista histórica:
   `qwen-image-edit-2511-Q4_K_M.gguf`, `qwen_image_2.1_int8_convrot`,
   `qwen3vl_8b_int8_convrot`, `qwen_2.5_vl_7b_fp8_scaled`, `qwen_image_2.1_vae_bf16`,
   `Qwen-Rapid-AIO-NSFW-v23`, `Qwen-Image-Edit-2511-Lightning-4steps`, `Krea2NSFWV4`.
   Si se re-descarga alguno, anotar la URL en el manifiesto y en `INVENTARIO_BOX_VAST.md` §5.
3. **Secretos**: `CIVITAI_TOKEN` (Civitai da 401 sin él) y `HF_TOKEN` (solo repos gated).
4. **Reiniciar el bot**: vive en la máquina del repo, no en el box → `grokbot restart`.
5. **La decisión de flujos**: el script despliega lo que haya en `templates/`; no decide qué
   flujos quedan habilitados ni con qué parámetros.

> **Aviso de vigencia:** el manifiesto del script es histórico. Todavía incluye el checkpoint
> Dirty (`krea2SATDirtyRealism_uncut_fp8`), retirado del bot el 2026-11-07. Antes de correrlo,
> revisar el manifiesto o usar `--skip models` y descargar solo lo que el runtime actual
> necesita. Los templates Dirty viven en `workflows/templates/_disabled/` y no se despliegan.

## 4. Checklist para preparar una instancia Vast

1. **Crear la instancia** con la imagen de ComfyUI y VRAM acorde al perfil:
   - `a100` → 80 GB (A100/H100).
   - `l4` → 24 GB.
   - `t4` → 16 GB.
2. **Anotar** host y puerto SSH (rotan; no hardcodearlos en docs).
3. **Verificar** que la imagen trae lo esperado:

```bash
SSH="ssh -p <PORT> root@<HOST>"
$SSH 'nvidia-smi; df -h /workspace; test -f /workspace/ComfyUI/main.py && echo ComfyUI OK; \
      test -x /venv/main/bin/python && echo venv OK; supervisorctl status comfyui'
```

4. **Proveer** con una de las dos vías:

```bash
# Rápida (imagen ya lista)
cd /home/ubuntu/comfy-colab
./provision_vast.sh <HOST> <PORT> --download

# Completa (reconstruir stack/pesos/workflows)
cd /home/ubuntu/repos/grokV2
CIVITAI_TOKEN=... scripts/provision_vast_box.sh
```

5. **Verificar** ComfyUI, nodos y flujos (sección 7).
6. **Apuntar el bot** al box en `.env` (sección 5) y reiniciarlo.
7. **Elegir la fuente de flujos**: `remote` (desplegar a `api_workflows` por `_meta.id`) o
   `embed` (templates del repo). No mezclar sin querer: si `remote` no encuentra el archivo,
   cae a `embed` **sin fallar**.

## 5. Conexión con el bot en Vast

El bot abre un túnel local-forward y habla con `127.0.0.1:18188` dentro del box.

`.env` del bot:

```dotenv
COMFYUI_HOST=<HOST_O_IP>
COMFYUI_PORT=<PUERTO_SSH>
COMFYUI_REMOTE_PORT=18188
COMFYUI_TUNNEL_LOCAL_PORT=0
COMFYUI_WORKFLOW_SOURCE=remote
COMFYUI_WORKFLOWS_DIR=/workspace/ComfyUI/user/default/api_workflows
COMFYUI_WORKFLOW_CACHE_TTL=45
COMFYUI_PERFIL=auto
```

Equivalente manual al túnel que abre el provider:

```bash
ssh -p <PUERTO_SSH> -N \
  -L 127.0.0.1:18188:127.0.0.1:18188 \
  -o BatchMode=yes -o ExitOnForwardFailure=yes \
  root@<HOST_O_IP>
```

Después de cambiar `.env`:

```bash
grokbot restart
grokbot status
grokbot logs
```

Notas:

- El provider usa `BatchMode=yes`: la clave debe autenticar sin interacción.
- Si el puerto local ya está abierto, usa **modo directo** y no lanza otro SSH.
- Si se prefiere `COMFYUI_WORKFLOW_SOURCE=embed`, no hace falta desplegar templates al box;
  el bot usa `templates/` del repo y el box solo aporta GPU y pesos.

## 6. Rutas del runtime Vast

```
/workspace/ComfyUI/                                  # ComfyUI
/workspace/ComfyUI/main.py
/workspace/ComfyUI/models/                           # pesos
/workspace/ComfyUI/custom_nodes/
/workspace/ComfyUI/user/default/api_workflows/       # flujos, nombrados por _meta.id
/venv/main/bin/python                                # Python del entorno
/etc/supervisor/conf.d/comfyui.conf                  # servicio supervisor
/var/log/portal/comfyui.log                          # log
/root/descargar_local.py                             # dejado por provision_vast.sh
/root/secrets.env                                    # tokens 0600
/root/descarga.log                                   # log de descargas
```

Comando de arranque típico de la imagen (supervisor):

```
COMFYUI_ARGS = --disable-auto-launch --enable-cors-header --port 18188
```

Tras `provision_vast.sh` se añade `--enable-manager`.

## 7. Verificación

```bash
SSH="ssh -p <PORT> root@<HOST>"

# Stack
$SSH '/venv/main/bin/python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_capability())"'
$SSH 'ldd /venv/main/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so | grep -c "not found"'   # debe dar 0

# ComfyUI
$SSH 'supervisorctl status comfyui'
$SSH 'curl -s http://127.0.0.1:18188/system_stats'
$SSH 'tail -200 /var/log/portal/comfyui.log | grep -iE "IMPORT FAILED|Failed to import|Traceback"'

# Flujos vs nodos/pesos reales
$SSH '/venv/main/bin/python -' <<'PY'
import json, os, glob, urllib.request
oi = json.load(urllib.request.urlopen("http://127.0.0.1:18188/object_info", timeout=60))
KEYS = {"unet_name":"diffusion_models","ckpt_name":"checkpoints","clip_name":"text_encoders",
        "vae_name":"vae","lora_name":"loras"}
M = "/workspace/ComfyUI/models"
for f in sorted(glob.glob("/workspace/ComfyUI/user/default/api_workflows/*.json")):
    wf = json.load(open(f)); meta = wf.pop("_meta", {})
    mn = sorted({n["class_type"] for n in wf.values()
                 if isinstance(n, dict) and n.get("class_type") not in oi})
    mm = sorted({f"{KEYS[k]}={v}" for n in wf.values() if isinstance(n, dict)
                 for k, v in (n.get("inputs") or {}).items()
                 if k in KEYS and isinstance(v, str)
                 and not os.path.exists(os.path.join(M, KEYS[k], v))})
    print(f"{meta.get('id'):16} nodos_faltan={mn or '-'} pesos_faltan={mm or '-'}")
PY
```

## 8. Gotchas de Vast

1. **Puerto 18188**, no 8188. El 8188 histórico ya no existe en esta receta.
2. **`python`/`pip` no existen en SSH no interactivo**: usar `/venv/main/bin/python` o
   `source /venv/main/bin/activate`.
3. **Los workflows se llaman por `_meta.id`**, no por el nombre del template. Si no coincide,
   el bot registra `SSH fetch workflow ... rc=1` y **cae a embed sin fallar**; parece
   funcionar y en realidad ignora el box. El journal sano dice
   `Loaded ComfyUI workflow <id> from remote`.
4. **`_meta` nunca se envía a ComfyUI**: `resolver.py` lo separa antes de encolar. Enviarlo
   crudo da `missing_node_type: Node 'ID #_meta' has no class_type`.
5. **Tras instalar un custom node hay que reiniciar ComfyUI** (`supervisorctl restart
   comfyui`). El bot es otro proceso: reiniciarlo no recarga los nodos del box.
6. **No purgar paquetes `nvidia-*`.** Las variantes cu12 y cu13 comparten rutas
   (`nvidia/cusparselt/lib`, `nvidia/nccl`, `nvidia/nvshmem`) y desinstalar una borra `.so`
   de la otra. Síntoma: `ImportError: libcusparseLt.so.0`. `uv pip check` no lo detecta.
   Si hay que tocar el set, reinstalar cu13 completo y verificar con `ldd`.
7. **`/object_info/<nodo>` devuelve 200 con `{}`** para un nodo inexistente. Comparar contra
   el listado completo, no contra el status HTTP.
8. **Secretos**: viajan en archivo 0600, nunca en `argv` ni en la salida de la terminal.
9. **Coordenadas**: IP, puerto, GPU y driver cambian al recrear la instancia. Solo `.env`.
10. **`sitecustomize.py`**: si el Python del box no trae `os.memfd_create`, ComfyUI 0.43+
    falla al streamear safetensors. El shim debe quedar en el `site-packages` del venv.

## 9. Face Swap en Vast (despliegue aparte)

`~/comfy-colab/vast_faceswap/` contiene un despliegue independiente de FaceFusion 3.9.1 en
`/workspace/faceswap` (HyperSwap 1a 256 + GPEN-BFR-512). Comparte GPU con ComfyUI:

- Si ComfyUI tiene modelos cargados (~28 GB), FaceFusion se queda sin VRAM.
- `face_swap_lote.py --free-comfyui` llama a `http://localhost:18188/free` para descargar
  los modelos de ComfyUI antes de empezar; ComfyUI los recarga en el siguiente prompt.
- No forma parte del provider ComfyUI del bot. Se documenta aquí solo para no confundirlo
  con el despliegue principal.

Archivos: `setup_faceswap.sh`, `face_swap_lote.py`,
`face_swap_hyperswap_lotes_vastai.ipynb`, `README.md`.
