---
name: comfyui-deploy
description: "Use when el usuario pida desplegar, arrancar, apagar, reparar o diagnosticar ComfyUI para grokbot en Google Colab o en una instancia vast.ai, o cuando pregunte cómo se preparan los pesos, qué scripts se usan, cómo se conecta el bot con la GPU, o por qué el bot no genera. Cubre el reconocimiento previo de la máquina (GPU, disco, velocidad), la elección de perfil, la instalación de ComfyUI desde cero en una imagen Vast limpia (sistema, torch, ComfyUI, nodos y supervisor), el arranque mínimo con agil_solo y el resto en segundo plano, fases CPU→GPU, perfiles a100/l4/t4, túneles SSH y verificación."
metadata:
  version: "1.2.0"
  source: repo-docs-and-deploy-scripts
---

# Despliegue de ComfyUI para grokbot (Colab y vast.ai)

Skill operativa de despliegue. Responde **dónde está cada archivo, qué comando correr y
cómo verificar** que el bot puede generar. No sustituye a las otras dos skills de ComfyUI:

| Skill | Para qué |
|---|---|
| `comfy-add-flow` | Registrar o reemplazar un **flujo** (`templates/<id>.json` + `COMFYUI_FLOWS`) |
| `comfyui-hyperrealism-krea2` | Diseñar/optimizar un **workflow** (modelo, sampler, pasos, LoRA) |
| **`comfyui-deploy`** (esta) | **Llevar ComfyUI a una GPU y conectarlo con el bot** |

> **Alcance de esta versión (1.2.0).** El despliegue **Vast** es por fases obligatorias:
> **medir la máquina → elegir perfil → asegurar la base de ComfyUI → dejar `agil_solo`
> funcionando de punta a punta → recién entonces descargar el resto en segundo plano.**
> La fase «asegurar la base» **no asume** que la imagen traiga ComfyUI: si la instancia
> está limpia, se instala desde cero (`references/vast-clean-install.md`). **Colab no
> cambia**: su ruta sigue igual en `references/colab.md`.

## 1. Los tres lugares que hay que distinguir

| Lugar | Ruta | Qué vive ahí | ¿Se commitea? |
|---|---|---|---|
| **Repo del bot** | `/home/ubuntu/repos/grokV2` | Código del bot, `templates/` de flujos, `profiles/` de pesos, docs, `scripts/provision_vast_box.sh` | Sí |
| **Repo de despliegue** | `/home/ubuntu/comfy-colab` | Lanzador de Colab, cuaderno, descargadores, proxy, tokens, provisión Vast | Sí (salvo `secrets.env`, `sessions.json`, logs) |
| **Runtime ComfyUI** | VM de Colab o instancia Vast | ComfyUI, modelos, custom nodes, salidas | No |

El usuario suele decir «la carpeta `colab-comfy`». La carpeta es **`/home/ubuntu/comfy-colab`**;
`colab-comfy` es el **comando** (symlink a `arrancar_comfy.sh`) y el **alias SSH** de
`~/.ssh/config`. No existe una carpeta `colab-comfy`.

## 2. Mapa rápido de rutas

### Repo del bot (`/home/ubuntu/repos/grokV2`)

```
src/grokbot/providers/comfyui/
├── transport.py              # túnel SSH local-forward (SshLocalForward)
├── client.py                 # ComfyApiClient: REST + WebSocket
├── provider.py               # ComfyUIProvider: flujo → render → encolar → descargar
└── workflows/
    ├── resolver.py           # carga templates, separa _meta, parchea prompt/seeds
    ├── loras.py              # inyección de LoRAs extra del usuario
    ├── profiles.py           # resuelve perfiles por GPU
    ├── profiles/{a100,l4,t4}.json  # fuente única de pesos y flujos por tarjeta
    └── templates/<id>.json   # grafos API-format (fuente de verdad en embed)
scripts/provision_vast_box.sh # reconstrucción completa del box Vast (fases)
docs/comfyui/
├── INVENTARIO_BOX_COLAB.md   # foto del runtime Colab (túnel, pesos, flujos)
├── INVENTARIO_BOX_VAST.md    # receta histórica Vast + §9 provisión
├── AVANCE_VAST_HTTP.md       # diseño del provider y estado del slice
├── PERFILES_GPU.md           # a100/l4/t4 y qué baja cada uno
└── REMOTE_API_WORKFLOWS.md   # modo remote (SSH-cat al box)
```

### Repo de despliegue (`/home/ubuntu/comfy-colab`)

```
arrancar_comfy.sh            # ENTRYPOINT Colab (symlink ~/bin/colab-comfy)
actualizar_cuaderno.py       # regenera celdas 1/5/7 del cuaderno desde el repo
Untitled0.ipynb              # cuaderno que ejecuta `colab exec` en la VM
preparar_modelos.py          # GENERADO: verifica/descarga pesos en fase CPU
descargar_local.py           # descarga pesos/nodos en un ComfyUI existente (Vast/local)
provision_vast.sh            # provisión rápida de una instancia Vast nueva
parar_comfy.sh               # apaga túnel, nginx, auto-off y sesión Colab
apagado_comfy.sh             # programa/amplía/cancela el apagado automático
colab_ssh_proxy.sh           # ProxyCommand: refresca el token y ejecuta colab ssh
refresh_runtime_token.py     # refresca/recupera el token del runtime (caduca ~1 h)
nginx_comfy.conf             # proxy con caché 8189 → 8188
nginx/                       # estado del proxy (run/cache/logs)
secrets.env                  # CIVITAI_TOKEN / HF_TOKEN (0600, NO versionar)
sessions.json                # estado de colab-cli (symlink a ~/.config/colab-cli/)
vast_faceswap/               # despliegue aparte de FaceFusion en Vast (opcional)
```

### Runtime Colab (dentro de la VM)

```
/content/ComfyUI                                  # código (efímero, se reclona)
/content/drive/MyDrive/ComfyUI                    # datos persistentes en Drive
  ├── models/{diffusion_models,text_encoders,vae,loras}
  ├── custom_nodes/
  ├── user/default/api_workflows/                 # flujos para la UI (no para el bot)
  ├── input/ output/ user/
  └── .perfil_activo                              # perfil detectado por el cuaderno
/content/comfy_local                              # SQLite, temp, previews (efímero)
/content/ComfyUI/comfyui.log                      # log de ComfyUI
/content/.comfy_reqs                              # marca de requirements ya instalados
/home/ubuntu/comfy-colab/preparacion.log          # log de la fase CPU
```

### Runtime Vast

```
/workspace/ComfyUI                                # ComfyUI
/workspace/ComfyUI/models/                        # pesos
/workspace/ComfyUI/user/default/api_workflows/    # flujos (nombrados por _meta.id)
/workspace/ComfyUI/custom_nodes/
/venv/main/bin/python                             # Python del entorno
/etc/supervisor/conf.d/comfyui.conf               # servicio supervisor
/var/log/portal/comfyui.log                       # log
/root/descargar_local.py                          # copia dejada por provision_vast.sh
/root/secrets.env                                 # tokens (0600)
/root/comfy_profiles/                             # profiles.py + profiles/ para la fase 2
/root/descarga.log                                # log de descargas en segundo plano
/usr/local/bin/comfyui-restart                    # solo instalación limpia (supervisor o nohup)
```

Estas rutas son también las que crea la **instalación limpia** (`references/vast-clean-install.md`),
para que las fases 1 y 2 trabajen sobre las mismas rutas sea cual sea el tipo de imagen.

## 3. Decidir el destino

| Situación | Camino |
|---|---|
| Uso diario del bot | **Colab A100 80 GB** (runtime actual desde 2026-10-24) |
| Probar en GPU chica | Colab T4/L4 (perfil de pesos automático) |
| Colab no disponible / se quiere un box persistente | **Vast** con imagen de ComfyUI |
| **Vast con imagen limpia (sin ComfyUI)** | **§5.3** instala sistema, torch, ComfyUI y nodos; luego se sigue en §5.4 |
| Instancia Vast nueva o recreada | **§5 en orden 5.1 → 5.2 → 5.3 → 5.4 → 5.5** (nunca `--download` de entrada) |
| Reconstruir Vast desde cero | `scripts/provision_vast_box.sh` (fases; exige ComfyUI ya instalado) |
| Preparar Vast rápido tras recrear | `~/comfy-colab/provision_vast.sh` (exige ComfyUI ya instalado) |

## 4. Colab en 60 segundos

```bash
# Arranque normal: A100 High-RAM, dos fases (CPU prepara pesos → GPU arranca ComfyUI)
colab-comfy

# Otras tarjetas / opciones
colab-comfy T4
colab-comfy L4
colab-comfy --sin-preparar      # omite la fase CPU (ComfyUI verifica/descarga en GPU)
colab-comfy --preparar          # fuerza re-verificar aunque la sesión esté activa
colab-comfy --no-drive          # sin Drive (efímero; no recomendado)
colab-comfy --extras            # instala requirements de nodos viejos (lento)
COMFY_REF=v0.43.2 colab-comfy   # fija versión de ComfyUI

# Operación
colab-comfy status              # cuenta, sesión, túnel
colab-comfy logs                # últimas líneas de comfyui.log
colab-comfy tunnel              # reconecta solo el túnel SSH
colab-comfy apagado             # cuánto queda de auto-off
colab-comfy ampliar 1h          # suma tiempo sin reiniciar nada
colab-comfy stop                # cierra túnel/nginx/auto-off y apaga la VM
colab-comfy cache               # limpia la caché nginx
```

Qué hace `arrancar_comfy.sh`, en orden:

1. Carga `secrets.env` (`CIVITAI_TOKEN`, `HF_TOKEN`).
2. Ejecuta `actualizar_cuaderno.py` → regenera celdas 1, 5 y 7 del `.ipynb` y
   `preparar_modelos.py` desde `profiles/` del repo. **No se edita el cuaderno a mano.**
3. **Fase CPU** (`comfy-prep`): monta Drive y corre `preparar_modelos.py` con
   `MODO_PREPARACION=1`; verifica pesos por tamaño y solo descarga lo que falte.
   Si falta algo, **no abre la GPU** (revisa `preparacion.log`).
4. **Fase GPU** (`comfy`): crea/reutiliza la sesión y ejecuta el cuaderno
   (clonar/actualizar ComfyUI, deps, pesos, custom nodes, arrancar `main.py`).
5. Abre el túnel SSH local-forward `127.0.0.1:8188 → 127.0.0.1:8188`.
6. Levanta nginx con caché en `127.0.0.1:8189` (el navegador usa este puerto).
7. Despliega a Drive los flujos del perfil que falten.
8. Programa el apagado automático (2 h por defecto).

Detalle completo: `references/colab.md`.

## 5. Vast: despliegue por fases (obligatorio)

El orden no se negocia: **primero se mide, después se descarga; primero la base y un flujo
funcionando, después el resto.** `--download` no se usa hasta cerrar la fase 1.

### 5.1 Fase 0 — Reconocer la máquina (antes de descargar nada)

```bash
SSH="ssh -o BatchMode=yes -p <SSH_PORT> root@<HOST>"

# GPU real, VRAM y driver
$SSH 'nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader'

# Disco donde viven los pesos: libres en GB y tipo de montaje
$SSH 'P=/workspace; [ -d "$P" ] || P=/; \
      df -B1 "$P" | tail -1 | awk "{printf \"libres: %.1f GB\n\", \$4/1e9}"; \
      findmnt -T "$P" -o TARGET,SOURCE,FSTYPE'

# ¿La imagen ya trae ComfyUI o está limpia?
$SSH 'test -f /workspace/ComfyUI/main.py && test -x /venv/main/bin/python \
  && echo "imagen con ComfyUI" || echo "instalación limpia"'

# Velocidad real contra Hugging Face (fuente de todos los pesos del mínimo)
$SSH 'curl -L -r 0-104857600 --max-time 15 -o /dev/null -w "HF: %{speed_download} B/s\n" \
  https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors'
```

Anotar **GPU/VRAM, GB libres, MB/s y si la base está limpia**. Sin esos números no se elige
perfil ni se lanza descarga alguna. Si `curl` no existe en la imagen, instalarlo
(`apt-get install -y curl`) y repetir la medición antes de elegir perfil. Exportar `SSH=...`
en la sesión: los bloques siguientes lo reutilizan.

### 5.2 Fase 0.5 — Elegir el perfil

La GPU/VRAM fija el **techo**; el disco puede forzar un perfil menor; la velocidad decide
**qué se baja ahora y qué queda para segundo plano**, no el perfil.

| GPU real | Perfil | Mínimo `agil_solo` | Perfil completo |
|---|---|---|---|
| A100/H100 (≥ 40 GB) | `a100` | ≈ 33 GB | ≈ 120 GB |
| L4/L40 (≥ 20 GB) | `l4` | ≈ 17 GB | ≈ 58 GB |
| T4 o < 20 GB | `t4` | ≈ 12 GB | ≈ 14 GB |

Tamaños según `weights[].min_size` de `profiles/*.json`; reservar 15 % de margen.
**Si la imagen no trae ComfyUI**, sumar antes **12–18 GB** de stack (venv + torch CUDA +
código de ComfyUI + dependencias de nodos): `t4` mínimo pasa a ≈ 30 GB y `a100` completo a
≈ 138 GB. Sin ese margen la instalación limpia puede llenar el disco a mitad de la fase 2.

Reglas:

1. **Disco**: libres ≥ completo + margen → plan completo, pero igual se empieza por la
   fase 1. Libres ≥ mínimo + margen pero < completo → solo el mínimo, el resto no se
   lanza y se reporta cuánto falta. Libres < mínimo → no descargar; bajar a `t4` si la
   tarjeta lo permite o pedir más disco.
2. **Velocidad** contra HF: ≥ 50 MB/s → el resto acaba en < 1 h; 10–50 MB/s → mínimo
   primero y resto en segundo plano; < 10 MB/s → mínimo primero, resto en segundo plano
   y avisar el ETA.
3. **GPU desconocida o VRAM < 20 GB** → `t4`. Nunca se elige un perfil mayor que la
   tarjeta.

El perfil elegido manda en todo: pesos, `COMFYUI_PERFIL` y flujos visibles en el menú.

### 5.3 Fase 0.7 — Asegurar la base de ComfyUI (imagen limpia o no)

No toda instancia Vast trae ComfyUI: puede venir un sistema base limpio, sin
`/workspace/ComfyUI` ni `/venv/main`. Esta fase lo resuelve **antes** de bajar pesos; no se
recrea la instancia solo por esto.

```bash
$SSH 'test -f /workspace/ComfyUI/main.py && test -x /venv/main/bin/python \
  && echo "imagen con ComfyUI" || echo "instalación limpia"'
```

- **Imagen con ComfyUI** → no hay nada que hacer; seguir en §5.4.
- **Instalación limpia** → instalar sistema (apt), venv `/venv/main`, torch según driver,
  ComfyUI, dependencias, custom nodes y supervisor en `18188`. Receta completa:
  `references/vast-clean-install.md`.

La fase 0.7 deja **las mismas rutas** que asume el resto de la skill; §5.4–§5.5 siguen
igual salvo que se **omite `provision_vast.sh`** y el reinicio se hace con
`comfyui-restart`. Puerta obligatoria antes de seguir:

```bash
$SSH 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18188/system_stats'  # 200
$SSH 'curl -s http://127.0.0.1:18188/object_info/CLIPLoader | grep -o "\"krea2\""' # "krea2"
```

Si `krea2` no aparece, ComfyUI es viejo o el stack no cargó: actualizar o corregir **antes**
de la fase 1.

### 5.4 Fase 1 — Mínimo viable (bloqueante)

Meta: **`agil_solo` generando de verdad desde Telegram**. Nada de descargas completas
hasta que esto pase.

1. **ComfyUI base.** Ya quedó asegurada en §5.3. `agil_solo` solo usa nodos core
   (`UNETLoader`/`CLIPLoader`/`VAELoader`/`KSampler`); la excepción es `t4`, que usa GGUF
   y necesita `ComfyUI-GGUF` con el parche `krea2`. No recrear la instancia si falta la
   base: se instala en §5.3.
2. **Copiar descargador, tokens y shim** (sin descargar todavía):

   ```bash
   cd /home/ubuntu/comfy-colab
   ./provision_vast.sh <HOST> <SSH_PORT>     # sin --download: inventario + copia + Manager
   ```

   **En instalación limpia, omitir este paso.** La fase 0.7 ya copió `descargar_local.py`,
   `secrets.env`, `sitecustomize.py` y los perfiles, e instaló los nodos.
   `provision_vast.sh` aborta si no encuentra ComfyUI y, si se ejecuta después, duplicaría
   el Manager (pip + nodo git). No mezclar las dos vías.

3. **Pesos mínimos** — solo los 3 archivos que referencia `agil_solo` tras aplicar el
   perfil. Todos son HF sin token:

   ```bash
   cd /home/ubuntu/repos/grokV2
   PERFIL=<a100|l4|t4>
   .venv/bin/python - "$PERFIL" <<'PY' > /tmp/minimo_agil.sh
   import json, sys
   from pathlib import Path
   from grokbot.providers.comfyui.workflows.profiles import (
       apply_profile, get_profile, TEMPLATES_DIR)
   p = get_profile(sys.argv[1])
   g = apply_profile(json.loads((TEMPLATES_DIR / "agil_solo.json").read_text()), p)
   names = {n["inputs"][k] for n in g.values() if isinstance(n, dict)
            for k in ("unet_name", "clip_name", "vae_name") if k in n.get("inputs", {})}
   print("set -euo pipefail")
   for w in p.weights:
       if Path(w["path"]).name not in names:
           continue
       dest = "/workspace/ComfyUI/models/" + w["path"]
       print(f'mkdir -p "$(dirname {dest})"')
       print(f'if [ ! -s {dest} ]; then')
       print(f'  curl -L --fail --show-error -o "{dest}.part" "{w["url"]}"')
       print(f'  mv "{dest}.part" "{dest}"')
       print("fi")
   PY
   ssh -p <SSH_PORT> root@<HOST> 'bash -s' < /tmp/minimo_agil.sh
   ```

   Quedan: el UNET del perfil (`krea2_turbo_bf16` en `a100`,
   `krea2_turbo_fp8_scaled` en `l4`, `Krea-2-Turbo-Q4_K_M.gguf` en `t4`), el TE
   `qwen3vl_4b_{bf16|fp8_scaled}.safetensors` y `vae/qwen_image_vae.safetensors`.
4. **Perfiles al box (siempre) y nodo solo si `t4`**. La fase 2 necesita `profiles.py` +
   `profiles/` en el box; en `t4` además se aplica el parche GGUF. `descargar_local.py`
   lee esos perfiles por `--perfiles`:

   ```bash
   cd /home/ubuntu/repos/grokV2
   ssh -p <SSH_PORT> root@<HOST> 'mkdir -p /root/comfy_profiles'
   scp -P <SSH_PORT> src/grokbot/providers/comfyui/workflows/profiles.py \
     root@<HOST>:/root/comfy_profiles/
   scp -P <SSH_PORT> -r src/grokbot/providers/comfyui/workflows/profiles \
     root@<HOST>:/root/comfy_profiles/
   $SSH 'python3 /root/descargar_local.py /workspace/ComfyUI --solo-nodos --perfil <PERFIL> \
     --perfiles /root/comfy_profiles/profiles.py --python /venv/main/bin/python'
   ```

   Pasar `--perfil <PERFIL>` siempre es seguro: solo `t4` declara `node_patches` (el parche
   GGUF); en `a100`/`l4` no hay nada que parchear. **En instalación limpia los nodos y los
   perfiles ya están** (fase 0.7): este bloque es idempotente y se puede omitir.

5. **Flujo**: copiar **solo** `agil_solo.json`, nombrado por `_meta.id`:

   ```bash
   cd /home/ubuntu/repos/grokV2
   scp -P <SSH_PORT> src/grokbot/providers/comfyui/workflows/templates/agil_solo.json \
     root@<HOST>:/workspace/ComfyUI/user/default/api_workflows/agil_solo.json
   ```

6. **Arrancar el box y comprobar salud**:

   ```bash
   # Imagen con ComfyUI:
   $SSH 'supervisorctl restart comfyui'
   # Instalación limpia (creado por la fase 0.7; usa supervisor si existe, si no nohup):
   $SSH 'comfyui-restart'

   $SSH 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18188/system_stats'  # 200
   $SSH 'tail -200 /var/log/portal/comfyui.log | grep -iE "IMPORT FAILED|Traceback"'   # vacío
   $SSH 'curl -s http://127.0.0.1:18188/object_info/CLIPLoader | grep -o "\"krea2\""' # debe salir "krea2"
   ```

7. **Túnel y bot**: `.env` con las coordenadas (§6) y `COMFYUI_PERFIL=<PERFIL>` fijado
   durante la fase 1; luego `grokbot restart` y comprobar
   `curl -s http://127.0.0.1:18188/system_stats`.
8. **Prueba real**: un prompt de texto con `agil_solo` desde Telegram. La fase 1 se
   cierra solo si la imagen llega y el journal dice
   `Loaded ComfyUI workflow agil_solo from remote`.

Si algo falla, se arregla **antes** de la fase 2; la descarga completa no se lanza "por
si acaso". Mientras corre la fase 2, los demás flujos del menú fallarán: el único
garantizado es `agil_solo`.

### 5.5 Fase 2 — El resto, en segundo plano

Solo con la fase 1 cerrada. Descarga el perfil completo (pesos restantes + nodos):

```bash
cd /home/ubuntu/repos/grokV2
$SSH 'cd /root && setsid nohup python3 -u descargar_local.py /workspace/ComfyUI \
  --perfil <PERFIL> --perfiles /root/comfy_profiles/profiles.py \
  --python /venv/main/bin/python > /root/descarga.log 2>&1 < /dev/null & \
  echo "PID=$! log=/root/descarga.log"'

$SSH 'tail -f /root/descarga.log'      # seguimiento
```

Al terminar:

```bash
# Imagen con ComfyUI:
$SSH 'supervisorctl restart comfyui'   # cargar nodos nuevos
# Instalación limpia (supervisor o nohup, lo resuelve el wrapper):
$SSH 'comfyui-restart'

scripts/provision_vast_box.sh --only verify
```

`descargar_local.py` salta por tamaño lo ya presente: los 3 pesos de la fase 1 no se
re-descargan. El reinicio de ComfyUI para cargar nodos va al final, no a mitad de la
descarga.

> `provision_vast.sh --download` lanza el mismo `descargar_local.py` pero **sin**
> `--perfiles`: en un box sin `/home/ubuntu/repos/grokV2` falla. En ese caso usar el
> comando directo de arriba.

### Camino completo (reconstruir el box desde el repo)

```bash
cd /home/ubuntu/repos/grokV2

scripts/provision_vast_box.sh                     # todo
scripts/provision_vast_box.sh --only verify       # solo reporta
scripts/provision_vast_box.sh --skip models       # sin descargas grandes
scripts/provision_vast_box.sh --only env,links
CIVITAI_TOKEN=... scripts/provision_vast_box.sh   # necesario para Civitai
```

Fases: `preflight → env → nodes → models → links → workflows → restart → verify`.
Lee `COMFYUI_HOST`/`COMFYUI_PORT` de `.env` (o `--host`/`--port`).

> El manifiesto de pesos de `provision_vast_box.sh` es histórico y **no** conoce los
> perfiles `a100/l4/t4`. Úsalo para infraestructura (`--skip models --only ...`) y deja
> los pesos al flujo por perfil de §5.4–5.5. Su `preflight` **exige** que ComfyUI ya
> exista: en una imagen limpia, ejecutarlo solo después de §5.3.

Detalle completo, rutas y gotchas: `references/vast.md`.

## 6. Conexión con el bot

El bot **nunca** expone ComfyUI a Internet. Abre un **túnel SSH local-forward** y habla
por loopback con la API nativa (REST + WebSocket):

```
Telegram → use case → registry → ComfyUIProvider
                                    └─ SshLocalForward.ensure()
                                         ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> root@host
                                         └─ ComfyApiClient http://127.0.0.1:<local>
```

### Variables según destino

| Variable | Colab (actual) | Vast |
|---|---|---|
| `COMFYUI_HOST` | `colab-comfy` (alias SSH) | IP/host de la instancia |
| `COMFYUI_PORT` | `22` | puerto SSH de la instancia |
| `COMFYUI_REMOTE_PORT` | `8188` | `18188` |
| `COMFYUI_TUNNEL_LOCAL_PORT` | `0` (efímero) | `0` (o `18188`) |
| `COMFYUI_WORKFLOW_SOURCE` | `embed` | `remote` (o `embed` si se prefiere) |
| `COMFYUI_WORKFLOWS_DIR` | `/content/drive/MyDrive/ComfyUI/user/default/api_workflows` | `/workspace/ComfyUI/user/default/api_workflows` |
| `COMFYUI_WORKFLOW_CACHE_TTL` | `45` | `45` |
| `COMFYUI_PERFIL` | `auto` | `auto` |

Después de cambiar `.env`, código o un `_meta`: **`grokbot restart`**. El bot no recarga solo.

- En Colab, el alias `colab-comfy` usa `~/.ssh/config` y `colab_ssh_proxy.sh`; el token
  del runtime caduca a la hora y se refresca con `refresh_runtime_token.py`.
- En Vast, el bot entra directo con `ssh -p <PORT> root@<HOST>` (`BatchMode=yes`): la
  clave debe funcionar sin interacción.
- Si `127.0.0.1:<local>` ya está abierto, el provider usa **modo directo** y no lanza otro SSH.

Detalle completo: `references/bot-connection.md`.

## 7. Verificación mínima

```bash
# Colab
colab-comfy status
curl -s http://127.0.0.1:8188/system_stats | python3 -m json.tool
curl -s http://127.0.0.1:8188/object_info/UNETLoader | head -c 300

# Vast
ssh -p <SSH_PORT> root@<HOST> 'supervisorctl status comfyui; \
  curl -s http://127.0.0.1:18188/system_stats | head -c 300'

# Vast con instalación limpia: stack y arranque
ssh -p <SSH_PORT> root@<HOST> '/venv/main/bin/python -c \
  "import torch; print(torch.__version__, torch.cuda.is_available())"; \
  test -f /workspace/ComfyUI/main.py && echo ComfyUI OK'

# Bot (en la máquina del repo)
grokbot status
grokbot logs
.venv/bin/pytest -q          # suite completa, no solo el módulo tocado
```

Una generación real vale más que un `200`: manda un prompt de texto y, si el flujo es de
edición, una foto con caption.

**Puerta de la fase 1 (Vast):** no se considera desplegado hasta que una generación real
con `agil_solo` llega a Telegram y el journal del bot registra
`Loaded ComfyUI workflow agil_solo from remote`. Un `200` en `/system_stats` solo prueba
que ComfyUI está vivo, no que el bot puede generar.

## 8. Diagnóstico rápido

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `Endpoint not found (HTTP 404)` / `Session not found` | Token del runtime Colab caducado o sesión desregistrada | `refresh_runtime_token.py --config ... --session comfy --recover`; reconectar con `colab-comfy tunnel` |
| El bot dice «ComfyUI no está disponible» | `COMFYUI_HOST` vacío o túnel caído | Revisar `.env`, `colab-comfy status`, `ss -tulpn \| grep 8188` |
| ComfyUI no responde tras arrancar | Arranque en frío (7–10 min desde Drive) | `colab-comfy logs`; esperar; no abrir otra sesión |
| `PREPARACION_INCOMPLETA=n` | Faltan pesos o tokens | Revisar `preparacion.log`, `secrets.env`; **no** abrir GPU a ciegas |
| El perfil no coincide | Colab entregó otra tarjeta | `COMFYUI_PERFIL=auto`; el cuaderno escribe `.perfil_activo` con la GPU real |
| Cambié un flujo y el bot usa el viejo | `embed`: no se reinició el bot; `remote`: no se desplegó al box | `grokbot restart`; en Vast copiar `<id>.json` por `_meta.id` y esperar TTL |
| En Vast `python: not found` | SSH no interactivo no activa el venv | Usar `/venv/main/bin/python` |
| Falta `/workspace/ComfyUI/main.py` o `/venv/main`; `provision_vast.sh` aborta | Imagen Vast limpia, sin ComfyUI | **No recrear**: instalar con la fase 0.7 (`references/vast-clean-install.md`) |
| `torch` no importa / `CUDA error: no kernel image` | Wheel CUDA más nueva que el driver | Driver ≥ 580.65 para `cu130`; si no, `cu128` (fase 0.7) |
| Dos Managers o `--enable-manager` ignorado | Se mezcló Manager pip con el nodo git | Elegir una vía: en instalación limpia usar el nodo git y **no** ejecutar `provision_vast.sh` |
| Un custom node nuevo no carga | ComfyUI no se reinició | `supervisorctl restart comfyui` |
| El auto-off corta una generación | Límite de 2 h | `colab-comfy ampliar 1h` (no reinicia nada) |
| Workflow remoto «funciona» pero ignora el box | El archivo no se llama por `_meta.id`; cayó a embed sin fallar | Renombrar a `<id>.json`; el journal sano dice `Loaded ... from remote` |

## 9. Reglas duras

1. **Nunca** imprimir, commitear ni pegar `secrets.env`, `sessions.json`, tokens, IPs o
   puertos SSH en docs/commits. Las coordenadas de Vast rotan al recrear la instancia.
2. **No** editar a mano `Untitled0.ipynb` ni `preparar_modelos.py`: son generados por
   `actualizar_cuaderno.py` desde `profiles/` del repo. Editar el generador o el perfil.
3. **No** abrir un segundo `colab ssh` con una sesión viva: puede dar 429 y tumbar la
   sesión. El bot solo usa el túnel ya abierto.
4. **No** leer ni tocar `data/` ni `tmp/` del bot sin que el usuario lo pida (regla dura 2
   de `AGENTS.md`).
5. **No** purgar paquetes `nvidia-*` en Vast: cu12/cu13 comparten rutas y se rompe torch.
   Si se toca, reinstalar el set cu13 y verificar `ldd ... | grep -c "not found"` = 0.
6. **Reiniciar el bot** (`grokbot restart`) tras cambiar código, `.env`, perfiles o flujos.
7. Un cambio de flujo va por `comfy-add-flow`; uno de calidad, por
   `comfyui-hyperrealism-krea2`. Esta skill no redefine grafos.
8. **Vast por fases**: prohibido lanzar `--download` o una descarga completa antes de
   medir GPU, disco y velocidad (§5.1), elegir perfil (§5.2), asegurar la base de ComfyUI
   (§5.3) y cerrar `agil_solo` de punta a punta (§5.4). El resto va en segundo plano (§5.5).
9. **No asumir que la imagen Vast trae ComfyUI.** Comprobar `/workspace/ComfyUI/main.py` y
   `/venv/main/bin/python` al inicio. Si faltan, instalar desde cero con
   `references/vast-clean-install.md`; no recrear la instancia solo por esto y no mezclar
   la instalación limpia con `provision_vast.sh`.
10. **Colab no cambia**: esta política por fases y la instalación limpia son solo para
    Vast. Colab sigue con `references/colab.md` y su arranque CPU→GPU.

## 10. Referencias

- `references/colab.md` — despliegue Colab completo (fases, cuaderno, perfiles, rutas).
- `references/vast.md` — despliegue Vast por fases, provisión rápida y reconstrucción completa.
- `references/vast-clean-install.md` — instalación de ComfyUI desde cero en una imagen Vast
  limpia: sistema, venv, torch, ComfyUI, nodos, supervisor y verificación.
- `references/bot-connection.md` — túnel, variables, `embed` vs `remote`, perfiles y errores.
- Docs del repo: `docs/comfyui/INVENTARIO_BOX_COLAB.md`,
  `docs/comfyui/INVENTARIO_BOX_VAST.md`, `docs/comfyui/AVANCE_VAST_HTTP.md`,
  `docs/comfyui/PERFILES_GPU.md`.
