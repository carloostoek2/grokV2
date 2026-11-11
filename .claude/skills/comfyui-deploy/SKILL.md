---
name: comfyui-deploy
description: "Use when el usuario pida desplegar, arrancar, apagar, reparar o diagnosticar ComfyUI para grokbot en Google Colab o en una instancia vast.ai, o cuando pregunte cómo se preparan los pesos, qué scripts se usan, cómo se conecta el bot con la GPU, o por qué el bot no genera. Cubre rutas exactas, fases CPU→GPU, perfiles a100/l4/t4, túneles SSH y verificación."
metadata:
  version: "1.0.0"
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
/root/descarga.log                                # log de descargas en segundo plano
```

## 3. Decidir el destino

| Situación | Camino |
|---|---|
| Uso diario del bot | **Colab A100 80 GB** (runtime actual desde 2026-10-24) |
| Probar en GPU chica | Colab T4/L4 (perfil de pesos automático) |
| Colab no disponible / se quiere un box persistente | **Vast** con imagen de ComfyUI |
| Reconstruir Vast desde cero | `scripts/provision_vast_box.sh` (fases) |
| Preparar Vast rápido tras recrear | `~/comfy-colab/provision_vast.sh` |

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

## 5. Vast en 60 segundos

### Camino rápido (instancia nueva con imagen de ComfyUI)

```bash
cd /home/ubuntu/comfy-colab

# 1) Inventario + copia de descargador/tokens/shim + ComfyUI-Manager
./provision_vast.sh <HOST> <SSH_PORT>

# 2) Además descarga modelos y nodos en segundo plano
./provision_vast.sh <HOST> <SSH_PORT> --download

# 3) Seguir la descarga
ssh -p <SSH_PORT> root@<HOST> 'tail -f /root/descarga.log'
```

`provision_vast.sh` asume que la imagen ya trae `/workspace/ComfyUI`, `/venv/main`,
supervisor `comfyui` en el puerto **18188** y los packs base. Copia a `/root/`:
`descargar_local.py`, `secrets.env`, `DESCARGAS_LOCALES.md` y `sitecustomize.py`;
instala `comfyui-manager`; habilita `--enable-manager`; luego ejecuta
`python3 descargar_local.py /workspace/ComfyUI --python /venv/main/bin/python`.

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

# Bot (en la máquina del repo)
grokbot status
grokbot logs
.venv/bin/pytest -q          # suite completa, no solo el módulo tocado
```

Una generación real vale más que un `200`: manda un prompt de texto y, si el flujo es de
edición, una foto con caption.

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

## 10. Referencias

- `references/colab.md` — despliegue Colab completo (fases, cuaderno, perfiles, rutas).
- `references/vast.md` — provisión rápida y reconstrucción completa del box Vast.
- `references/bot-connection.md` — túnel, variables, `embed` vs `remote`, perfiles y errores.
- Docs del repo: `docs/comfyui/INVENTARIO_BOX_COLAB.md`,
  `docs/comfyui/INVENTARIO_BOX_VAST.md`, `docs/comfyui/AVANCE_VAST_HTTP.md`,
  `docs/comfyui/PERFILES_GPU.md`.
