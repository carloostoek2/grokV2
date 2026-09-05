"""Integrate-reference use case: set/read the per-user fixed reference image (R4 Item 3)."""

from __future__ import annotations

from dataclasses import replace

from grokbot.domain.catalog import resolve_grok_config
from grokbot.repositories.base import IntegrateRefsRepository, SessionRepository

REQUIRES_XAI_MSG = (
    "La edición con referencia (/s) requiere el proveedor "
    "<b>xAI (oficial)</b>. Cambialo en /config."
)
NO_REFERENCE_MSG = (
    "No hay imagen de referencia configurada. "
    "Usa /cambiar_referencia para establecerla."
)


class IntegrateReferenceError(Exception):
    """Precondition failure of an integrate edit (user-safe)."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _effective_provider_is_xai(cfg) -> bool:
    # Solo grok usa integrate; el provider efectivo sale de grok_imagine_*.
    return cfg.model == "grok" and resolve_grok_config(
        cfg.grok_imagine_provider, cfg.grok_imagine_variant
    )["provider"] == "xai"


class IntegrateRefsUseCase:
    def __init__(self, *, sessions: SessionRepository, refs: IntegrateRefsRepository) -> None:
        self._sessions = sessions
        self._refs = refs

    def set_reference(self, user_id: int, data: bytes) -> str:
        path = self._refs.save(user_id, data)
        cfg = self._sessions.get_config(user_id)
        if cfg.integrate_ref_path != path:
            self._sessions.save_config(user_id, replace(cfg, integrate_ref_path=path))
        return path

    def reference_available(self, user_id: int) -> bool:
        cfg = self._sessions.get_config(user_id)
        return bool(cfg.integrate_ref_path) and self._refs.exists(user_id)

    def read_reference(self, user_id: int) -> bytes | None:
        if not self.reference_available(user_id):
            return None
        return self._refs.read(user_id)

    def load_for_edit(self, user_id: int, cfg) -> bytes:
        """Prereq provider==xai + ref presente; errores user-safe (grok 584-591)."""
        if not _effective_provider_is_xai(cfg):
            raise IntegrateReferenceError(REQUIRES_XAI_MSG)
        data = self.read_reference(user_id)
        if data is None:
            raise IntegrateReferenceError(NO_REFERENCE_MSG)
        return data
