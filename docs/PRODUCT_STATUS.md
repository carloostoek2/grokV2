# grokV2 — Estado de producto (consolidado)

> Fuente única de estado del producto `grokbot` (@grokV2). Arquitectura objetivo:
> `docs/SPEC_REFACTOR.md`. Fecha de consolidación: 2026-09-05.
> HEAD verificado: `841df64` — suite completa **653 passed** · baseline `grok/**` intacto.
> Wave de hardening R8/R9/R10 + smoke live + deploy (2026-09-04) cerrados (detalle §3/§5) y
> **wave-2/R4 cerrada 2026-09-05**: los **9 flujos** que degradaban D8 ya NO degradan —
> operan con copy byte-parity de grok (§2/§3). `D8_COMMANDS == ()`. **Deploy: grokV2 corre
> permanente** como `grok-bot.service`, administrado por el shell `grokbot` (detalle §3/§4.2).

---

## 1. Resumen de producto

**@grokV2** (`src/grokbot/`) es la re-arquitectura greenfield del bot de Telegram `@grok`
(generación de imagen/video con xAI Grok Imagine, Replicate, Kie.ai y ComfyUI remoto),
construida capa por capa a partir de las 6 fases de `SPEC_REFACTOR.md`. Reproduce la lógica
de negocio y el copy byte-a-byte de grok en los flujos **no degradados**, sobre una base
limpia y testeable. `grok/` (repo hermano) se usó solo como referencia de comportamiento
READ-ONLY; su baseline pre-existente se mantuvo intacto en todo momento.

### Arquitectura entregada (layering estricto)

| Capa | Rol |
|---|---|
| `domain/` | Entidades puras (dataclasses frozen, stdlib, sin I/O) |
| `application/` | Use cases como generadores de **9 eventos frozen** (JobsFull eliminado en R9); sin I/O |
| `providers/` + `repositories/` | Protocols; backend JSON; lazy re-exports PEP 562; tokens solo por constructor |
| `telegram/` | **Solo presentación** — traduce eventos↔mensajes/teclados; media I/O por seams inyectables `TelegramGateway`/`MediaDownloader` (offline-testable) |
| `shared/` | Helpers stdlib-only (logging, errores user-safe del entrypoint) |
| `main.py` | Composition root; sin side-effects en import; `grokbot` = console script |

### Características clave

- **Comandos funcionales**: `/start`, `/config` (FSM), `/listas` (CRUD paquetes/plantillas y
  creación pegando JSON), `/estado` (tarjeta de configuración), `/variables` + `/var`
  (batch random + prompt fijo, multipose, shuffle/blacklist, skip-on-fail, cancel),
  generación de imagen (xai/replicate), video (kie/comfyui), **refine 2-stage** con
  confirmación ComfyUI (single y batch, token opaco + owner gate + timeout), jobs de imagen
  con `cancel_job`, allowlist en message y callback_query. Wave-2/R4 (2026-09-05): **Face
  Swap** (`/cambiar_source` + confirm single/batch) y **edición con referencia `/s`**
  (`/cambiar_referencia`, modo grok xAI) operativos con parity de copy grok.
- **Perímetro (R9, bot single-owner)**: SOLO chat privado (gate global en message y callback);
  sin límites de uso por decisión (sin tope de procesos activos ni cuota horaria); con la
  allowlist vacía el boot avisa y el bot queda abierto a quien te escriba en privado.
- **Calidad**: **653 tests passed**, política **0-red / 0-mock** — el bot se prueba 100%
  offline contra `FakeTelegramGateway` grabador; el review de cierre del pool rearch (3
  reviewers: general + security + plan) fue íntegramente offline y terminó en **0 issues
  (Round 3)**; cada ítem de la wave-2/R4 cerró su review-loop en **0 issues** tras fix
  rounds.
- **Errores user-safe**: los mensajes de boot/error jamás exponen tokens/IDs/payloads
  (solo nombres de campo o `type(exception).__name__`).

---

## 2. Funcionalidad vs degradaciones

### Flujos operativos (no degradados)

Operan con backing completo y copy byte-parity de grok (0-red/0-mock). Los flujos con
marca **[R4]** son exactamente los que en la wave rearch degradaban **D8** (respondían
"no disponible" user-safe) y que la **wave-2/R4** resolvió (2026-09-05,
`fa2d1e0`→`841df64`, suite **653 passed**):

- Imagen (xai/replicate), video (kie/comfyui), `/variables`+`/var` (random y prompt fijo,
  incl. multipose), refine 2-stage con confirmación ComfyUI, `/config`, `/listas` (CRUD),
  jobs single-image (`cancel_job:<id>`), allowlist, `/start`.
- **Crear paquete de variables pegando JSON** en `/listas` (FSM `pack_json` →
  `save_package` + activación) **[R4 · Item 1]**.
- **Long-prompt collection** — caption > 1020 no edita directo: guarda el file_id y pide el
  prompt por texto (foto con/sin caption, grok_video incluido) **[R4 · Item 1]**.
- **Álbumes entrantes / media groups** — colección (`AlbumStore`) + edición secuencial
  "Editando i/N" (máx. 10, cancel, long-prompt multi-file) **[R4 · Item 1]**.
- **`/estado`** — tarjeta de configuración byte-parity con grok `cmd_estado` (sin lista de
  jobs activos por decisión del owner) **[R4 · Item 1]**.
- **Face Swap completo** — modo `faceswap` con `/cambiar_source` (foto/álbum, confirm
  single/batch con fallback foto-a-foto del media group, progreso + cancel, terminales
  byte-parity) **[R4 · Item 2]**.
- **Edición con referencia `/s`** — `/cambiar_referencia` (fija la referencia en
  `data/integrate_refs/`, solo modo grok), foto single/álbum/long-prompt con caption `/s`
  que editan cada imagen con la referencia vía xAI (`edit_with_reference`, 2 imágenes), y
  **regen integrate** que re-descarga el source original y recarga la referencia
  **[R4 · Item 3]**.

Backing completo de capa `application` para los flujos R4 (use cases `faceswap` /
`integrate_refs`, repos binarios atómicos, seams de providers `swap_face` /
`edit_with_reference`).

### Flujos degradados D8 — ninguno (wave-2/R4 resuelta 2026-09-05)

`D8_COMMANDS == ()` (tupla residual vacía en `telegram/handlers/_common.py`). No queda
ningún flujo de grok cableado a mensaje D8 de no-disponible por falta de backing: las
constantes `D8_*` (`D8_INTEGRATE_MSG`, `D8_ALBUM_MSG`, `D8_LONG_PROMPT_MSG`,
`D8_FACESWAP_MSG`, `D8_REPLY_NO_PHOTO`, `D8_CMD_MSG`), `_PACK_NEW_D8`, `is_d8_command` y
`_cmd_unavailable` están ausentes de `src/`+`tests/`. Si un provider no está configurado,
la degradación es `ProviderNotConfiguredError` user-safe (no un comando D8).

---

## 3. Pendientes / follow-ups (actualizado 2026-09-05 — wave de hardening R8/R9/R10 y wave-2/R4 cerradas)

Wave de hardening cerrada por decisión de producto (bot privado single-owner): R8, R9 y R10
resueltos; wave-2/R4 resuelta (R4 abajo). Ver commits y cierre en
`.grok/agent-memory/residuals/grokv2-rearch.md`.

| ID | Pendiente | Clase | Estado / Acción sugerida |
|---|---|---|---|
| **R8** | Botón "Regenerar" no scopeado al owner | hardening | **Resuelto** — `c25c1fe` (`owner_uid` en refs + validación en `regen`). |
| **R9** | Sin límites de uso + perímetro | hardening / decisión | **Cerrado por decisión** — sin tope de procesos ni cuota horaria (`2bbfdd3`, `5900809`); gate SOLO chat privado + aviso de allowlist abierta (`5e9e88b`). |
| **R10** | Tope 50MB duplicado; URL firmada | hardening / decisión | **Resuelto** — `096dd0d` (tope único `MAX_MEDIA_BYTES`); la URL se muestra: camino de recuperación por decisión del owner. |
| **R4** | Flujos degradados D8 (§2) — 9 flujos sin backing | follow-up de producto (wave 2) | **Resuelto (wave-2/R4, 2026-09-05)** — `fa2d1e0`→`841df64`, suite **653 passed**, review-loops 0-issues. Item 1 utilidades (`fa2d1e0`→`681ba13`): pack JSON en `/listas`, long-prompt >1020, álbumes, `/estado` tarjeta. Item 2 Face Swap (`db8a43b`→`3d0dc37`): `/cambiar_source` + confirm single/batch. Item 3 edición `/s` (`fd8dd43`→`841df64`): `/cambiar_referencia` + regen integrate. `D8_COMMANDS == ()`; §2 sin flujos degradados. |
| **Deploy** | grokV2 como bot permanente | decisión de deploy (owner) | **Resuelto 2026-09-04** — `grok-bot.service` repunteado a grokV2 (`.venv/bin/grokbot`), administrado por el shell `grokbot`; `enable`+start, lingering activo, allowlist activa desde `.env` (detalle abajo). v1 decommissioned (unidad respaldada). |
| **Smoke live** | Probar con infraestructura real | follow-up (owner) | **Completado 2026-09-04** con credenciales reales de grok v1: bug del panel `/config` corregido (nota abajo) y recorrido §4.3 validado por el owner — **todo ok** (sesión 36 updates, 0 errores). Pendiente: decidir el deploy (abajo). |
| **T1** | Mostrar el **tiempo que tarda cada generación** (hoy NO se muestra) | follow-up de producto (request del owner, uso real 2026-09-05) | **Pendiente** — añadir el elapsed time al terminal/status de generación (single, batch, video, refine, faceswap, `/s`) y al terminal de resultados. |
| R1/R2 | Baseline sucio de `grok/**` + DeprecationWarnings pytest-asyncio (Py3.14) | out-of-scope | cosmético / no tocar |

Registro detallado con origen y archivos: `.grok/agent-memory/residuals/grokv2-rearch.md`
(proceso, no commiteado en el detalle por convención de artefactos de pool).

**Wave-2/R4 — flujos R4 operativos (2026-09-05):** la wave rearch dejó 9 flujos de grok
degradando D8 (§2). La wave-2 los habilitó en 3 ítems con parity de copy byte-byte
(seams de providers/repos, use cases en `application`, handlers de presentación, tests
0-mock) y retiró el registro D8 del árbol:

- **Item 1 — utilidades** (`fa2d1e0`→`681ba13`, suite 521→556): pack JSON en `/listas`,
  long-prompt collection (>1020), álbumes/media groups con edición secuencial, `/estado`
  como tarjeta de configuración.
- **Item 2 — Face Swap** (`db8a43b`→`3d0dc37`, suite →613): `/cambiar_source`, modo
  faceswap, swap Replicate de 2 imágenes con confirm single/batch y cancel, fallback
  foto-a-foto del media group.
- **Item 3 — edición con referencia `/s`** (`fd8dd43`→`841df64`, suite →653):
  `/cambiar_referencia`, `/s` en single/álbum/long-prompt/regen-integrate con prereq
  provider xAI, retiro total de D8.

Suite final **653 passed** (521→556→613→653). Detalle: SUMMARYs en
`.planning/quick/20260904-grokv2-r4-item{1,2,3}/`, reviews en
`.grok/agent-memory/review/`, learnings en `.grok/agent-memory/documentador/`.

**Smoke live — hallazgo corregido y validación completa (2026-09-04):** el panel de
`/config` se re-renderizaba con la config del PROPIO BOT en vez de la del dueño: en Telegram
real el mensaje del panel (bot-sent) lleva `from_user` = el bot, y los showers leían
`get_config(target.from_user.id)` → la pantalla siempre mostraba el default (Grok Imagine /
Kie / Alta calidad) sin importar el tap; seedream/faceswap "volvían" a Grok Imagine y
video/ComfyUI parecían no cambiar (la generación sí usaba el modelo correcto). Los **519
tests offline NO lo veían**: los fakes de telegram construyen el mensaje del panel con
`from_user` = el dueño. Fix `742b566` (`uid` explícito en los showers) + 2 regresiones que
simulan el mensaje del bot (from_user = bot). Suite **521 passed**. Tras el fix el owner
validó en vivo el recorrido completo de §4.3 y reportó **todo ok** (36 updates manejados,
0 errores). Registro completo en el residual registry.

---

## 4. ¿Listo para arrancar?

**Código: sí.** El entrypoint `grokbot` (`main.run()`) hace polling con fail-fast claro y
arranque verificado por 519 tests offline. Para levantarlo **en vivo** se necesita:

### 1. Credenciales (fail-fast al boot)

Copiar `.env.example` → `.env` y completar. **Obligatorias**: `TELEGRAM_BOT_TOKEN`,
`REPLICATE_API_TOKEN`, `XAI_API_KEY`. **Opcionales**: `KIE_API_KEY`, `COMFYUI_HOST`/`PORT`
(vacíos = provider deshabilitado; el registry degrada a `ProviderNotConfiguredError`
user-safe). Sin obligatorias el boot sale con código 2 listando solo nombres de campo.

```bash
cp .env.example .env   # y completar los valores
.venv/bin/grokbot      # arranca polling
```

### 2. Decisiones de deploy

**Ejecutado 2026-09-04** — grokV2 corre como bot permanente: unidad systemd
`grok-bot.service` (user) con `WorkingDirectory=/home/ubuntu/repos/grokV2` y
`ExecStart=.venv/bin/grokbot`, `enable` + `start` + lingering activo (arranca al boot sin
login), administrable con el shell interactivo `grokbot` (`grokbot status|start|stop|restart|
logs|enable|disable` o el menú). Backup de la unidad v1 en `grok-bot.service.bak-v1`.
Referencia de decisiones:

- **Perímetro (R9)**: el bot responde SOLO en chat privado (grupos/canales reciben deny).
- **`ALLOWED_TELEGRAM_IDS`**: con allowlist vacía el bot queda **abierto a quien te escriba en
  privado** (aviso en el boot). Hoy **activa** — viene del `.env` copiado de v1 (el boot lo
  reporta `allowlist=activa`).
- **`GROK_DATA_DIR`**: no seteado → default `./data` relativo al `WorkingDirectory` del unit
  (`/home/ubuntu/repos/grokV2/data`, el mismo data dir validado en el smoke). Sin necesidad de
  variable.
- Sin webhook (D9): solo polling. Sin límites de uso (por decisión): sin tope de procesos
  activos ni cuota horaria.

### 3. Smoke live — EJECUTADO y validado (2026-09-04)

Los tests son **0-red por política**: ningún endpoint real ni el envío real a Telegram se
tocó en la suite. El smoke con infraestructura REAL sí se hizo (credenciales de grok v1
copiadas a `.env`, servicio v1 detenido → sin conflicto de polling) y el owner **validó todo
el recorrido: todo ok** (36 updates manejados, 0 errores). Lo que se usó, pieza por pieza
(queda como registro del recorrido validado):

**a) Credenciales / entorno (las mismas que ya usa el bot en `.env`):**
1. `TELEGRAM_BOT_TOKEN` — token real del bot (BotFather). **Mueve el bot real al arrancar.**
2. Tu **ID numérico de Telegram** (owner) — para escribirle al bot en privado y para
   `ALLOWED_TELEGRAM_IDS`. Sin él el smoke no puede enviarte nada ni afirmar que el bot es
   tuyo. Conseguirlo: escribe a @userinfobot y léelo de su respuesta.
3. `XAI_API_KEY` — para el modelo Grok Imagine (imagen xai/grok).
4. `REPLICATE_API_TOKEN` — para Seedream 5.0 y modelos de Replicate.
5. `KIE_API_KEY` — para Kie (grok, el default; y video de imagen).
6. `COMFYUI_HOST` (+ `PORT` si no es 22) — host SSH de tu box de ComfyUI; solo hace falta si
   quieres probar multipose / refine 2-stage / modelos comfyui.
7. `GROK_DATA_DIR` — ruta absoluta del data dir (no dejar el `./data` relativo).

**b) Pasos interactivos en Telegram (recorrido ejecutado y validado por el owner):**
1. Arrancar con `ALLOWED_TELEGRAM_IDS=<tu id>` y el entorno de (a): `.venv/bin/grokbot`.
2. En privado: `/start` → debe responder con el estado actual.
3. Prompt de texto → confirmar (botón) → esperar la imagen real → debe llegar como foto.
4. Tocar **Regenerar** bajo esa imagen → debe regenerar (owner correcto).
5. `/config` → cambiar de modelo a `seedream` → prompt → imagen por Replicate.
6. Foto + caption de edición → imagen editada (Kie o el provider configurado).
7. Video: `grok_video` o ComfyUI video → llega un video; si Telegram lo rechaza por tamaño,
   el mensaje ofrece el enlace de recuperación.
8. ComfyUI: `/config` → modelo comfyui multipose/refine → confirmación de refine (botón) →
   refinada.
9. `/listas` y `/variables 3` → batch de 3 (con cancel en medio, opcional).

Cada proveedor se valida con un job real; si un provider falla, el mensaje degrada user-safe
(eso también es parte del smoke: ver que el error llega limpio). En esta corrida no hubo
ningún error ni degradación inesperada.

### 4. (Opcional) Harness live automatizable

Se puede añadir un runner opt-in (p. ej. `tools/live_smoke.py`) que ejecute un job por
proveedor con los providers REALES escribiendo el media a disco (sin Telegram), útil para
validar credenciales xai/replicate/kie/comfyui de forma repetible; el tramo de Telegram
(botones/media) se mantiene manual. No se incluye todavía: requiere decidir dónde vive y no
debe correr sin las credenciales de (a).

---

## 5. Cómo se construyó (trazabilidad corta)

- Pipeline hardener-agile: las 6 fases SPEC como **ítems del pool** (cada una con
  impacto→planner→executor→arch→test-guardian→tests+commit-gate) y el **review-loop de
  3 reviewers únicamente al cierre** sobre el diff acumulado de los 6 ítems.
- Review de cierre: 18 hallazgos crudos → **14 únicos (C1–C15)**: 12 fixed + 3 wontfix→
  residuales (R8/R9/R10) + 1 nit re-abierto (C15) → fixed en `2f7ab39`. **0 issues en
  Round 3**.
- Progresión de suite por gate: 48→204→300→302→304→482→487→493→502→503→511→**513**.
- Wave de hardening R8/R9/R10 (2026-09-04, decisión del owner): 513→514 (R10 `096dd0d`)→521
  (R8 `c25c1fe`)→**519** (R9 `2bbfdd3`+`5900809`+`5e9e88b`, elimina los tests de los límites).
- Smoke live (2026-09-04, infra real con credenciales de grok v1): 519→**521** — hallazgo:
  el panel de `/config` se re-renderizaba con la config del BOT (mensaje del bot con
  `from_user` = el bot) en vez de la del dueño; fix `742b566` + 2 regresiones que simulan el
  mensaje del bot. **Validación final del owner: todo ok** — recorrido §4.3 completo con
  36 updates manejados y 0 errores. Registro: `.grok/agent-memory/residuals/grokv2-rearch.md`.
- Wave-2/R4 (2026-09-05): 521→**653** — los 9 flujos D8 (R4) habilitados en 3 ítems con
  parity de copy grok: Item 1 utilidades →556, Item 2 Face Swap →613, Item 3 edición `/s`
  →653. Review-loop por ítem hasta 0 issues (R1: Item1 7 opens / 2 rounds, Item2 6+1 /
  3 rounds, Item3 6+1 / 3 rounds). `D8_COMMANDS == ()`.
- Artefactos de proceso (sin commit, untracked por convención del pool):
  `.planning/quick/20260903-grokv2-rearch/CLOSURE.md` (resultado + learnings),
  `.grok/agent-memory/review/grokv2-rearch-poolclose.md` (review completo),
  `.grok/agent-memory/residuals/grokv2-rearch.md` (registry final, sí trackeado).
