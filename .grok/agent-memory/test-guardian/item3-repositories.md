# Test-Guardian Report: item3-repositories (pool grokv2-rearch)

Fecha: 2026-09-03 · Auditor: test-guardian · Suite: `.venv/bin/pytest tests/unit tests/integration/repositories -q`

**Verdict: suite protege adecuadamente**

## Coverage Audit

DoD del ítem (paridad R1/R2/R3, D1-D9, A4/A6) cubierto por 41 tests de repos + 11 de settings, todos sobre
filesystem `tmp_path` real, deterministas y sin red. Mapeo exhaustivo contra el PLAN (casos 1-12 por task):

| Área (DoD) | Cobertura | Archivo(s) |
|---|---|---|
| Session get_config usuario nuevo persiste record default completo (`video_hourly_timestamps: []`, source_path None) + missing-file crea + parent dir | 3 tests | `test_json_session_repo.py` |
| Session carga real-shape → UserConfig correcto; round-trip load→save→load `json.load == canonical_record()` EXACTO | 2 tests | idem |
| Session save_config NO destructivo: preserva `video_hourly_timestamps` + `_extra_user_key` + comfyui al mergear | 1 test | idem |
| Session legacy `grok_provider`→canónico migrado y DROPEADO al escribir; coexistencia A4 precedencia canónica (get_config persiste dropeo) | 2 tests | idem |
| Session quota horaria: record_usage prune ventana 3600; count_usage poda+persiste; count_global multi-usuario sin escribir; **count usuario nuevo persiste default y devuelve 0 (nuevo test-guardian)** | 4 tests | idem |
| Session corrupto propaga JSONDecodeError; top-level no-dict → ValueError (D4) | 1 test | idem |
| Session dump-flags sobre TEXTO: `ensure_ascii` default True → `"caf\\u00e9"` escapado (D9) | 1 test | idem |
| Variables seed en archivo nuevo/vacío/corrupto → LIST_NAMES + DEFAULT_LISTS verbatim + template + blacklist + file keys | 3 tests | `test_json_variables_repo.py` |
| Variables paquete-activo NO se siembra y preserva `_package`/`_extra_top`/blacklist/template tras add_item y set_template (R6) | 2 tests | idem |
| Variables CRUD add/update/delete (dup/blank/unknown/out-of-range/no-op same-text→True); get_list unknown → ValueError; set_template blank→False | 3 tests | idem |
| Variables blacklist add/dup→False/get `{("a","b")}`/raw shape `[["a","b"]]`/clear | 1 test | idem |
| Variables is_valid_list_name D8 (constante + self-describing paquete) | 1 test | idem |
| Variables packages D3: save con alias `fields`/slugify `Mi Paquete!`→`mi_paquete`, list sorted, load/package_exists, activate → `_package`+`blacklist:[]`, delete activo→False/missing→False, **delete non-activo existente → True y archivo removido (nuevo test-guardian)**, payload inválido → (False,error), activate missing → False | 3 tests | idem |
| Variables dump-flags sobre TEXTO: `ensure_ascii=False` → acento literal `"ángulo"`, NO `\\u00e1`, archivo activo y package | 1 test | idem |
| Refs save con kie_task_id shape exacta + created_at==now inyectado; no-op sin task/regen (no crea archivo); solo regen omite kie keys | 3 tests | `test_generation_refs_repo.py` |
| Refs prompt truncado 500; kie_index clamp 0..5 | 2 tests | idem |
| Refs get existente/missing; **TTL prune en save descarta record vencido de otra key conservando el vivo (nuevo test-guardian)**; prune get persiste solo si el key pedido existe; get key vencida → None sin escribir | 4 tests | idem |
| Refs claves extra de key viva preservadas tras save/get de otra key (dump dict completo); regen opaco round-trip | 2 tests | idem |
| Refs no-dict top → {} + save válido; malformado propaga JSONDecodeError (A3/D4); dump-flags `ensure_ascii` True escapado en texto | 2 tests | idem |
| Settings D1: data_dir default + env override + 4 props derivadas; conftest aísla `GROK_DATA_DIR` | 2 tests + conftest | `tests/unit/test_settings.py` |

**Aislamiento/realismo:** 100% `tmp_path` (nunca cwd, nunca filesystem grok, nunca `Path(__file__)`); fixtures
anonimizados con forma real (`111111111`, `/tmp/anon/...`, prompts dummy, `FAKE_FILE_ID`); deterministas
(`now=` inyectado salvo los `get`/TTL que usan `time.time()` real con margen de 14 días → sin flakiness);
sin red/Telegram.

## Mock Audit

| Archivo | Mock / patch | Clasificación | Path de negocio protegido | Acción |
|---|---|---|---|---|
| test_json_session_repo.py | ninguno | — | merge no destructivo, legacy A4, quota, round-trip, dump-flags | ninguna |
| test_json_variables_repo.py | ninguno | — | seed guard, paquete-activo, CRUD, blacklist, packages, dump-flags | ninguna |
| test_generation_refs_repo.py | ninguno | — | shape, no-op, clamp, truncado, TTL prune save/get, regen opaco | ninguna |
| test_settings.py | `monkeypatch.setenv` / conftest `delenv` | PERMITIDO (borde env de Settings; no sustituye lógica de repos) | defaults/validators de Settings | ninguna |

**Resumen mocks:** 0 PROHIBIDOS en scope del ítem. Los tests de los 3 repos corren el comportamiento real contra
disco `tmp_path` (load/save/merge/prune/seed reales) y leen el archivo resultante (texto + `json.load`); no hay
`MagicMock`/`patch`/mocks de serialización ni de métodos bajo test. El único `monkeypatch` está en `test_settings.py`
para setear/limpiar variables de entorno, que es borde externo legítimo.

**Confianza de realidad: alta** — los asserts verifican el JSON crudo escrito en disco (claves exactas, dump-flags por
texto, preservación de claves no modeladas, dropeo de legacy), no returns inventados por mocks.

## Re-run Results

- Suite del ítem: `.venv/bin/pytest tests/unit tests/integration/repositories -q` → **123 passed** (120 previos + 3 agregados por test-guardian), 0.26s.
- Subset repos: `.venv/bin/pytest tests/integration/repositories -q` → **39 passed** (36 + 3 nuevos), 0.16s.
- Suite completa: `.venv/bin/pytest tests -q` → **204 passed** (201 previos + 3 agregados), 0.38s.

Tests creados por test-guardian (cierre de gaps dentro del DoD, sin mocks, fixtures anonimizados):

1. `test_generation_refs_repo.py::test_save_prunes_expired_records` — cubre la rama de prune TTL **en save** (paridad
   explícita "TTL prune 14d en save y get"); previo solo estaba cubierto el prune por `get`.
2. `test_json_variables_repo.py::test_delete_package_non_active_removes_file` — cubre el happy path de `delete_package`
   (paquete no-activo existente → True y archivo removido); previo solo activo→False y missing→False.
3. `test_json_session_repo.py::test_count_video_hourly_usage_missing_user_creates_default` — cubre la rama
   `rec is None` de `count_video_hourly_usage` (usuario nuevo → persiste default y devuelve 0, paridad
   `_get_or_create_full`).

## Pre-existing vs Attributable

- Sin regresiones atribuibles al ítem: 201 passed baseline confirmado antes de mis adiciones; 204 tras agregar 3 tests.
- Deuda pre-existente documentada (NO de item 3, para review-loop de cierre del pool): arch O5 —
  `tests/unit/domain/test_user_config.py` (ítem 1) usa el ID real `6181290784` y ruta `/home/ubuntu/repos/grok/...`.
  Los fixtures de item 3 están 100% anonimizados (grep `6181290784|AgACAgE|/home/ubuntu/repos/grok` sobre
  `tests/integration/repositories/` → 0 hits).
- Notas O1/O2/O3 de arch-enforcer (KeyError `get_list` constante-ausente, CRUD contamina paquete-activo vía constante,
  save sin get previo dropea legacy) → comportamiento paridad grok, sin consumidor vivo; son notas para ítem 4, fuera
  del DoD de item 3 → residuales (no inflar).

## Handoff

Listo para Commit Gate. Veredicto positivo: suite protege adecuadamente el DoD de paridad/no-destrucción/seed/dump-flags
de los 3 repos con tests de comportamiento real sobre disco `tmp_path` y 0 mocks prohibidos en scope. Los 3 tests
agregados son aditivos dentro del DoD (mismos fixtures/patrones), no expanden scope ni tocan producción.
grok read-only intacto (baseline: `M sources/6181290784.jpg`, `?? variables_packages/hot.json`,
`?? variables_packages/sexy.json`).
