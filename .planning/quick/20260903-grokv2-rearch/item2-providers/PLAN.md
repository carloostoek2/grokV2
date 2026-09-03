---
phase: quick
plan: item2-providers
type: auto
item: "Item 2 — Extraer providers (Fase 2 SPEC §5.1/§5.2/§5.3/§7)"
source: user-request + SPEC_REFACTOR.md + CLARIFY.md (locked) + impact-analyzer/item2-providers.md
mode: standard
---

# PLAN — Item 2: Providers (base + xai + replicate + kie + comfyui + registry)

## Objective

Crear en `/home/ubuntu/repos/grokV2` la capa de infraestructura `src/grokbot/providers/` completa y testeable **sin red real**:
`base.py` (Protocol `ImageProvider`/`VideoProvider`, jerarquía de errores tipados, default aspect-ratio, helpers de bytes/mime/data-uri/i2v-size), `xai_provider.py`, `replicate_provider.py`, `kie_provider.py`, `comfyui/{__init__,ssh_client,provider}.py` y `registry.py`
(detección de proveedor activo desde `UserConfig` + `MediaType`). Los providers consumen únicamente los contratos de dominio del ítem 1 y reciben tokens/config/transporte **por constructor**. Ítems 3-6 NO entran en este plan.

Outcome medible: `.venv/bin/pytest tests/unit tests/integration/providers -q` en verde (tests nuevos), **0 cambios sobre `/home/ubuntu/repos/grok`**, y no existen `src/grokbot/{application,repositories,telegram,main.py,shared}`.

## Scope

- **In:**
  - Cambio **aditivo** a `src/grokbot/domain/generation.py`: campo `params: dict` en `GenerationRequest` (D1) + su test.
  - `src/grokbot/providers/`: `__init__.py`, `base.py`, `xai_provider.py`, `replicate_provider.py`, `kie_provider.py`, `registry.py`, `comfyui/__init__.py`, `comfyui/provider.py`, `comfyui/ssh_client.py`.
  - Deps en `pyproject.toml`: runtime `aiohttp` + `replicate`; dev `aioresponses`.
  - Tests: `tests/unit/providers/*.py` y `tests/integration/providers/*.py`; actualizar `tests/unit/domain/test_generation.py`.
- **Out / Non-goals:**
  - NO `application/`, `repositories/`, `telegram/`, `main.py`, `shared/errors.py` (ítems 4-6).
  - NO `settings.py` importado por providers (reciben valores por constructor; DI en ítem 6).
  - NO envío a Telegram, NO retry de negocio (ítem 4), NO downloader compartido de URL→bytes (ítem 4/6, D5).
  - NO tocar `grok/**` (read-only referencia semántica).
  - NO re-escribir `domain/*` salvo la adición D1 a `generation.py` (+ su test).
- **Constraints:** providers = 1 intento; polling de transients interno con backoff (no retry de negocio). Sin `os.environ` en `providers/*`. Sin `print` en código de librería.

## Assumptions

- A1 — **Bytes de imagen de entrada** (`source_image`) no viajan dentro de `GenerationRequest` (el docstring del ítem 1 lo prohíbe explícitamente). Los providers que hacen i2i/i2v aceptan `source_image: bytes | None` como kwarg extra de `generate()`; la capa de aplicación (ítem 4) lo resuelve a partir de `request.source` (download de Telegram/file/URL). En ítem 2 los tests pasan bytes sintéticos.
- A2 — **El modelo wire a usar para t2i de xAI/Replicate/Kie se deriva del `model_id` del request**; para ramificaciones que necesitan la clave de catálogo top-level (`seedream` vs `faceswap` vs `grok`) el provider compara contra ids de `domain/catalog` (nunca string-matching suelto) y/o lee `request.params["key"]` si el caller lo provee.
- A3 — **Parámetros provider-específicos por-request viven en `request.params`** (D1): ComfyUI `model/lora/refine`, `mode` (video), `prompts` (batch comfyui). El provider hace fallback a defaults de `domain/user_config` cuando faltan. `params` es tratado como read-only por los providers.
- A4 — **No existe consumidor aún**: `providers/` solo se testea directo en este ítem. El contrato queda fijado por tests; ítems 4-6 lo consumen sin cambios de firma.
- A5 — **ComfyUI tmpdir** es configurable (`tmp_path` en tests); default local fuera de `/tmp` (espejo de `bot.py` `_comfyui_tmpdir`), DI de ítem 6 podrá fijarlo.
- A6 — **aioresponses intercepta `aiohttp.ClientSession`** sin necesidad de inyectar sesión; los providers crean su propia sesión por llamada (patrón grok).
- A7 — **VideoProvider.generate hace submit + poll hasta completar** (paridad de comportamiento con grok, que bloquea hasta 600s). La cancelación de jobs (ítem 4) cancela la task asyncio que corre `generate` (los `asyncio.sleep` del poll son puntos de cancelación naturales). No se expone `poll()` en el Protocol en este ítem (SPEC §5.3 es ilustrativo; se documenta la desviación para el arch-enforcer).

## Architecture Approach

### QUÉ (comportamiento / contratos)

#### 1. Decisiones D1-D8 resueltas

**D1 — carrier de params provider-específicos (CRITICAL R1).**
- Cambio **aditivo** a `src/grokbot/domain/generation.py` (commit separado `feat(domain)`): agregar como **último campo** de `GenerationRequest`:
  ```python
  params: dict = field(default_factory=dict)  # provider/model extras (comfyui model/lora/refine, mode, prompts, key)
  ```
  Posición final + default ⇒ no rompe ninguna construcción posicional existente del ítem 1.
- Contenido convencional de `params` (documentado para ítems 4-5):
  - `"key"`: clave top-level del catálogo (`"grok"`, `"seedream"`, `"faceswap"`, `"comfyui"`, `"grok_video"`) — lo inyecta el registry/use-case.
  - ComfyUI: `"model"`, `"lora"`, `"refine"` (valores de `cfg.comfyui`, fallback a defaults de `domain/user_config`).
  - Video: `"mode"` (`fun|normal|spicy`).
  - ComfyUI batch: `"prompts"` (lista de frases de rama para `multipose_batch`).
- `params` es inmutable por convención (frozen no lo garantiza a nivel dict; los providers NO mutan el request).
- Justificación: es el único carrier que no rompe el contrato frozen ni mete payload binario en dominio; cubre comfyui model/lora/refine, referencia-xAI (via `edit_with_reference`, D7) y multi-output (D4).

**D2 — `DEFAULT_IMAGE_ASPECT_RATIO = "9:16"`.**
- Vive en **`src/grokbot/providers/base.py`** (no en `domain/catalog.py`).
- Justificación: es un default de wire compartido por xai/replicate/kie para armar payloads HTTP t2i cuando `request.aspect_ratio is None`; NO es un valor persistido por usuario (a diferencia de `video.aspect_ratio` en `UserConfig`) ni un spec de catálogo. Mantener `domain/` libre de defaults de transporte preserva el principio "dominio sin detalles de infraestructura" del ítem 1.

**D3 — excepciones tipadas en `providers/base.py` (R8/R9).**
```python
class ProviderError(Exception):
    retryable = True
    def __init__(self, message="", *, user_message=None): ...   # user_message default = message

class ProviderNotConfiguredError(ProviderError): retryable = False
class ProviderInputError(ProviderError):         retryable = False
class ProviderContentError(ProviderError):       retryable = False   # moderación/política
class ProviderAuthenticationError(ProviderError): retryable = False
class ProviderGenerationError(ProviderError):   retryable = False   # fail/expired/sin output
class ProviderRateLimitError(ProviderError):    retryable = True
class ProviderUnavailableError(ProviderError):  retryable = True    # 5xx/red/ssh no disponible
class ProviderTimeoutError(ProviderError):      retryable = False
```
- Regla: **precondiciones y estados terminales → no reintentables** (`retryable=False`); transients de red/HTTP 429/5xx → reintentables (`retryable=True`) para que el use-case de ítem 4 reintente si decide.
- Re-export a `shared/errors.py` se hace en ítem 6 (no crear `shared/` ahora).

**D4 — convención multi-output en `GenerationResult` (R3).**
- **Primary** en campo top-level: `remote_url` para providers que devuelven URLs (xai/replicate/kie); `file_path` para comfyui local.
- **Lista completa** en `meta`:
  - URLs: `meta["urls"] = [u1, u2, ...]` (primaria == `remote_url` == `urls[0]`).
  - ComfyUI local: `meta["file_paths"] = [...locals]` y `meta["comfyui_remotes"] = [.../workspace paths]`.
  - Kie: además `meta["task_id"]`, `meta["provider"]="kie"`, `meta["index"]=0` (si aplica).
- **Allowlist de descarga** (R7/D5): provider que devuelve URLs setea `meta["download_allowlist"]`: `"xai"` | `"kie"` | `None`. Ítem 4/6 lo usa para enforcear hosts antes de descargar.
- Esta convención es la que formatters de ítem 5 consumen (`len(meta["urls"])` / iterar `meta["file_paths"]`). Documentarla en docstring de cada provider y en `base.py`.

**D5 — descarga URL→bytes y allowlist (R7/SSRF).**
- **Los providers de este ítem NO descargan bytes de URLs arbitrarias.** Toda descarga de media final (URL→bytes para reenviar a Telegram) se difiere al downloader compartido con host-allowlist de ítem 4/6.
- Única resolución de URL intra-provider permitida: **Kie** resuelve `KieTaskRef` → URL de resultado vía API `recordInfo` (misma API de Kie, sin SSRF) y reusa esa URL en `createTask` (`image_urls`/`task_id`). Los `resultUrls` que Kie devuelve se **filtran por host** con su allowlist interno (igual que grok `_is_allowed_kie_asset_url`) antes de ponerlos en `meta`.
- Providers que devuelven URLs remotas setean `meta["download_allowlist"]` (D4) para que ítem 4 enforcee el allowlist al descargar.

**D6 — API del registry (R9/R11).**
```python
@dataclass(frozen=True)
class ProviderResolution:
    name: str            # "xai"|"replicate"|"kie"|"comfyui"
    provider: object     # ImageProvider | VideoProvider (instancia concreta)
    model_id: str        # id wire para el request (variante resuelta / video model / "comfyui")
    available: bool

class ProviderRegistry:
    def __init__(self, *, xai=None, replicate=None, kie=None, comfyui=None): ...
    def provider(self, name) -> object | None
    def is_available(self, name) -> bool          # provider present and provider.available
    def resolve_image(self, cfg: UserConfig) -> ProviderResolution
    def resolve_video(self, cfg: UserConfig) -> ProviderResolution
    def resolve(self, cfg: UserConfig, media_type: MediaType) -> ProviderResolution  # dispatch
```
- Espejo de `bot.py` (`get_model`/`get_video_provider_for_user`/`get_grok_imagine_config`), pero **puro sobre `UserConfig`**:
  - **imagen:** `cfg.model=="grok"` → `resolve_grok_config(cfg.grok_imagine_provider, cfg.grok_imagine_variant)` (name+model_id); `"seedream"/"faceswap"` → `replicate` + id estático de `MODELS`; `"comfyui"` → `comfyui` + `"comfyui"`; `"grok_video"` → `ProviderInputError` (no genera imagen).
  - **video:** `cfg.model=="grok_video"` → provider = `resolve_grok_config(...)["provider"]` con regla **replicate→xai**; `model_id = cfg.video.model`; `cfg.model=="comfyui"` → `comfyui` + `"comfyui"`; otro → `ProviderInputError`.
- `resolve_*` lanza `ProviderNotConfiguredError` (mensaje user-facing, R9) si el proveedor resuelto falta/`not available`. Registro y disponibilidad se inyectan por constructor (DI ítem 6).

**D7 — métodos extra fuera del Protocol (documentados).**
- `XaiProvider.edit_with_reference(request, source_image, reference_image)` — edición xAI con 2 imágenes (`/images/edits`, cuerpo `images:[{data_uri},{data_uri}]`). NO está en el Protocol; solo xAI lo expone.
- `ComfyUIProvider.refine(request, remote_paths)` — refine 2-stage (`REFINE_ONLY=1`, `REFINE_INPUT=...`, timeout 1200*N+300). Fuera del Protocol; lo usa `refine_flow` de ítem 4.
- `ComfyUIProvider.generate()` cubre t2i (sin `source_image`) e img2img (con `source_image`) según el modelo comfyui.

**D8 — `mode:spicy` de Kie solo con `kie_source_ref` (validación).**
- En `kie_provider`: si `request.params.get("mode") == "spicy"` y `request.source` **no** es `KieTaskRef` → `ProviderInputError("El modo spicy solo está disponible al editar imágenes generadas por el bot.")` (retryable=False). Aplica a imagen y video por igual (validación temprana, no downgrade silencioso).

#### 2. DoD medible del ítem
- [ ] `pytest tests/unit tests/integration/providers -q` desde `/home/ubuntu/repos/grokV2/.venv` en verde.
- [ ] `git -C /home/ubuntu/repos/grok status --porcelain` vacío (grok intacto).
- [ ] No existen `src/grokbot/{application,repositories,telegram,main.py,shared}`.
- [ ] `GenerationRequest.params` presente con default `{}`; construcciones posicionales del ítem 1 siguen pasando.
- [ ] `providers/*` sin imports de `os.environ`, `settings`, `telegram`, `grok`; sin `print`.
- [ ] Cada provider testea body/payload y **assert de que el token NO viaja en el body/url/log** (R2).
- [ ] Registry resuelve imagen/video por `UserConfig` según D6; disponibilidad kie/comfyui respetada (R9).
- [ ] Tests de video con `asyncio.sleep` no-opeado (sin esperas reales).
- [ ] `meta` sigue la convención D4 (primary top-level + lista en `meta` + `download_allowlist`).

### CÓMO (estructura / patrones / orden)

#### Estructura exacta (paths absolutos)
```
/home/ubuntu/repos/grokV2/
├── pyproject.toml                                  # EDIT: +deps runtime aiohttp/replicate; dev aioresponses
├── src/grokbot/domain/generation.py                # EDIT: + params: dict (D1)
├── src/grokbot/providers/__init__.py
├── src/grokbot/providers/base.py
├── src/grokbot/providers/xai_provider.py
├── src/grokbot/providers/replicate_provider.py
├── src/grokbot/providers/kie_provider.py
├── src/grokbot/providers/registry.py
├── src/grokbot/providers/comfyui/__init__.py
├── src/grokbot/providers/comfyui/ssh_client.py
├── src/grokbot/providers/comfyui/provider.py
├── tests/unit/domain/test_generation.py            # EDIT: + casos params
├── tests/unit/providers/test_base.py
├── tests/unit/providers/test_kie_helpers.py
├── tests/unit/providers/test_replicate_normalize.py
└── tests/integration/providers/
    ├── test_xai_provider.py
    ├── test_replicate_provider.py
    ├── test_kie_provider.py
    ├── test_comfyui_ssh_client.py
    ├── test_comfyui_provider.py
    └── test_registry.py
```
No crear ningún otro path. `grok/**` read-only.

#### Pattern-to-copy (grok = referencia SEMÁNTICA; transcribir payloads, no copiar archivos)
| Pieza nueva | Referencia `grok/bot.py` (o `sessions.py`) | Qué encapsula |
|---|---|---|
| `providers/base.py` | 235-266 (msgs user), 590-623 (mime/data-uri/i2v-size), 664-669 (regla video replicate→xai), 175-198 (timeouts/allowlist), `sessions.py:21` (9:16) | Protocol + errores + `DEFAULT_IMAGE_ASPECT_RATIO` + helpers `detect_image_mime`/`bytes_to_data_uri`/`validate_image_for_i2v` + consts poll `POLL_MAX_RETRIES=3`, `POLL_RETRY_BACKOFF_SEC=(2,4,8)`, `VIDEO_POLL_INTERVAL_SEC=5`, `IMAGE_MAX_POLL_SEC=120`, `VIDEO_MAX_POLL_SEC=600`, `I2V_MAX_IMAGE_BYTES=5MB` |
| `xai_provider.py` | 4530 `XAI_BASE`; 4533-4589 `_generate_xai` (t2i/i2i/edits); 5164-5297 `_generate_xai_video` + `_poll_video_once`; 269-276 http_ok; 607-623 data-uri/i2v | `POST /images/generations` (t2i `{model,prompt,n:1,aspect_ratio}`), `POST /images/edits` (i2i 1 img `image:{url:data_uri}`; 2 imgs vía `edit_with_reference` `images:[...]`), `POST /videos/generations` (`{model,prompt,duration,aspect_ratio,resolution,image?}`; 200/202 ok) + poll `GET /videos/{request_id}` hasta `done` (moderación `respect_moderation:false`→`ProviderContentError`; `failed/expired`→`ProviderGenerationError`; timeout 600s→`ProviderTimeoutError`) |
| `replicate_provider.py` | 3835-3852 `_generate_replicate`; 3220-3226 `_get_replicate_client`; 4960-4970 `_normalize_image_urls`; 190-199 timeouts | Rama por id: seedream i2i `image_input:[data_uri]`+`size:"2K"`; faceswap/otro i2i `image:BytesIO` + `file_encoding_strategy="base64"`; grok t2i `aspect_ratio` default 9:16. `Client(api_token=...).run(...)` en `to_thread` (NUNCA env). Normaliza FileOutput/str/list→`meta["urls"]` |
| `kie_provider.py` | 4592-4658 consts/slug/headers/upload; 4661-4855 create/poll/result-at-index/transients; 4882-4959 `_generate_kie_once`; 5300-5370 `_generate_kie_video`; 1107-1157 aspect/slug/duration/sanitize | `KIE_BASE/UPLOAD_BASE`, `file-base64-upload`→URL (host allowlist), `createTask`→poll `recordInfo`; `resultUrls` filtrados por host (KIE allowlist); t2i `aspect_ratio` default; i2i imagen `mode:"normal"`, i2i KieTaskRef `mode:"spicy"` (D8); video slug base/1.5, clamp 6-30, aspect-restricción por modelo; `KIE_API_KEY` vacío→`ProviderNotConfiguredError` |
| `comfyui/ssh_client.py` | 3858-3953 `_comfyui_ssh_opts`/`_comfyui_ssh_base`/`_comfyui_run_remote`/`_comfyui_pull`/`_comfyui_tmpdir` | Subprocess `ssh`/`scp` AISLADO acá; runner inyectable; parse stdout `/workspace`; returncode `None` en timeout; ControlMaster/ControlPersist; tmpdir configurable; host vacío→not-configured |
| `comfyui/provider.py` | 3956-4040 `_generate_comfyui`; 4041-4084 `_generate_comfyui_refine`; 4139-4141 `_comfyui_is_video`; 4262+ album (solo lista) | `generate`: t2i/img2img por comfyui model; JSON `{prompt,image_b64,prompts}` por stdin cuando hay source; precondiciones foto (`ProviderInputError`); video = model `wan_i2v/minimax_i2v`; returns local paths + `meta["comfyui_remotes"]`; `refine(...)` 2-stage; returncode 2/3→errores |
| `registry.py` | 664-669 `get_video_provider_for_user`; 848-922 `get_model`/`get_grok_imagine_config` (parte resolución) | Resolución pura desde `UserConfig`+`MediaType` (D6); disponibilidad (provider.available) |

#### Firmas clave (interfaces first — implementar en este orden)

`providers/base.py`:
```python
from typing import Protocol, runtime_checkable
from grokbot.domain.generation import GenerationRequest, GenerationResult

DEFAULT_IMAGE_ASPECT_RATIO = "9:16"
POLL_MAX_RETRIES = 3; POLL_RETRY_BACKOFF_SEC = (2, 4, 8)
VIDEO_POLL_INTERVAL_SEC = 5; IMAGE_MAX_POLL_SEC = 120; VIDEO_MAX_POLL_SEC = 600
I2V_MAX_IMAGE_BYTES = 5 * 1024 * 1024

def detect_image_mime(data: bytes) -> tuple[str, str]: ...   # ("image/png","png")|("image/webp","webp")|("image/jpeg","jpg") — espejo 590-605
def bytes_to_data_uri(data: bytes, mime: str | None = None) -> str: ...   # espejo 607-612
def validate_image_for_i2v(data: bytes) -> str | None: ...   # None OK; msg si > I2V_MAX_IMAGE_BYTES (espejo 615-623)

class ProviderError(Exception): ...   # jerarquía D3 (ver arriba)

@runtime_checkable
class ImageProvider(Protocol):
    @property
    def available(self) -> bool: ...
    def supports(self, request: GenerationRequest) -> bool: ...
    async def generate(self, request: GenerationRequest, *, source_image: bytes | None = None) -> GenerationResult: ...

@runtime_checkable
class VideoProvider(Protocol):
    @property
    def available(self) -> bool: ...
    def supports(self, request: GenerationRequest) -> bool: ...
    async def generate(self, request: GenerationRequest, *, source_image: bytes | None = None) -> GenerationResult: ...
```
Nota: `VideoProvider` no declara `poll()` en el Protocol (A7); el poll es interno a `generate`. Documentar la desviación del §5.3 de la SPEC (ilustrativo) para el arch-enforcer.

`providers/xai_provider.py`:
```python
class XaiProvider:   # implementa ImageProvider y VideoProvider
    def __init__(self, api_key: str): ...          # available = bool(api_key)
    @property
    def available(self) -> bool: ...
    def supports(self, request): ...                # provider=="xai" y media image|video
    async def generate(self, request, *, source_image=None) -> GenerationResult: ...   # rutea image/video
    async def edit_with_reference(self, request, source_image: bytes, reference_image: bytes) -> GenerationResult: ...  # D7 (fuera de Protocol)
```

`providers/replicate_provider.py`:
```python
class ReplicateProvider:   # implementa ImageProvider
    def __init__(self, api_token: str, *, client=None): ...   # client inyectable (fake en tests); default lazy replicate.Client(api_token=...)
    @property
    def available(self) -> bool: ...
    def supports(self, request): ...                # provider=="replicate" y media==IMAGE
    async def generate(self, request, *, source_image=None) -> GenerationResult: ...   # run en asyncio.to_thread
```
Helpers puros del módulo (unit-testeables sin importar replicate): `_replicate_kind(model_id)`, `_normalize_output_urls(output) -> list[str]`, `_image_to_data_uri(data)` (via base).

`providers/kie_provider.py`:
```python
class KieProvider:   # implementa ImageProvider y VideoProvider
    def __init__(self, api_key: str): ...           # available = bool(api_key)
    @property
    def available(self) -> bool: ...
    def supports(self, request): ...                # provider=="kie" y media image|video
    async def generate(self, request, *, source_image=None) -> GenerationResult: ...   # t2i/i2i/video + poll
```
Helpers puros del módulo: `_kie_video_slug(model, *, image_to_video)`, `_kie_aspect_ratios_for_model(model)`, `_kie_map_duration(d)`, `_sanitize_kie_fail_log(msg, limit=80)`, `_is_allowed_kie_asset_url(url)`, `_kie_poll_error_is_transient(http_status, api_code=None)`.

`providers/comfyui/ssh_client.py`:
```python
class SshClient:
    def __init__(self, host: str, port: int = 22, *, tmpdir: str | Path | None = None, runner=None): ...
    # runner: async (argv: list[str], *, input_bytes: bytes, timeout: int) -> CompletedProcess-like
    @property
    def is_configured(self) -> bool: ...            # bool(host)
    async def run_remote(self, cmd: str, payload: str, *, timeout: int = 600) -> tuple[list[str], int | None]: ...
        # → ([líneas /workspace...], returncode); ([], None) si timeout/host vacío
    async def pull(self, remote_path: str) -> str: ...   # scp → local bajo tmpdir; "" si falla/host vacío
```

`providers/comfyui/provider.py`:
```python
class ComfyUIProvider:   # implementa ImageProvider y VideoProvider
    def __init__(self, host: str, port: int = 22, *, ssh: SshClient | None = None): ...
    @property
    def available(self) -> bool: ...                # bool(host)
    def supports(self, request): ...                # provider=="comfyui"; media VIDEO requiere model wan_i2v/minimax_i2v
    async def generate(self, request, *, source_image=None) -> GenerationResult: ...
    async def refine(self, request: GenerationRequest, remote_paths: list[str]) -> GenerationResult: ...  # D7
```

`providers/registry.py`: según D6.

`providers/__init__.py`: re-exporta `ImageProvider/VideoProvider`, jerarquía `ProviderError`, `XaiProvider/ReplicateProvider/KieProvider/ComfyUIProvider/SshClient`, `ProviderRegistry/ProviderResolution`.

#### Wiring final del ítem
`tests → providers.*` y `tests → domain`; `providers/* → domain/*` (generation, catalog, user_config). `registry` consume instancias de providers y `domain/user_config`+`domain/catalog`. Nada más.

#### Orden de implementación (dependencias)
1. **D1**: `generation.py.params` + test (libera el carrier que comfyui/registry necesitan).
2. **base.py** + deps `pyproject` (libera contrato/errores/helpers a todos los providers).
3. **xai** y **replicate** (independientes, directos, sin dep entre sí).
4. **kie** (depende de base; helpers + HTTP).
5. **comfyui** `ssh_client` primero, luego `provider` (depende de ssh_client + base).
6. **registry** (depende de todos los providers + domain).

## Context

- SPEC: `/home/ubuntu/repos/grokV2/docs/SPEC_REFACTOR.md` §5.1/§5.2/§5.3/§6/§7.
- CLARIFY locked: `/home/ubuntu/repos/grokV2/.planning/quick/20260903-grokv2-rearch/CLARIFY.md`.
- Impact (input principal): `/home/ubuntu/repos/grokV2/.grok/agent-memory/impact-analyzer/item2-providers.md`.
- Dominio ítem 1 (commiteado): `/home/ubuntu/repos/grokV2/src/grokbot/domain/{generation,catalog,user_config,job}.py`, `/home/ubuntu/repos/grokV2/src/grokbot/settings.py`.
- Referencia semántica read-only: `/home/ubuntu/repos/grok/{bot.py,sessions.py}` (rangos en tabla Pattern-to-copy).

---

## Tasks

### Task 1: D1 (carrier params en dominio) + contrato base + deps
**type:** auto
**Objective:** `GenerationRequest.params` aditivo y verde; `providers/base.py` con Protocol, errores D3, helpers y consts; deps declaradas e instaladas.
**Files:**
- Edit: `/home/ubuntu/repos/grokV2/pyproject.toml`, `/home/ubuntu/repos/grokV2/tests/unit/domain/test_generation.py`.
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/providers/__init__.py`, `/home/ubuntu/repos/grokV2/src/grokbot/providers/base.py`, `/home/ubuntu/repos/grokV2/tests/unit/providers/test_base.py`.
**Action:**
- `pyproject.toml`: agregar a `dependencies` `"aiohttp>=3.11,<4"` y `"replicate>=1,<2"`; a `[project.optional-dependencies].dev` `"aioresponses>=0.7,<1"`. Luego correr `.venv/bin/pip install -e ".[dev]"`.
- `domain/generation.py`: agregar `params: dict = field(default_factory=dict)` como **último** campo de `GenerationRequest`, con docstring corto en inglés. NO tocar nada más de domain.
- `tests/unit/domain/test_generation.py`: agregar casos `params` default `{}`, se conserva al construir, y las construcciones posicionales del ítem 1 siguen pasando.
- `providers/base.py`: implementar exactamente las firmas de la sección CÓMO (Protocol, jerarquía D3 con `retryable`/`user_message`, `DEFAULT_IMAGE_ASPECT_RATIO`, consts poll/size, `detect_image_mime`, `bytes_to_data_uri`, `validate_image_for_i2v`). Solo imports de stdlib + `grokbot.domain.generation`. Ningún `print`.
- `providers/__init__.py`: re-exportar `ImageProvider`, `VideoProvider`, la jerarquía `ProviderError*`, consts/helpers clave.
- `tests/unit/providers/test_base.py`: test de errores (jerarquía, `retryable` por clase, `user_message`), `detect_image_mime` (png/webp/jpeg), `bytes_to_data_uri` (prefix `data:<mime>;base64,`), `validate_image_for_i2v` (bytes 5MB+1 → msg), default aspect.
**Verification:** `.venv/bin/pytest tests/unit/domain/test_generation.py tests/unit/providers/test_base.py -q`.
**Done:** params aditivo sin romper nada; base.py importable sin aiohttp/replicate; jerarquía/helpers cubiertos por tests; deps instaladas.

### Task 2: providers xai + replicate (directos, imagen/video)
**type:** auto
**Objective:** `XaiProvider` (image t2i/i2i/edit_with_reference + video create/poll) y `ReplicateProvider` (t2i/i2i por modelo) producen `GenerationResult` normalizados (D4) con fakes/aioresponses; token nunca en body.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/providers/xai_provider.py`, `/home/ubuntu/repos/grokV2/src/grokbot/providers/replicate_provider.py`, `/home/ubuntu/repos/grokV2/tests/unit/providers/test_replicate_normalize.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_xai_provider.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_replicate_provider.py`.
**Action:**
- `xai_provider.py`: transcribir payloads y flujo de `bot.py` 4530-4589 y 5164-5297. `XaiProvider.generate` rutea por `request.media_type`. t2i body `{model, prompt, n:1, aspect_ratio}` (default 9:16 si `request.aspect_ratio is None`); i2i con `source_image` body `{model, prompt, image:{url:data_uri, type:"image_url"}}` previa validación `validate_image_for_i2v` (error→`ProviderInputError`); `edit_with_reference` body `images:[...]`. Video: crear `POST /videos/generations`, aceptar 200/202, poll `GET /videos/{request_id}` cada `VIDEO_POLL_INTERVAL_SEC` hasta `done`/`failed`/`expired`/timeout; transients con `POLL_MAX_RETRIES`/`POLL_RETRY_BACKOFF_SEC`; `respect_moderation is False`→`ProviderContentError`. Normalizar a `GenerationResult` con `remote_url=urls[0]`, `meta["urls"]=urls`, `meta["download_allowlist"]="xai"` (video: `meta["urls"]=[url]`). HTTP status no-ok→`ProviderUnavailableError`/`ProviderAuthenticationError` según corresponda (nunca loguear headers/body/token).
- `replicate_provider.py`: helpers puros `_replicate_kind(model_id)` (compara con `MODELS["seedream"]["id"]`, `MODELS["faceswap"]["id"]`, prefix `xai/grok-imagine-`) y `_normalize_output_urls(output)` (FileOutput `.url`/str/list). `generate`: rama seedream i2i `image_input:[data_uri]`+`size:"2K"`; otra i2i `image` BytesIO + `file_encoding_strategy="base64"`; grok t2i `aspect_ratio` default; correr `await asyncio.to_thread(client.run, model_id, input=input_data, **kw)` con `client` inyectado o default `replicate.Client(api_token=api_token)` (lazy import de replicate SOLO al construir el client default). Normalizar output→`remote_url`/`meta["urls"]`.
- `tests/unit/providers/test_replicate_normalize.py`: FileOutput falso (attr `.url`), str, list[str]→urls; `_replicate_kind` para seedream/faceswap/grok/genérico.
- `tests/integration/providers/test_xai_provider.py` (aioresponses): t2i body correcto y `remote_url`; assert `api_key` NO en body; i2i single-image data-uri; `edit_with_reference` con 2 imágenes; i2v oversized→`ProviderInputError`; video create→poll pending/processing/done→URL; failed/expired→`ProviderGenerationError`; moderation false→`ProviderContentError`; timeout→`ProviderTimeoutError` (monkeypatch `asyncio.sleep` no-op).
- `tests/integration/providers/test_replicate_provider.py` (fake client `.run`): grok t2i con aspect_ratio; seedream t2i sin aspect y i2i con `image_input`+`size`; faceswap i2i con `image`+`file_encoding_strategy`; normalización del output del fake.
**Verification:** `.venv/bin/pytest tests/unit/providers/test_replicate_normalize.py tests/integration/providers/test_xai_provider.py tests/integration/providers/test_replicate_provider.py -q`.
**Done:** ambos providers sin red real en tests, token nunca en body, resultados según convención D4, commits atómicos por provider.

### Task 3: provider kie (helpers + imagen/video + D8)
**type:** auto
**Objective:** `KieProvider` con helpers puros, upload/create/poll con transients, i2i imagen, i2i desde `KieTaskRef` (spicy), video base/1.5, allowlist de hosts y `ProviderNotConfiguredError` cuando no hay key.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/providers/kie_provider.py`, `/home/ubuntu/repos/grokV2/tests/unit/providers/test_kie_helpers.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_kie_provider.py`.
**Action:**
- `kie_provider.py`: constantes `KIE_BASE`, `KIE_UPLOAD_BASE`, slugs (transcribir de 4592-4658); helpers puros listados en CÓMO. `generate` rutas:
  - api_key vacío → `ProviderNotConfiguredError(_KIE_NOT_CONFIGURED_MSG)`.
  - t2i: `{model: request.model_id, input:{prompt, aspect_ratio default, enable_pro:True, nsfw_checker:False}}`.
  - i2i imagen (source_image, sin KieTaskRef): upload `file-base64-upload`→URL (filtrar host), input `{image_urls:[url], prompt, enable_pro:True, nsfw_checker:False, mode:"normal"}`, slug `grok-imagine/image-to-image`.
  - i2i `KieTaskRef`: resolver URL vía `recordInfo` (index), input `mode:"spicy"` si `params["mode"]=="spicy"`; si `mode:"spicy"` sin KieTaskRef → `ProviderInputError` (D8).
  - video: slug por `_kie_video_slug`; validar aspect-ratio del modelo; clamp duration 6-30; image_to_video con `source_image` → upload; desde KieTaskRef → `task_id/index`.
  - createTask→poll `recordInfo` con transients (429/5xx/422/404; backoff 2/4/8; timeout `IMAGE_MAX_POLL_SEC`/`VIDEO_MAX_POLL_SEC`); failCode/failMsg sanitizado (80ch); `resultUrls` filtrados por `_is_allowed_kie_asset_url`; resultado → `remote_url=urls[0]`, `meta["urls"]=urls`, `meta["task_id"]`, `meta["provider"]="kie"`, `meta["download_allowlist"]="kie"`.
- `tests/unit/providers/test_kie_helpers.py`: slug base/1.5 t2v/i2v, `_kie_aspect_ratios_for_model`, clamp 6-30, sanitize 80ch, allowed-host predicates (incluye hosts bloqueados).
- `tests/integration/providers/test_kie_provider.py` (aioresponses): t2i create+poll success (meta task_id, urls filtradas); no-key→`ProviderNotConfiguredError`; upload→URL→i2i normal; KieTaskRef→resolver→i2i `mode:"spicy"`; spicy sin ref→`ProviderInputError`; aspect inválido video→`ProviderInputError`; video 1.5 slug i2v; transients/backoff con `asyncio.sleep` no-op; host bloqueado descartado.
**Verification:** `.venv/bin/pytest tests/unit/providers/test_kie_helpers.py tests/integration/providers/test_kie_provider.py -q`.
**Done:** helper unit + integración kie en verde; allowlist de hosts enforced; token no en body; D8 validado con test.

### Task 4: comfyui ssh_client + provider (generate + refine)
**type:** auto
**Objective:** `SshClient` aísla subprocess/SCP con runner inyectable y tmpdir configurable; `ComfyUIProvider` genera t2i/img2img, valida precondiciones de foto, detecta video por modelo comfyui y expone `refine` (D7).
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/providers/comfyui/__init__.py`, `/home/ubuntu/repos/grokV2/src/grokbot/providers/comfyui/ssh_client.py`, `/home/ubuntu/repos/grokV2/src/grokbot/providers/comfyui/provider.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_comfyui_ssh_client.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_comfyui_provider.py`.
**Action:**
- `ssh_client.py`: clase `SshClient(host, port=22, *, tmpdir=None, runner=None)`. Default runner ejecuta `subprocess.run` en `asyncio.to_thread` con los args SSH de `_comfyui_ssh_opts`/`_comfyui_ssh_base` (ControlMaster/ControlPersist/BatchMode; ControlPath bajo tmpdir). `run_remote` arma `ssh -p PORT [opts] root@HOST -- cmd`, pasa payload por stdin, parsea líneas `/workspace...`; timeout→`([], None)`; host vacío→not-configured. `pull` arma `scp -P PORT [opts] root@HOST:remote local` (local en tmpdir con ext del remote), timeout 120; devuelve local o `""`. Validación opcional de remote path para refine se hace en provider.
- `provider.py`: `ComfyUIProvider(host, port=22, *, ssh=None)` (si `ssh` dado, usarlo; si no construir `SshClient(host, port)`). `generate`:
  - Precondiciones foto (sin `source_image`) → `ProviderInputError` para `wan_i2v/minimax_i2v`, loras `krea_edit*` sobre `krea2*`, `qwen`+`multipose_batch`, `qwen_aio` (transcribir mensajes de 3956-4032).
  - t2i: correr `MODEL=... LORA=... python3 /workspace/gen_comfy.py` con `prompt` por stdin; img2img: payload JSON `{prompt, image_b64, prompts?}`. Timeout imagen 600 / video 1500.
  - pull de cada remote → locals; resultado `file_path=locals[0]`, `meta["file_paths"]=locals`, `meta["comfyui_remotes"]=remotes`, `meta["download_allowlist"]=None`. Sin remotes → `ProviderUnavailableError`.
  - host vacío → `ProviderNotConfiguredError`; returncode 2/3 (en refine) → errores tipados.
  - `refine(request, remote_paths)`: valida remote paths regex `/workspace/[...]`→`ProviderInputError` si no; comando `REFINE_ONLY='1' REFINE_INPUT='...' MODEL= LORA=`; timeout `1200*len+300`; returncode 2→`ProviderInputError`, 3→`ProviderGenerationError`; resultado como `generate`.
  - `supports`: comfyui; media VIDEO requiere model en `("wan_i2v","minimax_i2v")`.
  - model/lora leídos de `request.params` con fallback a `DEFAULT_COMFYUI_MODEL`/`DEFAULT_COMFYUI_LORA` de `domain/user_config`.
- `tests/integration/providers/test_comfyui_ssh_client.py` (fake runner inyectado): command building (MODEL/LORA/REFINE_ONLY/REFINE_INPUT/timeouts), payload JSON stdin con image_b64, parse `/workspace` lines, returncode 2/3, pull→local path, host vacío→not-configured, timeout→`([], None)`.
- `tests/integration/providers/test_comfyui_provider.py` (fake ssh_client): t2i→`file_path`+`meta["file_paths"]`; img2img con source; precondiciones sin foto→`ProviderInputError`; video detectado por model; multi-output→`meta["file_paths"]`; refine 2-stage (fake verifica comando con `REFINE_ONLY`); host vacío→`ProviderNotConfiguredError`.
**Verification:** `.venv/bin/pytest tests/integration/providers/test_comfyui_ssh_client.py tests/integration/providers/test_comfyui_provider.py -q`.
**Done:** ssh_client/provider testeables sin red ni SSH real; refine expuesto fuera de Protocol; local paths nunca tocados por Telegram en este ítem.

### Task 5: registry + suite completa + self-check
**type:** auto
**Objective:** `ProviderRegistry` (D6) puro con disponibilidad; suite de providers completa en verde; 0 cambios sobre grok; no existen carpetas de ítems 3-6.
**Files:**
- Create: `/home/ubuntu/repos/grokV2/src/grokbot/providers/registry.py`, `/home/ubuntu/repos/grokV2/tests/integration/providers/test_registry.py`.
**Action:**
- `registry.py`: `ProviderResolution` frozen y `ProviderRegistry` según D6. `resolve_image`/`resolve_video` sobre `UserConfig`; `resolve` dispatch. Disponibilidad = instancia presente y `provider.available`. `ProviderNotConfiguredError` con mensaje user-facing al resolver no disponible. `ProviderInputError` para combinaciones modelo/media no soportadas.
- `tests/integration/providers/test_registry.py` (con providers reales con tokens dummy para no tocar red — resolve no genera): imagen `grok`→xai/replicate/kie según `cfg.grok_imagine_provider` y variant; `seedream`/`faceswap`→replicate; `comfyui`→comfyui; video `grok_video` con provider replicate→xai; video `grok_video` con kie→kie (model_id=cfg.video.model); comfyui video; kie no configurado (provider available False)→`ProviderNotConfiguredError`; comfyui host vacío→`ProviderNotConfiguredError`; media_type/model no soportado→`ProviderInputError`.
- Correr suite completa del ítem; verificar self-check de la sección Instrucciones; escribir SUMMARY del ítem.
**Verification:** `.venv/bin/pytest tests/unit tests/integration/providers -q` → todo verde.
**Done:** registry puro y cubierto; suite de providers verde; DoD del ítem cumplido.

---

## Instrucciones para gsd-executor

- **Dónde trabajar:** SOLO bajo `/home/ubuntu/repos/grokV2`. `/home/ubuntu/repos/grok` es read-only: se lee para transcribir payloads/valores de las tablas Pattern-to-copy, jamás se edita ni se importa.
- **No-touch (prohibido crear/editar):** `src/grokbot/{application,repositories,telegram,main.py,shared}/`, cualquier `domain/*` salvo `generation.py` (solo D1), `settings.py` (no lo importan providers), `docs/SPEC_REFACTOR.md`, `.planning/**` (salvo commits), `.grok/**`. No escribir fuera de la lista de Files de cada task.
- **Convenciones:** providers en inglés (docstrings cortos), sin `print` en código de librería (usar `logging` módulo-level si hace falta; por defecto sin logging). Type hints completos. Dataclasses frozen del dominio se tratan como inmutables. `request.params` es read-only.
- **Seguridad (R2/R7):** nunca loguear/incrustar tokens en URLs o mensajes de error; errores user-facing normalizados (sin request_ids sensibles); tests con assert de que `api_key`/token no está en el body serializado.
- **Anti-patterns prohibidos:** leer `os.environ` en providers; re-setear env global (bot.py:41-49); inyectar `settings.Settings` en providers; importar `telegram`/`aiohttp` en `base.py` (base es stdlib + domain); `str.format` no aplica acá pero mantener el estilo del repo.
- **Mock policy:** solo bordes externos — aioresponses para HTTP, fake `client.run` para replicate, fake runner para SSH, `asyncio.sleep` no-op en polls, `tmp_path` para tmpdir comfyui. NO mockear domain ni lógica interna.
- **Commits atómicos por work unit** (formato del repo, trailer incluido):
  1. `feat(domain): GenerationRequest.params carrier aditivo + tests`
  2. `feat(providers): base contract ImageProvider/VideoProvider + errores tipados + helpers + deps`
  3. `feat(providers): xai provider (image + video + edit_with_reference) + tests`
  4. `feat(providers): replicate provider (grok/seedream/faceswap) + tests`
  5. `feat(providers): kie provider (image/video/helpers/allowlist/D8) + tests`
  6. `feat(providers): comfyui ssh_client + provider (generate/refine) + tests`
  7. `feat(providers): registry resolve por UserConfig + disponibilidad + tests`
  No commitear `.venv/`, `.env`, cachés, `.pytest_cache`.
- **Self-check antes de cada commit:** correr la suite del ítem (abajo); `git status --porcelain` solo con archivos esperados; `git -C /home/ubuntu/repos/grok status --porcelain` vacío.
- **Registro:** mantener log corto de ejecución y escribir `SUMMARY.md` al cierre del ítem (como en ítem 1).
- **Residuales:** si algo no cierra (versión de aiohttp sin wheel cp314, timeout real de replicate, etc.) reportarlo en el SUMMARY y en la respuesta final; no cambiar decisiones bloqueadas ni el alcance.

## Test commands

- Suite del ítem (desde `/home/ubuntu/repos/grokV2`):
  `.venv/bin/pytest tests/unit tests/integration/providers -q`
- Solo unit providers:
  `.venv/bin/pytest tests/unit/providers -q`
- Solo integration providers:
  `.venv/bin/pytest tests/integration/providers -q`
- Sanidad de import (sin env, sin instanciar Settings):
  `.venv/bin/python -c "import grokbot.providers; print('ok')"`
- Verificación read-only grok intacto:
  `git -C /home/ubuntu/repos/grok status --porcelain` (vacío)

## Risks + Mitigation

| Riesgo (impact) | Mitigación en este PLAN |
|---|---|
| **R1 CRITICAL** params carrier ausente | D1: campo aditivo `params: dict` último en `GenerationRequest` (Task 1); providers con fallback a defaults de `domain/user_config` cuando faltan |
| **R2 CRITICAL** fuga de secretos/payloads pagos | Solo constructor; nunca loguear headers/body; errores normalizados; tests assert token no en body (Tasks 2-3) |
| **R3 CRITICAL** normalización heterogénea multi-output | D4: primary top-level + `meta["urls"]`/`meta["file_paths"]` + `download_allowlist`; tests deterministas por provider (Tasks 2-4) |
| **R4 MEDIUM** xAI video estados/moderation/timeout | Mapeo `pending/processing/done/failed/expired`; `respect_moderation:false`→`ProviderContentError`; `failed/expired`→`ProviderGenerationError`; timeout→`ProviderTimeoutError`; `asyncio.sleep` no-op en tests (Task 2) |
| **R5 MEDIUM** Kie límites/aspect/modo/transients/allowlist | Helpers puros + unit tests; clamp 6-30; aspect por modelo; D8 spicy solo con KieTaskRef; poll transients con backoff; filtro de `resultUrls` por host (Tasks 3) |
| **R6 MEDIUM** ComfyUI SSH embebido/paths inseguros | Todo subprocess en `ssh_client.py` con runner inyectable; tmpdir configurable; validación regex de remote paths en refine; timeouts por tipo (Task 4) |
| **R7 MEDIUM** SSRF descarga de URLs | D5: providers NO descargan bytes de URLs; Kie resuelve solo su propia API `recordInfo` y filtra hosts; media final la descarga ítem 4/6 con allowlist (Tasks 3 + documentación) |
| **R8 LOW** reintentos fuera del provider | D3: errores tipados `retryable` correcto; el use-case de ítem 4 decide reintentar (Task 1) |
| **R9 LOW** disponibilidad "no configurado" | Providers con `available`; registry lanza `ProviderNotConfiguredError` con mensaje user-facing; tests kie/comfyui no configurados (Task 5) |
| **R10 LOW** deps nuevas | `aiohttp>=3.11,<4`, `replicate>=1,<2`, `aioresponses>=0.7,<1`; pip install en Task 1; si cp314 sin wheel → residual en SUMMARY |
| **R11 LOW** `supports()` vs video solo replicate | `supports` por provider; registry resuelve por `cfg.model` + regla replicate→xai para video (Tasks 2-5) |
| **R12 LOW** pytest-asyncio/Py3.14 warnings | Cosmético (ya presente en ítem 1); no bloquear; no filtrar en este ítem |

## Success Criteria

- [ ] `pytest tests/unit tests/integration/providers -q` en verde desde `.venv` de grokV2.
- [ ] `git -C /home/ubuntu/repos/grok status --porcelain` vacío; 0 cambios sobre grok.
- [ ] No existen `src/grokbot/{application,repositories,telegram,main.py,shared}`.
- [ ] `GenerationRequest.params` aditivo con default `{}`; tests de dominio del ítem 1 siguen en verde.
- [ ] `providers/base.py` con Protocol ImageProvider/VideoProvider + jerarquía `ProviderError*` (D3) + `DEFAULT_IMAGE_ASPECT_RATIO` (D2) + helpers mime/data-uri/i2v + consts poll.
- [ ] Providers sin `os.environ`, sin `settings`, sin `print`, sin Telegram; token no en body (tests).
- [ ] Convención D4: primary top-level + lista en `meta` + `download_allowlist`; documentada para ítem 5.
- [ ] `registry` (D6) resuelve imagen/video por `UserConfig`; disponibilidad kie/comfyui; `ProviderNotConfiguredError` user-facing.
- [ ] ComfyUI `refine`/xai `edit_with_reference` fuera del Protocol (D7); Kie `mode:spicy` validado con KieTaskRef (D8).
- [ ] Commits atómicos por work unit creados según la lista; `.venv/` y `.env` no commiteados.
