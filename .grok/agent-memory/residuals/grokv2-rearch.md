# Residuales — pool grokv2-rearch

Pool: grokv2-rearch · Fuente: ítems del pipeline. Clasificación §5b.
**Pool cerrado: 2026-09-04** (review-loop 0-issues Round 3, HEAD 2f7ab39, suite 513 passed).
Estado final por residual en el bloque "Estado al cierre del pool" al pie.

## R1 — grok: cambios pre-existentes sin commitear
- Origen: gsd-executor item1 (baseline detectado al arrancar y en cada gate).
- Clase: out-of-scope (documentar). Archivos: /home/ubuntu/repos/grok/sources/6181290784.jpg (M),
  /home/ubuntu/repos/grok/variables_packages/hot.json y sexy.json (untracked). Posible trabajo en curso de la dueña.
- Acción: documentar. NO tocar grok.

## R2 — DeprecationWarnings pytest-asyncio 0.26 bajo Python 3.14
- Origen: gsd-executor item1 (tooling, afecta a todo el pool).
- Clase: out-of-scope (documentar). Cosmético, suite verde.
- Acción: documentar; posible follow-up tooling del pool (downgrade/ajuste pytest-asyncio) → deferred.

## R3 — anonimizar tests/unit/domain/test_user_config.py (ítem 1)
- Origen: arch-enforcer item3 (O5).
- Clase: in-scope-followup del pool (limpieza/privacidad, test-only).
- Detalle: contiene ID real de Telegram 6181290784 y path absoluto /home/ubuntu/repos/grok/sources/6181290784.jpg (viola regla de fixtures anonimizados del pool).
- Acción: fix en el review-loop de cierre (anonimizar con usuario 111111111 y paths dummy preservando forma).
- **Confirmado (ítem 6):** sigue abierto para el review-loop de cierre del pool (el ítem 6 no
  toca tests existentes).
- **Resuelto:** d6f55fe (review-loop Round 1, C3) — anonimizado: user `6181290784` →
  `111111111` y path absoluto `/home/ubuntu/repos/grok/sources/6181290784.jpg` →
  `/var/tmp/grok-fixtures/sources/111111111.jpg`, assert ajustado a la forma preservada.
  El ID real queda mencionado solo en este registry (registro intencional del hallazgo) y en
  artefactos de proceso untracked (`.planning/*`, `.grok/agent-memory/*`) que no se commitean.

## R4 — Item 5 D8: flujos grok degradados (capa telegram)
- Origen: gsd-executor item5 (Task 4; degradación D8 en handlers con mensaje user-safe, sin implementación).
- Clase: in-scope-followup (cierre item 6 / review-loop). Cada uno necesita use case o dato de providers antes de cablearse.
- Detalle (features de grok que en grokV2 degradan con mensaje, NO silencioso):
  1. Face Swap (modo `faceswap`; requiere pipeline de swap + `/cambiar_source`).
  2. Álbumes entrantes / media groups (recibir varias fotos de una).
  3. integrate_ref (`/s` foto + caption con referencia).
  4. Long-prompt collection (caption > 1024 en foto sin caption limpio).
  5. `/cambiar_source` (configurar cara fuente de Face Swap).
  6. `/cambiar_referencia` (referencia de estilo/integración).
  7. `/estado` (estado de un job/cola por mensaje).
  8. Regen de integración (regenerar un resultado de integrate_ref).
  9. Crear paquete de variables pegando JSON (`/listas` → “➕ Crear paquete”): degrada por layering §5.2 (handlers sin parseo de JSON) aunque el resto del flujo de paquetes opera sobre payloads persistidos.
- Archivos: `src/grokbot/telegram/handlers/generation.py` (D8_CMD_MSG + degradaciones), `src/grokbot/telegram/handlers/listas_cmd.py` (_PACK_NEW_D8), `src/grokbot/telegram/handlers/start.py` (notice faceswap).
- Acción: registrar como follow-ups del pool; NO expandir el PLAN en silencio.
- **Nota ítem 6:** El entrypoint (ítem 6) registra estos comandos como degradados vía
  `register_all`; NO los habilita (fuera de scope). `/estado` tiene backing parcial
  (`JobManager.active_jobs`) pero requiere handler nuevo → queda para el review-loop de cierre.
- **Estado al cierre (2026-09-04):** diferido — el review-loop NO implementó los flujos
  (cerró solo la consistencia del help, C10). Las degradaciones D8 quedaron verificadas
  user-safe en el review ("Verificados sin hallazgo: R4 degradación user-safe"). Los 9 flujos
  completos siguen como follow-up del pool (requieren use case o dato de providers antes de cablearse).
- **Resuelto (wave-2/R4, 2026-09-05):** los 9 flujos quedaron habilitados con parity de copy
  grok en 3 ítems (pool wave-2/R4; suite 521→**653 passed**, HEAD `841df64`):
  - Item 1 — utilidades (`fa2d1e0`→`681ba13`): pack JSON `/listas`, long-prompt >1020,
    álbumes/media groups, `/estado` tarjeta (R4 #9, #4, #2, #7).
  - Item 2 — Face Swap (`db8a43b`→`3d0dc37`): `/cambiar_source` + modo faceswap + swap
    Replicate 2-imágenes con confirm single/batch (R4 #1 y #5).
  - Item 3 — edición `/s` (`fd8dd43`→`841df64`): `/cambiar_referencia`, `/s`
    single/álbum/long-prompt/regen integrate (R4 #3, #6, #8).
  `D8_COMMANDS == ()`; sin constantes D8_* ni `_cmd_unavailable` en `src/`+`tests/`. Detalle
  y residuales documentados del pool en la sección "wave-2/R4" al pie y en
  `docs/PRODUCT_STATUS.md` §2/§3.

## R5 — Item 5 M1 (arch): cancel en refine de batch no suprime la refinada en vuelo
- Origen: arch-enforcer item5 (M1). `stream_presenter.present_batch` llama `run_refine_flow(... cancel_event=None)` (:336) aun con job real; un cancel del job durante el refine en batch (post-yes) no suprime la refinada en vuelo, a diferencia del single-image (:167).
- Clase: in-scope-followup (ítem 5). Fix: pasar el cancel_event del job en present_batch, o confirmar paridad grok y documentar. A resolver en fix round / review-loop.
- Archivos: `src/grokbot/telegram/stream_presenter.py`.
- **Resuelto:** d79edde — `present_batch` recibe `job_manager` (inyectado por handlers/variables_cmd) y pasa el `cancel_event` del job del batch a `run_refine_flow` (paridad single-image). Suite 487 passed.

## R6 — Item 5 M2 (arch): get_file_bytes sin try/except user-safe
- Origen: arch-enforcer item5 (M2). `deps.gateway.get_file_bytes` en `handlers/generation.py` y `variables_cmd.py` sin manejo user-safe: file_id expirado lanza `TelegramBadRequest` crudo al usuario.
- Clase: in-scope-followup (ítem 5 / robustez ítem 6). Fix: envolver en error user-safe (mensaje degradado, log sin file_id).
- Archivos: `src/grokbot/telegram/handlers/generation.py`, `src/grokbot/telegram/handlers/variables_cmd.py`.
- **Resuelto:** d79edde — excepción neutral `MediaFetchError` (ports), `AiogramGateway` traduce `TelegramBadRequest`, handlers degradan con `SOURCE_MEDIA_UNAVAILABLE_MSG` sin exponer el file_id. Suite 487 passed.

## R7 — Import transitivo de transport al importar telegram/ (lazy re-exports)
- Origen: arch-enforcer item4 (M2) — pendiente de registro; arch-enforcer item5 (M3) confirma que sigue sin registrarse.
- Clase: deferred (pool, cierre item 6/review-loop). `providers/__init__.py` y `repositories/__init__.py` re-exportan eager todos los concretos → `import grokbot.telegram.*` (y application) carga transitivamente xai/kie/replicate/comfyui-ssh + JSON repos. Sin I/O/env en import-time (settings NO se carga), no rompe hoy. Lazy re-exports de ambos `__init__` en ensamblaje (item 6).
- Archivos: `src/grokbot/providers/__init__.py`, `src/grokbot/repositories/__init__.py`.
- **Resuelto:** e74dd59 — lazy re-exports PEP 562 en `providers/__init__` y
  `repositories/__init__` (ítem 6). `__all__` idéntico; probe (test_lazy_reexports) en
  subproceso: `import grokbot.telegram.handlers` ya NO carga xai/kie/replicate/comfyui+ssh
  ni los 3 JSON repos, y la API pública sigue resolviendo.

## O-series ítem 6 (arch) — observaciones del arch-enforcer item6 resueltas en review-loop
- Origen: gsd-arch-enforcer item6 (auditoría de ensamblaje). Registro obligatorio (C11).
- O1 — `run()` no capturaba errores fatales del polling (main.py:248-252). **Resuelto:** 3fd3167 (C6)
  — handler que loguea `type(exc).__name__` (C3-safe, sin secretos), imprime aviso a stderr y
  retorna 1. Test: `test_run_polling_error_fatal_loguea_tipo_y_sale_1`.
- O2 — `@dp.errors` global del dispatcher (TelegramBadRequest 409 etc.). **Resuelto en ítem 6** —
  cubierto por test del guardian (error handler registrado + test en test_main).
- O3 — SPEC §5.1 drift sobre `shared/errors.py` (lo describía como jerarquía de errores). **Resuelto:**
  691c1e1 (C13) — texto reconciliado con D8 + nota de implementación.
- O4 — docstrings EN/ES mezclados en `providers/__init__.py` y `repositories/__init__.py`.
  **Resuelto:** 438004e (C14) — traducidos a español.

## Quirk pytest 8.4.2 — colección de rutas anidadas
- Origen: gsd-executor item6 / review-loop (hallazgo de tooling, C11).
- Clase: documentar. Detalle: en una MISMA invocación, `pytest <directorio> <ruta-anidada>` colecciona
  solo la ruta más específica (el directorio ya no expande al resto). No es falla de la suite.
- Acción: al correr subset + directorio juntos, invocarlos por separado o como directorios completos.

## R8 — Botón "Regenerar" no scopeado al owner (C5, review-loop Round 1)
- Origen: review 3e662ee1 C5. Ref por chat_id:message_id sin owner_uid; en modo edit un tercero
  en grupo puede inducir a reprocesar el source_file_id ajeno vía regen.
- Clase: in-scope-followup (diferido). NO fix en Round 1.
- Por qué wontfix ahora: paridad grok — el regen original no scopea por owner; el job corre bajo el
  user que clickea (allowlist-gated en grupos) y no escala privilegios ni costo. Scopear exige
  threadear owner_uid por el path de guardado de refs del sender (varios call sites).
- Acción sugerida: en un follow-up, persistir `owner_uid` en `generation_refs` y validarlo en el
  callback `regen` (misma mecánica que C4 en PendingPrompts).
- Archivos: `src/grokbot/telegram/sender.py`, `src/grokbot/telegram/handlers/generation.py` (regen).
- **Resuelto (wave hardening, 2026-09-04):** `c25c1fe` — ``GenerationRefsRepository.save`` gana
  ``owner_uid`` (campo top-level, nunca dentro del ``regen`` opaco) thread-eado
  handler→presenter→sender (5 call sites de ``save``) con fallback fail-closed a
  ``regen_context["user_id"]``; ``handle_regenerate`` valida el dueño igual que C4
  ("Esta regeneración pertenece a otro usuario."). Refs legacy sin ``owner_uid`` no bloquean.
  Suite 521 passed.

## R9 — Bot abierto por default + sin rate limiting + estado sin TTL (C8, review-loop Round 1)
- Origen: review 3e662ee1 C8. `sessions.json` crece con cada user nuevo; PendingPrompts sin
  eviction; abuso de generación paga.
- Clase: out-of-scope (diferido). NO fix en Round 1.
- Por qué wontfix ahora: paridad grok — grok no tiene rate limit ni TTL de sesión; la allowlist
  vacía (bot abierto) es una decisión de settings del deploy (gate de admisión por
  `allowed_telegram_ids`); PendingPrompts está acotado a una entrada por user + TTL implícito del
  refine confirm timeout.
- Acción sugerida: follow-up de hardening (deploy real): documentar configuración de allowlist,
  evaluar rate limit por user y expiración de entradas de sesión.
- Archivos: `src/grokbot/settings.py`, `src/grokbot/main.py`, `src/grokbot/telegram/deps.py`.
- **Resuelto por decisión de producto (wave hardening, 2026-09-04):** bot single-owner en SOLO
  chat privado → sin rate limit ni TTL (R9 declarado en 2bbfdd3/5900809/5e9e88b):
  - `2bbfdd3` — quitado el tope de 3 procesos activos (`MAX_ACTIVE_JOBS_PER_USER`, evento
    `JobsFull` y su rama en `present_batch`; `JobManager.start` SIEMPRE registra).
  - `5900809` — eliminada la cuota horaria de video muerta (Protocol + JSON repo + key legacy
    `video_hourly_timestamps`, que se purga al 1er load de archivos viejos).
  - `5e9e88b` — gate GLOBAL de SOLO chat privado (`PrivateChatOnlyMiddleware` en message y
    callback) + aviso de allowlist abierta en el boot.
  - **Residual que queda:** la IDENTIDAD sigue abierta hasta que el owner fije
    `ALLOWED_TELEGRAM_IDS=<su ID numérico>` (aviso de boot lo recuerda; el gate de chat privado
    no reemplaza la allowlist).

## R10 — Video remoto rechazado publica URL firmada + rama >50MB muerta (C9a/b, review-loop Round 1)
- Origen: review 3e662ee1 C9a/b. (a) El fallback de `send_video` rechazado edita el status con la
  URL firmada cruda (visible en grupos). (b) Rama ">50MB → fallback de texto" muerta: downloader y
  sender usan la MISMA constante (el downloader ya corta en 50MB).
- Clase: in-scope-followup (diferido). NO fix en Round 1 (C9c — video local con TelegramBadRequest
  — SÍ se fixeó: 98350a7).
- Por qué wontfix (a): paridad grok — la URL es el único camino de recuperación del video; el
  warning (`SENSITIVE_DOWNLOAD_WARNING`) ya avisa que el link es privado/temporal.
- Por qué wontfix (b): rama defensiva detrás de un `MediaDownloader` inyectado; en producción el
  downloader capa con la misma constante → invariante alcanzable solo si un downloader futuro no
  capa; mantener como red de seguridad.
- Acción sugerida: follow-up — mover el tope de 50MB a configuración única compartida o evaluar
  ocultar la URL tras un comando de descarga autenticado.
- Archivos: `src/grokbot/telegram/sender.py`, `src/grokbot/telegram/downloader.py`.
- **Resuelto (wave hardening, 2026-09-04):** `096dd0d` — tope único `MAX_MEDIA_BYTES = 50MB` en
  `telegram/media.py` como fuente única de verdad (downloader + sender lo importan; sin
  constantes duplicadas). La URL firmada SÍ se muestra como camino de recuperación: decisión de
  producto del owner (bot privado single-owner, nadie más en grupo) — se conserva la
  degradación user-safe cuando el video local es rechazado por Telegram.

---

## Estado al cierre del pool (2026-09-04)

Reconciliación final contra el merged review `/tmp/grok-hardener-review-3e662ee1.md`
(Round 3 = 0 open) y verificación en disco (HEAD `2f7ab39`, suite 513 passed). Sin inventar resoluciones.

| Residual | Clase | Estado final al cierre |
|---|---|---|
| R1 — grok cambios pre-existentes sin commitear | out-of-scope | Documentado. Baseline grok verificado intacto en disco (solo `sources/6181290784.jpg` M + `variables_packages/{hot,sexy}.json` untracked, pre-existentes). |
| R2 — DeprecationWarnings pytest-asyncio 0.26 / Py 3.14 | out-of-scope | Documentado (cosmético; 2188 warnings en la corrida de cierre). Deferred tooling. |
| R3 — anonimizar test_user_config | in-scope-followup | **Resuelto** — d6f55fe (C3). |
| R4 — Item 5 D8: 9 flujos grok degradados (capa telegram) | in-scope-followup | **Diferido** con acción sugerida (follow-up del pool). Degradaciones user-safe verificadas en review (sin hallazgo). |
| R5 — cancel en refine de batch no suprime la refinada en vuelo | in-scope-followup | **Resuelto** — d79edde. |
| R6 — get_file_bytes sin try/except user-safe | in-scope-followup | **Resuelto** — d79edde. |
| R7 — import transitivo de transport (lazy re-exports) | deferred | **Resuelto** — e74dd59 (item 6). |
| O1–O4 (O-series ítem 6) | observaciones arch | **Resueltos** — O1→3fd3167, O2→test guardian ítem 6, O3→691c1e1, O4→438004e. |
| R8 — Botón "Regenerar" no scopeado al owner (C5) | in-scope-followup (diferido) | **Diferido** con acción sugerida (persistir `owner_uid` en `generation_refs` + validar en `regen`). |
| R9 — Bot abierto + sin rate limiting + estado sin TTL (C8) | out-of-scope (diferido) | **Diferido** con acción sugerida (hardening de deploy real). |
| R10 — URL firmada cruda + rama >50MB muerta (C9a/b) | in-scope-followup (diferido) | **Diferido** con acción sugerida (config única del tope / comando de descarga autenticado). |
| Quirk pytest 8.4.2 — rutas anidadas | out-of-scope (tooling) | Documentado con workaround (no invocar args anidados juntos). |

Nota: el cierre del pool no abre residuales nuevos (Round 3 plan: "sin residual sin registrar").
El review-loop quedó en 0 open; los diferidos R4/R8/R9/R10 son follow-ups deliberados con paridad grok.

---

## Actualización post-cierre — wave de hardening R8/R9/R10 (2026-09-04)

El owner priorizó cerrar R8/R9/R10 sobre el pool ya cerrado (comunicación producto, en español).
Decisiones de producto del owner: (1) bot privado single-owner → máximo hardening SIN límites de
uso; (2) R10: la URL firmada SÍ se muestra (único camino de recuperación); (3) SOLO chat privado.
R4 (9 flujos D8) queda diferido a una wave posterior. Verificación en disco: HEAD `5e9e88b`,
suite **519 passed**.

| Residual | Estado final tras la wave |
|---|---|
| R8 — Regen no scopeado al owner (C5) | **Resuelto** — `c25c1fe` (owner_uid top-level + validación en `handle_regenerate`). Suite 521. |
| R9 — Bot abierto + sin rate limit + sin TTL (C8) | **Cerrado por decisión** — `2bbfdd3` (sin tope de concurrencia), `5900809` (sin cuota horaria), `5e9e88b` (gate SOLO chat privado global + aviso de allowlist abierta). Sin rate limit/TTL queda POR DECISIÓN (single-owner). **Residual:** fijar `ALLOWED_TELEGRAM_IDS` con el ID del owner (config de deploy; el boot lo avisa). |
| R10 — URL firmada cruda + rama >50MB (C9a/b) | **Resuelto** — `096dd0d` (tope único `MAX_MEDIA_BYTES`). URL conservada por decisión de producto. |
| R4 — 9 flujos D8 degradados | **Diferido (wave 2)** — degradaciones user-safe activas; flujos completos requieren use cases/datos. |
| Smoke live con infraestructura real | **Completado** — 2026-09-04 con credenciales de grok v1; hallazgo corregido (panel /config, abajo) y validación final del owner **todo ok** (sesión 36 updates, 0 errores). **Deploy decidido**: grokV2 queda como bot permanente vía `grok-bot.service` (abajo). |

---

## Actualización post-cierre — smoke live (2026-09-04, infraestructura real)

El owner arrancó el smoke live con las credenciales REALES de grok v1 (`.env` copiado a
grokV2, misma máquina; servicio v1 `grok-bot.service` estaba detenido → sin conflicto de
polling). El smoke destapó un bug que **519 tests offline no podían ver**: los fakes de
telegram fabrican el mensaje del panel con `from_user` = el dueño, pero en Telegram real un
mensaje enviado por el bot lleva `from_user` = el BOT.

- **Hallazgo:** todo el panel de `/config` (modelo/proveedor/variante/video/ComfyUI) se
  re-renderizaba leyendo `deps.sessions.get_config(target.from_user.id)` donde `target` es el
  mensaje del bot → la pantalla siempre mostraba la config DEFAULT del bot (Grok Imagine /
  Kie.ai / Alta calidad) sin importar los taps: "Seedream 5.0" volvía a Grok Imagine, Face
  Swap igual, video/ComfyUI "se quedaban fijados", y los taps parecían no-op. La generación
  SÍ usaba el modelo elegido (los handlers de generation leen `message.from_user` del
  mensaje del dueño, correcto); solo el panel estaba mal. Efecto colateral: `sessions.json`
  acumulaba un registro fantasma keyed por el id del propio bot (7296782314), creado por esas
  lecturas con `get_config(bot_id)`.
- **Fix:** `742b566` — los showers de `/config` reciben `uid` explícito (el del user que
  configura: `callback.from_user.id` / `message.from_user.id`) y leen `get_config(uid)`.
  Regresión añadida (`test_config_cmd.py`): el mensaje del panel se construye con
  `from_user` = el bot y se verifica que el cambio se guarda y refleja en la config del
  dueño. Se purgó el registro fantasma del bot de `sessions.json` (backup local). Suite
  **521 passed**.
- **Validación final (misma sesión, panel ya corregido):** el owner probó TODO el recorrido
  del smoke (§4.3: proveedor xAI, Replicate/Seedream, edición por foto, video, refine ComfyUI,
  `/variables N`, etc.) y reportó **"todo ok"**. Sesión de 36 updates manejados (18:06→18:13
  UTC) con **0 errores/avisos** en el log. R4 (9 flujos D8) sigue diferido a wave 2.
- **Deploy permanente (decisión del owner, 2026-09-04):** grokV2 queda como bot permanente,
  administrado por el shell interactivo `grokbot` (`/home/ubuntu/bin/grokbot`) sobre la unidad
  systemd `grok-bot.service`. La unidad se repunteó a grokV2 (`WorkingDirectory` + `ExecStart`
  = `.venv/bin/grokbot`; backup de la v1 en `grok-bot.service.bak-v1`), `PID_PATTERN` del
  wrapper apunta a grokV2, servicio `enable`+`start` con lingering ya activo (arranca al boot
  sin login). `ALLOWED_TELEGRAM_IDS` ya viene del `.env` de v1 (allowlist activa en el boot);
  `data_dir=/home/ubuntu/repos/grokV2/data` (WorkingDirectory del unit; sin `GROK_DATA_DIR` en
  `.env`, el default `./data` relativo al repo es el correcto). v1 `grok/bot.py` queda
  decommissioned (unidad original respaldada).

---

## Actualización post-cierre — wave-2/R4 (2026-09-05): R4 resuelto

Pool wave-2/R4 (3 ítems, suite 521→**653 passed**, HEAD `841df64`): los 9 flujos que
degradaban D8 operan con parity de copy grok y `D8_COMMANDS == ()`. Reconciliación contra
los SUMMARYs de los 3 ítems y los reviews (0 issues tras fix rounds). Verificación en
disco: `D8_*`/`_cmd_unavailable`/`_PACK_NEW_D8` ausentes de `src/`+`tests/`;
`_common.py` `D8_COMMANDS == ()`; umbral long-prompt `TELEGRAM_CAPTION_COLLECT_THRESHOLD
= 1020`.

| Flujo R4 | Item wave-2 | Rango/commit | Estado |
|---|---|---|---|
| Face Swap + `/cambiar_source` | Item 2 — Face Swap completo | `db8a43b`→`3d0dc37` | **Resuelto** |
| Álbumes / media groups | Item 1 — utilidades | `fa2d1e0`→`681ba13` | **Resuelto** |
| integrate_ref `/s` | Item 3 — edición con referencia | `fd8dd43`→`841df64` | **Resuelto** |
| Long-prompt collection | Item 1 | `375e670` (Task 2) | **Resuelto** |
| `/estado` (tarjeta de configuración) | Item 1 | `8e62d4e` (Task 4) | **Resuelto** |
| `/cambiar_referencia` | Item 3 | `2d00312` (Task 3) | **Resuelto** |
| Regen de integración | Item 3 | `2d00312`/`c46dcef` | **Resuelto** |
| Crear paquete pegando JSON | Item 1 | `fa2d1e0` (Task 1) | **Resuelto** |
| Registro D8 (tupla/comandos) | retiro total D8 | `2d00312` (Item 3, Task 3) | **Resuelto** — `D8_COMMANDS == ()` |

### Nuevos residuales del pool wave-2 (documentados only; ninguno bloquea)

- **out-of-scope (test gap, Item 1):** el completado **multi-file** de long-prompt
  (`_complete_long_prompt_collection` vía texto sobre un álbum → `_process_album_edit`) no
  tiene test directo; quedan cubiertas la ruta single-file y el defer álbum→texto. Acción
  sugerida (deferred): test 0-mock del tramo multi-file.
- **out-of-scope (Item 1, parity grok):** `estado_card` no cubre la rama ComfyUI "Listo…"
  con detalle de modelo/lora; grok v1 tampoco la expone en `/estado` (solo "Listo para
  generar/editar imagenes.") → se dejó la rama genérica del PLAN. Documentar.
- **out-of-scope (Item 3, observación arch):** los labels del álbum **no-integrate** de
  grokV2 siguen sin sufijo `({backend})` (grok álbum sí lo incluye) — pre-existente del
  Item 2 (A2, documentado en el docstring de `_process_album_edit`), NO regresionado por
  Item 3. Posible follow-up de parity: añadir el sufijo a la rama no-integrate.
- **out-of-scope (Item 3, observación arch):** copy inventado "No se pudo recuperar la
  imagen original para regenerar." en el regen integrate con `source_file_id` ausente del
  ctx (grok no cubre ese caso con ese copy). User-safe, no expone ids. Documentar.

Nota: el cierre del pool wave-2 no abre otros residuales. Los aceptados/wontfix de los
reviews (desviación preflight `/s` antes de `validate_prompt`, etc.) son desviaciones
documentadas con Response y comentario en código/SUMMARY — no diferidos con follow-up.
