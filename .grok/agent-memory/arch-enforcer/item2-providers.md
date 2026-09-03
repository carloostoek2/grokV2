# Arch Audit: item2-providers (capa providers + registry, pool grokv2-rearch)

Fecha: 2026-09-03 · Auditor: arch-enforcer · Fuente: CLARIFY.md (locked), PLAN.md item2 (D1-D8, DoD),
SUMMARY.md, gsd-executor.log, SPEC_REFACTOR.md §3.2/§4.2/§5.1/§5.2/§5.3/§6/§7, código `src/grokbot/providers/**`,
`domain/generation.py`, tests item1+item2, referencia read-only `grok/bot.py` (81832a5).

**Verdict:** PASS WITH NOTES
**Critical violations:** 0 · **Minor:** 3 · **Observations:** 5

## Findings

### Critical (must fix before advance)
Ninguna.

### Minor (non-blocking; corregir antes de que ítems 4-5 consuman el contrato)

- **M1 — ComfyUI: `model`/`lora` se interpolan en comando shell sin validar ni quote.**
  `src/grokbot/providers/comfyui/provider.py` `_gen_command()` y `refine()` componen
  `MODEL='{cm}' LORA='{cl}' ...` con f-string. Hoy `cm`/`cl` provienen de `request.params`
  con fallback a `domain/user_config` (`VALID_COMFYUI_MODELS`/`VALID_COMFYUI_LORAS`), pero el provider
  no valida el valor final: si en ítem 4/5 `params` se puebla desde texto no confiable (FSM de Telegram,
  prompt injection, config mal saneada), un `model`/`lora` con `'`/`;`/`$()` rompe el single-quote y da
  ejecución arbitraria en la box remota (SSH `root@`, key-based). El box es infraestructura paga y crítica.
  **Fix concreto:** en `_model_lora()` (o antes de componer el cmd) validar
  `cm in VALID_COMFYUI_MODELS and cl in VALID_COMFYUI_LORAS` → `ProviderInputError` si no, o `shlex.quote`
  ambos. Aplicar también en `refine()`. Es defensa en profundidad barata; hoy sin consumidor vivo.

- **M2 — `ComfyUIProvider.generate()` ignora `request.media_type` (seam video-comfyui).**
  El resultado (`GenerationResult.media_type`) se deriva del modelo comfyui (`_is_comfy_video_model(cm)`),
  no del request. `ProviderRegistry.resolve_video()` devuelve `comfyui` para `cfg.model == "comfyui"` sin
  mirar `cfg.comfyui.model`: si el modelo configurado es de imagen (krea2) y el use-case de ítem 4 pide video,
  `generate()` correría una generación de imagen y devolvería `media_type=IMAGE` para un request VIDEO, sin
  error. `supports()` existe para esto, pero ni el registry ni `generate()` lo enforcean.
  **Fix concreto:** en `generate()`, si `request.media_type is MediaType.VIDEO` y `cm not in COMFY_VIDEO_MODELS`
  → `ProviderInputError` (espejo de las precondiciones de foto ya presentes). Alternativa/complemento: que
  `resolve_video` lea `cfg.comfyui.model` y valide capacidad de video. Dejar constancia para ítem 4 (use case
  debe consultar `provider.supports(request)` antes de generar).

- **M3 — Clasificación retryable de 4xx no-auth: hoy caen en `ProviderUnavailableError` (retryable=True).**
  `xai_provider._raise_for_http_status()` y `kie_provider._raise_http_status()`/`_raise_api_code()` mapean
  todo status que no es 401/403/429 (y en kie no-404/422) a `ProviderUnavailableError`, incluyendo un 400
  (payload inválido, terminal). Bajo D3, eso haría que el use-case de ítem 4 reintente condiciones terminales
  (desperdicio en APIs pagas). **Fix concreto:** mapear 400/otros 4xx terminales a
  `ProviderInputError`/`ProviderGenerationError` (`retryable=False`) y reservar `ProviderUnavailableError`
  para 5xx/red/timeout-de-transporte. Verificar contra la semántica real de la API en el review-loop del pool.

### Observations

- **O1 (A7) — `VideoProvider` sin `poll()` en el Protocol: ACEPTABLE.** `base.py` documenta la desviación del
  §5.3 (que es ilustrativo): `generate()` hace submit+poll interno hasta completar (cap 600s), los
  `asyncio.sleep` del poll son puntos de cancelación naturales y la cancelación del job (ítem 4) cancela la
  task asyncio que corre `generate`. Coincide con el comportamiento bloqueante de grok y con A7 del PLAN.
  Nota para ítem 4: si a futuro se quiere un `JobManager` persistente/restartable con jobs de video largos,
  habrá que re-exponer `poll()` (deferred).
- **O2 — `ProviderResolution.provider: object`** en `registry.py` es débilmente tipado. Sugerir
  `ImageProvider | VideoProvider` (Protocol union) para que el type-checker de ítem 4 atrape usos erróneos.
  Según PLAN, no bloquea.
- **O3 — ComfyUI SSH: `pull()` no valida el charset del remote path.** `_pull_and_build_result` scp-ea toda
  línea stdout que empiece `/workspace` sin el regex de `refine()`. La box es confiable (paths generados por
  `gen_comfy.py`); fuera del modelo de amenaza hoy. Si se quiere hardening, aplicar el mismo
  `_validate_refine_remote_path` en generate. Informativo.
- **O4 — Sin logging en providers (por diseño).** R2 se cumple: `user_message` es seguro y los `message`
  técnicos (status HTTP, `exc`) quedan solo en la excepción. Recomendar que el logging centralizado de ítem 6
  nunca exponga `exception.message` como user-facing (loggear a debug).
- **O5 — Proceso: en el log git solo hay UN commit standalone de hardening R2** (`493aa74`, replicate); los
  asserts de xai/kie vinieron en sus commits de provider originales. No es desvío de scope (todo test-only);
  aclaración para el Commit Gate.

## Compliance Checklist

- [x] Capas respetadas: `providers/*` solo importa `domain/*` (+ aiohttp/replicate como transporte en xai/
      replicate/kie); `base.py` = stdlib + domain (sin aiohttp/telegram/settings); ComfyUI subprocess/SCP
      AISLADO en `ssh_client.py` con runner inyectable; `provider.py` no corre ssh directo.
- [x] Aislamiento infraestructura: sin `os.environ`/`settings`/`telegram`/`grok`/`print` en providers
      (grep: solo menciones en docstrings); tokens/config/transporte por constructor.
- [x] Interfaz común/extensibilidad: `ImageProvider`/`VideoProvider` runtime Protocol con
      `available/supports/generate`; providers concretos implementan el contrato; métodos extra
      (`edit_with_reference` xAI, `refine` ComfyUI) FUERA del Protocol (D7). Provider nuevo = módulo +
      registro en registry, sin tocar otros providers ni application (aún no existe).
- [x] Registry puro e inyectado: resuelve por `UserConfig`+`MediaType`; regla video replicate→xai;
      `ProviderNotConfiguredError` user-facing para kie/comfyui vacíos; `ProviderInputError` para
      combinaciones no soportadas.
- [x] Seguridad/R2/R7 (SSRF): tokens nunca en body/URL/log (tests assert en xai ×3, kie ×1 + header, replicate
      ×1); errores user-facing normalizados; única resolución intra-provider = Kie `recordInfo` (propia API)
      con filtro de hosts; ninguna descarga de URL arbitraria en providers (D5).
- [x] Contrato dominio intacto: `domain/generation.py` solo cambio aditivo D1 (`params: dict` último campo,
      default_factory); tests de dominio ítem 1 en verde dentro de la suite (146 passed).
- [x] A7: desviación documentada y aceptable (O1).
- [x] No-touch: no existen `src/grokbot/{application,repositories,telegram,main.py,shared}`;
      `git -C /home/ubuntu/repos/grok status --porcelain` = baseline pre-existente de ítem 1
      (M sources/6181290784.jpg; ?? variables_packages/{hot,sexy}.json), sin cambios atribuibles.
- [x] Scope del PLAN respetado: archivos creados == lista exacta del PLAN (+ commits de hardening R2
      test-only); pyproject deps acotadas (aiohttp/replicate runtime, aioresponses dev); sin cambios en
      settings.py ni domain salvo D1.

## Residuales (§5b) — no DoD

- **aioresponses 0.7.9 × aiohttp 3.14.x**: incompatibilidad (`ClientResponse` exige `stream_writer`),
  mitigada con shim test-only duplicado en `test_xai_provider.py` y `test_kie_provider.py` (subclase +
  monkeypatch de `aioresponses.core.ClientResponse`). No toca producto. Follow-up tooling del pool:
  pin aiohttp a una versión compatible o upgrade aioresponses cuando exista; luego borrar shims.
- **DeprecationWarnings pytest-asyncio 0.26/Py3.14**: cosmético, ya residual de ítem 1 (R2 pool).

## Handoff

Siguiente paso: **test-guardian** (auditar cobertura/mocks de providers; verificar M1/M2 si decide incluir
fixes de hardening en una fix round, o dejarlos como nota para ítems 4-5). Veredicto PASS WITH NOTES con
0 critical → avanza a test-guardian; M1-M3 no bloquean el avance pero conviene corregirlos antes de que
ítems 4-6 consuman `providers/` como contrato estable.
