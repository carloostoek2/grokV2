"""Fakes y fixtures comunes de la capa telegram (item 5, tests unit).

Carga los fakes de ``tests/unit/application/conftest.py`` con un loader
``importlib`` (módulo alias ``_app_conftest`` cacheado) y los re-exporta, sin
duplicar código. Añade los fakes de borde de telegram:

* ``FakeTelegramGateway`` — graba ``calls`` con kwargs + ``sent``; ids de
  mensaje incrementales; ``get_file_bytes`` devuelve bytes dummy para
  ``FAKE:*``.
* ``FakeMediaDownloader`` — devuelve bytes dummy o lanza ``DownloadError`` y
  registra ``allowlist`` por descarga.
* ``PendingPrompts`` — store efímero user → prompt (A6, paridad grok).

0 red, 0 ``unittest.mock``: los fakes implementan los Protocols de la capa
(ports.py / repositories / providers). IDs de usuario/chat y prompts
ANONIMIZADOS (R8).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

# --- Cargar los fakes de application (módulo alias cacheado) -----------------
_APP_CONFTEST = pathlib.Path(__file__).resolve().parent.parent / "application" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_app_conftest", _APP_CONFTEST)
_app_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_app_conftest)  # type: ignore[union-attr]

# Re-export de fakes/constantes de application.
USER_ID = _app_conftest.USER_ID
DUMMY_PROMPT = _app_conftest.DUMMY_PROMPT
FakeSessionRepo = _app_conftest.FakeSessionRepo
FakeVariablesRepo = _app_conftest.FakeVariablesRepo
FakeRefsRepo = _app_conftest.FakeRefsRepo
FakeImageProvider = _app_conftest.FakeImageProvider
FakeVideoProvider = _app_conftest.FakeVideoProvider
FakeComfyuiProvider = _app_conftest.FakeComfyuiProvider
make_result = _app_conftest.make_result
make_registry = _app_conftest.make_registry

# Anonimizado de chats (mismo id que el usuario en fixtures).
CHAT_ID = 111111111

from datetime import datetime, timezone  # noqa: E402

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Update,
    User,
)

from grokbot.application.generate_image import GenerateImageUseCase  # noqa: E402
from grokbot.application.generate_video import GenerateVideoUseCase  # noqa: E402
from grokbot.application.job_manager import JobManager  # noqa: E402
from grokbot.application.manage_config import UpdateUserConfigUseCase  # noqa: E402
from grokbot.application.manage_lists import ManageListsUseCase  # noqa: E402
from grokbot.application.refine_flow import ResolveRefineUseCase  # noqa: E402
from grokbot.application.run_variable_batch import RunVariableBatchUseCase  # noqa: E402
from grokbot.domain.variables import DEFAULT_TEMPLATE  # noqa: E402
from grokbot.telegram.deps import BotDeps  # noqa: E402
from grokbot.telegram.ports import MediaFetchError, SentMessage  # noqa: E402


# --- Builders de updates aiogram (offline; sin red/token) ----------------------
def make_user(user_id: int = USER_ID, *, is_bot: bool = False) -> User:
    return User(id=user_id, is_bot=is_bot, first_name="Test")


def make_chat(chat_id: int = CHAT_ID, *, type: str = "private") -> Chat:
    return Chat(id=chat_id, type=type)


def text_message(
    text: str,
    *,
    user_id: int = USER_ID,
    chat_id: int = CHAT_ID,
    chat_type: str = "private",
    message_id: int = 1,
    reply_to_message: Message | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=make_chat(chat_id, type=chat_type),
        from_user=make_user(user_id),
        text=text,
        reply_to_message=reply_to_message,
    )


def make_photo_message(
    *,
    caption: str | None = None,
    user_id: int = USER_ID,
    chat_id: int = CHAT_ID,
    chat_type: str = "private",
    message_id: int = 1,
    file_id: str = "FAKE:photo1",
    media_group_id: str | None = None,
    reply_to_message: Message | None = None,
) -> Message:
    photo = PhotoSize(
        file_id=file_id,
        file_unique_id=f"U:{file_id}",
        width=128,
        height=128,
        file_size=1024,
    )
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=make_chat(chat_id, type=chat_type),
        from_user=make_user(user_id),
        photo=[photo],
        caption=caption,
        media_group_id=media_group_id,
        reply_to_message=reply_to_message,
    )


# Alias histórico (foto con caption).
photo_message = make_photo_message


def callback_query(
    data: str,
    *,
    user_id: int = USER_ID,
    chat_id: int = CHAT_ID,
    chat_type: str = "private",
    message_id: int = 1,
    callback_id: str = "cb-1",
    message: Message | None = None,
) -> CallbackQuery:
    if message is None:
        message = text_message(
            "base", user_id=user_id, chat_id=chat_id, chat_type=chat_type, message_id=message_id
        )
    return CallbackQuery(
        id=callback_id,
        from_user=make_user(user_id),
        chat_instance="chat-instance",
        message=message,
        data=data,
    )


def message_update(message: Message) -> Update:
    return Update(update_id=1, message=message)


def callback_update(callback: CallbackQuery) -> Update:
    return Update(update_id=1, callback_query=callback)


# --- Telegram gateway fake ----------------------------------------------------
class FakeTelegramGateway:
    """Implementa TelegramGateway en memoria: graba llamadas y devuelve ids reales.

    ``calls`` es una lista de dicts ``{"method", "sent", **kwargs}`` en orden de
    invocación; ``sent`` es el :class:`SentMessage` devuelto (message_id
    incremental por gateway).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._next_id = 1000
        # file_id → bytes para get_file_bytes (FAKE:* → bytes dummy por defecto).
        self.file_bytes: dict[str, bytes] = {}

    # -- helpers de test ---------------------------------------------------
    def _record(self, method: str, **kwargs) -> dict:
        self._next_id += 1
        sent = SentMessage(message_id=self._next_id, chat_id=kwargs["chat_id"])
        record = {"method": method, "sent": sent, **kwargs}
        self.calls.append(record)
        return record

    def calls_by_method(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method]

    def reset(self) -> None:
        self.calls.clear()

    # -- TelegramGateway ---------------------------------------------------
    async def send_message(self, chat_id, text, *, parse_mode="HTML", reply_markup=None, reply_to_message_id=None):
        return self._record(
            "send_message",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )["sent"]

    async def send_photo(self, chat_id, photo, *, filename="generated.png", caption=None, parse_mode="HTML", reply_markup=None, reply_to_message_id=None):
        return self._record(
            "send_photo",
            chat_id=chat_id,
            photo=photo,
            filename=filename,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )["sent"]

    async def send_video(self, chat_id, video, *, filename="generated.mp4", caption=None, parse_mode="HTML", reply_markup=None, reply_to_message_id=None):
        return self._record(
            "send_video",
            chat_id=chat_id,
            video=video,
            filename=filename,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )["sent"]

    async def send_media_group(self, chat_id, media, *, reply_to_message_id=None):
        record = self._record(
            "send_media_group",
            chat_id=chat_id,
            media=media,
            reply_to_message_id=reply_to_message_id,
        )
        # El álbum devuelve un SentMessage por elemento.
        group = []
        for i in range(len(media)):
            self._next_id += 1
            group.append(SentMessage(message_id=self._next_id, chat_id=chat_id))
        record["sent_group"] = group
        return group

    async def edit_message_text(self, chat_id, message_id, text, *, parse_mode="HTML", reply_markup=None):
        self._record(
            "edit_message_text",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True

    async def edit_message_caption(self, chat_id, message_id, caption, *, parse_mode="HTML", reply_markup=None):
        self._record(
            "edit_message_caption",
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        self._record(
            "edit_message_reply_markup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
        return True

    async def delete_message(self, chat_id, message_id):
        self._record("delete_message", chat_id=chat_id, message_id=message_id)

    async def answer_callback(self, callback_id, text=None, *, show_alert=False):
        self._record(
            "answer_callback",
            chat_id=0,
            callback_id=callback_id,
            text=text,
            show_alert=show_alert,
        )

    async def get_file_bytes(self, file_id):
        if file_id in self.file_bytes:
            payload = self.file_bytes[file_id]
        elif file_id.startswith("FAKE:"):
            payload = b"fake-file-bytes"
        else:
            # M2: file_id no resuelto → MediaFetchError neutral (sin el id en el msg).
            raise MediaFetchError()
        self._record("get_file_bytes", chat_id=0, file_id=file_id)
        return payload


# --- Media downloader fake ----------------------------------------------------
class FakeMediaDownloader:
    """Implementa MediaDownloader: bytes dummy o lanza ``DownloadError``."""

    def __init__(self, payload: bytes = b"fake-media-bytes") -> None:
        self.payload = payload
        self.error: Exception | None = None
        self.calls: list[dict] = []

    async def download(self, url, *, allowlist=None):
        self.calls.append({"url": url, "allowlist": allowlist})
        if self.error is not None:
            raise self.error
        return self.payload


# --- PendingPrompts (A6: confirmación efímera, sin FSM) ------------------------
class PendingPrompts:
    """Mapa user_id → prompt pendiente de confirmación (paridad grok)."""

    def __init__(self) -> None:
        self._pending: dict[int, str] = {}

    def set(self, user_id: int, prompt: str) -> None:
        self._pending[user_id] = prompt

    def get(self, user_id: int) -> str | None:
        return self._pending.get(user_id)

    def pop(self, user_id: int) -> str | None:
        return self._pending.pop(user_id, None)

    def clear(self, user_id: int) -> None:
        self._pending.pop(user_id, None)


# --- Fixtures de pytest --------------------------------------------------------
@pytest.fixture
def user_id() -> int:
    return USER_ID


@pytest.fixture
def chat_id() -> int:
    return CHAT_ID


@pytest.fixture
def gateway() -> FakeTelegramGateway:
    return FakeTelegramGateway()


@pytest.fixture
def downloader() -> FakeMediaDownloader:
    return FakeMediaDownloader()


@pytest.fixture
def sessions() -> FakeSessionRepo:
    return FakeSessionRepo()


@pytest.fixture
def variables_repo() -> FakeVariablesRepo:
    return FakeVariablesRepo(
        lists={
            "poses": ["de pie", "sentada"],
            "angles": ["frontal", "perfil"],
            "actions": ["mirando a cámara", "sonriendo"],
        }
    )


@pytest.fixture
def refs_repo() -> FakeRefsRepo:
    return FakeRefsRepo()


@pytest.fixture
def registry() -> FakeSessionRepo:
    return make_registry()


@pytest.fixture
def pending() -> PendingPrompts:
    return PendingPrompts()


# --- Helpers para extraer callback_data de un markup ----------------------------
def flat_callback_data(markup: InlineKeyboardMarkup | None) -> list[str]:
    """Todos los ``callback_data`` del markup, en orden de filas."""
    if markup is None:
        return []
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data is not None:
                out.append(btn.callback_data)
    return out


# --- Composición de deps y dispatcher (Task 3) ---------------------------------
_DEFAULT_LISTS = {
    "poses": ["de pie", "sentada"],
    "angles": ["frontal", "perfil"],
    "actions": ["mirando a cámara", "sonriendo"],
}


def make_deps(
    *,
    gateway: FakeTelegramGateway | None = None,
    downloader: FakeMediaDownloader | None = None,
    refs: FakeRefsRepo | None = None,
    sessions: FakeSessionRepo | None = None,
    variables: FakeVariablesRepo | None = None,
    registry=None,
    job_manager: JobManager | None = None,
    refine_uc: ResolveRefineUseCase | None = None,
    pending: PendingPrompts | None = None,
    allowed_telegram_ids: set[int] | None = None,
    variables_admin_ids: set[int] | None = None,
    **overrides,
) -> BotDeps:
    """Armar un :class:`BotDeps` con fakes (0 red / 0 unittest.mock).

    Los use cases se construyen con fakes compartidos; el ``refine_hook`` del
    ``JobManager`` queda cableado a ``refine_uc.cancel_for_job`` para que el
    cancel de un job resuelva las confirmaciones de refine pendientes (R7).
    """
    gateway = gateway or FakeTelegramGateway()
    downloader = downloader or FakeMediaDownloader()
    sessions = sessions or FakeSessionRepo()
    variables = variables or FakeVariablesRepo(lists=dict(_DEFAULT_LISTS))
    refs = refs or FakeRefsRepo()
    registry = registry if registry is not None else make_registry()
    refine_uc = refine_uc or ResolveRefineUseCase(provider=registry.provider("comfyui"))
    job_manager = job_manager or JobManager(refine_hook=refine_uc.cancel_for_job)
    generate_image = GenerateImageUseCase(sessions=sessions, registry=registry)
    generate_video = GenerateVideoUseCase(sessions=sessions, registry=registry)
    run_batch = RunVariableBatchUseCase(
        sessions=sessions,
        registry=registry,
        variables=variables,
        job_manager=job_manager,
        generate_image=generate_image,
    )
    update_config = UpdateUserConfigUseCase(sessions=sessions)
    manage_lists = ManageListsUseCase(variables=variables)
    return BotDeps(
        gateway=gateway,
        downloader=downloader,
        refs=refs,
        sessions=sessions,
        variables=variables,
        job_manager=job_manager,
        refine_uc=refine_uc,
        generate_image=generate_image,
        generate_video=generate_video,
        run_batch=run_batch,
        update_config=update_config,
        manage_lists=manage_lists,
        pending=pending or PendingPrompts(),
        allowed_telegram_ids=allowed_telegram_ids,
        variables_admin_ids=variables_admin_ids,
    )


def make_dispatcher(deps: BotDeps | None = None, *, register: bool = True):
    """Dispatcher offline con ``MemoryStorage`` y ``Bot("42:TEST")``.

    Si ``register=True`` (default) aplica ``register_all(dp, deps)`` con el deps
    dado o uno por defecto (fakes).
    """
    from aiogram import Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage

    if deps is None:
        deps = make_deps()
    dp = Dispatcher(storage=MemoryStorage())
    if register:
        from grokbot.telegram.handlers import register_all

        register_all(dp, deps)
    return dp, deps
