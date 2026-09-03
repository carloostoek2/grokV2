# Arch Audit: item1-domain-settings (dominio + settings, pool grokv2-rearch)

Fecha: 2026-09-03 · Auditor: arch-enforcer · Fuente: PLAN.md, SUMMARY.md, gsd-executor.log,
SPEC_REFACTOR.md §4/§5.1/§5.7/§6/§7, CLARIFY.md, código + tests en grokV2, referencia read-only grok@81832a5.

**Verdict:** PASS WITH NOTES
**Critical violations:** 0 · **Medium:** 0 · **Observations:** 4

## Findings

### Critical (must fix before advance)
Ninguna.

### Medium / Observations

- O1 (observation, informativa): `kie_api_key` implementado como `str = ""` mientras el sketch de SPEC §5.7
  muestra `str | None = None`. La implementación coincide con el runtime real de grok
  (`bot.py:44` `os.environ.get("KIE_API_KEY", "")`) y con la tabla del PLAN (`KIE_API_KEY opc ""`). No es desvío;
  se documenta para que un revisor posterior no lo "alinee" a la SPEC por error.
- O2 (observation, edge teórico): `UserConfig.from_record` da precedencia a `grok_imagine_provider` y solo usa
  el alias legacy `grok_provider` si el canónico falta. grok `sessions._ensure_full` en ese caso sobreescribiría
  con el legacy. El `sessions.json` real de grok (1 usuario) no tiene ambas claves (verificado), así que no hay
  impacto. Referencia para ítem 3 (repo) al decidir normalización de escritura: mantener precedencia canónica.
- O3 (observation, proceso/git): el árbol de grokV2 tiene 2 paths untracked post-ítem:
  `.planning/quick/20260903-grokv2-rearch/item1-domain-settings/SUMMARY.md` y `.grok/agent-memory/residuals/`.
  Son artefactos del workflow (SUMMARY + memoria de residuales), no scope creep de código. Resolver en el
  Commit Gate del ítem (commitear o dejar fuera explícitamente). `.venv/`, `src/grokbot.egg-info/`, caches ignorados OK.
- O4 (observation, nota de acoplamiento): `domain/__init__.py` re-exporta la API completa; importar cualquier
  submódulo (`grokbot.domain.user_config`) ejecuta el facade y carga todos los hermanos. Es el patrón pedido por
  el PLAN. Sin ciclo (catálogo/variables no importan el package). No bloquea; si en ítems 2-6 el costo de import
  o ciclos parciales incomodan, los imports ya apuntan a submódulos concretos.
- O5 (observation, in-scope-followup §5b): `resolve_grok_config` devuelve solo `{provider, variant, id}` (sin
  `label`/`desc`/`prov_label` que grok `get_grok_imagine_config` sí retorna). Es la decisión D1/A6 del PLAN: los
  mapas de UI van a `telegram/` en ítem 5. No es desvío del ítem.

## Compliance Checklist

- [x] Capas respetadas: `domain/` solo stdlib (dataclasses/enum/re/collections.abc/typing), imports verificados
      por módulo; sin settings/pydantic/grok; sin I/O (json/aiohttp/subprocess/Path/os), sin random global; frozen.
- [x] Contratos de dominio: catálogo con paridad de datos verificada por AST (MODELS 5 claves y GROK_IMAGINE_VARIANTS
      2 claves idénticos a bot.py; constantes video/comfyui idénticas a sessions.py); `GenerationRequest`
      provider-agnóstico sin campos provider-específicos; `UserConfig` modela solo porción persistida (sin
      `pending_prompt`/efímeros; claves desconocidas se ignoran); `VideoConfig`/`ComfyUIConfig` con coerción y
      fallback a valid-set; `PromptTemplate` render por regex (AST: cero `.format`/`format_map`/f-strings),
      fallback + clean-gaps; `Job`/`JobStatus`/`MAX_ACTIVE_JOBS_PER_USER=3`.
- [x] Settings: superficie de env == tabla (3 requeridos + 6 opcionales con defaults exactos); fail-fast
      ValidationError en requeridos ausentes y en no-int; `get_settings()` lru_cache; sin instancia en import;
      blank→None / CSV→set / no-int→error; `comfyui_port` int; `model_config` sin `env_file`.
- [x] Scope del PLAN respetado: archivos creados coinciden con la lista exacta; no existen
      `src/grokbot/{providers,repositories,application,telegram,shared,main.py}`.
- [x] No-touch grok: `git -C /home/ubuntu/repos/grok status --porcelain` = baseline pre-existente
      (`M sources/6181290784.jpg` + `?? variables_packages/{hot,sexy}.json`), HEAD 81832a5 sin cambios.
- [x] Logging/observabilidad: n/a para capa pura de dominio/settings (sin operaciones críticas de I/O en este ítem).
- [x] Tests: `tests/conftest.py` scrubea env y limpia caché de `get_settings` antes/después de cada test;
      `.venv/bin/pytest tests/unit -q` → 48 passed; cobertura unit de settings (9 casos: R1/R3/R5/R8/D6) y de
      dominio (D1/D2/D3, R2/R4/R11) suficiente para el DoD; carga real de sessions.json y render JSON parseable.

## Handoff

Siguiente paso: **test-guardian** (suite 48 passed; auditoría de mocks/capas de tests sobre el DoD del ítem).
Luego Commit Gate del ítem: resolver O3 (untracked SUMMARY.md + residuales).
