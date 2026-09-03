# Impact Item 1 — Dominio y settings (Fase 1) — grokV2-rearch

Run 2026-09-03. Agente: impact-analyzer. Target: /home/ubuntu/repos/grokV2 (greenfield). grok read-only de comportamiento.

## Resumen
Ítem aditivo: scaffolding + settings.py (Settings Pydantic centralizado) + domain/ (generation, user_config, variables, job). Consumidores = ítems 2-6. Riesgo real = fijación de contrato (JSON legacy + semánticas sutiles). Veredicto: LISTO para planear.

## Riesgos
- CRITICAL R1: allowlist/admins — blank→None (open), no→set() (lockout). No-int → ValidationError (fail-fast). Test "" / " " / "abc" / "1, 2,3".
- CRITICAL R2: render PromptTemplate con llaves JSON literales — regex+fallback+clean gaps (variables_store.py:79-85,252-273,306-320), NO str.format.
- CRITICAL R3: requeridos ausentes → fail-fast; KIE/COMFYUI/ids/timeout opcionales con defaults exactos.
- MEDIUM R4: UserConfig/VideoConfig tolerantes a JSON legacy (claves faltantes, duration str→int, comfyui_refine "0"/"1", grok_provider legacy alias).
- MEDIUM R5: Settings sin instanciar en import; get_settings() lru_cache + inyección; test con monkeypatch env + limpieza caché.
- MEDIUM R6: scope creep — catálogo de modelos MODELS/GROK_IMAGINE_VARIANTS NO está en file-set; labels de UI se difieren a telegram (ítem 5).
- MEDIUM R7: random global en variables → RNG inyectable o monkeypatch (tests deterministas).
- LOW R8: comfyui_port str hoy → int en Settings (provider SSH castea a str). R9: .env.example stale grok (no REPLICATE_MODEL; sí VARIABLES_ADMIN_IDS/REFINE_CONFIRM_TIMEOUT). R10: no parser estricto de variables (claves _package/blacklist). R11: MAX_ACTIVE_JOBS_PER_USER=3 fijar constante en domain/job o deferir a ítem 4.

## Decisiones para el planner
- D1 (recomendado): incluir ahora catálogo funcional domain/catalog.py mínimo (MODELS + GROK_IMAGINE_VARIANTS + resolución id por provider/variant); labels de UI → telegram ítem 5.
- D2 (recomendado): UserConfig = solo porción persistida en sessions.json; estado efímero (pending_prompt etc.) FUERA del dominio (app/FSM ítems 4-5). source_path/integrate_ref_path/state legacy → opcionales.
- D3 (recomendado): GenerationRequest provider-agnóstico mínimo (model_id, prompt, media_type, imagen/ref opcional, aspect, video: duration/resolution/mode). Sin campos provider-específicos en ítem 1.
- D4 (recomendado): Settings solo superficie env actual; paths (sessions_file, variables_file, packages_dir, sources_dir) → ítem 3.
- D5: grokV2 no es git → git init + commit base de scaffolding/planeación al arrancar ítem 1.
- D6 (recomendado): comfyui_port int en Settings; provider SSH castea str (ítem 2).

## Env var surface (settings)
TELEGRAM_BOT_TOKEN (req) · REPLICATE_API_TOKEN (req; grok lo re-inyecta a os.environ para SDK replicate — NO replicar; provider ítem 2 recibe por constructor) · XAI_API_KEY (req) · KIE_API_KEY (opc, ""=no disponible) · COMFYUI_HOST (opc "") · COMFYUI_PORT (opc "22") · ALLOWED_TELEGRAM_IDS (opc None) · VARIABLES_ADMIN_IDS (opc None) · REFINE_CONFIRM_TIMEOUT (opc 300).
grok HEAD ancla Fase 0: 81832a5.

## Files map (crear en grokV2)
pyproject.toml ([tool.pytest.ini_options] asyncio_mode=auto, testpaths tests; pydantic>=2 + pydantic-settings; layout src) · .env.example (superficie real) · README mínimo · src/grokbot/{__init__,settings}.py · src/grokbot/domain/{__init__,generation,user_config,variables,job}.py · (posible domain/catalog.py si D1) · tests/{conftest,unit/test_settings,unit/domain/test_user_config,test_variables,test_generation,test_job}.py. NO tocar grok/** ni dirs ítems 2-6.

## Tests a crear
conftest aislante env · test_settings (requeridos/defaults/ids/timeout/port) · test_user_config (defaults espejo _default_session_record; legacy; coerción VideoConfig/ComfyUIConfig) · test_variables (normalización, placeholders regex, JSON braces, fallback, combo) · test_generation · test_job. Reference oráculo grok (solo lectura): pytest tests/test_variables_store.py tests/test_sessions_video_config.py.
