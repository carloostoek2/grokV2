"""Política de reintentos de la capa application (D5, item 4).

Constante de retry del caso de uso de imagen y backoff espejo de grok
(``POLL_RETRY_BACKOFF_SEC`` = (2, 4, 8)). La constante vive acá (capa app) y el
backoff se importa de ``providers/base.py`` dentro de la función para no acoplar
al import-time del módulo.
"""

from __future__ import annotations

# Número máximo de REINTENTOS tras el primer intento (paridad grok bot.py:170).
# El loop total corre GENERATE_MAX_RETRIES + 1 intentos.
GENERATE_MAX_RETRIES = 5


def _retry_backoff(attempt: int) -> float:
    """Segundos a dormir ANTES de reintentar el intento ``attempt`` (0-based) ya fallido.

    Espejo de grok ``_retry_backoff`` (bot.py:243): backoff escalonado que se
    satura en el índice 2 → (2, 4, 8).
    """
    from grokbot.providers.base import POLL_RETRY_BACKOFF_SEC

    return POLL_RETRY_BACKOFF_SEC[min(attempt, 2)]
