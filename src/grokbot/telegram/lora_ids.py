"""Short ids para ``callback_data`` de LoRAs (límite de 64 bytes).

Un nombre de archivo como ``krea2/krea2_identity_edit_v1_2.safetensors`` no
cabe en un callback de Telegram junto al prefijo. El id corto es determinista
(sha1 truncado), así que un botón viejo sigue resolviendo si el catálogo se
vuelve a consultar tras un reinicio del bot.
"""

from __future__ import annotations

import hashlib

LORA_SHORT_ID_LEN = 8


def lora_short_id(name: str) -> str:
    """``sha1(name)[:8]`` estable para cualquier nombre de archivo."""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return digest[:LORA_SHORT_ID_LEN]


__all__ = ["LORA_SHORT_ID_LEN", "lora_short_id"]
