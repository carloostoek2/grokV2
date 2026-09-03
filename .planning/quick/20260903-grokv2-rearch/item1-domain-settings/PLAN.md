---
phase: quick
plan: item1-domain-settings
type: auto
item: "Item 1 — Extraer dominio y settings (Fase 1 SPEC §7)"
source: user-request + SPEC_REFACTOR.md (§4/§5.1/§5.7/§7) + CLARIFY.md + impact-analyzer
mode: standard
---

# PLAN — Item 1: Dominio + Settings (grokV2 greenfield, puramente aditivo)

## Objective

Crear en `/home/ubuntu/repos/grokV2` (hoy solo docs/planning, sin código, sin git) el scaffolding del proyecto
(`pyproject.toml` con pytest `asyncio_mode=auto`, layout `src/`, `.env.example` real, README mínimo, venv) y la
primera capa re-implementada del bot: **Settings Pydantic fail-fast centralizado** (`src/grokbot/settings.py`) y el
**paquete de dominio puro** (`src/grokbot/domain/`) con las entidades tipadas que los ítems 2-6 van a consumir.
Outcome medible: `pytest tests/unit -q` en verde con tests unit de dominio y settings, **0 cambios sobre
`/home/ubuntu/repos/grok`**, y ninguna carpeta de ítems 2-6 creada.

## Scope

- **In:**
  - `git init` en grokV2 + commit base (planning/docs) + primer commit del ítem (scaffolding).
  - Scaffolding: `pyproject.toml`, `.env.example`, `README.md`, `.gitignore`, `src/grokbot/__init__.py`, venv `.venv/`.
  - `src/grokbot/settings.py`: `Settings` (pydantic-settings, fail-fast, requeridos) + `get_settings()` con `lru_cache`.
  - `src/grokbot/domain/`: `catalog.py`, `generation.py`, `user_config.py`, `variables.py`, `job.py` (+ `__init__.py`).
  - Tests: `tests/conftest.py` (aislante de env + caché de settings) y `tests/unit/**` de settings y dominio.
- **Out / Non-goals:**
  - Nada de `providers/`, `repositories/`, `application/`, `telegram/`, `main.py`, `shared/` (ítems 2-6).
  - Nada de I/O (JSON, aiohttp, subprocess, random global) ni backend: el render de templates y la normalización
    son **funciones puras**; lectura/escritura de `sessions.json`/`variables_lists.json` es ítem 3.
  - Sin migración/escritura de datos; `DEFAULT_LISTS` (seed content) se difiere a ítem 3 (repos).
  - Sin cambio de comportamiento/UX respecto al bot actual; sin mejoras de producto.
- **Constraints:** 0 cambios sobre `/home/ubuntu/repos/grok` (solo lectura de referencia). Solo código nuevo en grokV2.
  Dominio sin dependencias externas. Python 3.14 host; `requires-python` decidido abajo.

## Assumptions

- A1 — **Python:** `requires-python = ">=3.11"` (sintaxis `str | None`, `tuple[...]`, dataclasses). El runtime/dev del
  target es Python 3.14.4 (verificado en el host); el venv del ítem usa `python3` del sistema (3.14).
- A2 — **Dominio = stdlib dataclasses frozen, NO pydantic.** Pydantic se reserva para `Settings` (frontera de env).
  `domain/` no importa nada fuera de stdlib (decisión que cumple "domain sin dependencias externas" y SPEC §4.6).
- A3 — **`DEFAULT_LISTS` (contenido seed de listas) NO entra en este ítem.** `domain/variables.py` define solo
  `LIST_NAMES`, `DEFAULT_TEMPLATE`, normalización y `PromptTemplate` (render). La semilla por defecto de archivo nuevo
  es responsabilidad del repo JSON (ítem 3), que la transcribirá de `variables_store.py` y la testeará allí.
- A4 — **`Settings` NO declara `env_file`** (`model_config` sin dotenv). Cargar `.env` es decisión del entrypoint
  (ítem 6, vía `load_dotenv()` o `Settings(_env_file=...)`). Esto mantiene los tests deterministas y evita arrastrar
  `python-dotenv` en este ítem. `.env.example` documenta la superficie real esperada.
- A5 — **`video_hourly_timestamps` queda fuera de `UserConfig`**: es dato de uso/medición (quota horaria), no
  preferencia de config. `UserConfig.from_record` lo **tolera** (lo ignora como clave desconocida, R4). El repo/session
  (ítem 3) es dueño de ese campo.
- A6 — **`domain/catalog.py` copia los spec records completos** de `MODELS` y `GROK_IMAGINE_VARIANTS` (incluyendo
  `name`/`desc`/`label`/`desc` ya presentes) porque son datos de registro consumidos como un todo por ítems 4-5.
  Los **mapas de UI separados** (`VIDEO_MODEL_LABELS`, `VIDEO_MODE_LABELS`, y cualquier label map de ComfyUI) NO entran
  aquí: van a `telegram/` en ítem 5 (D1).
- A7 — **`JobStatus` se introduce como vocabulario de dominio mínimo** (enum aditivo). Las transiciones/estado vivo
  (`asyncio.Event`, cancelación) son del `JobManager` en ítem 4. `Job` es un descriptor inmutable (id/user/kind/fecha).

## Architecture Approach

### QUÉ (comportamiento / contratos)

**1. Settings fail-fast (D4, D6, R1, R3, R5, R8):**
- Superficie de env = la tabla del impacto (y `bot.py:41-49,52-72,172`):
  `TELEGRAM_BOT_TOKEN` (req), `REPLICATE_API_TOKEN` (req), `XAI_API_KEY` (req), `KIE_API_KEY` (opc `""`),
  `COMFYUI_HOST` (opc `""`), `COMFYUI_PORT` (opc `22`, **int** — D6), `ALLOWED_TELEGRAM_IDS` (opc `None`),
  `VARIABLES_ADMIN_IDS` (opc `None`), `REFINE_CONFIRM_TIMEOUT` (opc `300`).
- Semántica de listas de IDs (R1): string vacío o solo espacios → `None` (abierto). CSV `"1, 2,3"` → `{1, 2, 3}`.
  Ítem no numérico → `ValidationError` (fail-fast). **Nunca** convertir blank a `set()` (lockout).
- No instanciación en import: `Settings()` solo se construye al llamar `get_settings()`; `get_settings` con
  `@lru_cache(maxsize=1)`. Tests con `monkeypatch` env + `get_settings.cache_clear()`.
- Requeridos ausentes → `ValidationError` al boot (fail-fast). Opcionales con defaults exactos de arriba.

**2. Contratos de dominio:**
- `catalog.py` — registro de modelos (D1): `MODELS`, `GROK_IMAGINE_VARIANTS` + constantes
  (`DEFAULT_MODEL="grok"`, `VALID_MODELS=("grok","seedream","faceswap","grok_video","comfyui")`,
  `DEFAULT_GROK_IMAGINE_PROVIDER="kie"`, `DEFAULT_GROK_IMAGINE_VARIANT="quality"`,
  `VALID_GROK_IMAGINE_PROVIDERS=("xai","replicate","kie")`, `VALID_GROK_IMAGINE_VARIANTS=("standard","quality")`)
  + función pura de resolución de id por provider/variant.
- `user_config.py` — (D2, R4) `UserConfig` modela SOLO la porción persistida de `sessions.json` (espejo de
  `_default_session_record`): `model`, `grok_imagine_provider`, `grok_imagine_variant`, config de video
  (`duration/aspect_ratio/resolution/model/mode`), config de ComfyUI (`model/lora/refine` como `"0"/"1"`), y legacy
  opcionales `source_path`, `integrate_ref_path`, `state`. Estado efímero (`pending_prompt`,
  `awaiting_long_prompt_text`, `pending_edit_file_ids`, ...) NO está en dominio (app/FSM, ítems 4-5).
  `from_record(dict)` tolerante (claves faltantes, `duration` como `str`, `comfyui_refine` `"0"/"1"`,
  alias legacy `grok_provider` → `grok_imagine_provider`) y con fallback a defaults + valid-set, igual que los getters
  de `sessions.py`.
- `generation.py` — (D3) `GenerationRequest` provider-agnóstico mínimo: `provider`, `model_id`, `media_type`,
  `prompt`, `source` opcional (referencia de imagen), `aspect_ratio`, `video_duration`, `video_resolution`,
  `video_mode`. **Sin** campos provider-específicos (data-URIs xAI, BytesIO replicate, upload ComfyUI). `GenerationResult`
  genérico (media + metadatos). `MediaType` (image/video).
- `variables.py` — (R2, R10) `PromptTemplate` value object con render por **regex** (`_PLACEHOLDER_RE` /
  `_FORMAT_EXPR_RE`), **NO `str.format`**, que tolera llaves JSON literales, con fallback a join y clean-gaps.
  Funciones puras `normalize_items`, `pluralize`, `list_for_placeholder`, `combo_key`, `combo_label`.
- `job.py` — (R11) `MAX_ACTIVE_JOBS_PER_USER = 3` como constante de dominio + `Job` (inmutable) + `JobStatus` enum.

**3. DoD medible del ítem:**
- [ ] `pytest tests/unit -q` desde `/home/ubuntu/repos/grokV2/.venv` en verde (tests nuevos).
- [ ] `git diff --stat /home/ubuntu/repos/grok` vacío (0 cambios sobre grok) y no existen `src/grokbot/{providers,
      repositories,application,telegram,main.py,shared}`.
- [ ] `import grokbot.settings` sin env no instancia `Settings`; `get_settings()` sin requeridos lanza `ValidationError`.
- [ ] `Settings` parsea IDs blank→`None`, CSV→`set`, no-int→`ValidationError`; `comfyui_port` es `int`.
- [ ] Un `sessions.json` real de grok (con `comfyui_refine:"0"`, `grok_imagine_provider`, etc.) se carga vía
      `UserConfig.from_record` sin error y conserva valores válidos (test con el JSON de referencia anclado en 81832a5).
- [ ] Un template JSON con llaves literales renderiza con regex y produce JSON parseable; fallback + clean-gaps OK.

### CÓMO (estructura / patrones / orden)

**Estructura exacta de archivos a crear (paths absolutos):**
```
/home/ubuntu/repos/grokV2/
├── .gitignore
├── .env.example
├── README.md
├── pyproject.toml
├── src/grokbot/__init__.py
├── src/grokbot/settings.py
├── src/grokbot/domain/__init__.py
├── src/grokbot/domain/catalog.py
├── src/grokbot/domain/generation.py
├── src/grokbot/domain/user_config.py
├── src/grokbot/domain/variables.py
├── src/grokbot/domain/job.py
├── tests/conftest.py
└── tests/unit/
    ├── test_smoke.py
    ├── test_settings.py
    └── domain/
        ├── test_catalog.py
        ├── test_generation.py
        ├── test_user_config.py
        ├── test_variables.py
        └── test_job.py
```
No crear ningún otro path. No tocar `/home/ubuntu/repos/grok/**` (lectura únicamente).

**Pattern-to-copy (grok = referencia SEMÁNTICA; se transcriben valores, no se copia implementación):**
| Pieza nueva | Fuente de referencia (grok, read-only) | Qué se adapta |
|---|---|---|
| `pyproject.toml` ini tests | `grok/pytest.ini` + `grok/requirements-dev.txt` | `asyncio_mode=auto`, `testpaths=["tests"]`, `pythonpath=["src"]`; deps runtime/dev exactas |
| `.env.example` | `grok/.env.example` + `grok/bot.py:41-49,52-72,172` | Superficie REAL (R9): quitar `REPLICATE_MODEL`; agregar `VARIABLES_ADMIN_IDS` y `REFINE_CONFIRM_TIMEOUT` |
| `settings.py` (validators IDs) | `grok/bot.py:52-68` `_parse_allowed_telegram_ids` / `_parse_admin_telegram_ids` | Semántica blank→None/CSV→set/no-int→error, sobre pydantic-settings con `NoDecode` |
| `domain/catalog.py` | `grok/bot.py:84-122` (MODELS), `141-156` (GROK_IMAGINE_VARIANTS), `157-158`, `856-874` (resolución) | Transcribir spec records; resolver id puro |
| `domain/user_config.py` | `grok/sessions.py:16-39` (constantes), `46-66` (`_default_session_record`), `82-133` (`_ensure_full` legacy), `246-278` (`get_video_config`), `383-394` (`get_comfyui_config`) | Mismos defaults/valid-sets y fallbacks, como dataclasses + `from_record` |
| `domain/variables.py` | `grok/variables_store.py:78-85` (regex), `106-114` (normalize), `252-337` (render/fallback/clean/fields), `323-330` (combo_key) | Funciones puras sobre `PromptTemplate` |
| `domain/job.py` | `grok/bot.py:437-467` (MAX=3, shape de job id/kind) | Constante + `Job`/`JobStatus` |

**Contratos / firmas clave (interfaces first — implementar en este orden):**

`settings.py`:
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")  # sin env_file (A4)
    telegram_bot_token: str
    replicate_api_token: str
    xai_api_key: str
    kie_api_key: str = ""
    allowed_telegram_ids: Annotated[set[int] | None, NoDecode] = None
    variables_admin_ids: Annotated[set[int] | None, NoDecode] = None
    refine_confirm_timeout: int = 300
    comfyui_host: str = ""
    comfyui_port: int = 22
    @field_validator("allowed_telegram_ids", "variables_admin_ids", mode="before")
    @classmethod
    def _parse_id_set(cls, v): ...   # None|set→pasar; str blank→None; CSV→{int(...)}; no-int→deja que pydantic eleve ValidationError

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```
El patrón `Annotated[set[int] | None, NoDecode]` + `field_validator(mode="before")` está **validado empíricamente**
con pydantic-settings 2.15.0 / pydantic 2.13.5: `""`→None, `"   "`→None, `"1, 2,3"`→`{1,2,3}`, `"abc"`→ValidationError.
`NoDecode` importa de `pydantic_settings`.

`domain/catalog.py`:
```python
MODELS: dict[str, dict]            # spec records idénticos a grok bot.py:84-122
GROK_IMAGINE_VARIANTS: dict[str, dict]
DEFAULT_MODEL = "grok"
VALID_MODELS = tuple(MODELS)       # ("grok","seedream","faceswap","grok_video","comfyui")
DEFAULT_GROK_IMAGINE_PROVIDER = "kie"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"
VALID_GROK_IMAGINE_PROVIDERS = ("xai", "replicate", "kie")
VALID_GROK_IMAGINE_VARIANTS = ("standard", "quality")

def model_spec(key: str) -> dict: ...
    # MODELS.get(key, MODELS[DEFAULT_MODEL])  — espejo de get_model

def resolve_grok_config(provider: str, variant: str) -> dict:
    # normaliza provider→VALID_GROK_IMAGINE_PROVIDERS si no (default kie); variant→valid si no (default quality)
    # devuelve {"provider","variant","id"} donde id = spec[variant][{replicate:replicate_id, kie:kie_id, else:id}]
    # espejo EXACTO de bot.py get_grok_imagine_config (856-874)

def resolve_model_id(key: str, *, provider: str | None = None, variant: str | None = None) -> str:
    # key=="grok" → resolve_grok_config(provider, variant)["id"]
    # else → model_spec(key)["id"]
```

`domain/generation.py`:
```python
class MediaType(str, Enum):
    IMAGE = "image"; VIDEO = "video"

@dataclass(frozen=True)
class TelegramFileRef: file_id: str
@dataclass(frozen=True)
class LocalPathRef: path: str
@dataclass(frozen=True)
class UrlRef: url: str
@dataclass(frozen=True)
class KieTaskRef: task_id: str; index: int = 0
ImageSource = TelegramFileRef | LocalPathRef | UrlRef | KieTaskRef

@dataclass(frozen=True)
class GenerationRequest:
    provider: str
    model_id: str
    media_type: MediaType
    prompt: str
    source: ImageSource | None = None
    aspect_ratio: str | None = None
    video_duration: int | None = None
    video_resolution: str | None = None
    video_mode: str | None = None

@dataclass(frozen=True)
class GenerationResult:
    provider: str; model_id: str; media_type: MediaType
    data: bytes | None = None
    file_path: str | None = None
    remote_url: str | None = None
    mime_type: str | None = None
    meta: dict = field(default_factory=dict)   # extras provider (ej. kie_task_id) — ítem 2 puede extender aditivamente
```

`domain/user_config.py`:
```python
# constantes de video/comfyui tomadas de sessions.py:16-39 (DEFAULT_*/VALID_* exactas)
@dataclass(frozen=True)
class VideoConfig:
    duration: int = 5; aspect_ratio: str = "16:9"; resolution: str = "720p"
    model: str = "grok-imagine-video"; mode: str = "normal"
    @classmethod
    def from_record(cls, rec) -> "VideoConfig": ...  # int() tolerante a str; fallback a default si ∉ valid-set
    def to_record(self) -> dict: ...                 # {"duration","aspect_ratio","resolution","model","mode"}

@dataclass(frozen=True)
class ComfyUIConfig:
    model: str = "krea2"; lora: str = "none"; refine: str = "1"   # refine "0"/"1" como str (paridad JSON)
    @classmethod
    def from_record(cls, rec) -> "ComfyUIConfig": ...             # model/lora ∉ valid-set → default; refine ∉ {"0","1"} → "1"
    def to_record(self) -> dict: ...
    @property
    def refine_enabled(self) -> bool: return self.refine == "1"

@dataclass(frozen=True)
class UserConfig:
    model: str = DEFAULT_MODEL
    grok_imagine_provider: str = DEFAULT_GROK_IMAGINE_PROVIDER
    grok_imagine_variant: str = DEFAULT_GROK_IMAGINE_VARIANT
    video: VideoConfig = VideoConfig()
    comfyui: ComfyUIConfig = ComfyUIConfig()
    source_path: str | None = None
    integrate_ref_path: str | None = None
    state: str = "IDLE"
    @classmethod
    def defaults(cls) -> "UserConfig": ...
    @classmethod
    def from_record(cls, rec: Mapping) -> "UserConfig": ...
        # claves faltantes→default; "grok_provider" presente y sin "grok_imagine_provider" → alias (legacy);
        # model/provider/variant normalizados (model ∉ VALID_MODELS→"grok"; provider ∉ valid→"kie"; variant ∉ valid→"quality");
        # delega en VideoConfig/ComfyUIConfig.from_record para los grupos video_*/comfyui_*;
        # claves desconocidas (incl. video_hourly_timestamps) se ignoran (R4/A5).
    def to_record(self) -> dict: ...
        # dict plano con claves del record de sesión (source_path, integrate_ref_path, state, model,
        # grok_imagine_provider, grok_imagine_variant, video_duration/aspect_ratio/resolution/model/mode,
        # comfyui_model/lora/refine). NO incluye video_hourly_timestamps (dueño: repo ítem 3).
```

`domain/variables.py`:
```python
LIST_NAMES = ("poses", "angles", "actions")
DEFAULT_TEMPLATE = "{pose}, {angle}, {action}"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")      # idéntica a variables_store.py:79
_FORMAT_EXPR_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*(?:[.!:])")  # idéntica a variables_store.py:85

def normalize_items(raw) -> list[str]: ...          # espejo variables_store.py:106-114
def pluralize(word: str) -> str: ...
def list_for_placeholder(placeholder, lists) -> str | None: ...   # espejo variables_store.py:362-371
def clean_gaps(text: str) -> str: ...               # espejo variables_store.py:306-314 (colapsar ", ,", trim)

@dataclass(frozen=True)
class PromptTemplate:
    template: str
    @classmethod
    def default(cls) -> "PromptTemplate": ...
    def fields(self) -> list[str]: ...               # _PLACEHOLDER_RE.findall(template), en orden
    def has_format_expr(self) -> bool: ...
    def missing_fields(self, values) -> list[str]: ...
    def render(self, values: Mapping[str, str]) -> str:
        # si has_format_expr o missing_fields → ", ".join(values.values())  (fallback)
        # else → _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)  → tolera llaves JSON literales
    def render_inline(self, fields: list[str]) -> str: ...   # espejo build_prompt_inline (posicional + clean_gaps; join si sin placeholders)
    def render_positional(self, values: list[str]) -> str: ...  # espejo _render_positional
def combo_key(template, values: Mapping) -> tuple: ...   # tuple(values.get(f,"") for f in PromptTemplate(template).fields())
def combo_label(values: Mapping) -> str: ...             # ", ".join(values.values())
```
Regla dura de R2: **ningún `str.format`** ni `str.format_map` para renderizar templates.

`domain/job.py`:
```python
MAX_ACTIVE_JOBS_PER_USER = 3          # espejo bot.py:438
class JobStatus(str, Enum):
    RUNNING = "running"; CANCELLED = "cancelled"; COMPLETED = "completed"; FAILED = "failed"
@dataclass(frozen=True)
class Job:
    job_id: str
    user_id: int
    kind: str
    created_at: float
    status: JobStatus = JobStatus.RUNNING
```

**Orden de implementación y wiring:** pre-task (git) → Task 1 scaffolding (habilita el venv y pytest) → Task 2 settings
+ conftest (habilita testear env) → Task 3 dominio (sin dependencias entre sí excepto `user_config`→`catalog`).
`settings.py` NO es importado por `domain/*` (dominio puro). Quién llama a quién al final del ítem:
`tests` → `grokbot.settings.get_settings` / `grokbot.domain.*`; nada más.

## Context

- `docs/SPEC_REFACTOR.md` §4 (principios), §5.1 (estructura), §5.7 (Settings), §7 (fases). `/home/ubuntu/repos/grokV2/docs/SPEC_REFACTOR.md`
- CLARIFY (decisiones bloqueadas 1-7, assumptions). `/home/ubuntu/repos/grokV2/.planning/quick/20260903-grokv2-rearch/CLARIFY.md`
- Impact report. `/home/ubuntu/repos/grokV2/.grok/agent-memory/impact-analyzer/item1-domain-settings.md`
- Referencia de comportamiento (read-only): `/home/ubuntu/repos/grok/{bot.py,sessions.py,variables_store.py,.env.example,pytest.ini,requirements.txt,requirements-dev.txt,tests/test_variables_store.py,tests/test_sessions_video_config.py,sessions.json,variables_lists.json}`. Grok HEAD ancla Fase 0: `81832a5`.

---

## Tasks

### Task 1: Scaffolding del proyecto grokV2 (pyproject + layout src + venv + smoke test)
**type:** auto
**Objective:** grokV2 es un repo de Python instalable con `src/`, pytest configurado (`asyncio_mode=auto`,
`testpaths=["tests"]`, `pythonpath=["src"]`), `.env.example` real, README mínimo, venv creado y un smoke test en verde.
**Files:**
- Crear: `/home/ubuntu/repos/grokV2/pyproject.toml`, `.env.example`, `README.md`, `.gitignore`,
  `src/grokbot/__init__.py`, `tests/unit/test_smoke.py`.
**Action:**
- `pyproject.toml`:
  - `[build-system]` setuptools>=68; `[project]` name `grokbot`, version `0.1.0`, `requires-python = ">=3.11"`,
    `dependencies = ["pydantic>=2.9,<3", "pydantic-settings>=2.2,<3"]`,
    `[project.optional-dependencies] dev = ["pytest>=8,<9", "pytest-asyncio>=0.24,<1"]`.
  - `[tool.setuptools.packages.find] where=["src"]`.
  - `[tool.pytest.ini_options]` con `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `pythonpath = ["src"]`.
- `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.env`, `*.egg-info/`, `dist/`, `build/`,
  `.coverage`, `htmlcov/`.
- `.env.example` (superficie REAL — R9; NO incluir `REPLICATE_MODEL`):
  ```
  TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
  REPLICATE_API_TOKEN=your_replicate_api_token_here
  XAI_API_KEY=your_xai_api_key_here
  KIE_API_KEY=your_kie_api_key_here            # opcional; vacío = provider no disponible

  # Telegram user IDs separados por coma. Vacío = bot abierto.
  ALLOWED_TELEGRAM_IDS=123456789,987654321
  # Admins del panel /listas. Vacío = todos los permitidos (o todos si no hay allowlist).
  VARIABLES_ADMIN_IDS=

  # Tiempo (seg) de espera de confirmación de refine ComfyUI.
  REFINE_CONFIRM_TIMEOUT=300

  # ComfyUI remoto (SSH/Vast). Puerto default 22.
  COMFYUI_HOST=
  COMFYUI_PORT=22
  ```
- `README.md` mínimo: qué es el repo (re-implementación por capas de @grok, ver docs/SPEC_REFACTOR.md), layout,
  setup (`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`), correr tests
  (`.venv/bin/pytest tests/unit -q`). Sin doc extensa.
- `src/grokbot/__init__.py`: `__version__ = "0.1.0"` + docstring corto. Nada más.
- `tests/unit/test_smoke.py`:
  ```python
  import grokbot

  async def test_import_and_asyncio_auto():
      assert grokbot.__version__ == "0.1.0"
  ```
  (valida `pythonpath=["src"]` y `asyncio_mode=auto`).
- NO crear `tests/unit/domain/` todavía (lo hace Task 3).
- Crear venv e instalar: `cd /home/ubuntu/repos/grokV2 && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
**Verification:** `.venv/bin/pytest tests/unit -q` → 1 passed (test_smoke). `.venv/bin/python -c "import grokbot; print(grokbot.__version__)"` → `0.1.0`.
**Done:** pyproject parsea, instalación editable OK, pytest levanta la config y pasa el smoke test; `.gitignore`
excluye `.venv/` y `.env`; `.env.example` sin `REPLICATE_MODEL`.

### Task 2: Settings pydantic fail-fast + conftest aislante de env + test_settings
**type:** auto
**Objective:** `src/grokbot/settings.py` carga/valida la superficie de env con fail-fast; `get_settings()` cachea;
tests prueban R1/R3/R5/R8/D6. El módulo se importa sin instanciar.
**Files:**
- Crear: `/home/ubuntu/repos/grokV2/src/grokbot/settings.py`, `/home/ubuntu/repos/grokV2/tests/conftest.py`,
  `/home/ubuntu/repos/grokV2/tests/unit/test_settings.py`.
**Action:**
- Implementar `Settings` y `get_settings()` **exactamente** como en el contrato de la sección CÓMO. Detalles:
  - `from typing import Annotated`; `from functools import lru_cache`; `from pydantic import field_validator`;
    `from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode`.
  - El validator `mode="before"` de IDs: si `v` es `None` o un `set` ya → devolver tal cual; si no es `str` → devolver
    (pydantic decidirá); si `str`: `s = v.strip()`; `if not s: return None`; parsear
    `{int(part.strip()) for part in s.split(",") if part.strip()}` dejando que `ValueError` de `int()` se convierta en
    `ValidationError` (fail-fast). NO filtrar silenciosamente ítems no numéricos.
  - `model_config = SettingsConfigDict(extra="ignore")` — **sin** `env_file` (A4). Mapeo field→env es case-insensitive
    automático (`telegram_bot_token` ↔ `TELEGRAM_BOT_TOKEN`).
  - `get_settings()`: `@lru_cache(maxsize=1)`. **Prohibido** instanciar `Settings()` a nivel de módulo.
- `tests/conftest.py`:
  ```python
  SETTINGS_ENV_VARS = ("TELEGRAM_BOT_TOKEN","REPLICATE_API_TOKEN","XAI_API_KEY","KIE_API_KEY",
                       "COMFYUI_HOST","COMFYUI_PORT","ALLOWED_TELEGRAM_IDS","VARIABLES_ADMIN_IDS",
                       "REFINE_CONFIRM_TIMEOUT")

  @pytest.fixture(autouse=True)
  def _isolate_settings_env(monkeypatch):
      from grokbot.settings import get_settings
      for var in SETTINGS_ENV_VARS:
          monkeypatch.delenv(var, raising=False)
      get_settings.cache_clear()
      yield
      get_settings.cache_clear()
  ```
- `tests/unit/test_settings.py` — casos mínimos (cada test setea su propio env vía `monkeypatch.setenv`):
  1. `test_missing_required_fails_fast`: con los 3 requeridos ausentes, `Settings()` (o `get_settings()`) lanza
     `pydantic.ValidationError` y el mensaje nombra los campos requeridos.
  2. `test_required_present_and_defaults`: setear los 3 requeridos → `get_settings()` devuelve Settings con
     `kie_api_key==""`, `comfyui_host==""`, `comfyui_port==22` (int), `refine_confirm_timeout==300`,
     `allowed_telegram_ids is None`, `variables_admin_ids is None`.
  3. `test_allowlist_csv`: `ALLOWED_TELEGRAM_IDS="1, 2,3"` → `{1, 2, 3}`.
  4. `test_allowlist_blank_is_none`: `""` y `"   "` → `None` (no `set()`, no error).
  5. `test_allowlist_invalid_raises`: `"abc"` → `ValidationError`; también `"1,abc"` → `ValidationError`.
  6. `test_admin_ids_independent`: `VARIABLES_ADMIN_IDS="10, 20"` → `{10, 20}` mientras allowlist es `None`.
  7. `test_comfyui_port_int`: `COMFYUI_PORT="19956"` → `19956` (int); `REFINE_CONFIRM_TIMEOUT="10"` → `10`.
  8. `test_import_does_not_instantiate`: tras scrubbing, `import grokbot.settings` (ya importado) + construir un
     Settings con env mínimo no debe requerir env al importar; y llamar `get_settings()` sin requeridos lanza.
  9. `test_get_settings_cached_and_cache_clear`: dos llamadas devuelven `is` mismo objeto; tras
     `get_settings.cache_clear()` + cambio de env, devuelve objeto nuevo con el nuevo valor.
**Verification:** `.venv/bin/pytest tests/unit -q` → test_smoke + test_settings en verde.
**Done:** los 9 casos pasan; R1/R3/R5/R8/D6 cubiertos con tests; sin `.env` en cwd los tests son deterministas.

### Task 3: Paquete de dominio puro (catalog, generation, user_config, variables, job) + tests unit
**type:** auto
**Objective:** `src/grokbot/domain/` implementa las entidades/constantes/contratos de la sección CÓMO, sin I/O ni
deps externas; tests unit cubren R2/R4/R6/R11/D1/D2/D3. Se sugiere commit por sub-grupo (a y b).
**Files:**
- Crear: `/home/ubuntu/repos/grokV2/src/grokbot/domain/{__init__,catalog,generation,user_config,variables,job}.py`
  y `/home/ubuntu/repos/grokV2/tests/unit/domain/{test_catalog,test_generation,test_user_config,test_variables,test_job}.py`.
**Action:**
- Implementar cada módulo según los contratos y tablas de pattern-to-copy de la sección CÓMO. Reglas:
  - **Solo stdlib** en `domain/*` (`dataclasses`, `enum`, `re`, `typing`, `collections.abc`). Prohibido importar
    `settings`, pydantic, o grok.
  - Transcribir constantes con valores exactos de las referencias (paridad de datos, no copia de archivos).
  - `domain/__init__.py` re-exporta la API pública: `MODELS, GROK_IMAGINE_VARIANTS, model_spec,
    resolve_model_id, resolve_grok_config, DEFAULT_MODEL`, `MediaType, GenerationRequest, GenerationResult,
    ImageSource`, `UserConfig, VideoConfig, ComfyUIConfig`, `PromptTemplate, normalize_items, combo_key,
    combo_label`, `Job, JobStatus, MAX_ACTIVE_JOBS_PER_USER`.
  - Orden de sub-grupos (para commits atómicos):
    - (a) `catalog.py` + `generation.py` + `job.py` + `domain/__init__.py` + sus tests → commit 1.
    - (b) `user_config.py` (importa constantes de `catalog`) + `variables.py` + sus tests → commit 2.
- `tests/unit/domain/test_catalog.py` — casos mínimos:
  1. `MODELS` tiene exactamente las 5 claves `VALID_MODELS`; cada spec tiene `key`, `id`, `provider`.
  2. `model_spec("no_existe")` cae a `MODELS[DEFAULT_MODEL]` (grok).
  3. `resolve_model_id("grok", provider="xai", variant="standard") == "grok-imagine-image"`.
  4. `resolve_model_id("grok", provider="replicate", variant="quality") == "xai/grok-imagine-image-quality"`.
  5. `resolve_model_id("grok", provider="kie", variant="standard") == "grok-imagine-image-2-0/text-to-image"`.
  6. Variant inválida → cae a `quality`; provider inválido → cae a `kie` (default).
  7. `resolve_model_id("comfyui") == "comfyui"`; `resolve_model_id("grok_video") == "grok-imagine-video"`.
- `tests/unit/domain/test_generation.py`:
  1. `MediaType.IMAGE.value == "image"` y `MediaType.VIDEO.value == "video"`.
  2. Construir `GenerationRequest(provider="xai", model_id="grok-imagine-image-quality",
     media_type=MediaType.IMAGE, prompt="hola")` → campos default `None`; frozen (setattr lanza `FrozenInstanceError`).
  3. Con `source=TelegramFileRef("AgAA...")` y campos de video seteados → se conservan.
  4. `GenerationResult` con `data=bytes` y `meta={"kie_task_id": "x"}`; igualdad estructural entre dos instancias.
- `tests/unit/domain/test_user_config.py`:
  1. `UserConfig.defaults()` espeja `_default_session_record`: `model=="grok"`, provider `"kie"`, variant `"quality"`,
     `video.duration==5`, `video.aspect_ratio=="16:9"`, `video.resolution=="720p"`, `video.model=="grok-imagine-video"`,
     `video.mode=="normal"`, `comfyui.model=="krea2"`, `comfyui.lora=="none"`, `comfyui.refine=="1"`,
     `source_path is None`, `integrate_ref_path is None`, `state=="IDLE"`.
  2. `from_record({})` (o `{"model": "grok"}`) → defaults (R4 claves faltantes).
  3. Legacy alias: `from_record({"grok_provider": "xai"})` → `grok_imagine_provider=="xai"` y variant default
     `"quality"`; y `to_record()` **no** contiene `grok_provider` (solo `grok_imagine_provider`).
  4. `video_duration` como `"10"` (str) → `video.duration==10`; `"99"` → default `5`; `video_model` inválido → default;
     `video_resolution=="1080p"` → `"720p"`; `video_aspect_ratio=="3:2"` es válido y se conserva.
  5. `comfyui_refine=="0"` se conserva como `"0"` y `refine_enabled is False`; `"1"` → `True`; `"banana"` → default `"1"`.
  6. `comfyui_model` obsoleto (ej. `"realvisxl"`) → default `"krea2"`.
  7. `from_record` ignora claves desconocidas (ej. `video_hourly_timestamps`, `source_path` extra) sin error (R4/A5).
  8. Round-trip: `UserConfig.from_record(rec).to_record() == rec` para un record canónico completo (el del sessions.json
     real de grok, sin `video_hourly_timestamps`); y un JSON real de grok (con `comfyui_refine:"0"`, provider `"kie"`)
     se carga sin pérdida de los valores válidos.
- `tests/unit/domain/test_variables.py` (R2):
  1. `normalize_items` limpia no-list/no-str/blancos y hace `.strip()`.
  2. `PromptTemplate("{pose}, {angle}, {action}").fields() == ["pose","angle","action"]`.
  3. Template JSON con llaves literales y placeholders (copiar `JSON_TEMPLATE` de
     `grok/tests/test_variables_store.py:159-166`) → `render` produce texto que es `json.loads`-able y los placeholders
     quedan sustituidos (assert sobre `"pose": "de pie"`), llaves literales `{}` intactas.
  4. Fallback por placeholder desconocido: `PromptTemplate("{unknown} {pose}").render({"pose":"a","angle":"b",
     "action":"c"}) == "a, b, c"`.
  5. Fallback por format-expr: `PromptTemplate("{pose.foo}").render(...)` → join.
  6. `render_inline`: template default con `["de pie"]` → `"de pie"` (clean-gaps quita el placeholder vacío y el
     separador); `["de pie","frontal","extra"]` → `"de pie, frontal, extra"`; template sin placeholders → join.
  7. `render_positional` con template `"{pose} {angle}"` y `["A","B"]` → `"A B"`.
  8. `combo_key` usa solo los campos del template: template `"{pose} {angle}"`, values con `action` → `("de pie",
     "de frente")`.
  9. `list_for_placeholder("{bodies}")` resuelve a la lista `bodies` (exacta) y `{body}` → `bodies` (plural regular).
- `tests/unit/domain/test_job.py`:
  1. `MAX_ACTIVE_JOBS_PER_USER == 3`.
  2. `Job(job_id="ab12cd34", user_id=1, kind="imagine", created_at=0.0)` → campos conservados, default
     `status == JobStatus.RUNNING`, frozen, igualdad estructural.
  3. `JobStatus` expone `running/cancelled/completed/failed`.
**Verification:** `.venv/bin/pytest tests/unit -q` → suite completa en verde (smoke + settings + domain).
**Done:** dominio puro implementado (sin imports externos), DoD del ítem cumplido; tests de R2/R4/R6/R11 y D1-D3 verdes.

---

## Instrucciones para gsd-executor

- **Dónde trabajar:** SOLO bajo `/home/ubuntu/repos/grokV2`. `/home/ubuntu/repos/grok` es read-only: se lee para
  transcribir valores de referencia, jamás se edita ni se importa.
- **No-touch (prohibido crear/editar):** `src/grokbot/providers/`, `repositories/`, `application/`, `telegram/`,
  `main.py`, `shared/`, `docs/SPEC_REFACTOR.md`, `.planning/**` (salvo commits), `.grok/**`. No escribir fuera de la
  lista de Files de cada task.
- **Convenciones:** dominio = dataclasses frozen + enums de stdlib, sin pydantic y sin I/O. `settings.py` = pydantic.
  Docstrings cortos en inglés (estilo de `sessions.py`). Sin `print` en código de librería. Type hints completos.
- **Commits atómicos por work unit** (mensajes en español/inglés cortos, con el formato del repo; incluir el trailer):
  1. Base (pre-task): `chore: bootstrap grokV2 repo (planning + scaffolding base)` — tras `git init`, commit inicial de
     `.planning/`, `docs/`, `.grok/`, `.gitignore`.
  2. Task 1: `feat(scaffold): pyproject src layout env-example y pytest config`.
  3. Task 2: `feat(settings): Settings pydantic fail-fast + get_settings cache + tests env`.
  4. Task 3a: `feat(domain): catalog + generation + job (entidades puras) + tests`.
  5. Task 3b: `feat(domain): user_config + variables (tolerancia legacy + template regex) + tests`.
  No commitear `.venv/`, `.env`, cachés, `.pytest_cache`.
- **Self-check antes de cada commit:** correr `.venv/bin/pytest tests/unit -q`; verificar con
  `git status --porcelain` que solo están los archivos esperados; confirmar 0 cambios sobre grok con
  `git -C /home/ubuntu/repos/grok status --porcelain` (debe estar limpio/inalterado por vos).
- **Registro:** mantener un log corto de ejecución (comandos + resultados + desvíos) como SUMMARY al final del ítem.
- **Residuales:** si algo no cierra (p. ej. versión de pydantic-settings resuelta distinta), reportarlo en el SUMMARY y
  en la respuesta final; no cambiar decisiones bloqueadas por cuenta propia.

## Test commands

- Suite del ítem (desde `/home/ubuntu/repos/grokV2`):
  `.venv/bin/pytest tests/unit -q`
- Solo settings:
  `.venv/bin/pytest tests/unit/test_settings.py -q`
- Solo dominio:
  `.venv/bin/pytest tests/unit/domain -q`
- Sanidad de import (sin env, no debe instanciar Settings):
  `.venv/bin/python -c "import grokbot.settings, grokbot.domain; print('ok')"`
- Verificación read-only grok intacto:
  `git -C /home/ubuntu/repos/grok status --porcelain` (vacío; no tocar)

## Risks + Mitigation

| Riesgo (impact) | Mitigación en este PLAN |
|---|---|
| **R1 CRITICAL** allowlist/admins blank vs set(), no-int fail-fast | Validator `NoDecode`+`before` (validado empíricamente); tests `""`/`"   "`→None, `"1, 2,3"`→set, `"abc"`→ValidationError (Task 2) |
| **R2 CRITICAL** render PromptTemplate con llaves JSON | `PromptTemplate.render` por regex (`_PLACEHOLDER_RE.sub`), jamás `str.format`; tests JSON literal parseable + fallback + clean-gaps (Task 3b, test_variables) |
| **R3 CRITICAL** requeridos fail-fast vs opcionales con defaults | `Settings` con 3 campos requeridos sin default; opcionales con defaults exactos; test de ausencia → ValidationError (Task 2) |
| **R4 MEDIUM** UserConfig tolerante a JSON legacy | `UserConfig.from_record` ignora claves faltantes/desconocidas, alias `grok_provider`, `duration` str, `comfyui_refine` "0"/"1"; tests con record real (Task 3b) |
| **R5 MEDIUM** no instanciar Settings en import; lru_cache | `get_settings()` cachea; conftest autouse borra env y caché antes/después de cada test (Task 2) |
| **R6 MEDIUM** scope creep por catálogo | D1 acotado: `catalog.py` con MODELS + variantes + resolución; mapas UI separados difieren a ítem 5; 0 dirs de ítems 2-6 (Tasks 3a + no-touch) |
| **R7 MEDIUM** random global en variables | Diferido: ítem 1 no incluye selección aleatoria ni combos aleatorios (solo funciones puras); el RNG inyectable se decide en ítems 3-4 (Assumption A3) |
| **R8 LOW** comfyui_port str→int | D6 aplicado: `comfyui_port: int = 22`; test `"19956"`→`19956` (Task 2) |
| **R9 LOW** .env.example stale | Reescrito con la superficie real del impacto/bot.py; sin `REPLICATE_MODEL` (Task 1) |
| **R10 LOW** sin parser estricto de variables | Se mantiene: normalización tolerante + templates self-describing; parser estricto no se introduce (Task 3b) |
| **R11 LOW** MAX_ACTIVE_JOBS_PER_USER | Constante fijada en `domain/job.py` (`=3`), consumida por JobManager en ítem 4 (Task 3a) |

## Success Criteria

- [ ] `pytest tests/unit -q` en verde (smoke + settings + 5 suites de dominio) desde `.venv` de grokV2.
- [ ] `git -C /home/ubuntu/repos/grok status --porcelain` vacío; 0 cambios sobre grok.
- [ ] No existen `src/grokbot/{providers,repositories,application,telegram,main.py,shared}`.
- [ ] `Settings` cubre exactamente la superficie env del impacto; `comfyui_port` int; IDs blank→`None`/no-int→error.
- [ ] `get_settings()` cachea; importar `grokbot.settings` no instancia; requeridos ausentes → `ValidationError`.
- [ ] `UserConfig.from_record` carga un `sessions.json` real de grok sin pérdida de valores válidos ni error por legacy.
- [ ] `PromptTemplate` renderiza templates JSON con llaves literales (JSON parseable) y hace fallback/clean-gaps sin
      `str.format`.
- [ ] Dominio sin imports externos (solo stdlib); dataclasses frozen; constantes de registro con paridad de valores.
- [ ] Commits atómicos por work unit creados según la lista de Instrucciones; `.venv/` y `.env` no commiteados.
