# Spec de Re-arquitectura — Bot de Telegram para Generación de Contenido Multimedia

## 1. Contexto y objetivo

El proyecto actual @grok es un bot de Telegram funcional que permite a usuarios generar y editar imágenes/video mediante múltiples proveedores (xAI Grok Imagine, Replicate, Kie.ai, ComfyUI remoto vía SSH), con un sistema de batch por variables (`/variables`, `/var`), un panel administrativo de listas (`/listas`), configuración por usuario (`/config`) y persistencia en JSON.

Funcionalmente el proyecto cumple, pero técnicamente es un monolito: **`bot.py` tiene 5,497 líneas** y concentra el 90% de la lógica de negocio, integración con proveedores externos, manejo de estado, teclados de Telegram y orquestación de jobs — todo en un solo archivo, sin capas ni contratos internos definidos.

**Objetivo de este documento:** especificar cómo se re-implementaría el mismo alcance funcional desde cero en @grokV2 con una arquitectura modular, testeable y con bajo acoplamiento, que permita agregar nuevos proveedores, nuevos comandos o nuevos flujos sin tocar código no relacionado.

---

## 2. Alcance funcional (lo que el sistema debe seguir haciendo)

| Área | Descripción |
|---|---|
| **Generación de imagen** | Texto→imagen e imagen→imagen (edición), vía xAI, Replicate, Kie.ai o ComfyUI remoto |
| **Generación de video** | Texto→video e imagen→video, vía xAI o Kie.ai, con duración/aspect ratio/resolución configurables 
| **Batch por variables** | `/variables N` y `/var texto` — generación de N imágenes con prompts armados a partir de listas admin-managed (poses, ángulos, acciones) o texto libre, con cancelación y control de concurrencia |
| **Panel de administración de listas** | `/listas` — CRUD de listas y template, gestión de "paquetes" (export/import de configuraciones) |
| **Configuración por usuario** | `/config` — selección de modelo, proveedor, variante, parámetros de video/ComfyUI, persistidos por usuario |
| **Álbumes de Telegram** | Procesamiento de grupos de fotos (media groups) |
| **Control de acceso** | Allowlist de usuarios, admins separados para el panel de listas |
| **Jobs cancelables** | Límite de jobs concurrentes por usuario, cancelación vía botón inline |

---

## 3. Diagnóstico del sistema actual

### 3.1 Problemas estructurales identificados

1. **Monolito de 5,497 líneas en `bot.py`.** Mezcla: parsing de config de entorno, validación de inputs, teclados de Telegram, lógica de negocio de cada proveedor (xAI, Replicate, Kie.ai, ComfyUI), orquestación de jobs, manejo de callbacks, y el entrypoint `main()`.
2. **Sin capa de abstracción de proveedores.** Cada proveedor (`_generate_xai`, `_generate_replicate`, `_generate_kie_once`, `_generate_comfyui`) tiene su propia forma de recibir parámetros y devolver resultados — no hay una interfaz común, lo que hace costoso agregar un proveedor nuevo o testear la lógica de selección de proveedor de forma aislada.
3. **Persistencia acoplada a JSON plano sin repositorio.** `sessions.py` y `variables_store.py` leen/escriben archivos JSON directamente y exponen funciones sueltas (no clases/interfaces), lo que impide cambiar de backend (ej. Redis, SQLite) sin reescribir cada call site.
4. **Estado compartido mutable vía diccionarios globales.** Jobs, refines pendientes y colecciones de prompt largo se gestionan con diccionarios módulo-level en `bot.py` (`_register_pending_refine`, `_start_job`, etc.), sin encapsulamiento — riesgo de condiciones de carrera y difícil de testear.
5. **FSM en memoria (`MemoryStorage`).** Documentado como limitación conocida: se pierde en reinicios y no escala horizontalmente. No hay abstracción que permita swap a Redis sin tocar los flows.
6. **ComfyUI vía subprocess SSH embebido en el módulo de bot.** `_comfyui_ssh_opts`, `_comfyui_run_remote`, `_comfyui_pull` ejecutan `subprocess`/SCP directamente dentro de la capa de handlers de Telegram, mezclando I/O de infraestructura con lógica de presentación.
7. **Falta de tipado de dominio.** Los "modelos" de generación se pasan como `dict` sueltos (`model: dict`) en vez de dataclasses/Pydantic — facilita errores silenciosos de claves mal escritas.
8. **Duplicación entre `/variables` y `/var`.** `_run_variables_batch` y `_run_var_batch` son flujos casi idénticos (mismo motor de batch, cancelación, notificación) implementados por separado.
9. **Tests unitarios contra un monolito.** Los 19 archivos de test en `tests/` (más de 300K en total) validan comportamiento a través de `bot.py` directamente, lo que los vuelve frágiles ante refactors y lentos de mantener.
10. **Configuración de entorno dispersa.** Variables de entorno (`TELEGRAM_BOT_TOKEN`, `COMFYUI_HOST`, límites, etc.) se leen con `os.environ` en múltiples puntos del código sin un objeto de configuración central validado al arranque.

---

## 4. Principios de la nueva arquitectura

1. **Separación por capas**: presentación (Telegram) → aplicación (casos de uso/orquestación) → dominio (entidades y reglas) → infraestructura (proveedores externos, persistencia).
2. **Providers detrás de una interfaz común.** Todo proveedor de generación (xAI, Replicate, Kie.ai, ComfyUI) implementa el mismo contrato (`ImageProvider` / `VideoProvider`), permitiendo agregar proveedores nuevos sin tocar la capa de aplicación.
3. **Repositorios en vez de I/O directo.** Sesiones, listas de variables y paquetes se acceden vía interfaces de repositorio; el backend (JSON, SQLite, Redis) es un detalle de implementación intercambiable.
4. **Casos de uso explícitos (Use Cases / Services).** Cada acción de negocio (generar imagen, correr batch de variables, resolver refine) es una clase/función de aplicación testeable sin Telegram ni red real, usando dobles de prueba para providers y repos.
5. **Configuración centralizada y validada al boot.** Un único objeto `Settings` (Pydantic) carga y valida todas las env vars una vez; falla rápido si falta algo crítico (ya existe una validación parcial en `_parse_allowed_telegram_ids`, se generaliza).
6. **Dominio tipado.** Entidades como `GenerationRequest`, `GenerationResult`, `UserConfig`, `VideoConfig` como dataclasses/Pydantic models, no `dict` sueltos.
7. **Job/Task management como servicio propio.** Un `JobManager` encapsula creación, cancelación y límites de concurrencia — no diccionarios módulo-level dispersos en el archivo de handlers.
8. **Testing por capas.** Dominio y aplicación se testean sin mocks pesados; infraestructura se testea con contratos (fakes/mocks del proveedor externo); handlers de Telegram se testean con mensajes/callbacks simulados, delegando la lógica real a los casos de uso.

---

## 5. Arquitectura propuesta

### 5.1 Estructura de directorios

```
grok-bot/
├── pyproject.toml
├── .env.example
├── README.md
├── src/
│   └── grokbot/
│       ├── __init__.py
│       ├── main.py                    # entrypoint: bootstrap, DI, arranque del Dispatcher
│       ├── settings.py                # Settings (Pydantic BaseSettings), validación al boot
│       │
│       ├── domain/                    # entidades y value objects, sin dependencias externas
│       │   ├── __init__.py
│       │   ├── generation.py          # GenerationRequest, GenerationResult, MediaType
│       │   ├── user_config.py         # UserConfig, VideoConfig, ComfyUIConfig
│       │   ├── variables.py           # VariableList, PromptTemplate, Combo
│       │   └── job.py                 # Job, JobStatus
│       │
│       ├── application/               # casos de uso, orquestación, sin detalles de infraestructura
│       │   ├── __init__.py
│       │   ├── generate_image.py      # GenerateImageUseCase
│       │   ├── generate_video.py      # GenerateVideoUseCase
│       │   ├── run_variable_batch.py  # RunVariableBatchUseCase (unifica /variables y /var)
│       │   ├── manage_config.py       # UpdateUserConfigUseCase
│       │   ├── manage_lists.py        # CRUD de listas/paquetes (para /listas)
│       │   ├── refine_flow.py         # ResolveRefineUseCase (ComfyUI 2-stage)
│       │   └── job_manager.py         # JobManager: crear/cancelar/limitar jobs concurrentes
│       │
│       ├── providers/                 # integraciones con proveedores externos de generación
│       │   ├── __init__.py
│       │   ├── base.py                # Protocol/ABC: ImageProvider, VideoProvider
│       │   ├── xai_provider.py
│       │   ├── replicate_provider.py
│       │   ├── kie_provider.py
│       │   ├── comfyui/
│       │   │   ├── __init__.py
│       │   │   ├── provider.py        # implementa ImageProvider/VideoProvider
│       │   │   └── ssh_client.py      # subprocess/SCP aislado aquí, no en handlers
│       │   └── registry.py            # resuelve proveedor activo según UserConfig
│       │
│       ├── repositories/              # persistencia, detrás de interfaces
│       │   ├── __init__.py
│       │   ├── base.py                # SessionRepository, VariablesRepository (Protocols)
│       │   ├── json_session_repo.py   # implementación actual (JSON en disco)
│       │   ├── json_variables_repo.py
│       │   └── generation_refs_repo.py
│       │
│       ├── telegram/                  # capa de presentación — SOLO adaptación a/desde Telegram
│       │   ├── __init__.py
│       │   ├── middlewares.py         # AllowlistMiddleware, etc.
│       │   ├── keyboards.py           # construcción de InlineKeyboardMarkup, agrupados por flujo
│       │   ├── formatters.py          # mensajes de texto (captions, status), sin lógica de negocio
│       │   ├── handlers/
│       │   │   ├── __init__.py
│       │   │   ├── generation.py      # /model /imagine handlers de texto/foto → llama use cases
│       │   │   ├── video.py
│       │   │   ├── variables_cmd.py   # /variables /var
│       │   │   ├── config_cmd.py      # /config /model /imaginess /video
│       │   │   ├── listas_cmd.py      # /listas (admin panel FSM)
│       │   │   ├── jobs.py            # botón Cancelar, callbacks de refine
│       │   │   └── album.py           # media groups
│       │   └── fsm_states.py          # StatesGroup centralizados
│       │
│       └── shared/
│           ├── __init__.py
│           ├── logging.py
│           └── errors.py              # helpers user-safe del entrypoint (D8: sin jerarquía de errores)
│
└── tests/
    ├── unit/                          # domain + application, sin red ni Telegram
    │   ├── application/
    │   └── domain/
    ├── integration/                   # providers y repos, con fakes de HTTP
    │   ├── providers/
    │   └── repositories/
    └── e2e/                           # handlers de Telegram con mensajes simulados
        └── telegram/
```

> **C13/D8 — jerarquía de errores.** A diferencia de lo que sugería un borrador
> anterior, `shared/errors.py` NO contiene la jerarquía de excepciones del dominio.
> Las excepciones tipadas viven en su capa: `ProviderError*` en `providers/base.py`,
> errores de media/descarga en `telegram/ports.py` y `telegram/downloader.py`.
> `shared/errors.py` solo expone helpers user-safe del entrypoint (p. ej. formatear
> errores de Settings por nombre de campo para el mensaje al usuario).

### 5.2 Flujo de una petición típica (ej. `/variables 5` con foto)

```
Telegram update
   → AllowlistMiddleware (autorización)
   → handlers/variables_cmd.py::cmd_variables_photo
        - parsea el comando (N, caption) — SOLO parsing de input Telegram
        - arma un GenerationBatchRequest (domain)
   → application/run_variable_batch.py::RunVariableBatchUseCase.execute()
        - valida modelo configurado vía UserConfig (repositorio de sesión)
        - resuelve combos random vía VariablesRepository
        - pide un slot al JobManager (límite de concurrencia)
        - por cada combo: resuelve el provider vía registry.py y llama provider.generate()
        - emite eventos de progreso (callback/async generator) que el handler traduce a mensajes Telegram
   → providers/xai_provider.py (o el que corresponda)
        - hace la llamada HTTP real, normaliza la respuesta a GenerationResult (domain)
   → handlers/variables_cmd.py
        - recibe GenerationResult y lo traduce a mensajes/fotos de Telegram (formatters.py, keyboards.py)
```

La regla dura: **los handlers de `telegram/` nunca llaman directamente a `aiohttp`/`replicate`/`subprocess` ni tocan archivos JSON.** Solo hablan con `application/*` y traducen `domain/*` a UI de Telegram.

### 5.3 Contrato de providers (ejemplo)

```python
# providers/base.py
class ImageProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    def supports(self, request: GenerationRequest) -> bool: ...

class VideoProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    async def poll(self, task_id: str) -> GenerationResult: ...
```

Cada proveedor concreto (`xai_provider.py`, `kie_provider.py`, etc.) implementa esto y encapsula sus propios detalles (headers, polling, mapeo de aspect ratios, límites de Kie.ai, etc.), que hoy están mezclados con handlers de Telegram en `bot.py` (líneas ~4533–4960).

### 5.4 Unificación de `/variables` y `/var`

Ambos comandos hoy duplican el motor de batch (`_run_variables_batch` vs `_run_var_batch`, ~600 líneas combinadas). En la nueva arquitectura, un único `RunVariableBatchUseCase` acepta una estrategia de generación de prompts (`RandomComboStrategy` o `FixedPromptStrategy`), y los dos comandos de Telegram solo difieren en cómo arman el `PromptStrategy` inicial.

### 5.5 JobManager

Reemplaza los diccionarios module-level (`_start_job`, `_request_cancel_job`, `_finish_job`, `_register_pending_refine`) por una clase con estado encapsulado, inyectable y testeable:

```python
class JobManager:
    def start(self, user_id: int, kind: str) -> Job: ...
    def cancel(self, user_id: int, job_id: str | None = None) -> bool: ...
    def finish(self, job: Job) -> None: ...
    def active_count(self, user_id: int) -> int: ...
```

Esto permite, a futuro, backear el `JobManager` con Redis para escalar horizontalmente sin cambiar la capa de aplicación.

### 5.6 Persistencia

`repositories/base.py` define interfaces (`SessionRepository`, `VariablesRepository`, `GenerationRefsRepository`). La implementación inicial reutiliza JSON en disco (migración de bajo riesgo desde el sistema actual), pero el contrato permite sustituir por SQLite/Redis sin tocar `application/` ni `telegram/`.

### 5.7 Configuración

```python
# settings.py
class Settings(BaseSettings):
    telegram_bot_token: str
    replicate_api_token: str
    xai_api_key: str
    kie_api_key: str | None = None
    allowed_telegram_ids: set[int] | None = None
    variables_admin_ids: set[int] | None = None
    refine_confirm_timeout: int = 300
    comfyui_host: str = ""
    comfyui_port: int = 22
    ...
```

Se carga una sola vez en `main.py`, se valida con Pydantic (falla rápido si falta algo requerido), y se inyecta a providers/repos — reemplaza las ~10 lecturas dispersas de `os.environ` en `bot.py`.

---

## 6. Requerimientos no funcionales

| Requerimiento | Detalle |
|---|---|
| **Testabilidad** | Cada capa (domain, application) debe poder testearse sin red, sin Telegram y sin filesystem real (uso de fakes/in-memory repos) |
| **Extensibilidad de proveedores** | Agregar un proveedor nuevo de imagen/video no debe requerir cambios en `application/` ni `telegram/`, solo una nueva clase en `providers/` + registro en `registry.py` |
| **Observabilidad** | Logging estructurado centralizado (`shared/logging.py`), reemplaza los `print`/logs ad-hoc dispersos |
| **Resiliencia ante reinicio** | El `JobManager` y el FSM deben poder respaldarse en un backend persistente (Redis) sin romper contratos, aunque la implementación default siga siendo in-memory para desarrollo |
| **Seguridad** | Allowlist y admin-list se mantienen como middleware transversal, ahora testeado de forma aislada; validación de inputs (prompts, tamaños de imagen, hosts permitidos para descarga) se mueve a `domain`/`providers`/`repositories` como reglas explícitas, no funciones sueltas con prefijo `_`. D8: las excepciones tipadas viven en su capa (`ProviderError*` en `providers/base.py`, errores de media/descarga en `telegram/ports.py`/`telegram/downloader.py`); `shared/errors.py` solo tiene helpers user-safe del entrypoint (C13). |
| **Migración de datos** | Los JSON existentes (`sessions.json`, `variables_lists.json`) deben poder leerse tal cual por los nuevos repositorios sin requerir migración de datos manual |
| **Compatibilidad de comandos** | Los comandos y alias actuales (`/config`, `/model`, `/imagine`, `/imaginess`, `/video`, `/variables`, `/var`, `/listas`) se mantienen sin cambios de cara al usuario |

---

## 7. Plan de migración sugerido (incremental, no big-bang)

1. **Fase 0 — Congelar alcance:** documentar el comportamiento actual como base de comparación (tests de caracterización sobre `bot.py` si no existen ya para los flujos críticos).
2. **Fase 1 — Extraer dominio y settings:** crear `domain/` y `settings.py` sin tocar `bot.py` todavía; son puramente aditivos.
3. **Fase 2 — Extraer providers:** mover la lógica de cada proveedor (`_generate_xai`, `_generate_replicate`, `_generate_kie_once`, `_generate_comfyui*`) a `providers/`, detrás de la interfaz común, manteniendo `bot.py` como caller temporal.
4. **Fase 3 — Extraer repositorios:** envolver `sessions.py`/`variables_store.py` en las interfaces de `repositories/`, sin cambiar el formato de los JSON.
5. **Fase 4 — Extraer casos de uso:** mover la orquestación (batch, refine, config) a `application/`, con `bot.py` como delgado adaptador que solo llama casos de uso.
6. **Fase 5 — Reescribir capa Telegram:** dividir `bot.py` en `telegram/handlers/*`, dejando el archivo original como referencia hasta validar paridad completa vía los tests existentes + nuevos tests por capa.
7. **Fase 6 — Retirar `bot.py` monolítico** y `config_flow.py`/`variables_flow.py` legacy una vez que `telegram/handlers/` cubra el 100% del comportamiento verificado.

Cada fase debe mantener los tests de `tests/` (adaptados) en verde antes de avanzar a la siguiente.

---
