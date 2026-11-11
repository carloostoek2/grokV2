# Despliegue en Google Colab (runtime actual)

> Runtime actual desde 2026-10-24: **A100-SXM4-80GB**, ComfyUI en
> `127.0.0.1:8188`, datos persistentes en Google Drive. El bot entra por un túnel SSH
> local-forward. La foto del runtime está en `docs/comfyui/INVENTARIO_BOX_COLAB.md`.

## 1. Piezas del despliegue

| Archivo | Rol |
|---|---|
| `/home/ubuntu/comfy-colab/arrancar_comfy.sh` | Entrypoint. Orquesta fase CPU, sesión GPU, túnel, nginx, flujos y auto-off |
| `/home/ubuntu/bin/colab-comfy` | Symlink al entrypoint (el comando que se usa a diario) |
| `/home/ubuntu/comfy-colab/actualizar_cuaderno.py` | Regenera celdas 1, 5 y 7 del cuaderno y `preparar_modelos.py` desde el repo |
| `/home/ubuntu/comfy-colab/Untitled0.ipynb` | Cuaderno que ejecuta `colab exec` en la VM |
| `/home/ubuntu/comfy-colab/preparar_modelos.py` | **Generado**. Verifica/descarga pesos en la fase CPU |
| `/home/ubuntu/comfy-colab/descargar_local.py` | Descarga pesos/nodos en un ComfyUI existente (Vast/local) |
| `/home/ubuntu/comfy-colab/parar_comfy.sh` | Apaga túnel, nginx, auto-off y la sesión de Colab |
| `/home/ubuntu/comfy-colab/apagado_comfy.sh` | Programa/amplía/cancela el apagado automático |
| `/home/ubuntu/comfy-colab/colab_ssh_proxy.sh` | `ProxyCommand` del alias SSH: refresca el token y ejecuta `colab ssh` |
| `/home/ubuntu/comfy-colab/refresh_runtime_token.py` | Refresca o recupera el token del runtime (caduca ~1 h) |
| `/home/ubuntu/comfy-colab/nginx_comfy.conf` | Proxy con caché `8189 → 8188` |
| `/home/ubuntu/comfy-colab/secrets.env` | `CIVITAI_TOKEN` y `HF_TOKEN` (permisos 600; no versionar) |
| `/home/ubuntu/comfy-colab/sessions.json` | Estado de `colab-cli`; symlink a `~/.config/colab-cli/sessions.json` |

Dependencias locales:

- `google-colab-cli` en `/home/ubuntu/.local/bin/colab`.
- Python del CLI en `/home/ubuntu/.local/share/uv/tools/google-colab-cli/bin/python`.
- Clave SSH `~/.ssh/id_ed25519`.
- Alias en `~/.ssh/config`:

```
Host colab-comfy
    HostName colab-runtime
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ProxyCommand /home/ubuntu/comfy-colab/colab_ssh_proxy.sh
    ServerAliveInterval 25
    ServerAliveCountMax 3
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
```

`actualizar_cuaderno.py` tiene hardcodeado el repo del bot en
`/home/ubuntu/repos/grokV2/src/grokbot/providers/comfyui/workflows`. Si el repo se mueve,
hay que ajustar esa constante.

## 2. Arranque paso a paso

### 2.1 Preflight local

```bash
colab-comfy whoami          # cuenta de Google activa
colab-comfy status          # sesiones y túnel
ls -l /home/ubuntu/comfy-colab/secrets.env   # debe ser 600
/home/ubuntu/comfy-colab/actualizar_cuaderno.py --listar
```

`--listar` imprime cada perfil con su etiqueta, número de flujos y número de pesos. No toca
el cuaderno.

### 2.2 Fase 1 — preparación en CPU

`arrancar_comfy.sh` resuelve el perfil antes de crear la VM:

| `TIPO` | `PERFIL_HINT` |
|---|---|
| `A100`, `H100` | `a100` |
| `L4` | `l4` |
| `T4`, `G4`, `cpu` | `t4` |

Luego crea una sesión **solo CPU** (`<sesión>-prep`, no consume unidades GPU), monta Drive y
ejecuta `preparar_modelos.py`:

```bash
colab --config /home/ubuntu/comfy-colab/sessions.json new -s comfy-prep
colab --config /home/ubuntu/comfy-colab/sessions.json drivemount -s comfy-prep
colab --config /home/ubuntu/comfy-colab/sessions.json exec -s comfy-prep \
  -f /home/ubuntu/comfy-colab/preparar_modelos.py --timeout 5400 \
  --env USAR_DRIVE=1 \
  --env MODO_PREPARACION=1 \
  --env PERFIL_PESOS=a100 \
  --env PERMITIR_SIN_DRIVE=0 \
  --env CIVITAI_TOKEN=... \
  --env HF_TOKEN=...
```

Qué hace `preparar_modelos.py`:

- En `MODO_PREPARACION=1` **no importa `torch`**: el perfil sale de `PERFIL_PESOS`.
- Verifica cada peso del perfil con `os.path.getsize` (segundos).
- **Solo si falta alguno** instala `aria2` y descarga los archivos que falten.
- Si todo está presente, imprime `PREPARACION_COMPLETA`.
- Si falta algo, imprime `PREPARACION_INCOMPLETA=<n>`.

Reglas de la fase:

- `PREPARACION_INCOMPLETA` o Drive sin montar → **no se abre la GPU**. El lanzador aborta
  para no gastar unidades sin poder correr.
- Log: `/home/ubuntu/comfy-colab/preparacion.log`.
- `--sin-preparar` omite la fase y deja que el cuaderno verifique/descargue dentro de la GPU.
- `--no-drive` también la omite (no habría dónde persistir los pesos).
- Con una sesión GPU ya activa no se repite; `--preparar` la reinicia para re-verificar.

### 2.3 Fase 2 — sesión GPU y cuaderno

Si la fase CPU quedó completa, el lanzador:

1. Reutiliza la sesión `comfy` solo si coincide con la tarjeta y `High-RAM`.
2. Si no, la detiene y crea una nueva:

```bash
colab --config /home/ubuntu/comfy-colab/sessions.json new -s comfy --gpu A100 --high-mem
```

3. Monta Drive si hace falta.
4. Ejecuta el cuaderno:

```bash
colab --config /home/ubuntu/comfy-colab/sessions.json exec -s comfy \
  -f /home/ubuntu/comfy-colab/Untitled0.ipynb --timeout 1800 \
  --env USAR_DRIVE=1 \
  --env MODO_CPU=0 \
  --env COMFY_PORT=8188 \
  --env INSTALAR_EXTRAS=0 \
  --env PERFIL_PESOS=a100 \
  --env CIVITAI_TOKEN=... \
  --env HF_TOKEN=... \
  --env COMFY_REF=...
```

Si el websocket se corta a mitad de una celda, el lanzador hace `restart-kernel` y reintenta
una vez. El montaje de Drive sobrevive al reinicio.

### 2.4 Post-cuaderno

Con la VM ya arriba, `arrancar_comfy.sh`:

1. Abre el túnel SSH con `ControlMaster`:

```bash
ssh -f -N -M -S /home/ubuntu/comfy-colab/ssh_comfy.sock \
  -L 127.0.0.1:8188:127.0.0.1:8188 \
  -o ProxyCommand=/home/ubuntu/comfy-colab/colab_ssh_proxy.sh \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ServerAliveInterval=25 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -i ~/.ssh/id_ed25519 \
  root@colab-runtime
```

2. Levanta nginx con caché en `127.0.0.1:8189` apuntando a `8188`.
3. Despliega a Drive los flujos del perfil que falten.
4. Programa el apagado automático (`AUTO_OFF=2h` por defecto).

El proxy se levanta aunque ComfyUI todavía esté cargando nodos: el navegador recibe 502 en
vez de «conexión rechazada» y funciona en cuanto ComfyUI abre el puerto.

## 3. Qué hace cada celda del cuaderno

| Celda | Contenido | Rutas |
|---|---|---|
| 1 | Clona o actualiza ComfyUI a la última release estable (`COMFY_REF` lo cambia) y crea las carpetas de datos en Drive | `/content/ComfyUI`, `/content/drive/MyDrive/ComfyUI` |
| 2 | Instala `torch`, `torchvision`, `torchaudio` y `requirements.txt` + `manager_requirements.txt` | `/content/ComfyUI` |
| 5 | Detecta la GPU real con `torch`, elige perfil, verifica/descarga pesos y escribe `.perfil_activo` | `.../ComfyUI/models`, `.../ComfyUI/.perfil_activo` |
| 7 | Clona custom nodes y, una vez por VM, instala sus `requirements.txt`; aplica el parche GGUF de `krea2` | `.../ComfyUI/custom_nodes`, `/content/.comfy_reqs` |
| 9 | Arranca ComfyUI | `main.py` + `/content/ComfyUI/comfyui.log` |

Comando exacto de arranque (celda 9):

```bash
python main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --enable-manager \
  --enable-compress-response-body \
  --base-directory /content/drive/MyDrive/ComfyUI \
  --temp-directory /content/comfy_local \
  --database-url sqlite:////content/comfy_local/comfyui.db
```

Notas:

- `--base-directory` apunta a Drive: modelos, `custom_nodes`, `input`, `output` y `user`
  viven ahí. No hay symlinks ni `extra_model_paths.yaml`.
- SQLite, temporales y previews van a `/content/comfy_local` porque SQLite no se lleva bien
  con Drive; se pierden al recrear la VM.
- `--listen 127.0.0.1` es intencional: ComfyUI **no** se expone a Internet. Se llega por el
  túnel.

## 4. Perfiles de pesos

Fuente única: `src/grokbot/providers/comfyui/workflows/profiles/{a100,l4,t4}.json` y
`profiles.py`. `actualizar_cuaderno.py` los embebe en la celda 5; no hay listas duplicadas
en el `.ipynb`.

| Perfil | Tarjeta | Krea | Qwen Edit | Flujos | Descarga aprox. |
|---|---|---|---|---|---|
| `a100` | A100 / H100 | bf16 (24,5 + 8,3 GB) | fp8mixed (19,1 GB) | 13 | ~112 GB |
| `l4` | L4 | fp8 (12,2 + 4,9 GB) | GGUF Q4 (12,0 GB) | 12 | ~63 GB |
| `t4` | T4 16 GB | GGUF Q4 (7,0 GB) | no | 4 | ~21 GB |

Flujos del perfil `t4`: `grok_style`, `agil_solo`, `agil_nsfw`, `grok_edit`. Medido en T4:
179 s por edición en caliente (257 s el primer pase con carga). Qwen Edit funciona pero
tarda 775 s, por eso queda fuera.

La GPU real manda: la celda 5 vuelve a detectar la tarjeta con `torch.cuda.get_device_name()`
y la VRAM. Si se pidió una tarjeta y Colab entregó otra, el cuaderno usa la que hay y avisa.
Escribe `MyDrive/ComfyUI/.perfil_activo`; el lanzador lo lee para desplegar solo esos flujos.

El bot también detecta el perfil con `COMFYUI_PERFIL=auto` consultando `/system_stats` y
filtra los flujos permitidos.

## 5. Persistencia y arranque en frío

Sobrevive a recrear la VM (está en Drive):

- `models/`, `custom_nodes/`, `input/`, `output/`, `user/`.
- Pesos ya descargados.
- Workflows de la UI en `user/default/api_workflows/`.

No sobrevive:

- `/content/ComfyUI` (código; se reclona).
- `/content/comfy_local` (SQLite, temp, previews).
- Paquetes pip de los custom nodes (marca `/content/.comfy_reqs` por VM; se reinstalan).

Tras recrear la VM, la primera pasada de un flujo que carga 20 GB desde Drive tarda
**7–10 min**. Las siguientes son rápidas. Por eso `agil_edit_qwen` usa `timeout: 900` y
`agil_edit_hq` `timeout: 1200`.

## 6. Operación diaria

| Comando | Para qué |
|---|---|
| `colab-comfy` | Arranque A100 High-RAM (default) |
| `colab-comfy T4` / `L4` | Otra tarjeta → otro perfil |
| `colab-comfy status` | Cuenta, sesiones, túnel, puerto |
| `colab-comfy logs` | Últimas líneas de `/content/ComfyUI/comfyui.log` |
| `colab-comfy tunnel` | Reconectar solo el túnel SSH |
| `colab-comfy apagado` | Cuánto queda del auto-off |
| `colab-comfy apagado 3h` | Reprogramar el auto-off |
| `colab-comfy ampliar 1h` | Sumar tiempo sin reiniciar nada |
| `colab-comfy stop` | Cerrar túnel/nginx/auto-off y apagar la VM |
| `colab-comfy cache` | Vaciar la caché de nginx |
| `colab-comfy login` | Cambiar de cuenta de Google |
| `colab-comfy whoami` | Ver la cuenta activa |

Atajo: `colab-comfy +1h` equivale a `colab-comfy ampliar 1h`.

## 7. Actualizar flujos, pesos, versión o nodos

### Flujos

- El bot usa `COMFYUI_WORKFLOW_SOURCE=embed`: los templates del repo son la fuente de
  verdad. Editar un flujo se hace con `comfy-add-flow` y se aplica al **reiniciar el bot**.
- La UI de ComfyUI lee `.../user/default/api_workflows/` de Drive. Para probar un flujo en
  la UI hay que copiarlo ahí; el lanzador repara los que falten del perfil activo.
- `actualizar_cuaderno.py --archivos-perfil <perfil>` imprime `ruta<TAB>id` de las
  plantillas del perfil (lo usa `desplegar_flujos`).

### Pesos

1. Editar el JSON del perfil en `src/grokbot/providers/comfyui/workflows/profiles/`.
2. Ejecutar `actualizar_cuaderno.py` (lo hace el lanzador) para regenerar el cuaderno y
   `preparar_modelos.py`.
3. Arrancar; la fase CPU descarga solo lo que falte.

Alternativa dentro de la VM: usar el nodo **Workflow Models Downloader** de la UI o la API
`POST /workflow-models/download-url` a través del túnel. Después, reiniciar ComfyUI.

### Versión de ComfyUI

```bash
colab-comfy                    # última release estable (default)
COMFY_REF=master colab-comfy   # rama principal
COMFY_REF=v0.43.2 colab-comfy  # fijar etiqueta
```

La celda 1 compara `/content/ComfyUI` con la etiqueta y hace clone/checkout. No toca
`custom_nodes/` ni `user/` (viven en Drive).

### Custom nodes

La celda 7 clona los nodos requeridos y, la primera vez de cada VM, instala sus
`requirements.txt`:

- `ComfyUI-GGUF`
- `ComfyUI-Krea2-NAG` (`0afb38d`)
- `comfyui-krea2edit` (`86f886d`)
- `rgthree-comfy`
- `seedvr2_videoupscaler`
- `ComfyUI-Krea2T-Enhancer` (`20dd940`)
- `comfyui-impact-pack`

Los extras (`impact-subpack`, `essentials`, `kjnodes`, `workflow-models-downloader`, ...)
solo instalan requirements con `--extras` (`INSTALAR_EXTRAS=1`).

El perfil `t4` aplica un parche idempotente a `ComfyUI-GGUF` para añadir `krea2` a
`IMG_ARCH_LIST`. Si aparece `Unexpected architecture type in GGUF file: 'krea2'`, el parche
no se aplicó.

## 8. Acceso al navegador

El lanzador imprime las instrucciones. En resumen:

```bash
# Desde el dispositivo del usuario hacia esta máquina
ssh -L 8189:127.0.0.1:8189 ubuntu@<IP_O_HOST_DE_ESTA_MAQUINA>
```

Luego abrir `http://localhost:8189` (nginx con caché). En la propia máquina del bot se puede
abrir `http://127.0.0.1:8188` directo.

## 9. Verificación

```bash
colab-comfy status
ps -eo pid,etime,args | grep -E 'ssh .*8188|colab.*keep-alive' | grep -v grep
curl -s http://127.0.0.1:8188/system_stats | python3 -m json.tool

# Pesos visibles para los loaders
curl -s http://127.0.0.1:8188/object_info/UNETLoader
curl -s http://127.0.0.1:8188/object_info/CLIPLoader
curl -s http://127.0.0.1:8188/object_info/VAELoader
curl -s http://127.0.0.1:8188/object_info/LoraLoader

# Nodos que exigen los flujos HQ
for n in Krea2EditModelPatch Krea2EditGroundedEncode SeedNode ResolutionSelector; do
  curl -s "http://127.0.0.1:8188/object_info/$n" | grep -q "\"$n\"" \
    && echo "$n OK" || echo "$n MISSING"
done
```

## 10. Diagnóstico

| Síntoma | Causa | Acción |
|---|---|---|
| `Endpoint not found (HTTP 404)` | Token del runtime caducado (~1 h) | `refresh_runtime_token.py --config ... --session comfy --recover`; luego `colab-comfy tunnel` |
| `Session not found` | El alias `comfy` no está registrado en `sessions.json` | Reconstruir el `SessionState` desde `list_assignments()` o relanzar el stack; no editar el JSON a mano |
| Un segundo `ssh colab-comfy` da 404/429 | La única sesión SSH la ocupa el túnel | No abrir otra; usar el túnel vivo |
| `DRIVE_NO` | Drive no montó | `colab-comfy` reintenta; revisar permisos de la cuenta |
| `PREPARACION_INCOMPLETA=n` | Faltan pesos o tokens | Revisar `preparacion.log` y `secrets.env`; no abrir GPU |
| ComfyUI no abre el puerto | Arranque en frío o nodo roto | `colab-comfy logs`; esperar 7–10 min; buscar `IMPORT FAILED` |
| El perfil no coincide | Colab entregó otra tarjeta | El cuaderno usa la GPU real y reescribe `.perfil_activo` |
| La UI no ve un flujo | Está en el repo (`embed`), no en Drive | Copiarlo a `.../user/default/api_workflows/<id>.json` |
| La caché sirve una versión vieja | nginx cachea assets | `colab-comfy cache` |
| El auto-off corta una generación | Límite de 2 h | `colab-comfy ampliar 1h` |

## 11. Límites

- No abrir un segundo `colab ssh` con una sesión viva.
- No editar `Untitled0.ipynb` ni `preparar_modelos.py` a mano.
- No cambiar `COMFYUI_WORKFLOW_SOURCE` a `remote` en Colab para «arreglar» un flujo: la
  única sesión SSH la ocupa el túnel del bot.
- No commitear `secrets.env`, `sessions.json`, logs ni capturas con tokens.
