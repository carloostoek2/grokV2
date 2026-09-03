# Residuales — pool grokv2-rearch

Pool: grokv2-rearch · Fuente: ítems del pipeline. Clasificación §5b.

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
