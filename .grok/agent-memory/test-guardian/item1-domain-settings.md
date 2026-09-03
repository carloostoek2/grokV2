# Test-Guardian Report: item1-domain-settings

Run 2026-09-03. Agente: test-guardian. Target: /home/ubuntu/repos/grokV2 (greenfield). grok read-only de referencia @81832a5.

**Verdict:** suite protege adecuadamente — 1 gap menor dentro del DoD cerrado con test aditivo (normalización UserConfig). Sin mocks prohibidos en scope del ítem.

## Coverage Audit (DoD del ítem)

| Riesgo / DoD | Tests que lo fijan | Estado |
|---|---|---|
| R1 CRITICAL allowlist blank→None / CSV→set / no-int→error | test_settings: `test_allowlist_blank_is_none`, `test_allowlist_csv`, `test_allowlist_invalid_raises` | OK |
| R2 CRITICAL render PromptTemplate regex JSON-literal / fallback / clean-gaps | test_variables: `test_render_json_template_with_literal_braces`, `test_render_fallback_unknown_placeholder`, `test_render_fallback_format_expr`, `test_render_inline_clean_gaps_and_join`, `test_clean_gaps` | OK |
| R3 CRITICAL requeridos fail-fast, opcionales con defaults | test_settings: `test_missing_required_fails_fast`, `test_required_present_and_defaults` | OK |
| R4 MEDIUM UserConfig tolerancia legacy | test_user_config: record real, duration str, comfyui_refine "0"/"1", alias grok_provider, unknown keys | OK (+1 nuevo) |
| R5 MEDIUM import sin instanciar + lru_cache + aislamiento env | conftest autouse (scrub env + cache_clear) + `test_import_does_not_instantiate`, `test_get_settings_cached_and_cache_clear` | OK |
| R6 MEDIUM catálogo acotado (5 modelos + 2 variantes) | test_catalog: `test_models_exact_keys_and_required_spec_fields`, `test_validate_constants_against_variants` | OK |
| R7 MEDIUM random global | Diferido por PLAN (A3); dominio sin random. No requiere test en ítem 1 | n/a |
| R8/D6 LOW comfyui_port int | test_settings: `test_comfyui_port_int` | OK |
| R9 LOW .env.example sin REPLICATE_MODEL | Verificado manual (grep 0 en grokV2); no es test unit | OK (manual) |
| R10 LOW normalize_items tolerante | test_variables: `test_normalize_items_cleans_and_drops_invalid` | OK |
| R11 LOW MAX_ACTIVE_JOBS_PER_USER=3 | test_job: `test_max_active_jobs_per_user` | OK |
| Contratos de dominio paridad de valores (MODELS/variantes/VIDEO_*/COMFYUI_*) | test_catalog (ids por provider/variant), test_user_config (defaults espejo `_default_session_record`, video/comfyui coercion, real grok record) | OK — valores cotejados por test-guardian contra bot.py/sessions.py/variables_store.py |
| Dataclasses frozen | Pinneado en test_generation (FrozenInstanceError) y test_job; no pinneado para UserConfig/VideoConfig/ComfyUIConfig/PromptTemplate (deuda menor, no bloqueante — el diseño frozen está en código) | Nota |
| DoD sessions.json real de grok carga sin pérdida | `test_real_grok_session_record_loads_without_loss` (fixture == sessions.json working tree de grok, usuario 6181290784, cotejado) | OK |
| DoD `pytest tests/unit -q` en verde | 49 passed tras test aditivo | OK |
| No dirs ítems 2-6; 0 cambios sobre grok | Verificado (ls src/grokbot/ = solo domain+settings; grok status = baseline pre-existente del 2026-09-02) | OK |

## Tests creados/ajustados por test-guardian

- **Ajustado:** `/home/ubuntu/repos/grokV2/tests/unit/domain/test_user_config.py` — añadido
  `test_invalid_model_provider_variant_fall_back`. Protege la cláusula de normalización del contrato de
  `UserConfig.from_record` (`model ∉ VALID_MODELS → "grok"`, `provider ∉ valid → "kie"`, `variant ∉ valid →
  "quality"`) que el PLAN documenta (R4/D2) y que ningún test fijaba. Incluye preservación de valores válidos
  no-default (seedream/xai/standard) y round-trip del modelo normalizado.
- Sin otros cambios. No se tocó código de producción.

## Mock Audit

Grep `@patch|patch(|MagicMock|AsyncMock|Mock(|mocker|monkeypatch|jest/sinon/gomock` sobre tests/conftest del ítem:

| Archivo | Mock / patch | Clasificación | Path de negocio | Acción |
|---|---|---|---|---|
| tests/conftest.py:27-34 | `monkeypatch.delenv` de vars de settings + `get_settings.cache_clear()` | PERMITIDO (aislamiento de frontera env, necesario) | determinismo de Settings | ninguna |
| tests/unit/test_settings.py (varios) | `monkeypatch.setenv` de vars env | PERMITIDO (inyección de entrada real de frontera env) | parseo R1/R3/R8 | ninguna |
| tests/unit/domain/** | — | sin mocks | dominio puro | ninguna |

**Resumen mocks:** 2 usos PERMITIDOS (aislamiento env), 0 PROHIBIDOS en scope del ítem.
**Confianza de realidad:** alta — dominio puro ejercitado con valores reales (record real de sesión, JSON template
parseable), Settings validado contra env real; cero sustitución de lógica bajo test.

## Re-run Results

- `.venv/bin/pytest tests/unit -q` → **49 passed, 10 warnings** (0.12s). Los warnings son DeprecationWarnings de
  pytest-asyncio 0.26 bajo Python 3.14 (cosmético, residual R2 del pool).
- Sanidad extra (python directo, no en suite): JSON template `fields() == ["pose","angle"]`; `render_positional` OK;
  `list_for_placeholder` prefix (poses_long) OK; `resolve_grok_config(None,None)` = kie/quality; `clean_gaps` OK.

## Pre-existing vs Attributable

- **No atribuibles al ítem (pre-existentes, documentadas en residuals grokv2-rearch R1):**
  - grok working tree: `M sources/6181290784.jpg`, `?? variables_packages/{hot,sexy}.json` — mtimes 2026-09-02,
    un día antes de los commits del ítem (2026-09-03). No fueron tocados por el ítem.
- **Atribuibles al ítem:** ninguno. Todo el código nuevo está commiteado en grokV2; el único cambio en working tree
  es el test aditivo de test-guardian (todavía sin commitear, según rol).

## Gaps dentro del DoD (requieren volver a executor)

- Ninguno abierto. El único gap detectado (normalización model/provider/variant de `UserConfig.from_record`) se
  cerró aquí con un test aditivo en scope; no requiere volver a executor.

## Gaps residuales / deuda (fuera del DoD, NO bloquear)

- Frozenness de `UserConfig`/`VideoConfig`/`ComfyUIConfig`/`PromptTemplate` no pinneada por test (el diseño frozen
  está en el código y arch-enforcer lo verificó). Mejora opcional, no requerida por las listas de casos del PLAN.
- `fields()` del JSON template no se assert directo (cubierto funcionalmente por el render que sería no-JSON si
  fields() fallara). No se infla el ítem.
- Comportamiento de ítems 2-6 (providers, repos, handlers, UI label maps) no testeado aquí — correcto por scope.

## Handoff

Listo para Commit Gate del ítem (incluir en el commit del gate el test aditivo de test_user_config.py y resolver
untracked de proceso: SUMMARY.md, residuales, arch-enforcer log). Suite 49 passed. grok intacto (baseline
pre-existente). Sin mocks prohibidos.
