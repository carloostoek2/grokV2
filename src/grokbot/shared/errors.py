"""Helpers de error del entrypoint (item 6, D8) — stdlib-only.

La superficie de error de arranque del bot vive acá como funciones puras.
``settings_error_user_message`` formatea un ``pydantic.ValidationError`` de
``Settings`` listando SOLO los nombres de campo afectados: nunca incluye el
valor que falló ni el mensaje crudo de pydantic (que puede contener secretos).

La jerarquía de errores existente de las capas NO se toca ni se duplica:
``ProviderError*`` sigue en ``providers/base.py``, ``MediaFetchError`` en
``telegram/ports.py`` y ``DownloadError`` en ``telegram/downloader.py``. Este
módulo solo aporta helpers NUEVOS del entrypoint y usa duck-typing
(``hasattr(exc, "errors")``) para detectar errores de pydantic sin importarlo,
manteniendo el paquete ``shared`` desacoplado del modelo de datos.
"""

from __future__ import annotations


def settings_error_user_message(exc: Exception) -> str:
    """Mensaje user-safe de un ``pydantic.ValidationError`` de Settings.

    Lista SOLO nombres de campo (p. ej. ``telegram_bot_token``). Nunca incluye
    valores (no usa ``str(exc)`` ni ``err.get("input")``): si ``exc`` no parece
    un ValidationError (carece de ``errors()`` que devuelva ``list``), devuelve
    un mensaje genérico sin detalles.
    """
    errors_fn = getattr(exc, "errors", None)
    if callable(errors_fn):
        try:
            details = errors_fn()
        except Exception:
            details = None
        if isinstance(details, list):
            fields: list[str] = []
            for err in details:
                loc = err.get("loc") if isinstance(err, dict) else None
                if loc:
                    fields.append(".".join(str(part) for part in loc))
            fields = list(dict.fromkeys(fields))  # orden estable, sin duplicados
            if fields:
                return "Error de configuración: campos requeridos o inválidos — " + ", ".join(fields) + "."
    return "Error de configuración. Revisa las variables de entorno y vuelve a intentar."
