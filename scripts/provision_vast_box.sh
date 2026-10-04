#!/usr/bin/env bash
#
# provision_vast_box.sh — (re)build the ComfyUI side of the Vast box that serves grokV2.
#
# Runs FROM THE REPO (it needs the templates and .env) and drives the box over SSH.
# Idempotent: every step skips what is already present, so re-running it is a no-op
# and doubles as a status report.
#
# Usage:
#   scripts/provision_vast_box.sh                    # everything
#   scripts/provision_vast_box.sh --only verify      # report only, changes nothing
#   scripts/provision_vast_box.sh --skip models      # skip the big downloads
#   scripts/provision_vast_box.sh --only env,links
#   scripts/provision_vast_box.sh --skip-ohwx       # verify warns instead of failing
#
# Secrets (never hardcoded here; pass through the environment):
#   CIVITAI_TOKEN   required for the Civitai downloads (401 without it)
#   HF_TOKEN        optional; only for gated/private HF repos
#
# Coordinates come from .env (COMFYUI_HOST / COMFYUI_PORT) or from --host/--port.
#
set -euo pipefail

# ---------------------------------------------------------------- configuration
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
TEMPLATES_DIR="$REPO_ROOT/src/grokbot/providers/comfyui/workflows/templates"

BOX_USER="${BOX_USER:-root}"
BOX_HOST="${COMFYUI_HOST:-}"
BOX_PORT="${COMFYUI_PORT:-}"
COMFYUI_DIR="${COMFYUI_DIR:-/workspace/ComfyUI}"
BOX_VENV="${BOX_VENV:-/venv/main}"
BOX_PY="$BOX_VENV/bin/python"

# The stack the flows were built and measured against. Changing these means re-measuring.
TORCH_VERSION="2.11.0+cu130"
TORCHVISION_VERSION="0.26.0+cu130"
TORCHAUDIO_VERSION="2.11.0+cu130"
TORCH_INDEX="https://download.pytorch.org/whl/cu130"

PHASES_DEFAULT="preflight env nodes models links workflows restart verify"

# Custom nodes the flows depend on, pinned to the commits they were validated with.
NODES=(
  "ComfyUI-Krea2-NAG|https://github.com/iljung1106/ComfyUI-Krea2-NAG.git|0afb38d"
  "comfyui-krea2edit|https://github.com/lbouaraba/comfyui-krea2edit.git|86f886d"
)

# Weights with a VERIFIED source. Format: path under models/|url|auth(none|hf|civitai)
MODELS=(
  "diffusion_models/krea2_turbo_fp8_scaled.safetensors|https://huggingface.co/Comfy-Org/Krea-2/resolve/main/diffusion_models/krea2_turbo_fp8_scaled.safetensors|none"
  "text_encoders/qwen3vl_4b_fp8_scaled.safetensors|https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors|none"
  "vae/qwen_image_vae.safetensors|https://huggingface.co/Comfy-Org/Krea-2/resolve/main/vae/qwen_image_vae.safetensors|none"
  "diffusion_models/Moody-Krea-Mix-v4.1G_00001__clean_nvfp4.safetensors|https://huggingface.co/catlover1937/moody-krea-mix/resolve/main/Moody-Krea-Mix-v4.1G_00001__clean_nvfp4.safetensors|none"
  "loras/krea2/krea2_identity_edit_v1_2.safetensors|https://huggingface.co/conradlocke/krea2-identity-edit/resolve/main/krea2_identity_edit_v1_2.safetensors|hf"
  "vae/wan_2.1_vae.safetensors|https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors|none"
  "loras/grokstyle_krea2_v2.safetensors|https://civitai.com/api/download/models/3278913|civitai"
  "diffusion_models/krea2SATDirtyRealism_uncut_fp8.safetensors|https://civitai.com/api/download/models/3372523?fileId=3260775|civitai"
)

# Referenced by flows, but with NO verified origin — only reported, never downloaded.
MANUAL_WEIGHTS=(
  "diffusion_models/qwen-image-edit-2511-Q4_K_M.gguf"
  "diffusion_models/qwen_image_2.1_int8_convrot.safetensors"
  "text_encoders/qwen3vl_8b_int8_convrot.safetensors"
  "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
  "vae/qwen_image_2.1_vae_bf16.safetensors"
  "checkpoints/Qwen-Rapid-AIO-NSFW-v23.safetensors"
  "loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
  "loras/Krea2NSFWV4.safetensors"
)
MANUAL_LIST="${MANUAL_WEIGHTS[*]}"

# ----------------------------------------------------------------------- helpers
c_ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[33m%s\033[0m\n' "$*"; }
c_err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
step()   { printf '\n\033[1m== %s\033[0m\n' "$*"; }

usage() {
  sed -n '3,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  printf '\nFases: %s\n' "$PHASES_DEFAULT"
  exit "${1:-0}"
}

ONLY=""; SKIP=""
# Identity LoRA is not in the download manifest (Drive, split parts). verify fails
# closed unless this is set. See docs/comfyui/INVENTARIO_BOX_VAST.md §9.
OHWX_REQUIRED=1
OHWX_REL="loras/ohwx_krea2.safetensors"
OHWX_SHA256="d453337ccb17ae1696b763c1315ca606baf3907ffe6b75beb493a327c40e488e"
OHWX_BYTES="457111520"
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    --skip) SKIP="${2:-}"; shift 2 ;;
    --skip-ohwx) OHWX_REQUIRED=0; shift ;;
    --host) BOX_HOST="${2:-}"; shift 2 ;;
    --port) BOX_PORT="${2:-}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) c_err "Opción desconocida: $1"; usage 1 ;;
  esac
done

if [ -f "$ENV_FILE" ]; then
  [ -n "$BOX_HOST" ] || BOX_HOST="$(grep -E '^COMFYUI_HOST=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' || true)"
  [ -n "$BOX_PORT" ] || BOX_PORT="$(grep -E '^COMFYUI_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' || true)"
fi
[ -n "$BOX_HOST" ] || { c_err "Falta COMFYUI_HOST (en .env o --host)."; exit 1; }
[ -n "$BOX_PORT" ] || { c_err "Falta COMFYUI_PORT (en .env o --port)."; exit 1; }
[ -d "$TEMPLATES_DIR" ] || { c_err "No encuentro $TEMPLATES_DIR"; exit 1; }

enabled() {
  case ",$SKIP," in *",$1,"*) return 1 ;; esac
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

box_ssh() { ssh -p "$BOX_PORT" -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$BOX_USER@$BOX_HOST" "$@"; }

# box_script [extra env assignments] — runs the stdin script on the box. Everything the
# remote script needs is passed as environment, so no value is ever interpolated into
# shell source (which is where quoting bugs live).
box_script() {
  ssh -p "$BOX_PORT" -o StrictHostKeyChecking=no -o ServerAliveInterval=20 \
      "$BOX_USER@$BOX_HOST" \
      "BOX_COMFYUI_DIR='$COMFYUI_DIR' BOX_VENV='$BOX_VENV' BOX_PY='$BOX_PY' \
       TORCH_VERSION='$TORCH_VERSION' TORCHVISION_VERSION='$TORCHVISION_VERSION' \
       TORCHAUDIO_VERSION='$TORCHAUDIO_VERSION' TORCH_INDEX='$TORCH_INDEX' \
       MANUAL_LIST='$MANUAL_LIST' TOKEN_FILE='$TOKEN_FILE' ${1:-} bash -s"
}

TOKEN_FILE="/tmp/.provision_tokens.$$"
cleanup() { box_ssh "rm -f '$TOKEN_FILE'" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Secrets travel in a 0600 file (never in argv, never in this terminal's output).
push_secrets() {
  { printf 'CIVITAI_TOKEN=%s\n' "${CIVITAI_TOKEN:-}"
    printf 'HF_TOKEN=%s\n' "${HF_TOKEN:-}"; } | box_ssh "umask 077; cat > '$TOKEN_FILE'"
}

step "Box $BOX_USER@$BOX_HOST:$BOX_PORT   fases: ${ONLY:-todas}${SKIP:+  (menos: $SKIP)}"
push_secrets

# ---------------------------------------------------------------------- preflight
if enabled preflight; then
  step "preflight — SO, GPU, driver, disco, ComfyUI"
  box_script <<'EOF'
set -uo pipefail
. /etc/os-release 2>/dev/null || true
echo "  SO      : ${PRETTY_NAME:-?}"
nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader | sed 's/^/  GPU     : /'
df -h / | tail -1 | awk '{print "  disco   : "$3" usados, "$4" libres ("$5")"}'
if [ -d "$BOX_COMFYUI_DIR" ]; then
  v=$(grep -o '"[0-9][0-9.]*"' "$BOX_COMFYUI_DIR/comfyui_version.py" 2>/dev/null | tr -d '"')
  echo "  ComfyUI : $BOX_COMFYUI_DIR (v$v)"
else
  echo "  ComfyUI : FALTA $BOX_COMFYUI_DIR"; exit 1
fi
[ -x "$BOX_PY" ] && echo "  python  : $($BOX_PY -V)" || { echo "  python  : FALTA $BOX_PY"; exit 1; }
drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | cut -d. -f1)
if [ "${drv:-0}" -ge 580 ]; then echo "  driver  : OK para CUDA 13.0 (>=580.65)"
else echo "  driver  : AVISO — hace falta >= 580.65 para $TORCH_VERSION"; fi
EOF
fi

# --------------------------------------------------------------------------- env
if enabled env; then
  step "env — stack de torch ($TORCH_VERSION)"
  box_script <<'EOF'
set -uo pipefail
have=$($BOX_PY -c "import torch;print(torch.__version__)" 2>/dev/null || echo none)
if [ "$have" = "$TORCH_VERSION" ]; then
  echo "  torch ya en $have — sin cambios"
else
  echo "  torch actual: $have -> instalando $TORCH_VERSION"
  . "$BOX_VENV/bin/activate"
  uv pip install --no-cache-dir \
    "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION" "torchaudio==$TORCHAUDIO_VERSION" \
    --index-url "$TORCH_INDEX"
fi
echo "  --- verificacion ---"
$BOX_PY -c "
import torch
print('  torch  :', torch.__version__, '| cuda:', torch.version.cuda, '| cap:', torch.cuda.get_device_capability())
a = torch.randn(1024, 1024, device='cuda', dtype=torch.bfloat16)
print('  matmul :', tuple((a @ a).shape))
" || echo "  AVISO: torch no importa"
echo "  ldd 'not found' (debe ser 0): $(ldd $BOX_VENV/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so 2>/dev/null | grep -c 'not found')"
EOF
fi

# ------------------------------------------------------------------------- nodes
if enabled nodes; then
  step "nodes — custom nodes pinneados"
  for entry in "${NODES[@]}"; do
    IFS='|' read -r name url commit <<<"$entry"
    box_script "NODE_NAME='$name' NODE_URL='$url' NODE_COMMIT='$commit'" <<'EOF'
set -uo pipefail
D="$BOX_COMFYUI_DIR/custom_nodes/$NODE_NAME"
if [ -d "$D/.git" ]; then
  echo "  $NODE_NAME: ya presente ($(git -C "$D" rev-parse --short HEAD))"
else
  echo "  $NODE_NAME: clonando $NODE_URL"
  git clone -q "$NODE_URL" "$D" && echo "  $NODE_NAME: clonado ($(git -C "$D" rev-parse --short HEAD))"
fi
EOF
  done
  c_warn "  Un node nuevo solo lo carga ComfyUI al reiniciar (fase restart)."
fi

# ------------------------------------------------------------------------ models
if enabled models; then
  step "models — pesos con origen verificado"
  missing=0
  for entry in "${MODELS[@]}"; do
    IFS='|' read -r rel url auth <<<"$entry"
    if box_ssh "[ -s '$COMFYUI_DIR/models/$rel' ]" 2>/dev/null; then
      printf '  ya esta  %-58s %s\n' "$rel" "$(box_ssh "du -h '$COMFYUI_DIR/models/$rel' | cut -f1")"
      continue
    fi
    missing=$((missing + 1))
    printf '  bajando  %-58s (%s)\n' "$rel" "$auth"
    box_script "REL='$rel' URL='$url' AUTH='$auth'" <<'EOF'
set -uo pipefail
# Read the secrets without sourcing the file (no shell evaluation of its contents).
CIVITAI_TOKEN="$(grep -m1 '^CIVITAI_TOKEN=' "$TOKEN_FILE" 2>/dev/null | cut -d= -f2- || true)"
HF_TOKEN="$(grep -m1 '^HF_TOKEN=' "$TOKEN_FILE" 2>/dev/null | cut -d= -f2- || true)"
dest="$BOX_COMFYUI_DIR/models/$REL"
mkdir -p "$(dirname "$dest")"
case "$AUTH" in
  civitai)
    [ -n "${CIVITAI_TOKEN:-}" ] || { echo "  ERROR: falta CIVITAI_TOKEN"; exit 1; }
    hdr=(-H "Authorization: Bearer $CIVITAI_TOKEN") ;;
  hf)
    [ -n "${HF_TOKEN:-}" ] || { echo "  ERROR: falta HF_TOKEN para un repo con token"; exit 1; }
    hdr=(-H "Authorization: Bearer $HF_TOKEN") ;;
  *) hdr=() ;;
esac
if curl -L --fail --silent --show-error "${hdr[@]}" -o "$dest.part" "$URL"; then
  mv "$dest.part" "$dest"
  echo "  OK $(du -h "$dest" | cut -f1)"
else
  echo "  ERROR de descarga — no dejo archivo parcial"
  rm -f "$dest.part"
  exit 1
fi
EOF
  done
  [ "$missing" -eq 0 ] && c_ok "  los 8 pesos del manifiesto ya estaban"
fi

# ------------------------------------------------------------------------- links
if enabled links; then
  step "links — symlink del VAE en subcarpeta y hardlink del VAE Wan"
  box_script <<'EOF'
set -uo pipefail
M="$BOX_COMFYUI_DIR/models"
# donut_face pide vae/qwen-image/qwen_image_vae.safetensors; el archivo vive en vae/
mkdir -p "$M/vae/qwen-image"
ln -sfn ../qwen_image_vae.safetensors "$M/vae/qwen-image/qwen_image_vae.safetensors"
echo "  symlink : vae/qwen-image/qwen_image_vae.safetensors -> $(readlink "$M/vae/qwen-image/qwen_image_vae.safetensors")"
if [ ! -e "$M/vae/Wan2_1_VAE_fp32.safetensors" ] && [ -f "$M/vae/wan_2.1_vae.safetensors" ]; then
  ln "$M/vae/wan_2.1_vae.safetensors" "$M/vae/Wan2_1_VAE_fp32.safetensors"
fi
if [ -e "$M/vae/Wan2_1_VAE_fp32.safetensors" ]; then
  echo "  hardlink: Wan2_1_VAE_fp32.safetensors $(stat -c '(%h enlaces, inodo %i)' "$M/vae/Wan2_1_VAE_fp32.safetensors")"
else
  echo "  hardlink: FALTA (requiere vae/wan_2.1_vae.safetensors)"
fi
EOF
fi

# --------------------------------------------------------------------- workflows
if enabled workflows; then
  step "workflows — desplegar templates al box (nombrados por _meta.id, incluye ohwx_*)"
  count=0
  for tpl in "$TEMPLATES_DIR"/*.json; do
    id="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['_meta']['id'])" "$tpl" 2>/dev/null || true)"
    if [ -z "$id" ]; then c_warn "  sin _meta.id, salteo: $(basename "$tpl")"; continue; fi
    scp -q -P "$BOX_PORT" -o StrictHostKeyChecking=no "$tpl" \
      "$BOX_USER@$BOX_HOST:$COMFYUI_DIR/user/default/api_workflows/$id.json"
    count=$((count + 1))
  done
  c_ok "  $count workflows desplegados (el remoto se llama por _meta.id, no por el template)"
fi

# ----------------------------------------------------------------------- restart
if enabled restart; then
  step "restart — supervisor comfyui"
  box_script <<'EOF'
set -uo pipefail
supervisorctl restart comfyui >/dev/null 2>&1 || true
code=000
for _ in $(seq 1 45); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18188/system_stats || true)
  [ "$code" = "200" ] && break
  sleep 4
done
echo "  HTTP /system_stats: $code"
supervisorctl status comfyui | sed 's/^/  /'
EOF
fi

# ------------------------------------------------------------------------ verify
if enabled verify; then
  step "verify — chequeos del inventario §8 + LoRA Ohwx"
  box_script "OHWX_REL='$OHWX_REL' OHWX_SHA256='$OHWX_SHA256' OHWX_BYTES='$OHWX_BYTES' OHWX_REQUIRED='$OHWX_REQUIRED'" <<'EOF'
set -uo pipefail
echo "  torch          : $($BOX_PY -c 'import torch;print(torch.__version__)' 2>/dev/null || echo FALLA)"
echo "  cuda disponible: $($BOX_PY -c 'import torch;print(torch.cuda.is_available())' 2>/dev/null || echo FALLA)"
echo "  ldd not found  : $(ldd $BOX_VENV/lib/python3.12/site-packages/torch/lib/libtorch_cuda.so 2>/dev/null | grep -c 'not found')  (debe ser 0)"
echo "  comfy pytorch  : $(curl -s http://127.0.0.1:18188/system_stats | $BOX_PY -c 'import sys,json;print(json.load(sys.stdin)["system"]["pytorch_version"])' 2>/dev/null || echo FALLA)"
n=$(tail -200 /var/log/portal/comfyui.log 2>/dev/null | grep -ciE 'IMPORT FAILED|Failed to import' || true)
echo "  imports fallidos: ${n:-0}"
echo "  --- flujos (nodos y pesos) ---"
$BOX_PY - <<'PY'
import json, os, glob, urllib.request
oi = json.load(urllib.request.urlopen("http://127.0.0.1:18188/object_info", timeout=60))
KEYS = {"unet_name": "diffusion_models", "ckpt_name": "checkpoints", "clip_name": "text_encoders",
        "vae_name": "vae", "lora_name": "loras"}
comfy = os.environ.get("BOX_COMFYUI_DIR", "/workspace/ComfyUI")
M = os.path.join(comfy, "models")
ok = bad = 0
for f in sorted(glob.glob(os.path.join(comfy, "user/default/api_workflows/*.json"))):
    wf = json.load(open(f)); meta = wf.pop("_meta", {})
    mn = sorted({n["class_type"] for n in wf.values()
                 if isinstance(n, dict) and n.get("class_type") not in oi})
    mm = sorted({f"{KEYS[k]}={v}" for n in wf.values() if isinstance(n, dict)
                 for k, v in (n.get("inputs") or {}).items()
                 if k in KEYS and isinstance(v, str)
                 and not os.path.exists(os.path.join(M, KEYS[k], v))})
    if mn or mm:
        bad += 1
        print("    %-16s ROTO   nodos=%d pesos=%d %s" % (meta.get("id"), len(mn), len(mm), mm[:2]))
    else:
        ok += 1
print("    %d ejecutables, %d rotos" % (ok, bad))
PY
echo "  --- pesos sin origen verificado (cargar a mano) ---"
for w in $MANUAL_LIST; do
  if [ -s "$BOX_COMFYUI_DIR/models/$w" ]; then echo "    ok     $w"; else echo "    FALTA  $w"; fi
done
echo "  --- LoRA Ohwx (manual: Drive + join; no está en la fase models) ---"
dest="$BOX_COMFYUI_DIR/models/$OHWX_REL"
if [ ! -s "$dest" ]; then
  if [ "${OHWX_REQUIRED:-1}" = "0" ]; then
    echo "    OMITIDA  $OHWX_REL (--skip-ohwx). ohwx_krea2 / ohwx_edit / ohwx_dirty_edit no van a correr."
  else
    echo "    FALTA    $dest"
    echo "    ERROR: la LoRA de identidad Ohwx no está. No se descarga sola."
    echo "    Unir las 5 partes de Drive y dejar el archivo en models/loras/ (457111520 bytes)."
    echo "    SHA-256 esperado: $OHWX_SHA256"
    echo "    Receta: docs/comfyui/INVENTARIO_BOX_VAST.md §9. Para no abortar: --skip-ohwx"
    exit 1
  fi
else
  bytes=$(stat -Lc %s "$dest")
  sum=$(sha256sum "$dest" | awk '{print $1}')
  if [ "$bytes" != "$OHWX_BYTES" ] || [ "$sum" != "$OHWX_SHA256" ]; then
    echo "    CORRUPTA $OHWX_REL"
    echo "    bytes=$bytes (esperado $OHWX_BYTES)"
    echo "    sha256=$sum"
    echo "    esperado=$OHWX_SHA256"
    echo "    ERROR: no uses este archivo. Vuelve a unir las partes de Drive."
    exit 1
  fi
  echo "    ok       $OHWX_REL ($bytes bytes, sha256 coincide)"
fi
EOF
fi

step "recordatorio"
c_warn "Cambiar el _meta de un flujo exige la fase 'workflows' Y reiniciar el bot (grokbot restart)."
c_warn "Reiniciar ComfyUI NO basta para el bot: son procesos distintos."
