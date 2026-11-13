# Instalación limpia de ComfyUI en Vast

> **Cuándo usar este archivo.** La instancia Vast **no** trae ComfyUI: falta
> `/workspace/ComfyUI/main.py` o `/venv/main/bin/python`. Si la imagen ya trae ComfyUI, no
> uses esta ruta: sigue `references/vast.md` §0.4.
>
> **Colab no usa esta ruta.** Su despliegue CPU→GPU vive en `references/colab.md` y no
> cambia.

## 0. Confirmar que hace falta

```bash
SSH="ssh -o BatchMode=yes -p <SSH_PORT> root@<HOST>"

$SSH 'test -f /workspace/ComfyUI/main.py && test -x /venv/main/bin/python \
  && echo "imagen con ComfyUI" || echo "instalación limpia"'
```

| Resultado | Camino |
|---|---|
| `imagen con ComfyUI` | No usar este archivo: `references/vast.md` §0.4 |
| `instalación limpia` | Seguir aquí, en orden, antes de bajar pesos |

Antes de empezar, anotar del reporte de la fase 0 (`references/vast.md` §0.1): GPU/VRAM,
GB libres en `/workspace` y MB/s contra Hugging Face. Sin esos números no se elige perfil
ni se decide si el disco alcanza.

## 1. Disco y CUDA: las dos cuentas que no se pueden saltar

**Disco.** El stack de una instalación limpia ocupa **12–18 GB** adicionales a los pesos:
venv, torch CUDA (con sus librerías `nvidia-*`), código de ComfyUI, dependencias y nodos.
Sumarlo **antes** de aplicar las reglas de perfil de `references/vast.md` §0.2:

Si `/workspace` no existe, no instalar sobre `/` a ciegas: comprobar con `df -h` cuál es el
disco grande de la instancia y crear ahí el árbol (`/workspace` es el estándar de Vast).

| Perfil | Mínimo `agil_solo` + stack | Perfil completo + stack |
|---|---|---|
| `a100` | ≈ 51 GB | ≈ 138 GB |
| `l4` | ≈ 35 GB | ≈ 76 GB |
| `t4` | ≈ 30 GB | ≈ 32 GB |

Regla: libres ≥ (perfil + 18 GB) × 1,15. Si no alcanza, no se instala el perfil completo;
se queda en el mínimo y se reporta cuánto falta. Nunca dejar el disco al 100 %: los
`.download` parciales y los outputs necesitan margen.

**CUDA/driver.** El stack medido del repo es `torch 2.11.0+cu130` y exige driver
**≥ 580.65**. Comprobar antes de instalar:

```bash
$SSH 'nvidia-smi --query-gpu=driver_version --format=csv,noheader'
```

- Driver ≥ 580.65 → `cu130` (lo que usa el resto del repo).
- Driver anterior → **no** instalar `cu130`: usar `cu128`, que está verificado como
  alternativa en `docs/comfyui/INVENTARIO_BOX_VAST.md` §3. Anotar la desviación en el
  reporte del despliegue.
- Nunca instalar una wheel CUDA más nueva que el driver: el síntoma es
  `CUDA error: no kernel image is available for execution on the device`.

Si la instancia tiene un driver viejo y se quiere el stack exacto del repo, la salida
correcta es recrear la instancia en una máquina con driver ≥ 580.65, no forzar `cu130`.

## 2. Sistema base (apt)

La imagen limpia puede no traer `git`, `python3-venv`, `ffmpeg` ni las librerías que
necesitan algunos custom nodes. Instalarlas (idempotente):

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  git curl ca-certificates python3 python3-venv python3-dev \
  build-essential ffmpeg libgl1 libglib2.0-0 supervisor procps
rm -rf /var/lib/apt/lists/*
python3 -V
EOF
```

Notas:

- En Ubuntu 24.04 el paquete puede llamarse `libglib2.0-0t64`. Si `apt-get` no encuentra
  `libglib2.0-0`, ajustar el nombre a la distro; son dependencias de nodos con OpenCV.
- Si la imagen no tiene `apt-get` (raro en Vast), no es una base soportada por esta receta:
  recrear con una imagen Ubuntu/Debian o con la imagen de ComfyUI.
- `rm -rf /var/lib/apt/lists/*` ahorra cientos de MB en un disco que se comparte con los
  pesos.

## 3. venv en `/venv/main`

El resto de la skill, `provision_vast_box.sh` y `descargar_local.py` asumen
`/venv/main/bin/python`. Se crea ahí aunque la imagen tenga otro Python:

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
if [ ! -x /venv/main/bin/python ]; then
  python3 -m venv /venv/main
fi
/venv/main/bin/python -m pip install -q -U pip setuptools wheel uv
/venv/main/bin/python -V
EOF
```

`uv` queda instalado porque la fase `env` de `scripts/provision_vast_box.sh` lo usa; la
instalación de aquí usa `pip` para no depender de él.

El stack del repo está verificado con **Python 3.12** (el de la imagen Vast histórica y el de
Ubuntu 24.04). Si la distro trae 3.10/3.11 y `torch==2.11.0+cu130` no tiene wheel para esa
versión, no forzar el venv: usar una imagen base con Python 3.12.

## 4. Código de ComfyUI

Se instala la **última release estable** (la misma política que Colab) o la que se fije en
`COMFY_REF`. La versión exacta no es un contrato: el contrato es que `CLIPLoader` acepte
`krea2` (§10).

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
COMFY=/workspace/ComfyUI
COMFY_REF="${COMFY_REF:-}"
if [ -z "$COMFY_REF" ]; then
  COMFY_REF=$(git ls-remote --tags --refs https://github.com/comfyanonymous/ComfyUI.git \
    | awk -F/ '{print $NF}' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -V | tail -1)
fi
[ -n "$COMFY_REF" ] || COMFY_REF=master

if [ -f "$COMFY/main.py" ]; then
  echo "ComfyUI ya presente en $COMFY; no se toca"
else
  [ -e "$COMFY" ] && { echo "ERROR: $COMFY existe sin main.py; revisar a mano"; exit 1; }
  mkdir -p /workspace
  git clone -q https://github.com/comfyanonymous/ComfyUI.git "$COMFY"
  git -C "$COMFY" checkout -q "$COMFY_REF"
fi
git -C "$COMFY" log -1 --format='ComfyUI %h %d'
mkdir -p "$COMFY/user/default/api_workflows" "$COMFY/models"
EOF
```

Si se quiere congelar la versión, exportar `COMFY_REF=vX.Y.Z` antes del bloque. No editar
el código de ComfyUI a mano: los cambios se pierden al actualizar.

## 5. Dependencias Python (torch primero)

Orden obligatorio: **torch pineado → `requirements.txt` de ComfyUI → verificar que
requirements no cambió torch.** Si requirements se ejecuta antes, pip puede instalar un
torch CPU de PyPI y después hay que reemplazarlo igual.

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
PY=/venv/main/bin/python
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | tr -d ' ')

# ¿driver >= 580.65? (comparación de versión, no solo del major)
if [ "$(printf '%s\n' 580.65 "$DRIVER" | sort -V | head -1)" = "580.65" ]; then
  CU=cu130
else
  CU=cu128
  echo "AVISO: driver $DRIVER < 580.65; se instala cu128 en vez de cu130"
fi

$PY -m pip install --no-cache-dir \
  "torch==2.11.0+${CU}" "torchvision==0.26.0+${CU}" "torchaudio==2.11.0+${CU}" \
  --index-url "https://download.pytorch.org/whl/${CU}"

$PY -m pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# requirements.txt puede haber pisado torch si cambió el pin: dejar el stack medido.
$PY -m pip install --no-cache-dir --force-reinstall --no-deps \
  "torch==2.11.0+${CU}" "torchvision==0.26.0+${CU}" "torchaudio==2.11.0+${CU}" \
  --index-url "https://download.pytorch.org/whl/${CU}" >/dev/null

$PY - <<'PY'
import torch
print("torch:", torch.__version__, "| cuda:", torch.version.cuda,
      "| disponible:", torch.cuda.is_available(),
      "| cap:", torch.cuda.get_device_capability())
a = torch.randn(1024, 1024, device="cuda", dtype=torch.bfloat16)
print("matmul:", tuple((a @ a).shape))
PY
EOF
```

`--no-cache-dir` es deliberado: la caché de pip puede ocupar varios GB y en Vast el disco
se paga. Si `torch.cuda.is_available()` da `False`, no seguir: revisar driver y la wheel
instalada antes de bajar pesos.

## 6. Shim `memfd_create`

ComfyUI 0.43+ falla al streamear safetensors si el Python del entorno no expone
`os.memfd_create`. El shim vive en el `site-packages` del venv, no en `/root`:

```bash
# Desde la máquina local (repo de despliegue):
scp -P <SSH_PORT> /home/ubuntu/comfy-colab/sitecustomize.py root@<HOST>:/root/sitecustomize.py

$SSH 'bash -s' <<'EOF'
set -euo pipefail
PY=/venv/main/bin/python
if $PY -c 'import os; raise SystemExit(0 if hasattr(os, "memfd_create") else 1)'; then
  echo "memfd_create ya nativo; no hace falta el shim"
else
  PYLIB=$($PY -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  cp /root/sitecustomize.py "$PYLIB/sitecustomize.py"
  $PY -c 'import os; assert hasattr(os, "memfd_create"); print("memfd shim OK")'
fi
EOF
```

## 7. Descargador, secretos y perfiles

Copiar el descargador y los perfiles desde la máquina local. Los secretos son opcionales
para el mínimo (`agil_solo` usa solo HF sin token), pero hacen falta para Civitai en la
fase 2:

```bash
# Descargador (obligatorio)
scp -P <SSH_PORT> /home/ubuntu/comfy-colab/descargar_local.py root@<HOST>:/root/

# Secretos (opcional; nunca se imprimen)
if [ -f /home/ubuntu/comfy-colab/secrets.env ]; then
  scp -P <SSH_PORT> /home/ubuntu/comfy-colab/secrets.env root@<HOST>:/root/secrets.env
  $SSH 'chmod 600 /root/secrets.env'
fi

# Perfiles (obligatorios para la fase 2 y para el parche GGUF de t4)
cd /home/ubuntu/repos/grokV2
$SSH 'mkdir -p /root/comfy_profiles'
scp -P <SSH_PORT> src/grokbot/providers/comfyui/workflows/profiles.py \
  root@<HOST>:/root/comfy_profiles/
scp -P <SSH_PORT> -r src/grokbot/providers/comfyui/workflows/profiles \
  root@<HOST>:/root/comfy_profiles/
```

## 8. Custom nodes

Una sola vía: el instalador del proyecto. Clona el Manager como **nodo git** (no el paquete
pip), `ComfyUI-GGUF`, `ComfyUI-Krea2-NAG` y `comfyui-krea2edit` pineados, `rgthree-comfy` y
`seedvr2_videoupscaler`, instala sus requirements y aplica el parche `krea2` a
`ComfyUI-GGUF/loader.py` cuando el perfil es `t4`:

```bash
$SSH 'python3 /root/descargar_local.py /workspace/ComfyUI --solo-nodos \
  --perfil <PERFIL> --perfiles /root/comfy_profiles/profiles.py \
  --python /venv/main/bin/python'
```

- `--perfil <PERFIL>` es seguro en `a100`/`l4`: solo `t4` declara `node_patches`.
- Es idempotente: si se vuelve a correr, salta los nodos ya presentes y reaplica el parche
  sin duplicarlo.
- **No** ejecutar además `provision_vast.sh` ni `pip install comfyui-manager`: el Manager ya
  es `custom_nodes/ComfyUI-Manager` y el arranque **no** lleva `--enable-manager`. Mezclar
  las dos vías deja dos Managers y nodos duplicados.
- El pack `ComfyUI-Workflow-Models-Downloader` (botón **Workflow Models** de la UI) no está
  en la lista de `descargar_local.py`. Si se usa, clonarlo en `custom_nodes/` e instalar su
  `requirements.txt`; no es necesario para el bot ni para `agil_solo`.

## 9. Supervisor y arranque en 18188

El contrato del resto de la skill es ComfyUI en `127.0.0.1:18188`, log en
`/var/log/portal/comfyui.log` y reinicio por supervisor. Configurarlo así:

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
mkdir -p /var/log/portal /etc/supervisor/conf.d
cat > /etc/supervisor/conf.d/comfyui.conf <<'CONF'
[program:comfyui]
command=/venv/main/bin/python -u /workspace/ComfyUI/main.py --listen 127.0.0.1 --port 18188 --disable-auto-launch --enable-cors-header
directory=/workspace/ComfyUI
autostart=true
autorestart=true
startsecs=30
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=/var/log/portal/comfyui.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=3
environment=PYTHONUNBUFFERED="1"
CONF
pgrep -x supervisord >/dev/null || supervisord -c /etc/supervisor/supervisord.conf
supervisorctl reread
supervisorctl update comfyui
supervisorctl status comfyui
EOF
```

El primer arranque tarda: ComfyUI importa todos los nodos antes de abrir el puerto. No
lanzar un segundo proceso mientras `supervisorctl status` diga `STARTING`.

**Fallback sin supervisor** (imagen sin `supervisord` o sin permiso para instalarlo). Crear
un wrapper que use supervisor si está y, si no, `nohup`; así las fases 1 y 2 siguen usando
un solo comando:

```bash
$SSH 'bash -s' <<'EOF'
set -euo pipefail
cat > /usr/local/bin/comfyui-restart <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
LOG=/var/log/portal/comfyui.log
if [ -f /etc/supervisor/conf.d/comfyui.conf ] && command -v supervisorctl >/dev/null 2>&1; then
  if supervisorctl restart comfyui >/dev/null 2>&1; then
    echo "ComfyUI reiniciado por supervisor"
    exit 0
  fi
fi
pkill -f 'main.py --listen 127.0.0.1 --port 18188' 2>/dev/null || true
sleep 2
mkdir -p "$(dirname "$LOG")"
cd /workspace/ComfyUI
setsid nohup /venv/main/bin/python -u main.py \
  --listen 127.0.0.1 --port 18188 --disable-auto-launch --enable-cors-header \
  >> "$LOG" 2>&1 < /dev/null &
echo "ComfyUI relanzado con nohup (log: $LOG)"
WRAP
chmod +x /usr/local/bin/comfyui-restart
comfyui-restart
EOF
```

En una instalación limpia, usar `comfyui-restart` en lugar de `supervisorctl restart
comfyui` en todas las fases. El wrapper no crea un segundo proceso si supervisor ya lo
gestiona.

### 9.1 Dejar el Manager instalable desde la UI (obligatorio)

Un box nuevo **no puede instalar nodos desde la UI**: el botón *Install* devuelve
`A security error has occurred. Please check the terminal logs`. Manager 3.x escribió en
`user/__manager/config.ini`:

```ini
security_level = normal
allow_git_url_install = False
allow_pip_install = False
```

El *Install* de un nodo por git URL exige `allow_git_url_install = True` **y** un listener
de loopback; el terminal lo dice literalmente (`SECURITY_MESSAGE_FLAG_GIT_URL`). Los dos
flags se leen **una vez al arrancar**, y `config.ini` no existe hasta el primer arranque
del Manager: por eso el paso es *arrancar → parar → editar → arrancar*, y va como parte de
la instalación, no como un arreglo posterior.

```bash
$SSH 'supervisorctl stop comfyui'   # o el pkill del wrapper si no hay supervisor
$SSH 'bash -s' <<'EOF'
set -euo pipefail
CFG=/workspace/ComfyUI/user/__manager/config.ini
[ -f "$CFG" ] || { echo "ERROR: falta $CFG; ¿arrancó el Manager al menos una vez?"; exit 1; }
sed -i -E 's/^allow_git_url_install = .*/allow_git_url_install = True/;
           s/^allow_pip_install = .*/allow_pip_install = True/' "$CFG"
grep -E 'allow_git_url_install|allow_pip_install|security_level' "$CFG"
EOF
$SSH 'comfyui-restart'
```

Verificación **sin instalar nada**: la petición tiene que fallar por falta de `url`, no por
seguridad.

```bash
$SSH 'curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "Content-Type: application/json" -d "{}" \
  http://127.0.0.1:18188/customnode/install/git_url'   # 400 = puerta abierta
```

| Respuesta | Significado |
|---|---|
| `400` | La puerta pasó; solo falta el `url`. Correcto. |
| `403` / `404` | Sigue bloqueado: `sed` sobre otro archivo, o el arranque no es `--listen 127.0.0.1`. |

Detalles que importan:

- **`--enable-manager` no es la solución.** El propio mensaje de Manager dice que los flags
  son independientes de `security_level` y de la bandera del core.
- `allow_flagged_nodepack_install` se queda como está: con loopback ya se permite.
- Ambos flags están AND-ados con un listener de loopback: si el box se expone con
  `--listen 0.0.0.0`, se cierran solos. Activarlos no crea una puerta pública.
- `user/` es efímero por instancia. **Cada box nuevo hay que repetirlo**: es un paso de
  despliegue, no una configuración de una vez.
- Si el usuario confirma que instalará nodos por `pip` además de por git, ambos flags ya
  quedan puestos con el `sed` de arriba.

## 10. Verificación de la instalación

```bash
$SSH '/venv/main/bin/python -c \
  "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"'
$SSH 'ldd /venv/main/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so | grep -c "not found"'  # 0
$SSH 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18188/system_stats'                    # 200
$SSH 'curl -s http://127.0.0.1:18188/object_info/CLIPLoader | grep -o "\"krea2\""'                    # "krea2"
$SSH 'tail -200 /var/log/portal/comfyui.log | grep -iE "IMPORT FAILED|Failed to import|Traceback"'     # vacío
$SSH 'supervisorctl status comfyui || true'
```

Si la ruta del `site-packages` no es `python3.12` (imagen con otra versión), ajustar el
`ldd`; la comprobación que importa es que `torch.cuda.is_available()` sea `True`.

Puerta: **200 + `"krea2"`**. Sin `krea2`, actualizar ComfyUI a una release más nueva
(`COMFY_REF`) y reiniciar; no seguir a la fase 1 con un ComfyUI que no entiende el modelo.

Al terminar, volver a `references/vast.md` §0.4 (fase 1). Los pasos que ya quedaron hechos:

| Paso de la fase 1 | En instalación limpia |
|---|---|
| ComfyUI base | Hecho aquí (§4) |
| `provision_vast.sh` | **Omitir** (haría doble Manager) |
| Pesos mínimos de `agil_solo` | Pendiente: hacerlo en §0.4 |
| Perfiles y nodos | Hechos aquí (§7–§8); el bloque de §0.4 es idempotente |
| Flujo `agil_solo.json` | Pendiente: hacerlo en §0.4 |
| Arranque y verificación | Hecho aquí; en §0.4 usar `comfyui-restart` |
| Manager instalable desde la UI | Hecho aquí (§9.1) |

## 11. Gotchas de la instalación limpia

1. **`cu130` solo con driver ≥ 580.65.** Con un driver anterior, `cu128`; nunca una wheel
   más nueva que el driver.
2. **No mezclar Managers.** La ruta limpia usa el nodo git que instala
   `descargar_local.py`. No instalar el paquete pip ni pasar `--enable-manager`.
3. **No ejecutar `provision_vast.sh` después.** Aborta sin ComfyUI y, si se fuerza, duplica
   el Manager. La fase 0.7 ya hizo todo lo que hace falta.
4. **`requirements.txt` puede pisar torch.** Por eso torch se instala antes y se verifica
   después; `--force-reinstall --no-deps` lo deja en la versión medida.
5. **Disco.** `--no-cache-dir`, `rm -rf /var/lib/apt/lists/*` y 18 GB de margen. Un
   `No space left on device` a mitad de la fase 2 deja `.download` parciales.
6. **Nunca purgar `nvidia-*`.** Las variantes cu12/cu13 comparten rutas y borrar una rompe
   la otra (`libcusparseLt.so.0`).
7. **`python`/`pip` no existen en SSH no interactivo.** Usar `/venv/main/bin/python` y
   `/venv/main/bin/pip`.
8. **El portal, `comfy-cli` y los packs preinstalados de la imagen no vienen incluidos.**
   `agil_solo` solo necesita core (+ `ComfyUI-GGUF` en `t4`), por eso la fase 1 es viable.
   Los flujos que usan Impact, CR, Bleh, rgthree o el pack privado seguirán fallando hasta
   instalar esos packs; `provision_vast_box.sh --only verify` los reporta.
9. **`ffmpeg` y `libgl1`/`libglib`** son para nodos de video y OpenCV, no para el core.
   Instalarlos ahora evita un `IMPORT FAILED` más tarde.
10. **Si `/workspace` es un montaje de red**, la instalación y la carga de pesos serán más
    lentas aunque la red contra HF vaya bien. Anotarlo en el reporte.
11. **El Manager nace cerrado a instalaciones (§9.1).** `allow_git_url_install = False` y
    `allow_pip_install = False` de fábrica; cualquier *Install* desde la UI da
    «A security error has occurred». Se abren con el servidor **parado** y solo valen con
    `--listen` de loopback. `--enable-manager` **no** lo arregla: el mensaje del terminal
    aclara que es independiente de `security_level` y de esa bandera.
