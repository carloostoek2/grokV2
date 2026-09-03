# Impact Item 2 — Extraer providers (Fase 2) — grokV2-rearch

Run 2026-09-03. Agente: impact-analyzer. Target: `/home/ubuntu/repos/grokV2` (greenfield). `grok/` read-only fuente de verdad (NO editar).
Analysis only — no implementación.

## Executive Summary

Ítem 2 crea `src/grokbot/providers/` (base + xai + replicate + kie + comfyui/{provider,ssh_client} + registry) detrás del contrato `ImageProvider`/`VideoProvider` de SPEC §5.3, consumiendo los contratos de dominio del ítem 1 (`GenerationRequest`, `GenerationResult`, `MediaType`, `ImageSource`, `UserConfig`/`VideoConfig`/`ComfyUIConfig`, catalog `resolve_grok_config`/`resolve_model_id`). Origen semántico en `grok/bot.py` (nunca copiar texto): rangos por proveedor listados abajo. Los providers reciben tokens/config/transporte por constructor (inyección, item 6 hace DI); NO re-leen `os.environ` ni re-setear env global (a diferencia de `bot.py:41-49` que re-inyecta `REPLICATE_API_TOKEN`).

Riesgo global: MEDIO. No hay call-sites aún en grokV2 (ningún módulo consume providers hasta ítems 4-6), por lo que no hay breaking local; el riesgo real es fijar el contrato contra una semántica rica y hoy heterogénea (cada proveedor devuelve tipo distinto: `list[str]` urls, FileOutput de replicate, local paths de ComfyUI, meta `kie task_id`). El mayor hueco de diseño: `GenerationRequest` (item 1, frozen) NO tiene carrier para (a) parámetros ComfyUI por-usuario (`comfyui_model/lora/refine`), (b) edición xAI con referencia de 2 imágenes, (c) multi-output (álbumes/`resultUrls`>1). Sensible: tokens (xAI/Replicate/Kie/SSH ComfyUI) y payloads a APIs pagas — nunca en logs/results. Providers sin red real se testean con fakes de transporte (aioresponses / cliente replicate inyectado / runner SSH inyectado) — patrón ya usado por tests de grok.

Veredicto: **LISTO para planear** con ~7 decisiones (D1-D8 abajo) que el planner debe resolver antes de escribir código, siendo D1 (carrier de params provider-específicos) la única potencialmente bloqueante.

## Consumers / Call Sites Map

En grokV2 **no hay consumidores todavía**: `providers/` es capa de infraestructura que será consumida por `application/` (ítem 4 use-cases) y construida por `main.py/DI` (ítem 6). Consumidores futuros conocidos:
- Registry: ítem 4 (`RunVariableBatchUseCase`, `GenerateImageUseCase`, `GenerateVideoUseCase`) y ítem 5 (handlers traducen resultados a Telegram).
- Contratos de dominio que providers consumen (item 1, committed):
  - `domain/generation.py`: `GenerationRequest{provider,model_id,media_type,prompt,source,aspect_ratio,video_duration,video_resolution,video_mode}`; `GenerationResult{data|file_path|remote_url,mime_type,meta}`; `ImageSource = TelegramFileRef|LocalPathRef|UrlRef|KieTaskRef`.
  - `domain/catalog.py`: `MODELS`, `GROK_IMAGINE_VARIANTS`, `resolve_grok_config(provider,variant)->{provider,variant,id}`, `resolve_model_id(key,...)`.
  - `domain/user_config.py`: `UserConfig.model`, `.grok_imagine_provider`, `.grok_imagine_variant`, `.video` (`VideoConfig{duration,aspect_ratio,resolution,model,mode}`), `.comfyui` (`ComfyUIConfig{model,lora,refine}`).
  - `settings.py` (campos consumidos): `xai_api_key`, `replicate_api_token`, `kie_api_key`, `comfyui_host`, `comfyui_port`.

## Origen en grok (patrón semántico por módulo; NO copiar)

| Módulo grokV2 a crear | Origen `grok/bot.py` | Qué encapsula |
|---|---|---|
| `providers/base.py` | 235-266 msgs error/notice; 664-669 regla video-provider (replicate→xai); SPEC §5.3 | Protocol `ImageProvider{generate,supports}`/`VideoProvider{generate,poll}`; jerarquía de errores (ProviderError/ProviderNotConfigured/ProviderInput/ProviderTimeout); default aspect ratio imagen `9:16` (hoy `sessions.py:21`) |
| `providers/xai_provider.py` | 4530 XAI_BASE; 4533-4589 `_generate_xai` (img t2i/i2i/reference); 5164-5266 `_generate_xai_video`; 5269-5297 `_poll_video_once`; 615-623 `_validate_image_for_i2v`; 607-612 data-uri; 269-276 log/http_ok | POST `/images/generations` (t2i), `/images/edits` (i2i o reference 2-imgs), `/videos/generations` + poll `/videos/{id}`; normaliza a URLs; valida tamaño i2v; `respect_moderation:false`→error; timeouts poll 600s/intervalo 5s/backoff (2,4,8) |
| `providers/replicate_provider.py` | 3835-3852 `_generate_replicate`; 190-199 consts REPLICATE_* | `replicate.Client(api_token).run(...)` en `to_thread` (NUNCA `os.environ`); input grok t2i añade `aspect_ratio`; seedream t2i sin aspect, i2i `image_input=[data_uri]`,`size:"2K"`; faceswap `image=BytesIO`+`file_encoding_strategy=base64`; normaliza FileOutput/str→URL |
| `providers/kie_provider.py` | 4592-4604 consts; 4607-4658 slug/headers/upload; 4661-4855 create/poll/result-at-index; 4882-4959 `_generate_kie_once`; 5300-5370 `_generate_kie_video`; 1107-1157 aspect-ratios/slug/duration/sanitize | upload `file-base64-upload`→URL; createTask→poll recordInfo; `resultUrls` filtrados por host allowlist; i2i desde KieTaskRef (resolve URL at index); video slug/1.5-i2v/duration 6..30/aspect-ratio restricción; `mode:spicy` solo con kie_source_ref; KIE_API_KEY vacío→ProviderNotConfigured |
| `providers/comfyui/ssh_client.py` | 3858-3953 `_comfyui_ssh_opts`/`_comfyui_ssh_base`/`_comfyui_run_remote`/`_comfyui_pull`/`_comfyui_tmpdir` | Aísla subprocess `ssh`/`scp`: build de args (ControlMaster, ConnectTimeout, batch), run remoto con stdin (prompt o JSON payload) y timeout, scp pull→local tmp, parseo stdout `/workspace...`; inyectable (fake runner para tests) |
| `providers/comfyui/provider.py` | 3956-4032 `_generate_comfyui`; 4035-4084 `_generate_comfyui_refine`; 4139-4141 `_comfyui_is_video`; 4262+ `_build_comfyui_album_media` (solo lista) | `generate`: t2i/img2img según ComfyUI model; JSON `{prompt,image_b64,prompts}` por stdin cuando hay source; precondiciones (wan/minimax y krea_edit/multipose/qwen_aio requieren foto); returns local paths + `meta["comfyui_remotes"]`; `refine(...)` 2-stage REFINE_ONLY=1 REFINE_INPUT; returncode 2/3→errores; video MP4 detectado por `comfyui_model` |
| `providers/registry.py` | 664-669 `get_video_provider_for_user`; 848-922 `get_grok_imagine_config`/`get_model` (solo la parte de resolución, no UI) | Resuelve proveedor activo desde `UserConfig`+media_type: `grok` img→provider imaginate; `seedream`/`faceswap`→replicate; `grok_video`→video provider (replicate→xai); `comfyui`→comfyui (img/video por comfyui_model); disponibilidad (kie key vacía, comfyui host vacío→"no configurado") |

**NO tocar / no incluir en ítem 2** (pertenecen a otros ítems): envío a Telegram y teclados (`process_image_result` 4973-5063, `process_video_result` 5373-5422, `_send_comfyui_*` 4087-4242, `_image_regenerate_keyboard`, captions/formatters), descarga URL→bytes `download_url` 5066-5110 (compartida/SSRF — decidir D5), orquestación con reintentos + edición de status `generate_image` 3753-3802 / `_update_retry_status` 4863-4879, `safe_edit_text`, jobs/cancelación (`_start_job` etc.), `/config` FSM (`config_flow.py`), `sessions.py`/`variables_store.py`, `main.py`/DI, `shared/errors.py`, `domain/*` salvo decisión D1/D2, `tests/` grok (solo referencia), `grok/**` read-only.

## Riesgos

| Sev | Riesgo | Detalle | Mitigación |
|---|---|---|---|
| CRITICAL | R1 Carrier params provider-específicos ausente | ComfyUI necesita `comfyui_model/lora/refine` por request (per-user, no inyectable en constructor único); xAI edición-con-referencia usa 2 imágenes; `GenerationRequest` frozen no los modela. Sin resolver, comfyui provider no es testeable ni invocable en ítem 4. | D1: añadir `params: dict` opcional (default `{}`) a `GenerationRequest` (cambio aditivo dominio + test) o equivalente; comfyui provider lee `params` con fallback a defaults catalog. Resolver ANTES de ejecutar. |
| CRITICAL | R2 Fuga de secretos / payloads pagos | Tokens en headers/logs; payload enviado a APIs de pago; re-setear env global de replicate rompe aislamiento. | Solo constructor; nunca loguear headers/body/urls con query de token; mensajes de error normalizados sin secretos; en tests assert de que el body NO contiene api_key. |
| CRITICAL | R3 Normalización heterogénea multi-output | xai/kie devuelven `list[str]` URLs; replicate FileOutput/str; comfyui local paths (a veces N=5 álbum); kie i2i-from-task necesita `task_id/index`. `GenerationResult` es single-media. | D4: convención: media primaria en top-level (`remote_url`/`file_path`/`data`) + lista completa en `meta["urls"/"file_paths"/"remote_paths"]`; `meta["kie_task_id"]` para refs; item 4 hace fan-out. Tests de normalización deterministas. |
| MEDIUM | R4 Respuesta xAI video: status/`respect_moderation`/`video.url`; HTTP 200/202 ok | Mapeo `pending/processing/done/failed/expired`; moderación false→error de contenido; timeout 10min. | Test por cada transición de estado con fake; error tipado `ProviderContentError`/`ProviderTimeoutError`; no reintentar `failed`. |
| MEDIUM | R5 Kie: límites/aspect-ratio/modo/transients | Aspect ratios válidos dependen de modelo (base vs 1.5); duration clamp 6-30; `mode:spicy` solo con kie_source_ref; poll transients (429/5xx/422, backoff 2/4/8); failCode/failMsg sanitizado (80ch); host allowlist de resultados/upload (SSRF). | Helpers puros en `kie_provider` (slug/aspect/duration/sanitize) + unit tests; poll con retries transients y timeout `IMAGE_MAX_POLL_SEC`=120/`VIDEO_MAX_POLL_SEC`=600; filtro de URLs a `KIE_DOWNLOAD_HOSTS/SUFFIXES`. |
| MEDIUM | R6 ComfyUI SSH embebido / paths inseguros | subprocess/SCP hoy en bot.py; stdout del box se embedbe en shell (`_validate_refine_remote_path` charset); ControlMaster/ControlPersist; tmp local fuera de `/tmp` (cuota EDQUOT); timeouts 600/1500/1200*N. | Aislar TODO subprocess en `ssh_client.py` con runner inyectable; validar remote paths con regex `/workspace/[...]` antes de embeber; tmpdir configurable en tests (tmp_path). |
| MEDIUM | R7 Seguridad de descarga (SSRF) | Resultados xAI solo `*.x.ai/*.xai.com`; Kie solo hosts conocidos. Si providers NO descargan, quién enforcea allowlist. | D5: providers exponen `download_allowlist` token en `meta` (o descargan ellos con allowlist interna); el downloader compartido (ítem 4/6) lo enforcea. NO perder este control en la reescritura. |
| LOW | R8 Reintentos fuera del provider | `generate_image` reintenta 5× con backoff y mensajes de status (Telegram). Provider = 1 intento. Necesario tipar errores transients vs precondición para que el use-case reintente. | D3: excepciones tipadas en `providers/base.py` ahora; re-export a `shared/errors.py` en ítem 6; precondiciones (`ProviderInputError`) no reintentables. |
| LOW | R9 Disponibilidad "no configurado" | KIE vacío y COMFYUI_HOST vacío hoy devuelven mensaje amigable con `retryable:False` / error en `_comfyui_ssh_base`. | Registry consulta disponibilidad (api_key/host presentes); proveedor no configurado lanza `ProviderNotConfiguredError` con mensaje user-facing. |
| LOW | R10 Dependencias nuevas | providers necesitan `aiohttp` (xai/kie) y `replicate>=1,<2`; tests `aioresponses`. pyproject hoy solo pydantic. | Planner agrega runtime deps + dev dep `aioresponses`; `replicate.Client(api_token=...)` explícito (no env). |
| LOW | R11 `supports()` vs video solo replicate | replicate no tiene video; comfyui es image y video según `comfyui_model`, no según `media_type` del request. | `supports(request)` implementado por provider; registry resuelve por `cfg.model` + regla replicate→xai para video. |

## Affected Tests (a crear en grokV2)

Comando: `.venv/bin/pytest tests/unit tests/integration/providers -q` (asyncio_mode=auto; pythonpath=src).

- `tests/unit/providers/test_kie_helpers.py`: slug video (base/1.5 t2v/i2v), `_kie_aspect_ratios_for_model`, duration clamp 6..30, sanitize failMsg, allowed-host predicates.
- `tests/unit/providers/test_replicate_normalize.py`: FileOutput/str/list→URLs; mime/data-uri helpers (jpeg/png/webp).
- `tests/integration/providers/test_xai_provider.py` (aioresponses): t2i body (model/prompt/n/aspect_ratio 9:16) → `GenerationResult.remote_url`; i2i single-image data-uri body; i2v oversized rejection; video create→poll→done/failed/moderation/timeout; assert api_key NO en body.
- `tests/integration/providers/test_replicate_provider.py` (patch `client.run`): grok t2i aspect; seedream sin aspect / i2i `image_input`+`size`; faceswap `image` bytesio+base64; normalization.
- `tests/integration/providers/test_kie_provider.py` (aioresponses): t2i create+poll success (meta task_id, resultUrls filtradas); no-key→`ProviderNotConfigured`; upload→URL→i2i; KieTaskRef→resolve source→i2i `mode:spicy`; aspect inválido→error; video 1.5 slug; transients/backoff (monkeypatch sleep); host blocking.
- `tests/integration/providers/test_comfyui_ssh_client.py` (fake runner inyectado): command building (MODEL/LORA/REFINE_ONLY/REFINE_INPUT/timeouts), JSON payload stdin con image_b64, parse `/workspace` lines, returncode 2/3→errores, pull→local path, host vacío→not-configured.
- `tests/integration/providers/test_comfyui_provider.py` (fake ssh_client): t2i img→`file_path`; img2img con source; precondiciones (wan/minimax/krea_edit/multipose/qwen_aio sin foto→`ProviderInputError`); video detectado por comfyui_model; multi-output→`meta["file_paths"]`; refine 2-stage.
- `tests/integration/providers/test_registry.py`: resolución imagen/video por `UserConfig` (grok→xai/replicate/kie; seedream/faceswap→replicate; grok_video replicate→xai; comfyui→comfyui); disponibilidad kie/comfyui; media_type no soportado.

Fakes: `aioresponses` para HTTP (patrón tests grok), `replicate.Client.run` parcheado (sync, vía to_thread), runner de subprocess inyectado en `ssh_client`, `asyncio.sleep` no-op, `tmp_path` para ComfyUI tmpdir. Sin red real (SPEC §6).

## Files Map

- Edit: ninguno en item 1 salvo decisión D1/D2 (cambio aditivo a `domain/generation.py`/`domain/catalog.py` + sus tests unitarios).
- Create: `src/grokbot/providers/__init__.py`, `base.py`, `xai_provider.py`, `replicate_provider.py`, `kie_provider.py`, `registry.py`, `comfyui/__init__.py`, `comfyui/provider.py`, `comfyui/ssh_client.py`; `tests/unit/providers/*.py`; `tests/integration/providers/*.py`; deps en `pyproject.toml`.
- No touch: `application/`, `repositories/`, `telegram/`, `main.py`, `shared/` (ítems 3-6); `grok/**` read-only; tests grok read-only.
- Docs/otros: `.env.example` NO requiere cambios (superficie ya lista); MEMORY pointer.

## Ready for chain

Handoff a gsd-planner con scope tight: crear SOLO `providers/` (módulos arriba), tests `tests/{unit,integration}/providers/`, deps. NO crear app/repo/telegram/main/shared. Resolver D1 (params carrier) antes de escribir comfyui provider; D2 (aspect-ratio const), D3 (errores), D4 (multi-output), D5 (download/allowlist), D6 (registry API), D7/D8 (spicy/refine/reference) durante planeación con veredicto del arch-enforcer al cierre.

## Decisiones que necesita el planner (resumen para el plan)

- D1 (bloqueante): añadir `params: dict` (o equivalente aditivo) a `GenerationRequest` para comfyui workflow (model/lora/refine), reference-image y multi-source; o reducir alcance del provider y defaultear a catalog hasta ítem 4.
- D2: ubicación de `DEFAULT_IMAGE_ASPECT_RATIO = "9:16"` (recomendado domain/catalog aditivo; alternativa base.py).
- D3: jerarquía de excepciones de providers en `base.py` (re-export en shared/errors ítem 6) para reintentos/errores user-facing.
- D4: convención multi-output en `GenerationResult` (top-level primary + lista en `meta`).
- D5: quién descarga URL→bytes y dónde se enforcea el host allowlist (provider eager vs shared downloader ítem 4/6).
- D6: API del registry (mapa de instancias inyectadas + `resolve(cfg, media_type)` + disponibilidad).
- D7/D8: exponer `comfyui.refine()` y xai reference-image como métodos extra fuera del Protocol base; `mode:spicy` Kie solo con kie_source_ref.
