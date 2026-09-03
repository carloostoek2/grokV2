# SCOPE CLARIFICATION (--clarify)

- **Run:** /hardener-agile --clarify --effort 3 --spec @grokV2/docs/SPEC_REFACTOR.md — 2026-09-03
- **Fuente:** combinación — petición del usuario (flujo modificado) + SPEC_REFACTOR.md (§5 arquitectura, §7 plan de migración)
- **Pool:** `grokv2-rearch` — re-implementación de @grok en @grokV2 con arquitectura por capas

## Decisiones bloqueadas (no re-abrir)

1. **Ítems del pool = Fases 1–6 del plan de migración de la SPEC (§7).** Cada fase es un ítem:
   - Item 1 — Extraer dominio y settings (puramente aditivo)
   - Item 2 — Extraer providers (contrato común + xai/replicate/kie/comfyui + registry)
   - Item 3 — Extraer repositorios (interfaces + backend JSON que lee formatos actuales)
   - Item 4 — Extraer casos de uso (application/ + JobManager; unifica /variables y /var)
   - Item 5 — Reescribir capa Telegram (telegram/handlers/, middlewares, keyboards, formatters, FSM)
   - Item 6 — Ensamblaje final / entrypoint (main.py, DI) + suite completa + paridad funcional
2. **Fase 0 (congelar alcance / caracterización) NO es un ítem del pool.** Se considera dentro del
   análisis/planeación del ítem 1 (qué comportamiento de referencia fijar antes de empezar a crear dominio).
3. **Un ÚNICO pool de 6 ítems** (cap del skill ampliado de 4 → 6 por decisión explícita del usuario).
4. **Por ítem corre el flujo completo de pasos 1–6** (impact-analyzer → gsd-planner → gsd-executor →
   arch-enforcer → test-guardian → corrida de tests) **+ Commit Gate (paso 7b).**
5. **El Paso 7 (review loop de reviewers, --effort 3) NO corre por ítem.** Se difiere y corre **UNA única vez
   al cierre del pool** (después del ítem 6 + su commit gate), sobre el diff acumulado de los 6 ítems, hasta
   **0 issues** abiertos de cualquier severidad. Los fixes del review-loop son a nivel pool (pueden tocar
   varios ítems) y se commitean como work units de la fix round.
6. **Target de implementación = @grokV2 (greenfield).** `src/` y `tests/` nuevos se crean en
   `/home/ubuntu/repos/grokV2`. **@grok NO se modifica**: es repo read-only, fuente de verdad de
   comportamiento (se lee para derivar semántica, no se copia/cambia). La semántica "extraer/retirar
   bot.py" de la SPEC se reinterpreta para target verde como "re-implementar el equivalente modular + ensamblar".
7. **Compatibilidad de comandos y de datos:** comandos/alias idénticos al bot actual (§6 SPEC); los JSON
   existentes (`sessions.json`, `variables_lists.json`) se leen tal cual por los repos sin migración manual.

## Fuera de scope (explícito)

- Cambios de comportamiento/UX frente al bot actual (paridad funcional, no mejoras de producto).
- Backend no-JSON (SQLite/Redis) en esta implementación: solo se definen contratos; backend default JSON.
- Modificar la definición del skill hardener-agile (SKILL.md) o de /implement.
- Refactor in-place de @grok.

## Assumptions (grises menores resueltas por defecto)

- `grokV2` se inicializará como repo git al arrancar el ítem 1 (hoy no lo es). Necesario para commits por
  work unit y commit gates. Se `git init` y primer commit base (scaffolding/planning) al inicio del ítem 1.
- Los "tests adaptados" de la SPEC §7 son, en target verde, **tests nuevos por capa**
  (unit de domain/application, integration con fakes, e2e de handlers). Los tests existentes de @grok son
  referencia de comportamiento, no se copian.
- El review-loop de cierre usa `--effort 3` (1 general + especialistas según scope acumulado del pool).

## Deferred (ideas fuera de scope → no implementar ahora)

- Swap de backend de persistencia a SQLite/Redis (los contratos lo permiten a futuro).
- Escalar horizontalmente el JobManager con Redis.
- Cualquier nueva feature de producto no contemplada en la SPEC.

## Restricciones para agentes

- **impact-analyzer:** mapear impacto de cada fase sobre el árbol objetivo grokV2/src + qué leer de @grok
  como referencia; riesgos y tests por capa. Fase 0/paridad: qué comportamiento de referencia fijar.
- **gsd-planner:** PLAN ejecutable por ítem que materialice las decisiones 1–7; no re-abrir temas cerrados.
  En ítem 1, considerar scaffolding (pyproject, layout, settings de tests) + si hace falta baseline de
  caracterización.
- **gsd-executor:** implementar SOLO en grokV2 según PLAN; @grok solo lectura. Commits atómicos por work
  unit. Self-check PASSED + log. Reportar residuales.
- **arch-enforcer:** auditar contra arquitectura de la SPEC §4/§5 (capas, contratos, regla dura de
  `telegram/`). Veredicto PASS/PASS WITH NOTES/FAIL. 0 critical para avanzar.
- **test-guardian:** tests por capa (unit/integration/e2e), mock audit (solo mocks estrictamente
  necesarios en bordes externos). Cobertura del DoD del ítem.
- **documentador (cierre):** SUMMARY del pool, learnings, review stats del cierre, decisiones relevantes.
