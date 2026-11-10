"""Catálogo de LoRAs del box ComfyUI (item 4).

El usuario explora estilos desde Telegram: el caso de uso expone los nombres de
``models/loras`` sin que la capa telegram conozca `aiohttp`, el túnel ni el
provider concreto. El puerto :class:`LoraCatalog` lo implementa
``ComfyUIProvider.list_loras``; un fallo del box se degrada a ``ok=False`` para
que el panel muestre copy user-safe en vez de romper el callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from grokbot.providers.base import ProviderError


@runtime_checkable
class LoraCatalog(Protocol):
    """Puerto: nombres de LoRA visibles para ComfyUI (rutas relativas)."""

    async def list_loras(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class LoraCatalogResult:
    """Resultado del catálogo: ``ok=False`` = box inaccesible."""

    ok: bool
    names: tuple[str, ...] = ()


class ListComfyLorasUseCase:
    """Listar los LoRAs instalados en el box (sin I/O propio)."""

    def __init__(self, *, catalog: LoraCatalog) -> None:
        self._catalog = catalog

    async def execute(self) -> LoraCatalogResult:
        try:
            names = await self._catalog.list_loras()
        except ProviderError:
            # Degradación esperada (box caído / tunnel sin abrir). El provider
            # ya registró el detalle técnico; aquí no se filtra nada al usuario.
            return LoraCatalogResult(ok=False)
        return LoraCatalogResult(ok=True, names=tuple(names))


__all__ = [
    "LoraCatalog",
    "LoraCatalogResult",
    "ListComfyLorasUseCase",
]
