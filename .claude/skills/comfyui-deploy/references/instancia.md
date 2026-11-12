# Registro de la instancia actual

> Ayuda operativa de `comfyui-deploy`. El registro real vive en
> **`/home/ubuntu/comfy-colab/instancia_actual.txt`**: texto plano, permisos `0600` y
> **nunca versionado** (contiene host y puerto SSH). Este documento explica cómo crearlo,
> leerlo y rotarlo.

## 1. Para qué existe

El usuario envía la **dirección SSH una sola vez**, en el primer mensaje de la sesión.
A partir de ahí:

- la skill **lee** `instancia_actual.txt` en lugar de volver a pedir el acceso;
- las especificaciones medidas (GPU, VRAM, RAM, disco, SO, perfil, velocidad de HF) quedan
  guardadas para no repetir el reconocimiento en cada entrada;
- si se cambia de instancia, basta con enviar la nueva dirección: el archivo se reescribe y
  la anterior pasa al historial.

El archivo **no sustituye** a la `.env` del bot ni a los inventarios de
`docs/comfyui/`: es estado local de la sesión de despliegue.

## 2. Formato

Plano, `clave = valor`, una por línea. Las líneas que empiezan por `#` son comentarios.
El bloque `[historial]` guarda las instancias anteriores (máximo 10, la más reciente
primero).

```text
# instancia_actual.txt — estado local del despliegue. NO versionar. 0600.
actualizado = 2026-11-12T00:40:00Z
tipo = vast
host = <HOST>
puerto_ssh = <PUERTO>
usuario = root
comando_ssh = ssh -o BatchMode=yes -p <PUERTO> root@<HOST>
gpu = NVIDIA L4
vram_gb = 23.7
driver = 580.65.06
ram_gb = 62.8
cpu = AMD EPYC 7B13 8-Core Processor · 8 vCPU
so = Ubuntu 22.04.4 LTS
disco_path = /workspace
disco_libre_gb = 118.4
disco_fstype = ext4
imagen = con-comfyui
comfyui = /workspace/ComfyUI
comfyui_version = 0.43.2
comfyui_puerto = 18188
perfil = l4
hf_mb_s = 62.5
notas =

[historial]
2026-11-11T10:03:00Z | vast | <HOST_ANTERIOR>:<PUERTO_ANTERIOR> | NVIDIA L4 24 GB | perfil l4
```

`tipo` es `vast` o `colab`. En Colab, `host` es el alias `colab-comfy` y `puerto_ssh` es
`22` (el alias resuelve el resto).

## 3. Alta de una instancia Vast

### 3.1 Normalizar la dirección

Acepta cualquiera de estas formas y extrae host, puerto y usuario:

| Lo que escribe el usuario | host | puerto | usuario |
|---|---|---|---|
| `ssh -p 2222 root@203.0.113.10` | `203.0.113.10` | `2222` | `root` |
| `ssh root@203.0.113.10 -p 2222` | `203.0.113.10` | `2222` | `root` |
| `root@203.0.113.10:2222` | `203.0.113.10` | `2222` | `root` |
| `203.0.113.10:2222` | `203.0.113.10` | `2222` | `root` |
| `ssh root@203.0.113.10` | `203.0.113.10` | `22` | `root` |

Si falta el usuario, asume `root` (Vast). Si falta el puerto, `22`. **No inventes** una
dirección que el usuario no haya dado.

### 3.2 Comprobar acceso

```bash
HOST="<HOST>"; PORT="<PUERTO>"; USER=root
ssh -o BatchMode=yes -o ConnectTimeout=10 -p "$PORT" "$USER@$HOST" 'echo acceso_ok'
```

Si falla, no escribas el registro: reporta el error exacto (clave, puerto o host) y conserva
el archivo anterior.

### 3.3 Sondear la máquina en una sola conexión

```bash
HOST="<HOST>"; PORT="<PUERTO>"; USER=root
ssh -o BatchMode=yes -o ConnectTimeout=10 -p "$PORT" "$USER@$HOST" 'bash -s' <<'REMOTE'
set -u
line() { printf '%s = %s\n' "$1" "$2"; }

if command -v nvidia-smi >/dev/null 2>&1; then
  line gpu "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  line vram_gb "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits \
    | head -1 | awk '{printf "%.1f", $1/1024}')"
  line driver "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
else
  line gpu desconocida
  line vram_gb 0
  line driver desconocido
fi

line ram_gb "$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)"
line cpu "$(awk -F: '/model name/ {gsub(/^[ \t]+/,"",$2); print $2; exit}' /proc/cpuinfo \
  | sed 's/ $//') · $(nproc) vCPU"
line so "$(. /etc/os-release && printf '%s' "$PRETTY_NAME")"

P=/workspace; [ -d "$P" ] || P=/
line disco_path "$P"
line disco_libre_gb "$(df -B1 "$P" | awk 'NR==2 {printf "%.1f", $4/1e9}')"
line disco_fstype "$(findmnt -n -o FSTYPE -T "$P" 2>/dev/null || echo '?')"

if [ -f /workspace/ComfyUI/main.py ]; then
  line imagen con-comfyui
  line comfyui /workspace/ComfyUI
  ver_git="$(cd /workspace/ComfyUI && git describe --tags --always 2>/dev/null || echo '?')"
else
  line imagen limpia
  line comfyui -
  ver_git=-
fi

# Versión y puerto en vivo: se emiten una sola vez al final.
ver_api=-
puerto=-
for p in 18188 8188; do
  if curl -fsS --max-time 3 "http://127.0.0.1:$p/system_stats" >/tmp/_stats.json 2>/dev/null; then
    puerto=$p
    ver_api="$(grep -o '"comfyui_version"[[:space:]]*:[[:space:]]*"[^"]*"' \
      /tmp/_stats.json | head -1 | cut -d'"' -f4)"
    break
  fi
done
line comfyui_puerto "${puerto:--}"
line comfyui_version "${ver_api}"
if [ "$ver_api" = "-" ]; then
  line comfyui_version_git "$ver_git"
fi
REMOTE
```

Velocidad real contra Hugging Face (15 s, corta con `-r`):

```bash
ssh -o BatchMode=yes -p "$PORT" "$USER@$HOST" \
  'curl -L -r 0-104857600 --max-time 15 -o /dev/null -w "%{speed_download}" \
   https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors' \
  | awk '{printf "hf_mb_s = %.1f\n", $1/1e6}'
```

Si `curl` no existe en la imagen, instalar (`apt-get install -y curl`) y repetir; sin ese
dato no se elige perfil (§5.2 de `SKILL.md`).

El perfil se deduce de la GPU según la tabla de §5.2 y se escribe en `perfil`.

### 3.4 Escribir el archivo

```bash
REG=/home/ubuntu/comfy-colab/instancia_actual.txt
umask 077
# Si existía un registro, primero se copia su línea al [historial].
{
  printf '# instancia_actual.txt — estado local del despliegue. NO versionar. 0600.\n'
  printf 'actualizado = %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'tipo = vast\n'
  printf 'host = %s\n' "$HOST"
  printf 'puerto_ssh = %s\n' "$PORT"
  printf 'usuario = %s\n' "$USER"
  # …resto de claves medidas…
} > "$REG"
chmod 600 "$REG"
```

Nunca uses `>>` sobre un registro vivo: se reescribe entero. La única parte que se conserva
es `[historial]`.

## 4. Caso Colab

Colab no se registra con host/puerto: se registra con el alias y lo que ya sabe el CLI.

1. **No abras un segundo `colab ssh`**: con una sesión viva puede dar 429 y tumbarla
   (regla dura 3). Usa el alias `colab-comfy` y el túnel ya abierto.
2. Reúne los datos con:

   ```bash
   colab-comfy status
   curl -s http://127.0.0.1:8188/system_stats
   ```

   `system_stats` aporta GPU, VRAM, RAM, SO y `comfyui_version`.
3. Disco: `df -h /content` y `/content/drive` **dentro de la VM**. Si no hay canal de
   ejecución sin abrir sesión nueva, deja `disco_libre_gb` vacío y anótalo en `notas`; no
   arranques otra sesión solo para medir.
4. Escribe `tipo = colab`, `host = colab-comfy`, `puerto_ssh = 22`,
   `comfyui = /content/ComfyUI`, `comfyui_puerto = 8188`, `disco_path = /content/drive`.

## 5. Reutilización y rotación

Al empezar cualquier sesión de despliegue:

1. **Lee** `/home/ubuntu/comfy-colab/instancia_actual.txt` si existe.
2. Si el primer mensaje del usuario trae una dirección SSH:
   - mismo `host:puerto` → reutiliza; no vuelvas a probar ni preguntar;
   - distinto → es una instancia nueva: avisa en **una línea**, pasa la anterior a
     `[historial]` y reescribe.
3. Si no hay dirección y no hay archivo, **pide el acceso una sola vez**. No sigas con
   coordenadas inventadas ni con las de `.env` sin confirmarlas.
4. Si hay archivo pero el acceso ya no responde, repórtalo y pide la dirección nueva; no
   borres el registro hasta tener un reemplazo válido.

El archivo sobrevive a cerrar y abrir Claude Code. Solo se invalida si la instancia se
recrea (Vast rota IP/puerto) o si el usuario indica otra máquina.

## 6. Consistencia con el bot

Tras registrar una instancia nueva, comprueba **sin imprimir valores** que la `.env` del bot
apunta a la misma máquina:

```bash
ACTUAL=$(sed -n 's/^COMFYUI_HOST=//p' /home/ubuntu/repos/grokV2/.env \
  | tr -d '"' | tr -d "'" | tr -d '[:space:]' | head -1)
[ "$ACTUAL" = "$HOST" ] && echo "host coincide" || echo "host NO coincide"
```

Si no coincide, actualiza `COMFYUI_HOST`, `COMFYUI_PORT` y, en Vast,
`COMFYUI_REMOTE_PORT=18188`; después `grokbot restart`. Reporta solo «coincide» o
«actualizado», nunca el valor.

## 7. Seguridad

- `instancia_actual.txt` está en `.gitignore` de `/home/ubuntu/comfy-colab` y debe quedar en
  `0600`. No se commitea, no se copia a `docs/`, no se pega en el chat.
- No guardes aquí claves privadas, tokens ni `secrets.env`: solo coordenadas y
  especificaciones.
- Al resumir la instancia, di GPU, VRAM, perfil y disco; omite host y puerto salvo que el
  usuario los pida explícitamente.
