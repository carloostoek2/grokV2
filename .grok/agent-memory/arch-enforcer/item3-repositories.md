# Arch Audit: item3-repositories (capa repositories + JSON backends, pool grokv2-rearch)

Fecha: 2026-09-03 · Auditor: arch-enforcer · Fuente: CLARIFY.md (locked), PLAN.md item3 (D1-D9, DoD),
SUMMARY.md, gsd-executor.log, SPEC_REFACTOR.md §3.3/§4.3/§5.1/§5.6/§6/§7, código
`src/grokbot/repositories/**`, `settings.py` (D1), tests `tests/integration/repositories/`, referencia
read-only `grok/{sessions,variables_store}.py` (81832a5).

**Verdict:** PASS WITH NOTES
**Critical violations:** 0 · **Minor:** 0 · **Observations:** 5 (1 residual de pool, fuera de item 3)

## Findings

### Critical (must fix before advance)
Ninguna.

### Medium / Observations

- **O1 — `get_list(name)` puede lanzar `KeyError` (no `ValueError`) para una constante `LIST_NAMES` ausente del
  archivo activo.**
  En `src/grokbot/repositories/json_variables_repo.py`, `is_valid_list_name("poses")` es True por constante
  aunque un paquete activo (`bodies/hands/...`) no tenga `poses`; luego `get_lists()["poses"]` → `KeyError`,
  incoherente con el contrato "Unknown list → ValueError" del propio método. Reproduce grok `variables_store.py`
  idéntico (paridad). **Fix sugerido (no bloquea; puede hacerse en ítem 4):** en `get_list`, si el nombre no está
  en `get_lists()` (aunque esté en `LIST_NAMES`) → `ValueError`. Sin consumidor vivo hoy.

- **O2 — CRUD con nombre-constante puede "contaminar" un paquete activo (paridad grok, no alcanzable por UI).**
  `add_item("poses", "x")` sobre archivo paquete-activo devuelve True y crea una lista `poses` dentro del archivo
  self-describing (verificado en runtime). La invariante "el paquete activo nunca se contamina" del docstring solo
  protege contra el *seed* (`_ensure_full`), no contra CRUD explícito. Reproduce grok. No alcanzable por el panel
  `/listas` (que enumera vía `get_lists()`). **Nota para ítem 4:** `manage_lists` debe derivar los nombres editables
  de `get_lists()` y nunca pasar constantes ausentes del archivo activo.

- **O3 — `save_config` asume (implícitamente) un config obtenido vía `get_config` (la migración legacy ocurre en
  get/`_ensure_full`, no en save).** Si un caller construye un `UserConfig` desde defaults y lo persiste sobre un
  record legacy-only (`grok_provider`), se dropea el legacy y se escribe el provider default. Bajo el patrón
  previsto (get_config → mutar → save_config) no hay pérdida. **Nota para ítem 4:** documentar/garantizar el par
  load-then-save en los use cases.

- **O4 — Sin logging en repositories (por diseño, paridad grok).** No hay `logging` en los 3 repos. Aceptable para
  este ítem (shared/logging es ítem 6, y grok no logueaba en sessions/variables_store). Recomendar que el logging
  centralizado de ítem 6 registre a boot el `data_dir` resuelto y a nivel debug errores de persistencia, sin
  exponer contenido sensible de records.

- **O5 — RESIDUAL DE POOL (fuera de item 3): tests de ítem 1 contienen datos reales del dueño.**
  `tests/unit/domain/test_user_config.py` (commits de ítem 1, `905bf24`/`dfd7dc1`) fija el record real-shape con el
  ID real `6181290784` y el path absoluto `/home/ubuntu/repos/grok/sources/6181290784.jpg`. Viola la regla R8 del
  pool ("prohibido copiar IDs/prompts/file_id reales") en la suite. Los fixtures de item 3 están 100% anonimizados
  (111111111, /tmp/anon, FAKE_FILE_ID, prompts dummy) — este hallazgo NO es de item 3, pero debe corregirse en el
  review-loop de cierre del pool (anonimizar a `111111111` + `/tmp/anon/...`).

## Compliance Checklist

- [x] Interfaces vs implementación: `base.py` define los 3 Protocols `@runtime_checkable`
      (`SessionRepository`/`VariablesRepository`/`GenerationRefsRepository`) + `write_json_atomic`; backends JSON
      los implementan estructuralmente; sin fuga de I/O JSON de datos fuera de `repositories/` (grep: open/json.load
      de archivos de datos solo en `repositories/**`; providers usan json.dumps/loads solo para HTTP).
- [x] Sin imports cruzados prohibidos: `repositories/*` no importa settings/providers/telegram/grok/print; `base.py`
      = stdlib + `domain.user_config` (sin ciclos).
- [x] Paridad/no-destrucción (SPEC §6): lectura de JSON real tal cual; `save_config` merge no destructivo preserva
      `video_hourly_timestamps` + claves extra; variables muta el doc crudo preservando top-level extras y `_package`
      si paquete activo; refs preserva records/regen de otras keys; legacy `grok_provider`→canónico con precedencia
      canónica (A4) y dropeado al escribir; seed `DEFAULT_LISTS` solo en archivo vacío/corrupto/lists vacío; dump-flags
      exactos por archivo (D9) verificados por test sobre el texto.
- [x] Infraestructura: escritura atómica tmp+`os.replace` en los 3 `_save` (parent mkdir); métodos sync sin await
      entre load/save; `threading.Lock` solo en variables (instancia); paths requeridos por constructor (sin cwd/
      `__file__`); `Settings.data_dir` (env `GROK_DATA_DIR`) + 4 propiedades derivadas (D1) inyectables en ítem 6.
- [x] Frontera repos/domain: repos devuelven/consumen objetos de dominio (`UserConfig`); dicts solo donde el
      Protocol documenta crudo (`get_lists` dict[str,list[str]], refs `get`→record, blacklist→set de tuples);
      prompt-building NO duplicado (vive en `domain/variables.py`; repos solo `normalize_items`).
- [x] No-touch/scope: `git diff 4a6a8e6..d6ecb83 -- domain providers` vacío; commits item3 == lista de archivos del
      PLAN (5 work units atómicos); no existen `src/grokbot/{application,telegram,main.py,shared}`;
      `git -C /home/ubuntu/repos/grok status --porcelain` = baseline pre-existente (M sources/6181290784.jpg; ??
      variables_packages/{hot,sexy}.json). Settings: solo cambio D1 (data_dir + props), sin tocar superficie de
      tokens/validators.
- [x] Tests: 120 passed (item), 201 passed (suite completa, 0 regresiones); fixtures item3 anonimizados; tests
      cubren round-trip, dump-flags, corrupto, no-destrucción, legacy, paquete-activo, TTL prune.

## Handoff

Veredicto PASS WITH NOTES con 0 critical → **avanzar a test-guardian**. O1-O4 son notas para ítem 4 (consumidor de
los repos); O5 es residual de pool para el review-loop de cierre (anonimizar tests de dominio de ítem 1).
