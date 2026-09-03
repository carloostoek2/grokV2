"""Bootstrap central del logging del entrypoint (item 6, D1b) — stdlib-only.

``configure_logging`` es el único punto que llama a ``logging.basicConfig`` del
proceso (SPEC §5.1/§6 observabilidad). El formatter es plano (timestamp,
nivel, logger y mensaje) y no incluye secrets: los mensajes que el bot loguea
jamás deben contener tokens/IDs/payloads (las capas ya cuidan eso; el
entrypoint loguea solo nombres de campo y ``type(exception).__name__``).
"""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configurar el logging raíz del proceso (idempotente con ``force=True``)."""
    logging.basicConfig(level=level, format=_LOG_FORMAT, force=True)
