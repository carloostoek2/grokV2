# Conexión del bot con ComfyUI

> El bot no expone ComfyUI a Internet. Abre un **túnel SSH local-forward** y habla con la
> API nativa de ComfyUI (REST + WebSocket) por loopback.

## 1. Contrato

```
Telegram
  └─ handler / use case (application)
       └─ registry.resolve_image|resolve_video(cfg)
            └─ ComfyUIProvider
                 ├─ transport.ensure()  → http://127.0.0.1:<puerto_local>
                 │    └─ SshLocalForward
                 │         ssh -p <ssh_port> -N -L 127.0.0.1:<local>:127.0.0.1:<remote> root@host
                 ├─ ComfyApiClient(base_url)
                 │    ├─ POST /prompt
                 │    ├─ WS /ws  (fin por prompt_id)
                 │    ├─ GET /history/<prompt_id>
                 │    └─ GET /view  → bytes
                 └─ GenerationResult(file_path local en tmp/comfyui)
```

Componentes:

| Módulo | Rol |
|---|---|
| `providers/comfyui/transport.py` | `SshLocalForward`: abre/mantiene el túnel; `ensure()`, `restart()`, `close()`; spawner inyectable para tests |
| `providers/comfyui/client.py` | `ComfyApiClient`: `enqueue`, `run_workflow`, `history`, `view`, `upload_image`, `health`, `system_stats`, `object_info`, `models` |
| `providers/comfyui/provider.py` | `ComfyUIProvider`: valida flujo, resuelve, renderiza, encola, descarga, normaliza |
| `providers/comfyui/workflows/resolver.py` | Carga templates, separa `_meta`, parchea prompt/seeds |
| `providers/comfyui/workflows/loras.py` | Inyecta LoRAs extra del usuario al final de la cadena de modelo |

## 2. Variables de entorno

Fuente: `src/grokbot/settings.py`. Plantilla: `.env.example`.

| Variable | Default en código | Colab actual | Vast histórico |
|---|---|---|---|
| `COMFYUI_HOST` | `""` (provider deshabilitado) | `colab-comfy` | IP/host de la instancia |
| `COMFYUI_PORT` | `22` | `22` | puerto SSH de la instancia |
| `COMFYUI_REMOTE_PORT` | `18188` | `8188` | `18188` |
| `COMFYUI_TUNNEL_LOCAL_PORT` | `0` (efímero) | `0` | `0` (o `18188`) |
| `COMFYUI_WORKFLOW_SOURCE` | `remote` | `embed` | `remote` (o `embed`) |
| `COMFYUI_WORKFLOWS_DIR` | `/workspace/ComfyUI/user/default/api_workflows` | `/content/drive/MyDrive/ComfyUI/user/default/api_workflows` | `/workspace/ComfyUI/user/default/api_workflows` |
| `COMFYUI_WORKFLOW_CACHE_TTL` | `45.0` | `45` | `45` |
| `COMFYUI_PERFIL` | `auto` | `auto` | `auto` |

Si `COMFYUI_HOST` está vacío, el provider se considera **no configurado** y responde con el
mensaje user-safe «ComfyUI no está disponible en este momento».

## 3. Cómo funciona el túnel

`TunnelConfig(host, ssh_port, remote_port, local_port)`.

- `local_port=0` → pide un puerto libre al sistema operativo antes de lanzar SSH.
- Comando:

```bash
ssh -p <ssh_port> \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ConnectTimeout=25 \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=accept-new \
  -N \
  -L 127.0.0.1:<local>:127.0.0.1:<remote> \
  root@<host>
```

- Antes de cada generación, el provider prueba `/system_stats`; si el túnel se cayó, llama a
  `restart()`.
- **Modo directo**: si `127.0.0.1:<local>` ya está abierto (por ejemplo, el túnel que abrió
  `colab-comfy`), el provider lo reutiliza y **no lanza otro SSH**. El log dice:
  `ComfyUI port 8188 is already open locally; using direct connection`.
- `BatchMode=yes` implica que la autenticación no puede pedir contraseña: la clave debe
  estar configurada.

### 3.1 Colab: alias + proxy + token

El alias de `~/.ssh/config`:

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

`colab_ssh_proxy.sh`:

1. Ejecuta `refresh_runtime_token.py --config sessions.json --session comfy` (la salida va a
   `stderr`, porque `stdout` es el flujo SSH).
2. `exec colab ssh --proxy-mode -s comfy -i ~/.ssh/id_ed25519 "$@"`.

Motivo: `colab-cli` conserva el token del runtime y **no lo refresca**; caduca ~1 h y
responde `Endpoint not found (HTTP 404)`. Peor: en un 404 durante `exec`, el CLI borra la
sesión local y la siguiente creación levanta una VM nueva. `refresh_runtime_token.py`
refresca el token en `sessions.json` y, con `--recover`, readopta una asignación huérfana.

### 3.2 Vast: SSH directo

No hay proxy ni token intermedio. El bot usa `root@<host>` en el puerto SSH de la instancia.
La clave debe autenticar sin interacción. Las coordenadas rotan al recrear la instancia:
`COMFYUI_HOST`/`COMFYUI_PORT` en `.env`.

## 4. Fuente de los flujos: `embed` vs `remote`

| Modo | Qué lee | Cuándo |
|---|---|---|
| `embed` | `src/grokbot/providers/comfyui/workflows/templates/<id>.json` del repo | Colab actual; fuente de verdad versionada |
| `remote` | `COMFYUI_WORKFLOWS_DIR/<id>.json` en el box, por SSH `cat` | Vast u otro box con SSH; permite editar en la UI y desplegar |

### `embed`

- El bot carga el template del repo. Cambiar un flujo = editar el template (skill
  `comfy-add-flow`) y **reiniciar el bot**.
- Copiar archivos a Drive o al box **no cambia nada** en el bot: esa carpeta solo la lee la
  UI de ComfyUI.
- El resolver cachea los flujos embebidos por proceso (`_flows_cached`): sin reinicio, el
  bot sigue con la versión anterior.

### `remote`

- El resolver ejecuta `ssh ... cat -- <workflows_dir>/<flow_id>.json`.
- Cache corta (`COMFYUI_WORKFLOW_CACHE_TTL`, 45 s).
- Si el archivo falta o es inválido → **fallback silencioso** a `embed` con warning. El bot
  sigue funcionando, pero ignora el box. El journal sano dice
  `Loaded ComfyUI workflow <id> from remote`.
- El archivo remoto debe llamarse **por `_meta.id`**, no por el nombre del template. Ejemplo:
  `templates/krea2_t2i.json` con `_meta.id = grok_style` → en el box es `grok_style.json`.
- `_meta` se separa antes de encolar; nunca viaja a ComfyUI.

Cambiar un flujo en `remote`:

1. Exportar el grafo en **API format** desde la UI.
2. Copiarlo a `<workflows_dir>/<flow_id>.json` en el box.
3. Esperar el TTL o reiniciar el bot.
4. Confirmar en el journal que carga desde remoto.

## 5. Perfiles de pesos

`COMFYUI_PERFIL`:

- `auto` (default): el provider consulta `/system_stats` al primer uso y cada 300 s, y activa
  el perfil que corresponde a la GPU real.
- `a100` | `l4` | `t4`: fuerza un perfil (útil en tests/offline).
- `""`: no gestiona perfil.

El perfil filtra qué flujos acepta el provider (`supports()`), de modo que el menú de
Telegram no ofrece flujos que la tarjeta no puede correr. La fuente es
`workflows/profiles/{a100,l4,t4}.json`:

| Perfil | Tarjeta | Flujos |
|---|---|---|
| `a100` | A100/H100 | 13 |
| `l4` | L4 | 12 |
| `t4` | T4 16 GB | 4 (`grok_style`, `agil_solo`, `agil_nsfw`, `grok_edit`) |

Si el perfil activo cambia (por ejemplo, el usuario recrea la sesión Colab con otra GPU), el
provider devuelve `True` en `refresh_profile()` y el caller vuelve a resolver el flujo.

## 6. Catálogo de LoRAs

El menú `/config` → ComfyUI → LoRAs lee `GET /models/loras` del box, con cache de 60 s. No
depende del despliegue: si el archivo está en `models/loras/` del box, aparece. La inyección
se hace por flujo y usuario (`comfyui_lora_stacks`), al final de la cadena de modelo, sin
tocar los LoRAs horneados en el template.

## 7. Reiniciar y verificar

El bot corre como servicio de usuario `grok-bot.service`, gestionado por el shell `grokbot`.
**No recarga código solo.**

```bash
grokbot status
grokbot restart      # obligatorio tras cambiar código, .env, perfiles o _meta
grokbot logs
```

Verificación:

1. `COMFYUI_HOST` no vacío y con el valor correcto para el destino.
2. Túnel arriba: `curl -s http://127.0.0.1:<local>/system_stats`.
3. Journal del bot sin `SSH fetch workflow ... rc=1` (si `remote`).
4. `/config` → ComfyUI lista el flujo esperado.
5. Generación real: prompt de texto; para flujo de edición, foto + caption.
6. Suite completa: `.venv/bin/pytest -q` (no solo el módulo tocado).

## 8. Mapa de errores

| Mensaje / síntoma | Significado | Acción |
|---|---|---|
| «ComfyUI no está disponible en este momento» | `COMFYUI_HOST` vacío o provider no configurado | Completar `.env` y `grokbot restart` |
| «No se pudo establecer la conexión con la GPU remota» | El túnel no levanta o SSH falla | Probar el comando `ssh` a mano; revisar clave, host, puerto, `BatchMode` |
| `SSH fetch workflow ... rc=1` | En `remote`, el archivo no existe o no se llama por `_meta.id` | Desplegar `<id>.json` correcto; si no, usar `embed` |
| `Loaded ComfyUI workflow <id> from remote` | Camino sano en `remote` | — |
| El flujo no aparece en `/config` | El perfil activo no lo permite, o no está en `COMFYUI_FLOWS` | Revisar perfil y registro; usar `comfy-add-flow` |
| Un flujo de edición devuelve imagen sin foto | `_meta.requires_source` ausente o mal desplegado | Corregir `_meta` y redesplegar |
| La primera generación tarda 7–10 min | Carga en frío de pesos desde Drive | Esperar; no recrear la VM |
| El bot usa una versión vieja de un flujo | `embed` sin reinicio; `remote` con cache | `grokbot restart`; esperar TTL |

## 9. Seguridad

- `COMFYUI_HOST`/`COMFYUI_PORT` no son secretos, pero las coordenadas de Vast rotan: no
  fijarlas en documentación ni en commits.
- Nunca loguear tokens, `file_id`, payloads ni rutas privadas. Los errores user-safe solo
  llevan nombres de campo o `type(exception).__name__`.
- El túnel escucha en `127.0.0.1`, no en `0.0.0.0`.
- ComfyUI dentro del box escucha en loopback (`--listen 127.0.0.1`).
