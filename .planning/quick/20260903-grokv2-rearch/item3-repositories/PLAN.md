---
phase: quick
plan: item3-repositories
type: auto
item: "Item 3 — Extraer repositorios (Fase 3 SPEC §3.3/§4.3/§5.1/§5.6/§6/§7)"
source: user-request + SPEC_REFACTOR.md + CLARIFY.md (locked) + impact-analyzer/item3-repositories.md
mode: standard
---

# PLAN — Item 3: Repositorios (base + JSON session/variables/refs)

## Objective

Crear en `/home/ubuntu/repos/grokV2` la capa de infraestructura de persistencia `src/grokbot/repositories/`
(Protocols + backends JSON en disco) que consumirán `application/` (ítem 4), `telegram/` (ítem 5) y `main.py` (ítem 6):
`base.py` (Protocol `SessionRepository`/`VariablesRepository`/`GenerationRefsRepository` + escritura atómica),
`json_session_repo.py`, `json_variables_repo.py` (incl. seed `DEFAULT_LISTS` y packages con `packages_dir`) y
`generation_refs_repo.py`. Los JSON reales de grok (`sessions.json`, `variables_lists.json`, `generation_refs.json`)
se leen **tal cual** y se escriben con el MISMO shape y dump-flags (paridad §6), con escritura **NO destructiva** de
claves que el dominio no modela (`video_hourly_timestamps`, claves extra, `_package`, `regen`).

Outcome medible: `.venv/bin/pytest tests/unit tests/integration/repositories -q` en verde (tests nuevos), suite completa
`.venv/bin/pytest tests -q` en verde, **0 cambios sobre `/home/ubuntu/repos/grok`**, y no existen
`src/grokbot/{application,telegram,main.py,shared}` (ni `providers/` ni `domain/*` tocados).

## Scope

- **In:**
  - Edit `src/grokbot/settings.py` SOLO para D1 (`data_dir` + 4 propiedades derivadas de paths) + `tests/conftest.py`
    (`SETTINGS_ENV_VARS` += `GROK_DATA_DIR`) + `.env.example` + `tests/unit/test_settings.py`.
  - Create `src/grokbot/repositories/`: `__init__.py`, `base.py`, `json_session_repo.py`, `json_variables_repo.py`,
    `generation_refs_repo.py`.
  - Create `tests/integration/repositories/`: `test_json_session_repo.py`, `test_json_variables_repo.py`,
    `test_generation_refs_repo.py`.
- **Out / Non-goals:**
  - NO `application/`, `telegram/`, `main.py`, `shared/`, `providers/`, ni cambios en `domain/*` (ítems 1-2 commiteados
    se consumen tal cual). NO tocar `grok/**` (read-only referencia semántica).
  - NO backend no-JSON (SQLite/Redis): solo contratos (D-futuro). NO migración manual de datos (CLARIFY #7).
  - NO prompt-building/random-combination/FSM/setters finos: eso es dominio (ya existe) o `application/` ítem 4.
  - NO `sources_dir`/descargas (fuera de scope del ítem).
- **Constraints:** métodos sync (D6); escritura atómica tmp+`os.replace` (D5); repos requieren `Path` explícito en
  constructor (D1). Regla dura: nunca escribir en cwd ni derivar paths de `Path(__file__)`.

## Assumptions

- A1 — **D1 adoptado**: `Settings` gana un ÚNICO campo `data_dir: Path = Path("data")` (env `GROK_DATA_DIR`, alias
  pydantic) + 4 propiedades derivadas `sessions_file`/`variables_file`/`generation_refs_file`/`packages_dir`. Razón:
  grok mantiene los 4 archivos (3 JSON + `variables_packages/`) en el MISMO directorio runtime; un solo env var replica
  ese layout y reduce la superficie a documentar/limpiar en tests. Los repos NO leen Settings: se construyen con `Path`
  explícito; `main.py` (ítem 6) inyectará las propiedades. El default relativo `Path("data")` solo lo usa Settings
  (nada construye repos desde Settings en este ítem), y `.env`/ítem 6 lo override.
- A2 — **D3 adoptado**: las operaciones de packages viven en `VariablesRepository`/`json_variables_repo.py` (mismo
  archivo/lock/atomicidad; `activate_package` escribe el archivo activo). Diferir packages rompería `manage_lists` de
  ítem 4 sin repo de paquetes. `packages_dir` se fija ahora (default sibling del archivo activo).
- A3 — **Corrección menor al impacto (R4/D4)**: para `generation_refs.json`, grok `_load_generation_refs` NO tolera
  JSON malformado (propaga `JSONDecodeError`); solo top-level no-dict → `{}`. El PLAN sigue a grok (fuente de verdad):
  malformado propaga, top-level no-dict → `{}`. Variables tolera corrupto → seed (paridad). Sessions/refs: documento
  corrupto propaga (no backup `.corrupt-*` en este ítem; hardening diferido).
- A4 — **Legacy `grok_provider` coexistente con canónico (R3)**: precedencia canónica al leer; al escribir se dropea
  `grok_provider`. Esto es un endurecimiento sobre grok `_ensure_full` (que pisa el canónico con legacy cuando ambos
  existen) y materializa O2 del ítem 1.
- A5 — **`DEFAULT_LISTS` (contenido seed) se transcribe en `json_variables_repo.py`** (ítem 1 lo difirió a repos). No
  entra a `domain/`.
- A6 — **Fixtures de test 100% anonimizados** (usuario `111111111`, prompts dummy, `FAKE_FILE_ID`), con la MISMA forma
  de los JSON reales. Prohibido copiar IDs/prompts/`file_id` reales (R8).
- A7 — **`time` real en `get` de refs y quota**: los métodos de escritura exponen `now: float | None = None` (default
  `time.time()`) para tests deterministas; los `get`/`count` usan `time.time()` real y se testean sembrando `created_at`
  viejos vía `save(now=...)` o escritura directa del fixture.

## Architecture Approach

### QUÉ (comportamiento / contratos)

#### 1. Contratos de repositorio (interfaces first — `base.py`)

```python
# src/grokbot/repositories/base.py
from pathlib import Path
from typing import Protocol, runtime_checkable
from grokbot.domain.user_config import UserConfig

@runtime_checkable
class SessionRepository(Protocol):
    def get_config(self, user_id: int) -> UserConfig: ...
    def save_config(self, user_id: int, config: UserConfig) -> None: ...
    def record_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> None: ...
    def count_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> int: ...
    def count_global_video_hourly_usage(self, *, now: float | None = None) -> int: ...

@runtime_checkable
class VariablesRepository(Protocol):
    def get_lists(self) -> dict[str, list[str]]: ...
    def get_list(self, name: str) -> list[str]: ...
    def is_valid_list_name(self, name: str) -> bool: ...
    def get_template(self) -> str: ...
    def set_template(self, template: str) -> bool: ...
    def add_item(self, name: str, item: str) -> bool: ...
    def update_item(self, name: str, index: int, item: str) -> bool: ...
    def delete_item(self, name: str, index: int) -> bool: ...
    def get_blacklist(self) -> set[tuple[str, ...]]: ...
    def blacklist_add(self, key: tuple[str, ...]) -> bool: ...
    def blacklist_clear(self) -> None: ...
    # packages (D3)
    def list_packages(self) -> list[str]: ...
    def save_package(self, name: str, payload: dict) -> tuple[bool, str | None]: ...
    def load_package(self, name: str) -> dict | None: ...
    def package_exists(self, name: str) -> bool: ...
    def active_package_name(self) -> str | None: ...
    def activate_package(self, name: str) -> bool: ...
    def delete_package(self, name: str) -> bool: ...

@runtime_checkable
class GenerationRefsRepository(Protocol):
    def save(
        self, chat_id: int, message_id: int, *,
        kie_task_id: str | None = None, kie_index: int = 0, provider: str = "kie",
        kind: str = "image", prompt: str = "", regen: dict | None = None,
        now: float | None = None,
    ) -> None: ...
    def get(self, chat_id: int, message_id: int) -> dict | None: ...

def write_json_atomic(path: Path, data: dict, *, ensure_ascii: bool = True, indent: int = 2) -> None:
    """Atomic JSON write: parent mkdir + NamedTemporaryFile in same dir + os.replace."""
```

`base.py` contiene SOLO: imports stdlib + `grokbot.domain.user_config.UserConfig`, los 3 Protocols, y
`write_json_atomic`. Sin imports de los repos concretos (sin ciclos).

#### 2. Decisiones D1-D9 resueltas

- **D1 — paths.** Repos con `__init__(self, path: Path)` (requerido, sin default a cwd ni `Path(__file__)`).
  `JsonVariablesRepository(path, *, packages_dir: Path | None = None)` con default `packages_dir = path.parent /
  "variables_packages"` (sibling del archivo activo = layout grok). `Settings` gana `data_dir: Path = Path("data")`
  (env alias `GROK_DATA_DIR`) + propiedades `sessions_file`/`variables_file`/`generation_refs_file`/`packages_dir`
  derivadas (`data_dir / "sessions.json"`, `"variables_lists.json"`, `"generation_refs.json"`, `"variables_packages"`).
  **Efecto conftest/env/tests**: ver Task 1. `main.py` (ítem 6) inyecta paths desde esas propiedades; los repos NUNCA
  caen a cwd.
- **D2 — superficie `SessionRepository`** = `get_config`/`save_config` (`UserConfig`) + ops de quota horaria
  (`record_video_hourly_usage`, `count_video_hourly_usage`, `count_global_video_hourly_usage`). `get_config` tiene el
  side-effect de **persistir el record default** de usuario nuevo (paridad grok `get_session`/`_get_or_create_full`).
  `video_hourly_timestamps` es repo-owned (no en `UserConfig.to_record`). Setters finos de grok (`set_model`,
  `set_video_config`, `set_grok_imagine_config`, `set_source`, ...) NO van al repo: son use-cases de ítem 4 que mutan
  `UserConfig` en memoria y llaman `save_config`.
- **D3 — packages dentro de `VariablesRepository` AHORA** (A2), en `json_variables_repo.py`, fijando `packages_dir`.
- **D4 — archivo corrupto por repo** (A3): `sessions.json` → propaga `JSONDecodeError` (espejo grok); top-level no-dict
  → `ValueError` claro (guard endurecido). `generation_refs.json` → propaga `JSONDecodeError`; top-level no-dict → `{}`
  (paridad `_load_generation_refs`). `variables_lists.json` → tolera corrupto/missing → `{}` → seed de defaults
  (paridad `_load`/`_data`). Sin backup `.corrupt-<ts>` en este ítem (diferido).
- **D5 — atomicidad**: los 3 repos escriben vía `write_json_atomic` (tmp en el MISMO dir + `os.replace`). Contenido
  idéntico a grok (mismos `json.dump` flags); infra más robusta ante crash a mitad de dump.
- **D6 — métodos sync**: transacción read-modify-write atómica dentro del event loop (sin `await` entre load y save).
  `JsonVariablesRepository` conserva un `threading.Lock` módulo-instancia alrededor de cada read-modify-write (paridad
  `variables_store._LOCK`). Sessions/refs sin lock (espejo grok; escritura atómica reduce el riesgo de torn write).
- **D7 — `list_packages() -> list[str]`**: slugs ordenados alfabéticamente (NO `dict[str, Path]` de grok) — app-friendly
  para `manage_lists` (ítem 4).
- **D8 — `is_valid_list_name` en el repo** (mezcla constante `LIST_NAMES` de domain + listas self-describing del
  archivo activo): `name in LIST_NAMES or name in self.get_lists()`.
- **D9 — dump flags exactos** (paridad R1): variables_lists.json + package files → `ensure_ascii=False`; sessions.json +
  generation_refs.json → `ensure_ascii=True` (default); SIEMPRE `indent=2`, sin `sort_keys`, separadores default.

#### 3. Convenciones de escritura (no destructiva / legacy / paridad)

- **No destructiva**: ningún `save` reconstruye el doc desde cero. Cada repo muta el documento crudo cargado y solo
  toca las claves que conoce:
  - `save_config`: `rec = data[uid]` (o default si falta); `rec.update(config.to_record())` (el update deja intactas
    claves que `to_record` no emite → `video_hourly_timestamps`, extras); luego `rec.pop("grok_provider", None)`.
  - Variables: muta `data["lists"]`/`template`/`blacklist`/`_package` sobre el doc cargado; las claves top-level
    desconocidas se preservan en cada `_save`.
  - Refs: `save` reemplaza el record de esa key (paridad grok) pero preserva los records de las otras keys
    (prune + dump del dict completo); `regen` se guarda **opaco** (sin re-modelar).
- **Legacy `grok_provider`**: al LEER, `UserConfig.from_record` ya resuelve alias; en el repo `_ensure_full(rec)` migra
  `grok_provider`→`grok_imagine_provider` SOLO si el canónico no está (A4), y dropea `grok_provider` al escribir.
- **Seed de variables** SOLO cuando `lists` falta o queda vacío (`_ensure_full` mirror de variables_store.py:117-138).
  Un archivo de paquete activo (`bodies/hands/angles` + `_package`) NUNCA se contamina con poses/angles/actions (R6).

#### 4. DoD medible de paridad

- [ ] Fixture sesión real-shape (usuario `111111111`) se carga vía `get_config` → `UserConfig` correcto; y
      `get_config` + `save_config` mismo `UserConfig` → el JSON en disco **vuelve a ser byte-semánticamente igual** al
      fixture (round-trip sin pérdida de `video_hourly_timestamps` ni claves extra).
- [ ] Fixture variables paquete-activo se carga SIN sembrar; una mutación (`add_item`/`set_template`) preserva
      `_package` y las claves top-level extra.
- [ ] Fixture refs con claves extra en records → un `get` (con key presente) que prunea persiste y NO pierde las
      claves extra de los records vivos.
- [ ] Dump-flags verificados por TEST sobre el texto del archivo: variables/packages conservan acentos literales
      (`á` crudo, `ensure_ascii=False`); sessions/refs escapan no-ASCII (`á`, `ensure_ascii=True`); ambos con
      `indent=2` y sin sort.
- [ ] 0 cambios sobre `grok/**`; no existen `application/`, `telegram/`, `main.py`, `shared/`.
- [ ] `pytest tests/unit tests/integration/repositories -q` y `pytest tests -q` en verde.

### CÓMO (estructura / patrones / orden)

#### Estructura exacta (paths absolutos)

```
/home/ubuntu/repos/grokV2/
├── .env.example                                              # EDIT: + GROK_DATA_DIR
├── src/grokbot/settings.py                                   # EDIT: + data_dir + 4 propiedades (D1)
├── src/grokbot/repositories/__init__.py                      # CREATE: re-export API (crece por task)
├── src/grokbot/repositories/base.py                          # CREATE: 3 Protocols + write_json_atomic
├── src/grokbot/repositories/json_session_repo.py             # CREATE: JsonSessionRepository
├── src/grokbot/repositories/json_variables_repo.py           # CREATE: JsonVariablesRepository + DEFAULT_LISTS
├── src/grokbot/repositories/generation_refs_repo.py          # CREATE: JsonGenerationRefsRepository
├── tests/conftest.py                                         # EDIT: SETTINGS_ENV_VARS += "GROK_DATA_DIR"
├── tests/unit/test_settings.py                               # EDIT: + casos data_dir/props
└── tests/integration/repositories/
    ├── test_json_session_repo.py                             # CREATE
    ├── test_json_variables_repo.py                           # CREATE
    └── test_generation_refs_repo.py                          # CREATE
```

No crear ningún otro path. `src/grokbot/domain/*`, `src/grokbot/providers/*`, `grok/**` read-only / no-touch.

#### Pattern-to-copy (grok = referencia SEMÁNTICA; transcribir comportamiento, no copiar archivos ni datos)

| Pieza nueva | Referencia grok | Qué se adapta |
|---|---|---|
| `json_session_repo.py` | `sessions.py:46-66` (`_default_session_record`), `69-79` (`_load/_save`), `82-148` (`_ensure_full`/`_get_or_create_full`), `241-243` (`_prune_hourly_timestamps`), `281-324` (count/record quota), `44` (`VIDEO_HOURLY_WINDOW_SEC`) | Clase con `get_config`/`save_config`/quota. `_default_record` construido desde `UserConfig.defaults().to_record()` + `video_hourly_timestamps: []`. `_ensure_full` mirror PERO sin pisar canónico cuando convive legacy (A4). Merge no destructivo en save. Prune de quota con ventana 3600. |
| `json_variables_repo.py` | `variables_store.py:28-33` (`VARIABLES_FILE`/`_LOCK`), `34-70` (`LIST_NAMES`/`DEFAULT_TEMPLATE`/`DEFAULT_LISTS` → transcribir `DEFAULT_LISTS`), `88-103` (`_load` tolerante/`_save`), `106-114` (normalize), `117-147` (`_ensure_full`/`_data`), `150-249` (valid/CRUD), `425-459` (blacklist), `462-579` (packages) | Clase con CRUD+blacklist+packages (D3). `_ensure_full` mirror EXACTO (seed solo si `lists` vacío). Reusa `LIST_NAMES`/`DEFAULT_TEMPLATE` y `normalize_items` de `grokbot.domain.variables` (NO duplicar). `threading.Lock` por instancia. Atomic write con `ensure_ascii=False`. |
| `generation_refs_repo.py` | `sessions.py:42-43` (`GENERATION_REFS_FILE`/`GENERATION_REF_TTL_SEC`), `424-435` (`_load/_save_generation_refs`), `438-455` (`_generation_ref_key`/`_prune_generation_refs`), `458-497` (`save_generation_ref`/`get_generation_ref`) | Clase `JsonGenerationRefsRepository` con `save`/`get`. Guard `if not kie_task_id and not regen: return`. `kie_index` clamp 0..5. `prompt[:500]`. Prune TTL 14d en cada save/get; `get` persiste el prune SOLO si el key pedido existe. Atomic write con `ensure_ascii=True`. |

#### Firmas de las clases concretas

```python
# json_session_repo.py
class JsonSessionRepository:
    def __init__(self, path: Path): ...                 # self._path
    def get_config(self, user_id: int) -> UserConfig: ...
    def save_config(self, user_id: int, config: UserConfig) -> None: ...
    def record_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> None: ...
    def count_video_hourly_usage(self, user_id: int, *, now: float | None = None) -> int: ...
    def count_global_video_hourly_usage(self, *, now: float | None = None) -> int: ...
    # privados: _load/_save/_ensure_full/_default_record/_prune
```

```python
# json_variables_repo.py
class JsonVariablesRepository:
    def __init__(self, path: Path, *, packages_dir: Path | None = None): ...
        # packages_dir default = path.parent / "variables_packages"
    # métodos del Protocol VariablesRepository (CRUD + blacklist + packages)
    # privados: _load/_save/_data/_ensure_full/_normalize_package_payload/_slugify  + _lock: threading.Lock
DEFAULT_LISTS: dict[str, list[str]]  # transcrito de variables_store.py:38-70 (poses 10 / angles 10 / actions 5)
```

```python
# generation_refs_repo.py
class JsonGenerationRefsRepository:
    GENERATION_REF_TTL_SEC = 14 * 24 * 3600
    def __init__(self, path: Path): ...
    def save(self, chat_id, message_id, *, kie_task_id=None, kie_index=0, provider="kie", kind="image", prompt="", regen=None, now=None) -> None: ...
    def get(self, chat_id: int, message_id: int) -> dict | None: ...
    # privados: _load/_save/_prune
```

#### Shape exacto de cada JSON (paridad obligatoria)

**sessions.json** — top: `{"<str(user_id)>": <record>, ...}`
| Campo del record | Valor default | Notas |
|---|---|---|
| `source_path` | `None` | preservar |
| `integrate_ref_path` | `None` | preservar |
| `state` | `"IDLE"` | str |
| `model` | `"grok"` | |
| `grok_imagine_provider` | `"kie"` | canónico; alias legacy `grok_provider` (solo lectura, dropeado al escribir) |
| `grok_imagine_variant` | `"quality"` | |
| `video_duration` | `5` | int |
| `video_aspect_ratio` | `"16:9"` | |
| `video_resolution` | `"720p"` | |
| `video_model` | `"grok-imagine-video"` | |
| `video_mode` | `"normal"` | |
| `video_hourly_timestamps` | `[]` | `list[float]` epoch; **repo-owned** (nunca en `to_record`) |
| `comfyui_model` | `"krea2"` | |
| `comfyui_lora` | `"none"` | |
| `comfyui_refine` | `"1"` | str `"0"`/`"1"` |
| `<claves extra>` | — | preservar intactas (ej `_extra_user_key`) |

**variables_lists.json** — top: `{"lists": {<campo>: [items...]}, "template": str, "blacklist": [[...], ...], "_package": str|ausente, <claves extra: preservar>}`. Archivo nuevo/ausente/corrupto → seed: `lists` = `{"poses": <10>, "angles": <10>, "actions": <5>}` (deep-copy de `DEFAULT_LISTS`), `template` = `"{pose}, {angle}, {action}"`, `blacklist` = `[]`. Guard de seed: solo si `lists` falta o queda vacío.

**Package file** `packages_dir/<slug>.json` — `{"lists": {...}, "template": str}` (SIN blacklist/_package). `save_package` normaliza y acepta top `"fields"` como alias de `"lists"`. `activate_package` escribe el archivo activo con `{"lists":..., "template":..., "blacklist": [], "_package": slug}`.

**generation_refs.json** — top: `{"<chat_id>:<message_id>": <record>, ...}`
| Campo del record | Valor/regla |
|---|---|
| `provider` | default `"kie"` (observados kie/replicate/comfyui) |
| `kind` | `"image"`/`"video"` |
| `prompt` | `""` o truncado a 500 |
| `created_at` | float epoch |
| `kie_task_id` | presente iff tarea kie |
| `kie_index` | int clamp 0..5, presente iff `kie_task_id` |
| `regen` | dict **opaco**, presente iff contexto regen; cualquier clave extra dentro → preservar |
| `<claves extra del record>` | preservar |

Guard: si `not kie_task_id and not regen` → no-op (no escribe). TTL prune 14 días descarta no-dict y `created_at` no-float/vencidos; corre en save y get.

#### Wiring final del ítem
`tests → grokbot.repositories.*` y `repositories/* → grokbot.domain.*` (`user_config`, `catalog`, `variables`).
`repositories/base.py` → nada de grok (solo stdlib + domain). No hay consumidores de app aún (los tests son el
consumidor inmediato).

#### Orden de implementación (dependencias)
1. **Task 1**: Settings D1 (independiente; libera surface de paths para ítem 6) + conftest/env/tests.
2. **Task 2**: `base.py` (Protocols + `write_json_atomic`) + `__init__.py` esqueleto (libera contrato a los 3 repos).
3. **Task 3**: `json_session_repo.py` (depende de base + domain/user_config).
4. **Task 4**: `json_variables_repo.py` (depende de base + domain/variables).
5. **Task 5**: `generation_refs_repo.py` (depende de base) + suite completa + SUMMARY.

## Context

- SPEC: `/home/ubuntu/repos/grokV2/docs/SPEC_REFACTOR.md` §3.3/§4.3/§5.1/§5.6/§6/§7.
- CLARIFY locked: `/home/ubuntu/repos/grokV2/.planning/quick/20260903-grokv2-rearch/CLARIFY.md`.
- Impact (input principal): `/home/ubuntu/repos/grokV2/.grok/agent-memory/impact-analyzer/item3-repositories.md`.
- Dominio ítem 1 (commiteado): `/home/ubuntu/repos/grokV2/src/grokbot/domain/{user_config,catalog,variables}.py`,
  `/home/ubuntu/repos/grokV2/src/grokbot/settings.py`, `/home/ubuntu/repos/grokV2/tests/conftest.py`.
- Referencia semántica read-only: `/home/ubuntu/repos/grok/{sessions.py,variables_store.py}` (rangos en tabla
  Pattern-to-copy). Grok HEAD: `81832a5`.

---

## Tasks

### Task 1: D1 — Settings `data_dir` + paths derivados + conftest/env/test_settings
**type:** auto
**Objective:** `Settings` expone `data_dir` (env `GROK_DATA_DIR`) y 4 propiedades de paths para repos; conftest
aisla la nueva env var; `.env.example` y `test_settings.py` actualizados. Sin cambios de comportamiento en settings
existentes.
**Files:**
- Edit: `/home/ubuntu/repos/grokV2/src/grokbot/settings.py`, `/home/ubuntu/repos/grokV2/tests/conftest.py`,
  `/home/ubuntu/repos/grokV2/.env.example`, `/home/ubuntu/repos/grokV2/tests/unit/test_settings.py`.
**Action:**
- `settings.py`: agregar `from pathlib import Path` y `from pydantic import Field`. En `Settings`, tras
  `refine_confirm_timeout` y ANTES de los validators, agregar:
  ```python
  # Runtime data directory for repositories (sessions/variables/generation_refs + packages).
  # main.py (item 6) reads the derived paths below to build repos; repos never default to cwd.
  data_dir: Path = Field(default=Path("data"), alias="GROK_DATA_DIR")

  @property
  def sessions_file(self) -> Path:
      return self.data_dir / "sessions.json"
  @property
  def variables_file(self) -> Path:
      return self.data_dir / "variables_lists.json"
  @property
  def generation_refs_file(self) -> Path:
      return self.data_dir / "generation_refs.json"
  @property
  def packages_dir(self) -> Path:
      return self.data_dir / "variables_packages"
  ```
  NO tocar ningún otro campo ni validator. `model_config` sigue `extra="ignore"`, sin `env_file`.
- `tests/conftest.py`: agregar `"GROK_DATA_DIR"` a `SETTINGS_ENV_VARS` (tupla). Nada más.
- `.env.example`: agregar bloque al final:
  ```
  # Directorio de datos de repos (sessions.json, variables_lists.json, generation_refs.json, variables_packages/).
  GROK_DATA_DIR=data
  ```
- `tests/unit/test_settings.py`: agregar 2 casos:
  1. `test_data_dir_default_and_derived_paths`: con los 3 requeridos seteados y sin `GROK_DATA_DIR`,
     `Settings().data_dir == Path("data")` y las 4 propiedades son `Path("data")/"sessions.json"`,
     `Path("data")/"variables_lists.json"`, `Path("data")/"generation_refs.json"`, `Path("data")/"variables_packages"`.
  2. `test_data_dir_env_override`: con `GROK_DATA_DIR="/tmp/grokdata"` → `data_dir == Path("/tmp/grokdata")` y
     `sessions_file == Path("/tmp/grokdata")/"sessions.json"`.
  Asegurar que los casos existentes (defaults, CSV, blank, invalid, cached) sigan en verde sin cambios.
**Verification:** `.venv/bin/pytest tests/unit/test_settings.py -q`.
**Done:** `data_dir` + 4 props derivadas; conftest aísla `GROK_DATA_DIR`; `.env.example` documenta; tests de settings
en verde (incl. los 2 nuevos). Commit atómico:
`feat(settings): data_dir GROK_DATA_DIR + paths derivados para repos + tests env`.

### Task 2: `repositories/base.py` (Protocols + write_json_atomic) + `__init__.py` esqueleto
**type:** auto
**Objective:** Contrato de los 3 repositorios fijado como Protocols `@runtime_checkable`; helper de escritura atómica
`write_json_atomic` listo para los 3 backends.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/base.py`,
  `/home/ubuntu/repos/grokV2/src/grokbot/repositories/__init__.py`.
**Action:**
- `base.py`:
  - Docstring corto en inglés; `from __future__ import annotations`; imports: `json`, `os`, `tempfile`, `Path`,
    `Protocol`, `runtime_checkable`, `UserConfig`.
  - Definir los 3 Protocols EXACTOS del bloque CÓMO (incl. `now: float | None = None` en session quota y refs.save).
    Docstring breve por Protocol indicando paridad grok y dueño de claves.
  - `write_json_atomic(path, data, *, ensure_ascii=True, indent=2)`:
    ```python
    def write_json_atomic(path: Path, data: dict, *, ensure_ascii: bool = True, indent: int = 2) -> None:
        """Atomically write ``data`` as JSON to ``path`` (parent created, tmp + os.replace)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    ```
  - Sin imports de módulos concretos de repos (evita ciclos); sin `print`.
- `__init__.py`: re-exportar SOLO por ahora `SessionRepository`, `VariablesRepository`, `GenerationRefsRepository`,
  `write_json_atomic` (los concretos se agregan en Tasks 3-5).
**Verification:** `.venv/bin/python -c "import grokbot.repositories as r; print(r.SessionRepository, r.write_json_atomic)"`.
**Done:** base importable; Protocols presentes; helper implementado. Commit atómico:
`feat(repositories): base Protocols (session/variables/refs) + write_json_atomic + package init`.

### Task 3: `json_session_repo.py` (UserConfig merge no destructivo + quota horaria) + tests
**type:** auto
**Objective:** `JsonSessionRepository` lee/escribe `sessions.json` con paridad de shape, migración legacy, merge no
destructivo y ops de quota horaria; cubierto por tests con fixtures anonimizados en `tmp_path`.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/json_session_repo.py`,
  `/home/ubuntu/repos/grokV2/tests/integration/repositories/test_json_session_repo.py`.
- Edit: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/__init__.py` (agregar `JsonSessionRepository` al re-export).
**Action:**
- Implementar `json_session_repo.py`:
  - `VIDEO_HOURLY_WINDOW_SEC = 3600` (módulo). Imports: stdlib (`json`, `time`, `Path`) + base
    (`write_json_atomic`) + domain (`UserConfig` y constantes default de `grokbot.domain.catalog` /
    `grokbot.domain.user_config` — reusar, NO duplicar valores).
  - `_default_record(**overrides) -> dict`: `rec = UserConfig.defaults().to_record(); rec["video_hourly_timestamps"] =
    []; rec.update(overrides); return rec`.
  - `_load() -> dict`: si el path no existe → `{}`; si existe → `json.load`; si el top-level NO es `dict` → `raise
    ValueError(f"{self._path} must contain a JSON object")`. JSON malformado → propaga `json.JSONDecodeError` (D4).
  - `_save(data)`: `write_json_atomic(self._path, data)` (ensure_ascii default True → D9).
  - `_ensure_full(rec) -> bool`: mirror de `sessions._ensure_full` (agrega model/grok_imagine_provider/variant,
    video_* + `video_hourly_timestamps`, comfyui_*, `integrate_ref_path`) PERO con la regla A4: si `grok_provider`
    presente y `grok_imagine_provider` ausente → migrar legacy; si ambos presentes → NO pisar el canónico; en ambos
    casos dropear `grok_provider` y marcar `changed=True`. NO agregar `source_path` si falta (paridad grok).
  - `get_config(user_id)`: `uid = str(user_id)`; `data = self._load()`; si `uid` no está → `rec = _default_record()`;
    `data[uid] = rec; self._save(data)`; si está → `rec = data[uid]`; si `rec` no es dict → `raise ValueError` claro;
    `if self._ensure_full(rec): self._save(data)`. Devolver `UserConfig.from_record(rec)`.
  - `save_config(user_id, config)`: `data = self._load()`; `rec = data.get(uid)`; si no dict → `rec =
    _default_record()`; `rec.update(config.to_record())`; `rec.pop("grok_provider", None)`; `data[uid] = rec`;
    `self._save(data)`. NO tocar `video_hourly_timestamps` ni claves extra (merge).
  - `record_video_hourly_usage(user_id, *, now=None)`: prune + append + save (mirror `sessions.record_video_hourly_usage`
    311-324) con `_default_record()` si el rec falta/no es dict.
  - `count_video_hourly_usage(user_id, *, now=None)`: mirror 281-296 (usuario nuevo → crea default y devuelve 0;
    `_ensure_full`; prune; persistir solo si `pruned != current`; devolver `len(pruned)`).
  - `count_global_video_hourly_usage(*, now=None)`: mirror 299-308 (itera, suma len(prune) por rec dict, sin
    escribir).
- `__init__.py`: agregar `JsonSessionRepository` al re-export.
- `tests/integration/repositories/test_json_session_repo.py` (fixtures inline, usuario `111111111`, NUNCA datos reales):
  - fixture `canonical_record()` dict real-shape completo ANON (como la tabla de shape: `source_path
    "/tmp/anon/sources/111.jpg"`, `state "IDLE"`, `video_hourly_timestamps [1710000000.0, 1710003600.0]`, clave extra
    `_extra_user_key: "keep"`, comfyui `krea2`/`none`/`"1"`).
  - fixture `legacy_record()`: canonical sin `grok_imagine_provider` pero con `grok_provider: "xai"`.
  - Casos:
    1. `get_config` usuario nuevo → `UserConfig.defaults()`-equivalente; el archivo se crea con record completo
       (JSON contiene `video_hourly_timestamps: []`, `source_path: None`, `model: "grok"`).
    2. Carga fixture real-shape → `UserConfig` correcto (model grok, provider kie, variant quality, video 5/"16:9",
       comfyui krea2; `_extra_user_key` ignorada por domain).
    3. `save_config` tras cargar real-shape PRESERVA `video_hourly_timestamps` + `_extra_user_key` + comfyui: mutar
       `model="seedream"` en memoria, `save_config`, leer JSON crudo → `_extra_user_key` y timestamps intactos.
    4. Legacy: `get_config` sobre fixture legacy → `grok_imagine_provider=="xai"`; tras `save_config` el JSON crudo NO
       contiene `grok_provider` y SÍ `grok_imagine_provider`.
    5. Coexistencia legacy+canónico (A4): fixture con ambos (canónico `"kie"`, legacy `"xai"`) → `get_config`
       conserva `"kie"` (canónico) y el archivo queda sin `grok_provider` tras el guardado de `get_config`.
    6. `record_video_hourly_usage`: fresh file → `record(now=1000.0)` y `record(now=5000.0)` → crudo tiene `[5000.0]`
       (el de 1000 se poda por ventana 3600).
    7. `count_video_hourly_usage`: sembrar archivo con timestamp `1000.0`, `count(now=5000.0)` → `0` y el crudo
       persiste `[]`.
    8. `count_global_video_hourly_usage`: multi-usuario (111111111 y 222222222) con timestamps frescos/viejos → suma
       correcta sin escribir.
    9. Archivo inexistente → `get_config` devuelve defaults y crea archivo.
    10. Corrupto: archivo con `{invalid` → `pytest.raises(json.JSONDecodeError)` en `get_config`; archivo con top-level
        `[]` → `ValueError`.
    11. Round-trip paridad: escribir `canonical_record()` (sin `_extra_user_key` legacy issues) como archivo → 
        `get_config` → `save_config` mismo `UserConfig` → `json.load(archivo) == canonical_record()` EXACTO.
    12. `write_json_atomic` (via repo): parent dir no existente se crea; archivo resultante parsea; dump-flags: un valor
        con carácter no-ASCII en una clave extra aparece ESCAPADO en el texto (`"\\u00e1"`), probando `ensure_ascii`
        default True (D9).
**Verification:** `.venv/bin/pytest tests/integration/repositories/test_json_session_repo.py -q`.
**Done:** session repo implementa contrato D2; merge no destructivo + legacy A4 + quota cubiertos; tests en verde.
Commit atómico: `feat(repositories): json session repo (UserConfig merge + quota horaria) + tests`.

### Task 4: `json_variables_repo.py` (seed DEFAULT_LISTS + CRUD + blacklist + packages D3) + tests
**type:** auto
**Objective:** `JsonVariablesRepository` con seed solo-en-vacío, CRUD/template/blacklist y packages
(save/load/list/activate/delete) con `packages_dir`; cubierto por tests (archivo vacío → seed; paquete-activo no se
contamina; dump `ensure_ascii=False`).
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/json_variables_repo.py`,
  `/home/ubuntu/repos/grokV2/tests/integration/repositories/test_json_variables_repo.py`.
- Edit: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/__init__.py` (agregar `JsonVariablesRepository`,
  `DEFAULT_LISTS`).
**Action:**
- Implementar `json_variables_repo.py`:
  - Imports: stdlib (`json`, `re`, `threading`, `Path`) + base (`write_json_atomic`) + domain
    (`from grokbot.domain.variables import LIST_NAMES, DEFAULT_TEMPLATE, normalize_items`).
  - `DEFAULT_LISTS: dict[str, list[str]]` transcrito EXACTO de `variables_store.py:38-70` (poses 10 ítems, actions 5,
    angles 10 — transcribir los strings literales del archivo grok; no inventar).
  - `_ACTIVE_PACKAGE_KEY = "_package"`. `_slugify` mirror 475-478. `_normalize_package_payload` mirror 497-519
    (acepta `lists` y `fields`; normaliza items; template requerido; devuelve `(payload, None)` o `(None, error)`).
  - `__init__(self, path, *, packages_dir=None)`: `self._path = path`; `self._packages_dir = packages_dir or
    (path.parent / "variables_packages")`; `self._lock = threading.Lock()`.
  - `_load() -> dict` tolerante (corrupto/OSError/missing/no-dict → `{}`; mirror 88-97). `_save(data)`:
    `write_json_atomic(self._path, data, ensure_ascii=False)`. `_save_package(slug, payload)`:
    `write_json_atomic(self._packages_dir / f"{slug}.json", payload, ensure_ascii=False)`.
  - `_ensure_full(data) -> bool` mirror EXACTO 117-138 (seed de `LIST_NAMES` con `list(DEFAULT_LISTS[name])` solo si
    `lists` no-dict o vacío; template default si vacío; blacklist [] si no-list). `_data()` mirror 141-147 (con lock).
  - Métodos públicos del Protocol: `get_lists` (normaliza items vía `normalize_items`, mirror 156-168); `get_list`
    (raise `ValueError(f"Unknown list: {name!r}")` si no válida, mirror 171-174); `is_valid_list_name` (D8: `name in
    LIST_NAMES or name in self.get_lists()`); `get_template`; `set_template` (blank → False, mirror 181-190);
    `add_item`/`update_item`/`delete_item` (mirror 193-249, validación + lock + no-op update misma texto → True);
    `get_blacklist`/`blacklist_add`/`blacklist_clear` (mirror 425-459; `get_blacklist` → `set[tuple[str, ...]]`;
    guard `blacklist_add` con key no-tuple → False; no-op si ya está → False).
  - Packages (D3/D7): `list_packages() -> list[str]` = sorted `p.stem for p in self._packages_dir.glob("*.json")`
    (vacío si dir no existe). `save_package(name, payload) -> (bool, str|None)` mirror 522-534 (slug vacío → error;
    normaliza; mkdir; `_save_package`). `load_package(name) -> dict|None` mirror 537-547 (JSONDecodeError/OSError/missing
    → None). `package_exists(name)`. `active_package_name()` = `_data().get(_ACTIVE_PACKAGE_KEY)` si str no-vacío.
    `activate_package(name) -> bool` mirror 554-568 (load → None → False; con lock escribe al archivo ACTIVO el doc
    `{"lists": payload["lists"], "template": payload["template"], "blacklist": [], _ACTIVE_PACKAGE_KEY: slug}` vía
    `_save`). `delete_package(name) -> bool` mirror 571-579 (missing → False; activo → False; unlink).
- `__init__.py`: agregar `JsonVariablesRepository`, `DEFAULT_LISTS`.
- `tests/integration/repositories/test_json_variables_repo.py` (fixtures inline, NUNCA datos reales):
  - fixture `package_active_doc()`: `{"lists": {"bodies": ["cuerpo A", "cuerpo B"], "hands": ["mano X"], "angles":
    ["ángulo 1"]}, "template": "{bodies}, {hands}, {angles}", "blacklist": [["cuerpo A", "mano X", "ángulo 1"]],
    "_package": "anon", "_extra_top": "keep"}`.
  - Casos:
    1. Archivo nuevo → `get_lists()` nombres `{"poses","angles","actions"}`; items == `DEFAULT_LISTS[name]` (importar
       `DEFAULT_LISTS` del repo para assert); `get_template() == DEFAULT_TEMPLATE`; `get_blacklist() == set()`; el
       archivo existe con `lists/template/blacklist`.
    2. Archivo vacío `{}` → seed (mismo assert). Archivo corrupto `{invalid` → seed (paridad, sin raise).
    3. Paquete-activo carga SIN sembrar: `get_lists()` = `{"bodies","hands","angles"}` (no poses); tras `add_item`
       o `set_template`, el crudo preserva `_package` y `_extra_top`.
    4. CRUD: `add_item` dup → False, blank → False, nombre desconocido → False, válido → True y persiste;
       `update_item` out-of-range → False, misma texto → True (no-op), a duplicado → False; `delete_item`
       out-of-range → False, válido → True.
    5. `set_template("")` → False; válido → True y `get_template()` lo devuelve.
    6. `blacklist_add(("a","b"))` → True; dup → False; `get_blacklist() == {("a","b")}`; raw shape `[["a","b"]]`;
       `blacklist_clear()` → `[]`.
    7. `is_valid_list_name`: `"poses"` True (constante), nombre de lista de paquete activo True, `"zzz"` False.
    8. Packages (D3/D7): `save_package("Mi Paquete!", {"fields": {...}, "template": "{...}"})` → `(True, None)` y
       archivo `mi_paquete.json` normalizado a `lists`; `list_packages() == ["anon", "mi_paquete"]` (sorted);
       `load_package("anon")` devuelve el payload; `package_exists` True/False; `activate_package("anon")` → True y el
       archivo ACTIVO tiene `lists/template`, `blacklist == []` y `_package == "anon"`; `delete_package("anon")` → False
       (activo) y `delete_package("otro")` → False (missing); `save_package` sin lists/template → `(False, error)`.
    9. `activate_package("missing")` → False.
    10. Dump-flags: archivo variables con un ítem acentuado (`"ángulo 1"`) → el TEXTO del archivo contiene `"ángulo"`
        literal (NO `\\u00e1`) → `ensure_ascii=False` (D9). Ídem para un package file.
**Verification:** `.venv/bin/pytest tests/integration/repositories/test_json_variables_repo.py -q`.
**Done:** variables repo con seed/CRUD/blacklist/packages implementado; guard de paquete-activo y dump-flags cubiertos.
Commit atómico: `feat(repositories): json variables repo (seed DEFAULT_LISTS + paquetes) + tests`.

### Task 5: `generation_refs_repo.py` (save/get + TTL prune) + tests + suite completa + SUMMARY
**type:** auto
**Objective:** `JsonGenerationRefsRepository` con `save`/`get` (guard kie/regen, clamp índice, truncado prompt, prune
TTL 14d en save y get, persistencia del prune solo si el key pedido existe); suite del ítem + suite completa en verde.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/generation_refs_repo.py`,
  `/home/ubuntu/repos/grokV2/tests/integration/repositories/test_generation_refs_repo.py`.
- Edit: `/home/ubuntu/repos/grokV2/src/grokbot/repositories/__init__.py` (agregar `JsonGenerationRefsRepository` al
  re-export).
**Action:**
- Implementar `generation_refs_repo.py`:
  - `GENERATION_REF_TTL_SEC = 14 * 24 * 3600` (módulo). Imports: `json`, `time`, `Path`, base `write_json_atomic`.
  - `_load() -> dict`: missing → `{}`; top-level no-dict → `{}` (paridad `_load_generation_refs`); malformado → propaga
    `json.JSONDecodeError` (A3). `_save(data)`: `write_json_atomic(self._path, data)` (ensure_ascii default True → D9).
  - `_key(chat_id, message_id) -> str`: `f"{chat_id}:{message_id}"`. `_prune(refs, now=None) -> dict`: mirror 442-455.
  - `save(...)`: si `not kie_task_id and not regen` → return (no-op). `now = now if now is not None else time.time()`.
    `refs = self._prune(self._load(), now)`. Record: `{"provider": provider, "kind": kind, "prompt": prompt[:500] if
    prompt else "", "created_at": now}`; si `kie_task_id` → `rec["kie_task_id"] = kie_task_id` y `rec["kie_index"] =
    max(0, min(int(kie_index), 5))`; si `regen` → `rec["regen"] = regen` (opaco). `refs[self._key(...)] = rec`;
    `self._save(refs)`.
  - `get(chat_id, message_id) -> dict | None`: `raw = self._load()`; `refs = self._prune(raw)`; `rec = refs.get(key)`;
    si `rec is None` → `None` (sin guardar); si `refs != raw` → `self._save(refs)`; devolver `rec`.
- `__init__.py`: agregar `JsonGenerationRefsRepository`.
- `tests/integration/repositories/test_generation_refs_repo.py` (fixtures inline; prompts dummy `"prompt de prueba
  #N"`; `regen` con 2-3 key-sets distintos; `FAKE_FILE_ID`; NUNCA IDs/prompts reales):
  1. `save` con `kie_task_id` → crudo tiene shape exacta (`provider/kind/prompt/created_at/kie_task_id/kie_index`) y
     `created_at` == `now` inyectado.
  2. `save` sin task y sin regen → no-op (archivo no creado si no existía; sin cambios si existía).
  3. `save` solo `regen` → record con `regen` y SIN `kie_task_id`/`kie_index`.
  4. Prompt truncado: `save` con prompt de >500 chars → crudo `prompt` de largo 500.
  5. `kie_index` clamp: `save(kie_index=99)` → `5`; `save(kie_index=-3)` → `0`.
  6. `get` existente → devuelve el record; `get` key inexistente → `None`.
  7. TTL prune persiste solo si el key pedido existe: sembrar archivo con record VIVO key A (`save(now=time.time())`) y
     record VENCIDO key B (`created_at` = `time.time() - (TTL+1)` escrito a mano en el archivo). `get(A)` → devuelve el
     record de A Y el archivo ya no contiene B. `get(B)` después → `None`.
  8. `get` de key vencida → `None` y el archivo NO se modifica (prune no persiste si el key pedido no existe).
  9. Record con claves extra (`"_extra": "keep"`) bajo key viva → tras un `get`/`save` de OTRA key, las claves extra de
     la key viva se preservan (dump del dict completo).
  10. `regen` opaco: dict con key-sets variados (incl. `source_file_id` / `kie_source_ref`) se guarda y devuelve
      IDÉNTICO (sin re-modelar).
  11. Archivo no-dict top-level (ej. `[]`) → tratado como `{}` y un `save` posterior escribe dict válido. Archivo
      malformado `{invalid` → `pytest.raises(json.JSONDecodeError)`.
  12. Dump-flags: un prompt con acento (`"prompt de prueba #1 á"`) aparece ESCAPADO en el texto (`"\\u00e1"`) →
      `ensure_ascii=True` (D9).
- Cierre: correr suite del ítem + suite completa; verificar self-check de Instrucciones; escribir `SUMMARY.md` del ítem
  (como en ítems 1-2).
**Verification:** `.venv/bin/pytest tests/unit tests/integration/repositories -q` y `.venv/bin/pytest tests -q`.
**Done:** refs repo implementado (save/get/TTL/guard/clamp/truncado/opacidad de regen); tests verdes; DoD del ítem
cumplido. Commit atómico: `feat(repositories): generation refs repo (save/get/TTL prune) + tests`.

---

## Instrucciones para gsd-executor

- **Dónde trabajar:** SOLO bajo `/home/ubuntu/repos/grokV2`. `/home/ubuntu/repos/grok` es read-only: se lee para
  transcribir valores/rangos de las tablas Pattern-to-copy, jamás se edita ni se importa.
- **No-touch (prohibido crear/editar):** `src/grokbot/{application,telegram,main.py,shared,providers}/`,
  `src/grokbot/domain/*` (se consumen tal cual), `docs/SPEC_REFACTOR.md`, `.planning/**` (salvo commits),
  `.grok/**`. NO crear archivos fuera de la lista de Files de cada task (ni `conftest.py` dentro de
  `tests/integration/repositories/`: fixtures inline en cada test).
- **Convenciones:** docstrings cortos en inglés; type hints completos; sin `print` en código de librería. Imports de
  constantes de dominio SIEMPRE desde `grokbot.domain.*` (no duplicar valores salvo `DEFAULT_LISTS`, que se transcribe
  del archivo grok como dato de seed). `json.dump` con los flags exactos (D9). `write_json_atomic` en los 3 `_save`.
- **Anti-patterns prohibidos:** `Path(__file__).parent` para resolver data; default de path a cwd; reconstruir el
  documento desde cero en un save (pierde claves extra); `str.format`/format_map (no aplica acá, mantener estilo);
  importar `settings`/`providers`/`telegram`/`grok` desde repos; `print`.
- **Fixtures (R8, crítica):** los datos reales de grok (IDs `6181290784`, prompts personales, `file_id` tipo
  `AgACAgE...`) son SENSIBLES. Prohibido copiarlos verbatim. Usar usuario `111111111`, prompts dummy
  `"prompt de prueba #N"`, `"FAKE_FILE_ID"`, rutas `/tmp/anon/...`, siempre con la MISMA forma estructural.
- **Mock policy:** solo `tmp_path` (filesystem real de tests) y `monkeypatch` NO necesario (los métodos exponen `now`).
  Cero mocks de red/Telegram; cero parches de métodos bajo test.
- **Commits atómicos por work unit** (formato del repo, trailer incluido; lista exacta en cada Task):
  1. `feat(settings): data_dir GROK_DATA_DIR + paths derivados para repos + tests env`
  2. `feat(repositories): base Protocols (session/variables/refs) + write_json_atomic + package init`
  3. `feat(repositories): json session repo (UserConfig merge + quota horaria) + tests`
  4. `feat(repositories): json variables repo (seed DEFAULT_LISTS + paquetes) + tests`
  5. `feat(repositories): generation refs repo (save/get/TTL prune) + tests`
  No commitear `.venv/`, `.env`, cachés, `.pytest_cache`, archivos `.tmp`.
- **Self-check antes de cada commit:** correr la suite del ítem (abajo); `git status --porcelain` solo con archivos
  esperados; `git -C /home/ubuntu/repos/grok status --porcelain` vacío (debe estar con sus 3 cambios pre-existentes
  documentados en residuals — NO tocarlos).
- **Registro:** mantener log corto de ejecución y escribir `SUMMARY.md` al cierre del ítem.
- **Residuales:** si algo no cierra (p. ej. matiz de pydantic con alias `GROK_DATA_DIR`), reportarlo en el SUMMARY y en
  la respuesta final; no cambiar decisiones bloqueadas ni el alcance.

## Test commands

- Suite del ítem (desde `/home/ubuntu/repos/grokV2`):
  `.venv/bin/pytest tests/unit tests/integration/repositories -q`
- Solo un repo:
  `.venv/bin/pytest tests/integration/repositories/test_json_session_repo.py -q`
  `.venv/bin/pytest tests/integration/repositories/test_json_variables_repo.py -q`
  `.venv/bin/pytest tests/integration/repositories/test_generation_refs_repo.py -q`
- Suite completa (debe seguir verde tras ítems 1-2):
  `.venv/bin/pytest tests -q`
- Sanidad de import (sin env, no instancia Settings):
  `.venv/bin/python -c "import grokbot.repositories; print('ok')"`
- Verificación read-only grok intacto:
  `git -C /home/ubuntu/repos/grok status --porcelain` (debe listar SOLO los 3 cambios pre-existentes: `M
  sources/6181290784.jpg`, `?? variables_packages/hot.json`, `?? variables_packages/sexy.json`)

## Risks + Mitigation

| Riesgo (impact) | Mitigación en este PLAN |
|---|---|
| **R1 CRITICAL** paridad de shape/format | Fixtures "espejo anonimizado" real-shape de los 3 archivos; tests round-trip load→save→load igualdad de `json.load`; dump-flags exactos por archivo (D9) verificados sobre el texto (Tasks 3-5) |
| **R2 CRITICAL** escritura no destructiva | `save_config` merge campo a campo sobre el crudo; variables muta el doc preservando top-level extras; refs preserva records/`regen` de otras keys; tests explícitos de preservación (Tasks 3-5) |
| **R3 CRITICAL** legacy `grok_provider` al escribir | `_ensure_full` con precedencia canónica + dropeo de `grok_provider` (A4); tests legacy y coexistencia (Task 3) |
| **R4 MEDIUM** corrupto/crash a mitad de dump | D5 atomicidad tmp+`os.replace` en los 3 `_save`; D4 semántica por repo (sessions/refs propagan; variables tolera seed); tests de corrupto (Tasks 3-5) |
| **R5 MEDIUM** concurrencia/lost-update | D6 sync sin awaits intra-método; lock `threading.Lock` en variables; `write_json_atomic` reduce torn write; lost-update entre use-cases = ítem 4 (Tasks 3-5) |
| **R6 MEDIUM** seed vs paquete activo | Guard de seed solo si `lists` ausente/vacío (mirror `_ensure_full`); tests con fixture paquete-activo y archivo vacío (Task 4) |
| **R7 MEDIUM** paths por defecto | D1 repos con `Path` requerido (sin cwd ni `__file__`); tests siempre `tmp_path`; Settings `data_dir` para ítem 6 (Task 1 + no-touch) |
| **R8 LOW** fuga de datos en fixtures | Fixtures anonimizados con MISMA forma (usuario 111111111, prompts dummy, FAKE_FILE_ID); test-guardian hará grep de IDs/rutas reales sobre tests |
| **R9 LOW** crecimiento generation_refs | Prune TTL 14d en save y get (paridad); sin cap adicional en ítem 3 (Task 5) |
| **R10 LOW** scope creep packages | D3 resuelto: packages DENTRO de `VariablesRepository` ahora, fijando `packages_dir`; 0 dirs de ítems 4-6 creados |

## Success Criteria

- [ ] `pytest tests/unit tests/integration/repositories -q` en verde desde `.venv` de grokV2; `pytest tests -q` verde.
- [ ] `git -C /home/ubuntu/repos/grok status --porcelain` con SOLO los 3 cambios pre-existentes (sin cambios nuevos).
- [ ] No existen `src/grokbot/{application,telegram,main.py,shared}`; `providers/*` y `domain/*` intactos.
- [ ] `Settings.data_dir` + 4 propiedades; `GROK_DATA_DIR` en `.env.example` y en `SETTINGS_ENV_VARS` de conftest.
- [ ] `repositories/base.py` con los 3 Protocols `@runtime_checkable` + `write_json_atomic` (tmp + `os.replace`).
- [ ] `get_config` de usuario nuevo persiste record default incl. `video_hourly_timestamps: []`.
- [ ] `save_config` preserva `video_hourly_timestamps` y claves extra; `grok_provider` dropeado al escribir.
- [ ] `count/record_video_hourly_usage` con prune ventana 3600 y persistencia solo si cambió.
- [ ] Variables: seed SOLO en archivo vacío/corrupto; paquete-activo nunca contaminado; CRUD/blacklist con semántica
      grok; packages (D3) con `list_packages() -> list[str]` (D7), `activate` → `_package` + `blacklist: []`.
- [ ] Refs: guard `not kie_task_id and not regen` → no-op; clamp `kie_index` 0..5; prompt truncado 500; TTL 14d;
      prune persiste solo si el key pedido existe; `regen` opaco preservado.
- [ ] Dump-flags verificados por test (variables/packages `ensure_ascii=False`; sessions/refs default True; `indent=2`;
      sin sort_keys).
- [ ] Commits atómicos por work unit creados según la lista; `.venv/`, `.env`, `.pytest_cache` y `*.tmp` no commiteados.
