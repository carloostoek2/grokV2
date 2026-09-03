# Impact Analysis: Item 3 — Extraer repositorios (Fase 3 SPEC) — grokV2-rearch

**Date:** 2026-09-03
**Change:** Crear `src/grokbot/repositories/` (base + JSON backends de sesiones/variables/refs) en grokV2, leyendo/escribiendo los MISMO formatos JSON que hoy usa grok (`sessions.py`, `variables_store.py`, gen-refs en `sessions.py`). Contratos detrás de Protocol para swap futuro SQLite/Redis. Sin migración manual de datos (SPEC §5.6/§6).
**Analysis only** — no implementación. Target: `/home/ubuntu/repos/grokV2` (greenfield). `grok/` read-only fuente de verdad @81832a5.

## Executive Summary

El ítem 3 crea la capa de infraestructura de persistencia que consumirán `application/` (ítem 4), `telegram/` (ítem 5) y `main.py`/DI (ítem 6). Origen semántico: `grok/sessions.py` (497 líneas) y `grok/variables_store.py` (579 líneas) — nunca copiar texto, transcribir comportamiento. En grokV2 NO hay consumidores aún (grep `repositories` = 0); los consumidores inmediatos son los tests. Riesgo global: **MEDIO-ALTO**, concentrado en paridad de formato exacto y escritura NO destructiva de claves que el dominio no modela (`video_hourly_timestamps`, `_package`, claves desconocidas, `regen` opaco). No hay breaking local porque nada importa los módulos todavía; el costo real es fijar el contrato de repos con la granularidad correcta para que el ítem 4 no tenga que re-diseñarlo.

Sistemas sensibles: los JSON reales de grok contienen IDs de Telegram, prompts personales y `file_id` de Telegram (generation_refs.json = 247 records, 441 KB; sessions.json usuario real). NO copiarlos verbatim a fixtures/tests: anonimizar preservando forma. Tokens NO viven en estos archivos (verificado).

Veredicto: **LISTO para planear** con ~9 decisiones (D1-D9). Ninguna bloqueante de arquitectura; D1 (superficie de paths en Settings/constructores) y D3 (¿packages dentro de VariablesRepository ahora?) deben cerrarse antes de escribir `json_variables_repo.py`/tests.

## Consumers / Call Sites Map

### Futuros (ítems 4-6; NO existen hoy en grokV2)
| Consumidor futuro | Repo que usará | Operaciones |
|---|---|---|
| `application/manage_config.py` (ítem 4, /config) | SessionRepository | get_config → mutate UserConfig → save_config |
| `application/run_variable_batch.py` (ítem 4) | VariablesRepository + GenerationRefs? | get_lists/get_template/get_blacklist (combos); no prompt-building (domain) |
| `application/manage_lists.py` (ítem 4, /listas) | VariablesRepository (incl. packages si D3) | CRUD listas/template/blacklist + save/activate/delete package |
| `application/refine_flow.py` / handlers jobs (ítem 4-5) | GenerationRefsRepository | get (resolve reply-kie-ref/regen), save (post-answer) |
| `telegram/handlers/*` (ítem 5) | vía application, nunca directo (regla dura §5.2) | — |
| `main.py` DI (ítem 6) | construye repos con paths | — |
| **Tests (consumidor inmediato)** | los 3 repos | fixtures tmp_path |

### Call sites en grok (referencia de capacidad requerida)
- `sessions.py` API completa usada en `bot.py`: `get_session`(bot 567, 829), `get_video_config`(1125,1170,1247,...), `set_video_config`(1135), `set_state`(1436), `set_source`(3690), `set_integrate_ref`(3707), `set_model`/`set_grok_imagine_config`/`get_comfyui_config`/`set_comfyui_config` vía `_CONFIG_DEPS` (5435-5443).
- Generation refs: `sessions.get_generation_ref` (bot 817 `_resolve_reply_kie_ref`, 1311 `handle_regenerate_image`), `sessions.save_generation_ref` (bot 4126 comfyui img, 4178 comfyui video, 4300 comfyui album, 5020/5060 kie/replicate img). Builder `_build_image_regen_context` (bot 780-815).
- `variables_store.py` en `variables_flow.py` (panel /listas: get_lists/template/CRUD/blacklist/packages) y en `bot.py` (2164-2407 batch engine: get_lists/random_combination/combo_key/blacklist_add/build_prompt_shuffled; 2716 build_prompt_inline).

### Qué NO pertenece al repo (ya en domain o será application ítem 4) — evitar duplicar
- Prompt-building/rendering de `variables_store.py` (252-353): `build_prompt*`, `template_fields`, `combo_key`, `combo_label`, `_clean_placeholder_gaps`, `_render_positional`, `_needs_fallback` → **ya implementado en `domain/variables.py`** (PromptTemplate, normalize_items, combo_key, combo_label, clean_gaps, list_for_placeholder).
- `random_combination` (374-417), `build_prompt_shuffled` (340-353), `MAX_COMBO_ATTEMPTS` → motor aleatorio de ítem 4 (`RunVariableBatchUseCase` con RNG inyectable); consume repo.get_lists/get_template/get_blacklist + domain.
- FSM/estado efímero (`pending_prompt`, refines en memoria) → ítem 4-5.
- `sessions.FsState.AWAITING_SOURCE` / máquina de estados legacy → ítem 5 (domain solo modela `state` como str, default "IDLE").
- Setters finos de grok (`set_model`, `set_video_config`, `set_grok_imagine_config`, `set_comfyui_config`, `set_source`, `set_integrate_ref`, `set_state`) → **casos de uso de ítem 4** que mutan un `UserConfig` en memoria y llaman `repo.save_config`. NO replicarlos como métodos de repo (la granularidad fina vive en application).
- `sources_dir` (descarga/guardado de imágenes fuente) → NO es parte del ítem 3 (scope lo omite); ítems 4-6.

## Formato exacto de cada JSON (shape) — paridad obligatoria

### sessions.json
Top: `{"<str(user_id)>": <record>, ...}`. Record = `sessions._default_session_record` (sessions.py:46-66):
`source_path: None · integrate_ref_path: None · state: "IDLE" · model: "grok" · grok_imagine_provider: "kie" · grok_imagine_variant: "quality" · comfyui_model: "krea2" · comfyui_lora: "none" · comfyui_refine: "1" (str) · video_duration: 5 · video_aspect_ratio: "16:9" · video_resolution: "720p" · video_model: "grok-imagine-video" · video_mode: "normal" · video_hourly_timestamps: [] (list[float] epoch)`.
Escritura: `json.dump(data, f, indent=2)` (sin `ensure_ascii=False`). Legacy: clave `grok_provider` → migrar a `grok_imagine_provider` y dropear al escribir. Claves desconocidas/extra → preservar. `video_hourly_timestamps` es repo-owned (no está en `UserConfig.to_record`).
Dump flags de sesión: `indent=2`, no sort, ensure_ascii default True (espejo grok).

### variables_lists.json
Top: `{"lists": {<campo>: [items...]}, "template": str, "blacklist": [[...], ...], "_package": str | ausente, <claves extra: preservar>}`.
- File nuevo/ausente/corrupto → seed: `lists` = LIST_NAMES ("poses","angles","actions") con **deep-copy** de `DEFAULT_LISTS` (10/5/10 items, transcribir de variables_store.py:38-70), `template` = DEFAULT_TEMPLATE "{pose}, {angle}, {action}", `blacklist` = [].
- **Guard de seed**: solo cuando `lists` falta o queda vacío (`_ensure_full` variables_store.py:117-138). Archivo activo de paquete (`bodies/hands/angles` + `_package`) NO debe contaminarse con poses/angles/actions.
- `get_lists()` normaliza items (str no-vacío, trim). Archivo self-describing: cualquier campo presente se combina.
- Escritura: `json.dump(data, f, indent=2, ensure_ascii=False)`.
- Package files en `packages_dir/<slug>.json`: `{"lists": {...}, "template": str}` (sin blacklist/_package). Import normaliza aceptando top `"fields"` como alias de `"lists"` (variables_store.py:497-519).

### generation_refs.json
Top: `{"<chat_id>:<message_id>": <record>, ...}`. Record: `provider` (default "kie"; observados kie/replicate/comfyui) · `kind` ("image"/"video") · `prompt` ("" o truncado 500) · `created_at` float · `kie_task_id` (present iff tarea kie) · `kie_index` int clamp 0..5 (present iff kie_task_id) · `regen` dict opaco (present iff regen context). Todos los 247 records vivos llevan `regen`; shapes de `regen` observados (5 key-sets): base `{mode, model_key, prompt, provider, user_id}`; +`imagine_provider/imagine_variant`; +`source_file_id`; +`kie_source_ref {task_id, index}`; +`integrate_mode`. Cualquier clave extra en record o regen → preservar intacta.
- Guard: si `not kie_task_id and not regen` → no-op (sessions.py:470-471).
- TTL prune 14 días (sessions.py:43, 442-455): descarta no-dict, `created_at` no-float/vencidos; corre en save y get. `get` persiste el prune solo si el key pedido existe.
- Escritura: `json.dump(data, f, indent=2)` (ensure_ascii default True → en el archivo real los no-ASCII aparecen escapados).

## Riesgos

| Sev | Riesgo | Detalle | Mitigación |
|---|---|---|---|
| **CRITICAL** | R1 Paridad de shape/format | Cualquier desvío en claves/defaults/dump-flags rompe leer los JSON reales "tal cual" (DoD §6, CLARIFY #7). | Fixtures "espejo anonimizado" de los 3 archivos reales; tests round-trip load→save→load byte-semántico (json igual). Espejar dump flags: variables `ensure_ascii=False`; sessions/refs default True; indent=2; sin sort_keys. |
| **CRITICAL** | R2 Escritura no destructiva | `UserConfig.to_record()` NO emite `video_hourly_timestamps` ni claves desconocidas; un save naive pisa el record y borra quota/legacy/extra. Igual `_package`/extra en variables y claves extra en refs. | `save_config` hace merge campo a campo sobre el record crudo cargado (update de las claves de `to_record`, preserva el resto). `JsonVariablesRepository` muta sobre el doc cargado preservando top-level extras. Refs guardan records completos sin re-modelar `regen`. Tests explícitos de preservación. |
| **CRITICAL** | R3 Migración legacy al escribir | Record con `grok_provider` legacy y canónico a la vez (O2 arch item1): precedencia canónica al leer; al escribir dropear `grok_provider` (espejo `_ensure_full` pop). | Normalización de escritura en repo (merge canónico); test con ambas claves. |
| **MEDIUM** | R4 Atomicidad/corrupción | grok escribe directo `open("w")` (sessions/refs/variables) sin tmp+rename: crash a mitad de dump → JSON corrupto. Variables tolera JSONDecodeError (→{} seed); sessions/refs NO (propaga). | (D5) Adoptar escritura atómica tmp+`os.replace` en los 3 repos (mismo contenido, infra más robusta). (D4) Decidir semántica de archivo corrupto por repo (espejo exacto vs hardening con backup `.corrupt-<ts>`). No silenciar pérdida de datos. |
| **MEDIUM** | R5 Concurrencia / lost-update | Read-modify-write de a pares. En asyncio single-thread, métodos sync no ceden entre load y save → sin interleave dentro del método. Riesgo real solo multi-proceso/hilo o si un método fuera async con awaits adentro. | (D6) Repos **sync** (espejo grok): transacción atómica dentro del event loop. `variables_store` tiene `threading.Lock` (variables_store.py:32): conservar lock en json_variables_repo para paridad hilo. Lost-update entre dos use-cases que editan el mismo UserConfig = problema de ítem 4 (app), no de repo; `save_config` merge reduce exposición. |
| **MEDIUM** | R6 Seed vs paquete activo | Sembrar defaults cuando el archivo activo es de paquete (bodies/hands/angles) rompe /variables. | Reproducir guard de `_ensure_full`: seed solo si `lists` ausente/vacío. Test con fixture de paquete activo (bodies/hands/angles + `_package`) + uno vacío. |
| **MEDIUM** | R7 Paths por defecto | `Path(__file__).parent / "*.json"` de grok NO aplica a `src/grokbot/repositories/`. Default cwd accidental escribiría basura en el árbol. | (D1) Repos requieren Path explícito en constructor (sin default silencioso a cwd). Settings gana paths opcionales (o `data_dir`); main (ítem 6) los inyecta. Tests siempre `tmp_path`. |
| **LOW** | R8 Fuga de datos en fixtures | JSON reales tienen IDs/prompts/file_ids de Telegram. Copiarlos verbatim a tests = fuga. | Fixtures anonimizados con MISMA forma (ver "Tests a crear"). test-guardian grep de `6181290784`/rutas reales/`AgACAgE` sobre tests. |
| **LOW** | R9 Crecimiento generation_refs.json | 441 KB hoy, TTL 14d lo acota, pero volumen por alta actividad. | Prune en cada save/get (paridad). Sin cap adicional en ítem 3. |
| **LOW** | R10 Scope creep packages | `variables_store.py` packages (462-579) NO están en el file-list explícito del ítem. Si se difieren, `manage_lists` (ítem 4) no tiene repo de paquetes. | (D3) Recomendado: incluir package ops dentro de `VariablesRepository`/`json_variables_repo.py` (mismo archivo, mismo lock/atomicidad; `activate_package` escribe el archivo activo). Alternativa tight: diferir a ítem 4 pero decidir `packages_dir` ahora. |

## Affected Tests (a crear)

Comando exacto (repo grokV2): `.venv/bin/pytest tests/unit tests/integration/repositories -q`. Suite completa: `.venv/bin/pytest tests -q`. Sin red ni Telegram: repos son I/O local a `tmp_path` → carpeta `tests/integration/repositories/`.

Fixtures (anonimizados, MISMA forma que los reales — NO copiar datos):
- `sessions`: usuario `111111111`, `source_path: "/tmp/anon/sources/111.jpg"` (real usa 6181290784 y ruta bajo grok). Variantes: record completo default, record legacy `grok_provider` sin canónico, record con claves extra + `video_hourly_timestamps` no-vacío.
- `variables_lists`: doc paquete-activo `{"lists": {bodies: [...], hands: [...], angles: [...]}, "template": "<JSON literal con placeholders {bodies}…>", "blacklist": [], "_package": "anon"}` + claves extra; doc vacío para seed; `blacklist` con `[["a","b"]]`.
- `generation_refs`: 2-3 records con shapes reales (con kie_task_id+regen; solo regen; `provider: "comfyui"`, `kind: "video"`), prompts dummy (`"prompt de prueba #N"`), `regen` con 2-3 variantes de key-set, `source_file_id: "FAKE_FILE_ID"`.

Tests por archivo:
- `tests/integration/repositories/test_json_session_repo.py`: get_config usuario nuevo → default + archivo creado con record completo incl. `video_hourly_timestamps: []`; carga fixture real-shape → `UserConfig` correcto; `save_config` **preserva** `video_hourly_timestamps` + claves extra + comfyui al mergear; legacy `grok_provider` → canónico y dropeado al escribir; `record_video_hourly_usage` append; `count_video_hourly_usage` poda (now=+7200) y persiste; `count_global_video_hourly_usage` multi-usuario; archivo inexistente → {}; corrupto → semántica D4; round-trip shape.
- `tests/integration/repositories/test_json_variables_repo.py`: seed en archivo nuevo (nombres LIST_NAMES, items DEFAULT_LISTS, template, blacklist, file existe); doc paquete-activo carga SIN sembrar y mutación preserva `_package`/extras; CRUD add/update/delete (dup/blank/unknown list name/out-of-range/noop); set_template (blank → False); blacklist add/get/clear + raw shape `[["a","b"]]`; corrupto → seed (paridad); si D3: save/load/list/activate/delete package, activate → blacklist [] + `_package`, delete activo → False, top `"fields"` aceptado, slugify.
- `tests/integration/repositories/test_generation_refs_repo.py`: save con kie_task_id → shape exacta; save sin task y sin regen → no-op; save solo regen → record sin kie_task_id; truncado prompt a 500; get existente/vencido/missing; prune persiste solo si key presente; record con claves extra preservadas; corrupto/no-dict → {} (paridad `_load_generation_refs`); TTL 14d.

Fakes/mocks: `tmp_path` (fixture filesystem), `monkeypatch` para reloj (`now=` param ya expuesto). Sin mocks de red/Telegram. Cero parches de métodos bajo test.

## Files Map

- **Edit (menor):** `src/grokbot/settings.py` + `tests/conftest.py` (tupla `SETTINGS_ENV_VARS`) + `.env.example` SOLO si D1 mete path/data-dir en Settings. `tests/unit/domain/test_user_config.py` NO (sin cambios de dominio salvo que D1 requiera algo).
- **Create:**
  - `src/grokbot/repositories/__init__.py` (re-export API, espejo `domain/__init__.py`)
  - `src/grokbot/repositories/base.py` — Protocol `SessionRepository`, `VariablesRepository`, `GenerationRefsRepository` + constantes de nombres de archivo/seed re-export (DEFAULT_LISTS transcrito aquí o en json_variables_repo).
  - `src/grokbot/repositories/json_session_repo.py`
  - `src/grokbot/repositories/json_variables_repo.py`
  - `src/grokbot/repositories/generation_refs_repo.py`
  - `tests/integration/repositories/test_json_session_repo.py`, `test_json_variables_repo.py`, `test_generation_refs_repo.py`
- **No touch:** `application/`, `telegram/`, `main.py`, `shared/`, `providers/` (ítems 2/4-6); `grok/**` read-only; `src/grokbot/domain/*` salvo decisión D1 (no debería); tests grok.
- **Ojo settings:** item1 D4 difirió paths a este ítem → D1.

## Decisiones para el planner (D's)

- **D1 — Cómo entran los paths (sessions_file/variables_file/packages_dir/generation_refs_file).** Recomendado: repos con `__init__(self, path: Path)` explícito (requerido, sin default cwd); Settings gana 4 campos `Path | None = None` (o un `data_dir` + propiedades derivadas) leídos de env `GROK_DATA_DIR` o rutas explícitas; `main.py` ítem 6 inyecta. `conftest` debe agregar la(s) var(s) a `SETTINGS_ENV_VARS`. NO replicar `Path(__file__).parent` de grok.
- **D2 — Superficie de SessionRepository.** Recomendado (arriba): `get_config/save_config` (UserConfig) + `record/count_video_hourly_usage/count_global_video_hourly_usage`. Confirmar side-effect `get_config` persiste default de usuario nuevo (paridad grok `get_session`/`_get_or_create_full`) — recomendado sí.
- **D3 — Packages en VariablesRepository ahora o ítem 4.** Recomendado ahora (mismo archivo). Si planner quiere ítem tight: diferir package ops, PERO decidir `packages_dir` path ahora y dejar la interfaz sin package methods (se agregan en ítem 4 sin romper — Protocol extensible).
- **D4 — Archivo corrupto por repo.** Espejo grok (variables→seed; sessions/refs→raise JSONDecodeError) vs hardening (rename a `.corrupt-<ts>` + fresh). Recomendado: espejo para variables; para sessions/refs espejo (raise) + test documenta. Hardening opcional diferido.
- **D5 — Atomicidad.** Recomendado: tmp+`os.replace` en los 3 repos (contenido idéntico, infra robusta). Alternativa paridad estricta: `open("w")` directo.
- **D6 — Métodos sync vs async.** Recomendado sync (transacción segura en event loop; app async los llama directo; tests triviales). Si el plan pide async → `asyncio.Lock` por repo alrededor del read-modify-write.
- **D7 — Forma de list_packages().** grok devuelve `dict[str, Path]`; recommend `list[str]` (slugs ordenados) para app-friendly; micro-decisión.
- **D8 — Granularidad get_list/is_valid_list_name.** `is_valid_list_name` mezcla constante LIST_NAMES + self-describing; puede vivir en repo (recomendado) o app.
- **D9 — Dump exacto.** Confirmar flags por archivo (variables `ensure_ascii=False`; sessions/refs default True; indent=2; sin sort_keys) — se fija en R1.

## Ready for chain

Handoff a gsd-planner con scope tight:
- Crear SOLO `src/grokbot/repositories/` (5 módulos: `__init__`, `base`, `json_session_repo`, `json_variables_repo`, `generation_refs_repo`) + `tests/integration/repositories/*`. NO app/telegram/main/shared/providers; NO tocar grok; NO copiar JSON reales a fixtures.
- Resolver D1-D9 antes de escribir código; D1 y D3 condicionan `json_variables_repo`/Settings. DoD de paridad: leer fixture real-shape tal cual + round-trip sin pérdida de claves desconocidas; seed DEFAULT_LISTS solo en archivo vacío; dump flags espejo.
- Siguiente paso: gsd-planner → gsd-executor → arch-enforcer → test-guardian → corrida `.venv/bin/pytest tests -q` (verde) + Commit Gate del ítem.
