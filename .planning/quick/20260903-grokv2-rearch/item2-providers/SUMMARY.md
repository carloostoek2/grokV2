# SUMMARY — Item 2: Providers (Fase 2, grokV2)

Pool: grokv2-rearch · Fecha: 2026-09-03 · Agente: gsd-executor · Self-check: PASSED

## Outcomes
- src/grokbot/providers/: base (Protocol ImageProvider/VideoProvider + errores tipados ProviderError* + helpers + DEFAULT_IMAGE_ASPECT_RATIO 9:16), xai_provider (image+video+poll interno+edit_with_reference), replicate_provider (grok/seedream/faceswap), kie_provider (image/video/helpers/allowlist/D8 mode:spicy), comfyui/{ssh_client,provider} (generate/refine, runner SSH inyectable), registry (ProviderRegistry/ProviderResolution, regla video replicate→xai). Sin os.environ/settings/telegram/grok en providers; tokens solo por constructor.
- Cambio aditivo a dominio: GenerationRequest.params (D1).
- pyproject: + aiohttp, replicate (runtime), aioresponses (dev).

## Verificaciones
- pytest tests/unit tests/integration/providers -q → 146 passed (0 regresiones dominio item1).
- import grokbot.providers ok. grok: 0 cambios (baseline). No existen application/repositories/telegram/main/shared.

## Commits
f088220 feat(domain) params · 4f88610 feat(providers) base+errores+deps · 3a3e3f5 feat(providers) xai ·
f567291 feat(providers) replicate · adf5f20 feat(providers) kie · 91cf6cc feat(providers) comfyui ssh+provider ·
8ac638c feat(providers) registry · 493aa74 test(providers) R2 assert token ausente · 8287fb0 chore(log)

## Decisiones implementadas
D1-D8 según PLAN (ver detalle en reporte del agente / agent-memory).

## Residuales
- out-of-scope (test-only, tooling): aioresponses 0.7.9 × aiohttp 3.14.3 exige stream_writer; mitigado con shim test-only en test_xai/test_kie (no toca producto).
- Desvío A7 documentado: VideoProvider no declara poll() en Protocol; poll interno a generate() (submit+poll). Para arch-enforcer: desviación del §5.3 (ilustrativo).

Log: .planning/quick/20260903-grokv2-rearch/item2-providers/gsd-executor.log
