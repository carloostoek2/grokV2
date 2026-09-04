"""Helpers compartidos de los handlers de telegram (item 5).

Utilidades de routing/armado SIN registro de handlers ni copy de negocio:
filtros de mensaje de prompt, degradaciones D8 (mensajes user-safe), constructor
del :class:`ResultSender`, reconstrucción de ``cfg_override`` para regen (parity
grok ``_model_from_regen`` bot.py 769-777) y resolución de ref Kie al responder a
una foto del bot (parity ``_resolve_reply_kie_ref`` bot.py 813-823).

Reglas de capa (SPEC §5.2): aquí solo se importa stdlib + aiogram + domain/
application; todo I/O de media se delega a los seams inyectados (gateway/
downloader) desde los handlers.
"""

from __future__ import annotations

import re
from dataclasses import replace

from aiogram import types

from grokbot.domain.catalog import DEFAULT_MODEL, MODELS, resolve_grok_config
from grokbot.domain.generation import KieTaskRef
from grokbot.domain.user_config import UserConfig
from grokbot.telegram.deps import BotDeps
from grokbot.telegram.ports import TelegramGateway
from grokbot.telegram.sender import ResultSender

# Tope de captions que disparan long-prompt collection en grok (bot.py:74);
# por encima de este largo, una foto no edita: guarda el file_id y pide el
# prompt como mensaje de texto (ver generation._complete_long_prompt_collection).
TELEGRAM_CAPTION_COLLECT_THRESHOLD = 1020

# --- Degradaciones D8 (flujos de grok sin use case; residuales del pool) ------
D8_FACESWAP_MSG = (
    "El modo Face Swap no está disponible en esta versión. Usa /config para cambiar de modelo."
)
D8_INTEGRATE_MSG = (
    "La edición con referencia (/s) no está disponible en esta versión. "
    "Envía el caption sin /s."
)
D8_CMD_MSG = "Este comando no está disponible en esta versión todavía."
D8_REPLY_NO_PHOTO = "Responde a una foto para editarla."

# Máximo de fotos de un media group que grok edita (paridad bot.py). Por encima
# el álbum degrada con el copy exacto de grok (generation._drain_grok_album).
INTEGRATE_MAX_ALBUM = 10

# Degradación user-safe de media de origen no recuperable (M2): file_id
# expirado/roto → este mensaje, nunca el error crudo del gateway.
SOURCE_MEDIA_UNAVAILABLE_MSG = (
    "No se pudo recuperar la imagen. Puede que el archivo haya expirado. "
    "Envíala de nuevo."
)

# Comandos residuales que se degradan (D8) sin implementar su flujo.
D8_COMMANDS = ("cambiar_source", "cambiar_referencia")


def is_d8_command(command: str | None) -> bool:
    """True cuando el comando es uno de los flujos residuales no implementados."""
    return command in D8_COMMANDS


# --- Filtros de routing (aiogram) --------------------------------------------
def is_plain_prompt(message: types.Message) -> bool:
    """Texto plano de usuario para generar (no comando, no reply, no caption).

    Espejo de grok ``_is_generation_prompt_message`` + ``_is_bot_command_message``
    (bot.py 285-304): excluye comandos Telegram y replies (esos rutean aparte).
    """
    if not message.text:
        return False
    if message.reply_to_message is not None:
        return False
    stripped = message.text.lstrip()
    if stripped.startswith("/") and len(stripped) > 1 and stripped[1].isalnum():
        return False
    return True


def is_reply_edit(message: types.Message) -> bool:
    """Texto con reply (edit/reply-edit); excluye comandos (rutean aparte)."""
    if not message.text or message.reply_to_message is None:
        return False
    stripped = message.text.lstrip()
    if stripped.startswith("/") and len(stripped) > 1 and stripped[1].isalnum():
        return False
    return True


def is_photo_caption(message: types.Message) -> bool:
    """Foto individual (no álbum) con caption. Los álbumes rutean a degradación."""
    return bool(message.photo) and bool(message.caption) and not message.media_group_id


def is_photo_no_caption(message: types.Message) -> bool:
    """Foto individual (no álbum) sin caption → hints de edit/video."""
    return bool(message.photo) and not message.caption and not message.media_group_id


def is_album(message: types.Message) -> bool:
    """Foto perteneciente a un media group (álbum) → colección/edición secuencial."""
    return bool(message.photo) and bool(message.media_group_id)


def largest_photo(message: types.Message) -> str | None:
    """file_id de la foto de mayor resolución (parity ``message.photo[-1]``)."""
    if not message.photo:
        return None
    return message.photo[-1].file_id


# --- Paridad de parse de captions /var y /variables (bot.py 2019-2045/2532-2578)
_VARIABLES_RE = re.compile(r"^/variables(?:@[A-Za-z0-9_]+)?(?:\s+(\d+))?\s*$", re.IGNORECASE)
_VAR_CMD_RE = re.compile(r"^/var(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def is_variables_command(text: str | None) -> bool:
    """True cuando caption/reply/text es una invocación /variables (palabra clave)."""
    if not text:
        return False
    return re.match(r"^/variables(?:@|(?:\s|$))", text.strip(), re.IGNORECASE) is not None


def parse_variables_count(text: str | None) -> int | None:
    """Parse '/variables N' → N clamp [1, VARIABLES_MAX]; bare → 1; inválido → None."""
    if not text:
        return None
    m = _VARIABLES_RE.match(text.strip())
    if not m:
        return None
    if m.group(1) is None:
        return 1
    n = int(m.group(1))
    if n < 1:
        return None
    from grokbot.domain.variables import VARIABLES_MAX

    return min(n, VARIABLES_MAX)


def is_var_command(text: str | None) -> bool:
    """True cuando caption/reply/text es una invocación /var (no /variables)."""
    if not text:
        return False
    return re.match(r"^/var(?:@|(?:\s|$))", text.strip(), re.IGNORECASE) is not None


def parse_var_prompt(text: str | None) -> str | None:
    """Extrae el prompt inline de '/var <texto>' (None si no hay prompt)."""
    if not text:
        return None
    m = _VAR_CMD_RE.match(text.strip())
    if not m or not m.group(1):
        return None
    prompt = m.group(1).strip()
    return prompt or None


def parse_var_count_and_text(text: str | None) -> tuple[int, str | None]:
    """Parse '/var [N] <texto>' → (count, prompt) (ambigüedad resuelta, bot.py 2561-2578)."""
    prompt = parse_var_prompt(text)
    if prompt is None:
        return 1, None
    from grokbot.domain.variables import VARIABLES_MAX

    parts = prompt.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        n = int(parts[0])
        if 1 <= n <= VARIABLES_MAX:
            return n, parts[1].strip()
    return 1, prompt


def parse_integrate_caption(caption: str) -> tuple[bool, str]:
    """Detecta el prefijo '/s' (integrate); en grokV2 se degrada D8."""
    text = caption.strip()
    if not text.startswith("/s"):
        return False, text
    prompt = re.sub(r"^/s(?:\s+|$)", "", text, count=1).strip()
    return True, prompt


# --- Armado (parity helpers de grok) -----------------------------------------
def make_sender(deps: BotDeps) -> ResultSender:
    """ResultSender con los seams inyectados del deps."""
    return ResultSender(gateway=deps.gateway, downloader=deps.downloader, refs=deps.refs)


def effective_image_provider(cfg: UserConfig) -> str:
    """Provider efectivo de IMAGEN de la config (parity get_model provider).

    Espejo de la resolución de grok ``get_model`` sin tocar el registry (este
    módulo no puede importar ``providers/*`` — layering). Para ``grok`` resuelve
    el provider granular; seedream/faceswap → replicate; comfyui → comfyui;
    grok_video no genera imágenes (devuelve su proveedor de video por completitud).
    """
    if cfg.model == "grok":
        return resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)["provider"]
    if cfg.model in ("seedream", "faceswap"):
        return "replicate"
    if cfg.model == "comfyui":
        return "comfyui"
    if cfg.model == "grok_video":
        from grokbot.domain.user_config import video_provider_for_config

        return video_provider_for_config(cfg) or "xai"
    return MODELS.get(cfg.model, MODELS[DEFAULT_MODEL]).get("provider", "?")


def resolve_reply_kie_ref(deps: BotDeps, reply: types.Message | None) -> KieTaskRef | None:
    """KieTaskRef cuando se responde a una imagen Kie del bot (sin re-descargar).

    Parity grok ``_resolve_reply_kie_ref`` (813-823): usa ``generation_refs`` del
    mensaje respondido; None cuando no es un ref kie o no hay ref.
    """
    if reply is None or not reply.photo:
        return None
    ref = deps.refs.get(reply.chat.id, reply.message_id)
    if not ref or ref.get("provider") != "kie" or not ref.get("kie_task_id"):
        return None
    return KieTaskRef(task_id=str(ref["kie_task_id"]), index=int(ref.get("kie_index", 0)))


def cfg_override_from_regen(cfg: UserConfig, regen: dict) -> UserConfig:
    """Reconstruir la config efectiva de un regen (parity ``_model_from_regen``).

    El ``regen_context`` NO guarda un ``UserConfig`` completo (solo claves
    mínimas): aplica el ``model_key`` y, para grok, el proveedor/variante de
    Grok Imagine del momento de la base. ComfyUI usa la config ACTUAL (grok
    ``get_model(uid)`` para comfyui en regen).
    """
    key = regen.get("model_key", cfg.model)
    if key != cfg.model:
        cfg = replace(cfg, model=key)
    if key == "grok":
        prov = regen.get("imagine_provider")
        var = regen.get("imagine_variant")
        if prov and prov != cfg.grok_imagine_provider:
            cfg = replace(cfg, grok_imagine_provider=prov)
        if var and var != cfg.grok_imagine_variant:
            cfg = replace(cfg, grok_imagine_variant=var)
    return cfg


async def answer_callback(gateway, callback: types.CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> None:
    """Respuesta de callback por el gateway inyectado (0 ``callback.answer``)."""
    await gateway.answer_callback(callback.id, text, show_alert=show_alert)


async def fetch_source_bytes(gateway: TelegramGateway, file_id: str | None) -> bytes | None:
    """Descargar el ``file_id`` de la imagen fuente → bytes, o ``None``.

    ``None`` cuando no hay ``file_id`` o cuando el gateway no pudo recuperar la
    media (file_id expirado/roto). Todo error se traduce a ``None`` — el handler
    degrada con :data:`SOURCE_MEDIA_UNAVAILABLE_MSG` (M2). Nunca loguea ni expone
    el ``file_id`` (R6/R8).
    """
    if not file_id:
        return None
    try:
        return await gateway.get_file_bytes(file_id)
    except Exception:  # noqa: BLE001 — todo error de fetch es user-safe (M2/R6)
        return None


__all__ = [
    "D8_CMD_MSG",
    "D8_COMMANDS",
    "D8_FACESWAP_MSG",
    "D8_INTEGRATE_MSG",
    "D8_REPLY_NO_PHOTO",
    "INTEGRATE_MAX_ALBUM",
    "SOURCE_MEDIA_UNAVAILABLE_MSG",
    "TELEGRAM_CAPTION_COLLECT_THRESHOLD",
    "answer_callback",
    "cfg_override_from_regen",
    "effective_image_provider",
    "fetch_source_bytes",
    "is_album",
    "is_d8_command",
    "is_photo_caption",
    "is_photo_no_caption",
    "is_plain_prompt",
    "is_reply_edit",
    "is_var_command",
    "is_variables_command",
    "largest_photo",
    "make_sender",
    "parse_integrate_caption",
    "parse_var_count_and_text",
    "parse_var_prompt",
    "parse_variables_count",
    "resolve_reply_kie_ref",
]
