# Test-Guardian Report: item2-providers (pool grokv2-rearch)

Fecha: 2026-09-03 · Auditor: test-guardian · Suite: `.venv/bin/pytest tests/unit tests/integration/providers -q`

**Verdict: suite protege adecuadamente**

## Coverage Audit

DoD/éxito del PLAN cubierto sin red real, determinista y rápido (163 tests en 0.36s):

| Área | Cobertura | Archivo(s) |
|---|---|---|
| D1 `params` aditivo (default `{}`, posicional estable, aislamiento) | unit | `tests/unit/domain/test_generation.py` |
| base: errores D3 (`retryable`/`user_message` por clase), helpers mime/data-uri/i2v, consts poll, aspect 9:16 | unit | `tests/unit/providers/test_base.py` |
| xAI imagen t2i/i2i/edit_with_reference + video submit+poll done/failed/moderation/timeout + M3 400/500 + retry poll 5xx + create sin request_id | integration aioresponses | `tests/integration/providers/test_xai_provider.py` |
| Replicate grok/seedream/faceswap payloads por modelo + normalización + SDK-exception + passthrough ProviderError | integration fake client + unit helpers | `tests/integration/providers/test_replicate_provider.py`, `test_replicate_normalize.py` |
| Kie upload→createTask→poll (multi-call), KieTaskRef→spicy i2i, D8, video slug/aspect/duration, transients 429, host allowlist, M3 HTTP y api-code | integration aioresponses + unit helpers | `tests/integration/providers/test_kie_provider.py`, `test_kie_helpers.py` |
| ComfyUI ssh_client (runner inyectable, argv/parse/timeout/pull/not-configured) | integration fake runner | `tests/integration/providers/test_comfyui_ssh_client.py` |
| ComfyUI generate t2i/img2img, precondiciones foto, video por modelo, multi-output, refine 2-stage, M1 (validación model/lora no-inyección), M2 (seam video) | integration fake ssh | `tests/integration/providers/test_comfyui_provider.py` |
| Registry resolución imagen/video por `UserConfig`, regla replicate→xai video, disponibilidad, errores | integration providers reales (dummy tokens, no genera) | `tests/integration/providers/test_registry.py` |

Fixes de hardening fijados por tests: **M1** (model/lora fuera del set válido o con `'`/`;`/`$()` → `ProviderInputError` antes de componer cmd; también en refine), **M2** (media VIDEO + modelo de imagen comfyui → `ProviderInputError`, no corrida de imagen silenciosa), **M3** (xai/kie: 400→`ProviderInputError`, 500→`ProviderUnavailableError`, code 400→Input, code 429→RateLimit).

Determinismo/velocidad: polls con `asyncio.sleep` no-opeado (xai/kie), `tmp_path` para tmpdir comfyui, sin red real, sin depender de orden (0.36s). `asyncio_mode=auto`; conftest aísla env de Settings.

## Mock Audit

| Archivo | Mock / patch | Clasificación | Path de negocio protegido | Acción |
|---|---|---|---|---|
| test_xai_provider.py | `aioresponses` HTTP | PERMITIDO (borde HTTP externo) | payload t2i/i2i/edits/video, mapeo status, normalización D4, R2 | ninguna |
| test_xai_provider.py | `monkeypatch asyncio.sleep` no-op | PERMITIDO (reloj/determinismo) | poll estados done/failed/timeout/retry | ninguna |
| test_xai_provider.py | `monkeypatch VIDEO_MAX_POLL_SEC=0` | PERMITIDO (acorta deadline, fuerza rama timeout; no sustituye lógica de estado) | timeout→`ProviderTimeoutError` | ninguna |
| test_xai_provider.py | `Mock()` stream_writer (shim aioresponses×aiohttp) | PERMITIDO (test-infra; residual de tooling documentado) | — | ninguna |
| test_kie_provider.py | `aioresponses`, sleep no-op, `Mock()` shim | PERMITIDO (igual que xai) | upload/create/poll, transients, allowlist, api-code M3 | ninguna |
| test_replicate_provider.py | `FakeClient`/`_FailingClient` inyectado como `client` | PERMITIDO (borde SDK replicate; assert sobre payload/inputs reales del provider, no sobre el mock) | ramas seedream/faceswap/grok, normalización, clasificación de errores | ninguna |
| test_comfyui_ssh_client.py | `FakeRunner` inyectado | PERMITIDO (borde subprocess ssh/scp) | build de argv, parse `/workspace`, timeout, pull | ninguna |
| test_comfyui_provider.py | `FakeSsh` inyectado como `ssh=` | PERMITIDO (transporte SSH no controlable en CI; `SshClient` real cubierto aparte con FakeRunner; lógica del provider corre real) | payload JSON, precondiciones, M1/M2, refine, D4 | ninguna |
| test_registry.py | providers reales con tokens dummy | PERMITIDO (sin mock; `resolve` no genera red) | resolución por UserConfig, disponibilidad | ninguna |
| conftest.py | `monkeypatch.delenv` vars de Settings + cache_clear | PERMITIDO (aislamiento; Settings es ítem separado) | env no afecta providers | ninguna |

**Resumen mocks:** 0 PROHIBIDOS en scope del ítem. Todos los mocks son bordes externos (HTTP, SDK replicate, subprocess/SSH) o determinismo (sleep, deadline). Ningún test mockea dominio, normalización, armado de payload, resolución del registry ni lógica interna del provider.

**Confianza de realidad: alta** — los asserts verifican payloads serializados reales (body/input), headers (`Authorization` Bearer), status mapeados a errores tipados y convención D4 en `meta`; los flujos multi-llamada corren el código real del provider contra fakes de transporte.

## Re-run Results

- Suite del ítem: `.venv/bin/pytest tests/unit tests/integration/providers -q` → **163 passed** (baseline 157 + 6 agregados por test-guardian), 0.36s.
- Subset nuevos: xai+replicate+kier nuevos pasan.

Tests creados por test-guardian (cierre de ramas de retry/error dentro del DoD, mismos fakes permitidos):
- `test_xai_provider.py::test_video_poll_transient_500_recovers` — retry con backoff del poll ante 5xx.
- `test_xai_provider.py::test_video_create_without_request_id_raises_generation_error` — create sin `request_id` → `ProviderGenerationError`.
- `test_replicate_provider.py::test_sdk_exception_maps_to_unavailable` — excepción SDK → `ProviderUnavailableError` (retryable).
- `test_replicate_provider.py::test_provider_error_passthrough_not_wrapped` — `ProviderError` tipado no se envuelve como transient.
- `test_kie_provider.py::test_create_task_business_code_400_raises_input_error` — M3 en api-code: 400 de negocio terminal (no retryable).
- `test_kie_provider.py::test_create_task_business_code_429_raises_rate_limit` — M3 en api-code: 429 de negocio retryable.

## Pre-existing vs Attributable

- Fallos: ninguno. 0 regresiones atribuibles al ítem ni a los tests agregados.
- Baseline grok: `M sources/6181290784.jpg`, `?? variables_packages/{hot,sexy}.json` = baseline pre-existente de ítem 1 (documentado por arch-enforcer), sin cambios atribuibles al ítem 2.
- Warnings de pytest-asyncio 0.26/Py3.14 = cosmético, residual de ítem 1 (R12 del PLAN), no bloquea.

## Gaps dentro del DoD → residuales

No hay gaps que exijan volver al executor. Residuales opcionales (fuera de DoD, no bloquean):
- xAI `_poll_video_once` rama final tras agotar reintentos de 5xx (raise `ProviderUnavailableError`) no testeada explícitamente; la rama de recovery sí quedó fijada.
- Estado xAI video `expired` comparte rama con `failed` (testeado solo `failed`).
- URL-token: estructuralmente imposible (tokens solo viajan en header; asserts de header `Authorization` == `Bearer ...` presentes). No se agregaron asserts tautológicos sobre URL.
- Replicate clasifica toda excepción SDK no-`ProviderError` como `ProviderUnavailableError`; si el SDK expusiera errores terminales tipados, sería refinable en ítems 4-6 (fuera del fix M3 concreto, que nombró xai/kie).

## Handoff

Listo para cierre de ítem. Suite en verde (163), Mock Audit sin prohibidos, M1/M2/M3 fijados por tests, R2/higiene confirmada, aislamiento sin filesystem real ni env. Commit Gate puede proceder (cambios de test-guardian: 3 archivos de tests modificados).
