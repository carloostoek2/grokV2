"""Fakes comunes para los tests de la capa application (item 4).

Fakes in-memory que implementan los Protocols de repositories/base.py y los
contratos de ImageProvider/VideoProvider de providers/base.py. Nada de red ni
Telegram. IDs de usuario y prompts ANONIMIZADOS (R8): user 111111111, prompts
dummy, sin tokens reales en asserts.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from grokbot.domain.generation import GenerationRequest, GenerationResult, MediaType
from grokbot.domain.user_config import (
    COMFY_VIDEO_MODELS,
    DEFAULT_COMFYUI_MODEL,
    UserConfig,
)
from grokbot.domain.variables import DEFAULT_TEMPLATE, LIST_NAMES
from grokbot.providers.registry import ProviderRegistry

# --- Anonymized fixtures (R8) ----------------------------------------------
USER_ID = 111111111
DUMMY_PROMPT = "retrato de una persona de pie"


# --- Session repo -----------------------------------------------------------
class FakeSessionRepo:
    """SessionRepository in-memory: dict ``user_id → UserConfig``.

    ``get_config`` crea el default si falta (paridad repo real) sin registrar
    save; ``save_config`` reemplaza y registra en ``saved``.
    """

    def __init__(self, *, seed: dict[int, UserConfig] | None = None) -> None:
        self._configs: dict[int, UserConfig] = dict(seed) if seed else {}
        self.saved: list[tuple[int, UserConfig]] = []

    def get_config(self, user_id: int) -> UserConfig:
        cfg = self._configs.get(user_id)
        if cfg is None:
            cfg = UserConfig.defaults()
            self._configs[user_id] = cfg
        return cfg

    def save_config(self, user_id: int, config: UserConfig) -> None:
        self._configs[user_id] = config
        self.saved.append((user_id, config))


# --- Variables repo ----------------------------------------------------------
class FakeVariablesRepo:
    """VariablesRepository in-memory con la semántica del repo real.

    ``is_valid_list_name`` acepta los nombres constantes (LIST_NAMES) o listas
    del archivo activo; ``get_list`` lanza KeyError cuando una constante está
    ausente del archivo activo (espejo del repo JSON).
    """

    def __init__(
        self,
        *,
        lists: dict[str, list[str]] | None = None,
        template: str = DEFAULT_TEMPLATE,
        blacklist: set[tuple[str, ...]] | None = None,
        packages: dict[str, dict] | None = None,
        active_package: str | None = None,
    ) -> None:
        self._lists = {name: list(items) for name, items in (lists or {}).items()}
        self._template = template
        self._blacklist: set[tuple[str, ...]] = set(blacklist or [])
        self._packages = {name: dict(payload) for name, payload in (packages or {}).items()}
        self._active_package = active_package
        # Spies.
        self.blacklist_add_calls: list[tuple[str, ...]] = []
        self.add_item_calls: list[tuple[str, str]] = []

    # -- lists/template ----------------------------------------------------
    def get_lists(self) -> dict[str, list[str]]:
        return {name: list(items) for name, items in self._lists.items()}

    def get_list(self, name: str) -> list[str]:
        if not self.is_valid_list_name(name):
            raise ValueError(f"Unknown list: {name!r}")
        return list(self._lists[name])

    def is_valid_list_name(self, name: str) -> bool:
        return name in LIST_NAMES or name in self._lists

    def get_template(self) -> str:
        return self._template

    def set_template(self, template: str) -> bool:
        if not isinstance(template, str) or not template.strip():
            return False
        self._template = template.strip()
        return True

    # -- CRUD ---------------------------------------------------------------
    def add_item(self, name: str, item: str) -> bool:
        self.add_item_calls.append((name, item))
        if not self.is_valid_list_name(name):
            return False
        if not isinstance(item, str) or not item.strip():
            return False
        clean = item.strip()
        items = self._lists.setdefault(name, [])
        if clean in items:
            return False
        items.append(clean)
        return True

    def update_item(self, name: str, index: int, item: str) -> bool:
        if not self.is_valid_list_name(name):
            return False
        if not isinstance(item, str) or not item.strip():
            return False
        clean = item.strip()
        items = self._lists.get(name, [])
        if not 0 <= index < len(items):
            return False
        if items[index] == clean:
            return True
        if clean in items:
            return False
        items[index] = clean
        return True

    def delete_item(self, name: str, index: int) -> bool:
        if not self.is_valid_list_name(name):
            return False
        items = self._lists.get(name, [])
        if not 0 <= index < len(items):
            return False
        del items[index]
        return True

    # -- blacklist -----------------------------------------------------------
    def get_blacklist(self) -> set[tuple[str, ...]]:
        return set(self._blacklist)

    def blacklist_add(self, key: tuple[str, ...]) -> bool:
        self.blacklist_add_calls.append(key)
        if not isinstance(key, tuple):
            return False
        if key in self._blacklist:
            return False
        self._blacklist.add(key)
        return True

    def blacklist_clear(self) -> None:
        self._blacklist = set()

    # -- packages ------------------------------------------------------------
    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
        return slug

    def list_packages(self) -> list[str]:
        return sorted(self._packages)

    def package_exists(self, name: str) -> bool:
        return self._slugify(name) in self._packages

    def load_package(self, name: str) -> dict | None:
        payload = self._packages.get(self._slugify(name))
        return dict(payload) if payload is not None else None

    def save_package(self, name: str, payload: dict) -> tuple[bool, str | None]:
        slug = self._slugify(name)
        if not slug:
            return False, "El nombre del paquete no es válido."
        self._packages[slug] = dict(payload)
        return True, None

    def active_package_name(self) -> str | None:
        return self._active_package

    def activate_package(self, name: str) -> bool:
        payload = self.load_package(name)
        if payload is None:
            return False
        slug = self._slugify(name)
        self._lists = {k: list(v) for k, v in payload.get("lists", {}).items()}
        self._template = str(payload.get("template", self._template))
        self._blacklist = set()
        self._active_package = slug
        return True

    def delete_package(self, name: str) -> bool:
        slug = self._slugify(name)
        if slug not in self._packages:
            return False
        if self._active_package == slug:
            return False
        del self._packages[slug]
        return True


# --- Source faces repo ----------------------------------------------------------
class FakeSourceFacesRepo:
    """SourceFacesRepository in-memory: dict user_id → bytes."""

    def __init__(self, *, seed: dict[int, bytes] | None = None) -> None:
        self._data = dict(seed or {})
        self.saved: list[tuple[int, bytes]] = []
        self.saved_paths: dict[int, str] = {}

    def save(self, user_id: int, data: bytes) -> str:
        self._data[user_id] = data
        self.saved.append((user_id, data))
        path = f"/sources/{user_id}.jpg"
        self.saved_paths[user_id] = path
        return path

    def read(self, user_id: int) -> bytes | None:
        return self._data.get(user_id)

    def exists(self, user_id: int) -> bool:
        return user_id in self._data


# --- Refs repo ----------------------------------------------------------------
class FakeRefsRepo:
    """GenerationRefsRepository in-memory (disponible para item 5)."""

    def __init__(self) -> None:
        self._data: dict[tuple[int, int], dict] = {}

    def save(
        self,
        chat_id: int,
        message_id: int,
        *,
        kie_task_id: str | None = None,
        kie_index: int = 0,
        provider: str = "kie",
        kind: str = "image",
        prompt: str = "",
        regen: dict | None = None,
        owner_uid: int | None = None,
        now: float | None = None,
    ) -> None:
        if not kie_task_id and not regen:
            return
        ref: dict = {
            "kie_task_id": kie_task_id,
            "kie_index": kie_index,
            "provider": provider,
            "kind": kind,
            "prompt": prompt,
        }
        if regen is not None:
            ref["regen"] = regen
        if owner_uid is not None:
            ref["owner_uid"] = int(owner_uid)
        self._data[(chat_id, message_id)] = ref

    def get(self, chat_id: int, message_id: int) -> dict | None:
        return self._data.get((chat_id, message_id))


# --- Providers ---------------------------------------------------------------
def make_result(
    *,
    provider: str = "xai",
    model_id: str = "fake-image-model",
    media_type: MediaType = MediaType.IMAGE,
    file_path: str | None = None,
    remote_url: str | None = None,
    meta: dict | None = None,
) -> GenerationResult:
    """Genera un GenerationResult dummy (sin tokens; R8)."""
    if remote_url is None and file_path is None:
        remote_url = "https://example.invalid/media/result"
    return GenerationResult(
        provider=provider,
        model_id=model_id,
        media_type=media_type,
        file_path=file_path,
        remote_url=remote_url,
        meta=meta or {},
    )


class _FakeProviderBase:
    """Base de proveedor fake: cola de outcomes (resultado o excepción).

    ``outcomes`` es una lista consumida por orden en cada ``generate``: cada
    elemento es una excepción (:class:`ProviderError`) o un
    :class:`GenerationResult`. Cuando la cola se vacía se devuelve un resultado
    default del media_type pedido.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        outcomes: list | None = None,
        supports_result: bool = True,
        name: str = "xai",
        media_type: MediaType = MediaType.IMAGE,
        default_result: GenerationResult | None = None,
    ) -> None:
        self.available = available
        self.outcomes = list(outcomes) if outcomes else []
        self.supports_result = supports_result
        self._name = name
        self._media_type = media_type
        self._default_result = default_result
        self.calls: list[tuple[GenerationRequest, bytes | None]] = []
        self.supports_calls: list[GenerationRequest] = []
        self.swap_face_calls: list[tuple[bytes, bytes]] = []

    def supports(self, request: GenerationRequest) -> bool:
        self.supports_calls.append(request)
        return self.supports_result

    async def generate(
        self,
        request: GenerationRequest,
        *,
        source_image: bytes | None = None,
    ) -> GenerationResult:
        self.calls.append((request, source_image))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self._default_result is not None:
            return self._default_result
        media = request.media_type if request.media_type is not None else self._media_type
        return make_result(provider=self._name, model_id=request.model_id, media_type=media)

    async def swap_face(
        self,
        *,
        swap_image: bytes,
        input_image: bytes,
    ) -> GenerationResult:
        self.swap_face_calls.append((swap_image, input_image))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self._default_result is not None:
            return self._default_result
        return make_result(provider=self._name, model_id="fake-faceswap", media_type=MediaType.IMAGE)

    @property
    def generate_count(self) -> int:
        return len(self.calls)


class FakeImageProvider(_FakeProviderBase):
    """Imagen: accepta IMAGE y (de forma configurable) cualquier request."""

    def __init__(self, **kwargs) -> None:
        super().__init__(name=kwargs.pop("name", "xai"), media_type=MediaType.IMAGE, **kwargs)


class FakeVideoProvider(_FakeProviderBase):
    """Video: devuelve resultados media_type VIDEO por defecto."""

    def __init__(self, **kwargs) -> None:
        super().__init__(name=kwargs.pop("name", "xai"), media_type=MediaType.VIDEO, **kwargs)


class FakeComfyuiProvider(_FakeProviderBase):
    """ComfyUI fake: ``supports`` espeja el provider real + método ``refine``."""

    def __init__(
        self,
        *,
        available: bool = True,
        outcomes: list | None = None,
        refine_outcomes: list | None = None,
        default_result: GenerationResult | None = None,
    ) -> None:
        super().__init__(
            available=available,
            outcomes=outcomes,
            name="comfyui",
            media_type=MediaType.IMAGE,
            default_result=default_result,
            supports_result=True,
        )
        self.refine_outcomes = list(refine_outcomes) if refine_outcomes else []
        self.refine_calls: list[tuple[GenerationRequest, list[str]]] = []

    def supports(self, request: GenerationRequest) -> bool:
        self.supports_calls.append(request)
        if request.media_type is MediaType.IMAGE:
            return True
        if request.media_type is MediaType.VIDEO:
            model = request.params.get("model") or DEFAULT_COMFYUI_MODEL
            return model in COMFY_VIDEO_MODELS
        return False

    async def refine(
        self,
        request: GenerationRequest,
        remote_paths: list[str],
    ) -> GenerationResult:
        self.refine_calls.append((request, list(remote_paths)))
        if self.refine_outcomes:
            outcome = self.refine_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return make_result(
            provider="comfyui",
            model_id=request.model_id,
            media_type=MediaType.IMAGE,
            file_path="/tmp/fake_refined.jpg",
            meta={"comfyui_remotes": ["/workspace/refined.png"]},
        )


def make_registry(**overrides) -> ProviderRegistry:
    """Registry con fakes en los 4 slots; ``overrides`` reemplaza por nombre."""
    defaults: dict[str, object] = {
        "xai": FakeImageProvider(name="xai"),
        "replicate": FakeImageProvider(name="replicate"),
        "kie": FakeImageProvider(name="kie"),
        "comfyui": FakeComfyuiProvider(),
    }
    defaults.update(overrides)
    return ProviderRegistry(**defaults)


# --- Fixtures de pytest -------------------------------------------------------
@pytest.fixture
def user_id() -> int:
    return USER_ID


@pytest.fixture
def sessions() -> FakeSessionRepo:
    return FakeSessionRepo()


@pytest.fixture
def variables_repo() -> FakeVariablesRepo:
    """Repositorio de variables con listas no vacías para el batch random."""
    return FakeVariablesRepo(
        lists={
            "poses": ["de pie", "sentada"],
            "angles": ["frontal", "perfil"],
            "actions": ["mirando a cámara", "sonriendo"],
        }
    )


@pytest.fixture
def registry() -> ProviderRegistry:
    return make_registry()


@pytest.fixture
def refs_repo() -> FakeRefsRepo:
    return FakeRefsRepo()


@pytest.fixture
def fast_sleep(monkeypatch):
    """Parchea ``asyncio.sleep`` a no-op para acortar backoffs en tests."""

    async def _no_sleep(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
