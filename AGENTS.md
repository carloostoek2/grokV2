# AGENTS.md

## Qué es este proyecto

**grokbot (@grokV2)** — bot de Telegram que genera/edita imagen y video con varios
proveedores (xAI Grok Imagine, Replicate, Kie.ai y ComfyUI remoto vía su API nativa
REST+WS sobre túnel SSH), más batch por variables (`/variables` `/var`), panel de
listas (`/listas`), configuración por usuario (`/config`), Face Swap
(`/cambiar_source`) y edición con referencia (`/s` `/cambiar_referencia`).

Es la re-implementación *por capas* de un monstruo de 5.497 líneas (`grok/bot.py`,
repo hermano usado como referencia **read-only**). Los flujos no degradados operan
con **copy byte-parity** de grok: no se toca ese copy.

- Arquitectura objetivo (capas, contratos, decisiones): `docs/SPEC_REFACTOR.md`.
- Estado de producto y flujos operativos: `docs/PRODUCT_STATUS.md`.
- Avance del slice ComfyUI HTTP/WS: `docs/comfyui/AVANCE_VAST_HTTP.md`.

## Cómo correrlo

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env     # y completar credenciales
grokbot                   # console script → polling; sin webhook
```

- Requiere Python ≥ 3.11.
- Env **obligatorias**: `TELEGRAM_BOT_TOKEN`, `REPLICATE_API_TOKEN`, `XAI_API_KEY`.
  Opcionales: `KIE_API_KEY` (vacío = provider no disponible), `COMFYUI_HOST`/`COMFYUI_*`
  (SSH del box; vacío = deshabilitado), `ALLOWED_TELEGRAM_IDS`/`VARIABLES_ADMIN_IDS`
  (CSV de IDs), `GROK_DATA_DIR`.
- `data/` (runtime JSON) y `tmp/` están en `.gitignore`; el `data_dir` default es
  `./data` relativo al cwd → correr desde la raíz del repo o setear `GROK_DATA_DIR`
  absoluto en deploy.
- Boot: `load_dotenv` → `Settings` fail-fast (pydantic) → `build_deps` (lanza
  `ValueError` si el token está vacío) → `assemble_dispatcher` → `start_polling`.
- Tests: `.venv/bin/pytest` (suite completa; hoy **655 passed**).

## Estructura del repo

```
src/grokbot/
├── main.py                  # Composition root; 0 side-effects al importar; console script
├── settings.py              # Settings (pydantic-settings) + get_settings() LRU-cached
├── domain/                  # Entidades puras (dataclasses frozen, stdlib, sin I/O)
│   ├── catalog.py           #   MODELS + variantes Grok Imagine (catálogo estático)
│   ├── user_config.py       #   UserConfig/VideoConfig/ComfyUIConfig + COMFYUI_FLOWS
│   └── generation.py job.py variables.py
├── application/             # Use cases; emiten AsyncIterator de eventos frozen (events.py); sin I/O
├── providers/               # Integraciones externas
│   ├── base.py              #   Protocols (ImageProvider/VideoProvider/…) + errores tipados
│   ├── registry.py          #   Resuelve provider activo desde UserConfig + MediaType
│   ├── xai_provider.py replicate_provider.py kie_provider.py
│   └── comfyui/             #   transport (SSH tunnel) + client (REST+WS) + provider
│       └── workflows/       #   resolver.py + templates/<id>.json (flujos API-format)
├── repositories/            # Protocols (base.py) + impls JSON/binarias; write_json_atomic
├── telegram/                # SOLO presentación
│   ├── handlers/            #   un módulo por comando/flujo; cada uno expone register_*(dp, deps)
│   ├── deps.py              #   BotDeps + stores efímeros (PendingPrompts, …)
│   ├── ports.py             #   Seams TelegramGateway / MediaDownloader (Protocols)
│   ├── adapters/aiogram_gateway.py
│   ├── chat_ui.py keyboards.py formatters.py sender.py stream_presenter.py
│   ├── fsm_states.py        #   ConfigStates / VarStates (único FSM real)
│   └── middlewares.py       #   PrivateChatOnly + Allowlist
└── shared/                  # Helpers stdlib-only (logging, errores user-safe del entrypoint)
```

## Patrones que hay que seguir

**Layering estricto** (la regla dura del proyecto):

- `domain/`: entidades puras; solo stdlib; nunca importa otras capas ni hace I/O.
- `application/`: use cases que reciben repos/providers por **constructor**; corren la
  lógica y emiten eventos frozen (ver `events.py`) con `reason`/copy siempre
  **user-safe** (sin HTML/emoji/payloads — eso lo decide la capa telegram).
- `providers/` y `repositories/`: detrás de `Protocol` (`runtime_checkable`) declarados
  en `base.py`. Un provider NUEVO = clase que implementa el Protocol + registro en
  `registry.py` (el registry es la única puerta; la app no conoce providers concretos).
- `telegram/`: **nunca** importa `aiohttp`/`replicate`/`subprocess` ni toca JSON directo;
  **nunca** llama `message.answer`/`edit_text` directo. Todo outbound sale por los seams
  inyectados `TelegramGateway`/`MediaDownloader` (vía `ChatUI`/`ResultSender`). Un handler
  solo: parsea input Telegram → arma request de domain → llama use case → traduce eventos
  a UI.

**Registrar un handler nuevo**: módulo en `telegram/handlers/` con `register_*(dp, deps)`;
añadirlo a `register_all` en `handlers/__init__.py` **respetando el orden** — los comandos
de panel/estado primero (para que no los capture el texto genérico), los flujos de
texto/foto/video después, y los callbacks transaccionales al final. Registros típicos:
`dp.message.register(partial(cb, deps=deps), Command(...) | StateFilter(...) | F.text | is_plain_prompt)`.

**Callback data con prefijos de namespace** para no colisionar: `cfg:*`, `var:*`,
`confirm:yes/no`, `faceswap:confirm:*`, `regen:*`, `refine:*`, `cancel_job:*`.

**FSM de aiogram SOLO para `/config` (`ConfigStates`) y `/listas` (`VarStates`)**, con
`MemoryStorage`. Las confirmaciones y el estado efímero **NO usan FSM**: viven en stores
de `BotDeps` (`PendingPrompts`, `FaceswapPending`, `LongPromptStore`, `AlbumStore`,
`integrate_ref_pending`), atados a su `(chat_id, message_id)` y dueño, con esa
semántica de ownership: un click de otro usuario no consume el pendiente ajeno.

**Jobs**: `JobManager` registra activos y cancela **cooperativamente** vía
`asyncio.Event` (sin tope de concurrencia — R9, decisión de producto); su `refine_hook`
se wirea a `ResolveRefineUseCase.cancel_for_job`.

**ComfyUI por flujos** (fuera el catálogo legacy modelo/LoRA/refine). Agregar un flujo =
dejar `templates/<id>.json` con grafo API-format (modelo/LoRA **horneados**) + `_meta`
(`id/name/media_type/positive_node/positive_input/negative_node/seed_nodes/save_nodes/
supports_source/timeout`) y registrar el id en `domain/user_config.COMFYUI_FLOWS`. El
resolver (`workflows/resolver.py`) separa `_meta`, nunca encola `_meta` a ComfyUI. El
refine 2-stage está **dormido**: no revivirlo sin sacarlo antes de ese estado.

**Idiomas**: el copy de usuario (formatters, keyboards, mensajes/errores user-safe) es
**siempre en español**; los docstrings/comentarios y nombres de código son **en inglés**;
los documentos de estado/avance en español. El repo tiene docstrings históricos en español
en `telegram/`/`application/`: no se migran en masa; la regla se aplica al código nuevo.

## Límites — qué no hacer sin pensarlo dos veces

- **No cambiar el copy byte-parity** de los flujos operativos (marcado en
  `PRODUCT_STATUS.md §2`). Si un flujo va a degradar, se registra ahí; hoy
  `D8_COMMANDS == ()` y debe seguir vacío.
- **Nunca filtrar secretos/IDs/file_ids/payloads** en logs ni en errores: los mensajes
  user-safe listan solo nombres de campo o `type(exception).__name__` (C3/R6/R8). Un
  `ProviderError`/`MediaFetchError` lleva `user_message` seguro; el `file_id` nunca se expone.
- **La jerarquía de errores vive en su capa**: `ProviderError*` en `providers/base.py`;
  `MediaFetchError` en `telegram/ports.py`; `DownloadError` en `telegram/downloader.py`.
  No se crea jerarquía nueva en `shared/errors.py` (solo helpers user-safe del entrypoint).
- **Persistencia solo vía repositories**; las escrituras JSON usan `write_json_atomic`.
  No escribir `data/*.json` a mano ni commitear runtime (`data/`, `tmp/`, `.env`
  ignorados). La **lectura** de runtime está regulada por la regla dura n.º 2.
- **No se agrega estado efímero al FSM**: confirmaciones/long-prompt/álbumes van a los
  stores de `BotDeps`.
- Al añadir una env var nueva: `Settings` + `.env.example` + `tests/conftest.py`
  (`SETTINGS_ENV_VARS`) en el mismo cambio.
- **No se prueba con red ni con `unittest.mock`** (política 0-red/0-mock): se usan los
  fakes (`FakeTelegramGateway`, `FakeMediaDownloader`, `make_deps` en
  `tests/unit/telegram/conftest.py`) y `aioresponses` para HTTP.

## Reglas duras

1. **Documentar el cambio** — todo cambio de comportamiento visible (comando, flujo,
   proveedor, copy de usuario) actualiza `docs/PRODUCT_STATUS.md` — y
   `docs/comfyui/AVANCE_VAST_HTTP.md` si toca el slice ComfyUI — **en el mismo cambio**,
   junto con sus tests.
2. **No leer datos de runtime sin que el usuario lo pida** — `data/` y `tmp/` (los
   archivos que escriben los repos: `sessions.json`, `variables_lists.json`,
   `generation_refs.json`, `variables_packages/`, `sources/`, `integrate_refs/`)
   contienen datos **privados del usuario** (config, fotos fuente, referencias). Un
   agente no los consulta por su cuenta para responder, decidir ni documentar: solo se
   accede a ellos si el usuario lo solicita explícitamente.

## Notas para el agente

- Los docstrings referencian invariantes por código (`D#`, `C#`, `R#`, `A#`, `O#`, `M#`,
  `T#`) trazados a `SPEC_REFACTOR.md` y a los pools de `.planning/`/`.grok/agent-memory`.
  No se borran al tocar ese código: son el hilo de trazabilidad.
- Se corre la **suite completa**, no solo el módulo tocado: hay convenciones que se fijan
  en tests ajenos (p. ej. `tests/e2e/telegram/test_routing.py` fija el orden de registro).
- Agregar un modelo de catálogo = entrada en `domain/catalog.MODELS` + resolución en
  `registry.resolve_image`/`resolve_video`; los labels de UI (`VIDEO_MODEL_LABELS`, etc.)
  viven en la capa telegram, no en `domain/`.
- `UserConfig` tolera claves extra en lectura (sesiones viejas) y nunca las emite en
  `to_record`.
- Commits en español, atómicos (código + tests + docs del mismo cambio).
