# grokV2 — Estado de producto (consolidado)

> Fuente única de estado del producto `grokbot` (@grokV2). Arquitectura objetivo:
> `docs/SPEC_REFACTOR.md`. Fecha de consolidación: 2026-09-04.
> HEAD verificado: `5e9e88b` — suite completa **519 passed** · baseline `grok/**` intacto.
> Wave de hardening R8/R9/R10 cerrada (detalle en §3).

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

- **Comandos funcionales**: `/start`, `/config` (FSM), `/listas` (CRUD paquetes/plantillas),
  `/variables` + `/var` (batch random + prompt fijo, multipose, shuffle/blacklist,
  skip-on-fail, cancel), generación de imagen (xai/replicate), video (kie/comfyui),
  **refine 2-stage** con confirmación ComfyUI (single y batch, token opaco + owner gate +
  timeout), jobs de imagen con `cancel_job`, allowlist en message y callback_query.
- **Perímetro (R9, bot single-owner)**: SOLO chat privado (gate global en message y callback);
  sin límites de uso por decisión (sin tope de procesos activos ni cuota horaria); con la
  allowlist vacía el boot avisa y el bot queda abierto a quien te escriba en privado.
- **Calidad**: **519 tests passed**, política **0-red / 0-mock** — el bot se prueba 100%
  offline contra `FakeTelegramGateway` grabador; el review de cierre del pool (3 reviewers:
  general + security + plan) fue íntegramente offline y terminó en **0 issues (Round 3)**.
- **Errores user-safe**: los mensajes de boot/error jamás exponen tokens/IDs/payloads
  (solo nombres de campo o `type(exception).__name__`).

---

## 2. Funcionalidad vs degradaciones

### Flujos operativos (no degradados)

Imagen (xai/replicate), video (kie/comfyui), `/variables`+`/var` (random y prompt fijo,
incl. multipose), refine 2-stage, `/config`, `/listas` (sobre payloads persistidos),
jobs single-image (`cancel_job:<id>`), allowlist. Backing completo de capa `application`.

### Flujos degradados D8 (responden con mensaje user-safe, NO silencioso) — R4

Estos flujos de grok **no** tienen backing en esta versión y responden con un mensaje
claro de no-disponible. Requieren un use case o dato de providers antes de cablearse:

1. Face Swap (modo `faceswap` + pipeline de swap).
2. Álbumes entrantes / media groups (recibir varias fotos de una).
3. `integrate_ref` (`/s` foto + caption con referencia).
4. Long-prompt collection (caption > 1024 en foto sin caption limpio).
5. `/cambiar_source` (configurar cara fuente de Face Swap).
6. `/cambiar_referencia` (referencia de estilo/integración).
7. `/estado` (estado de un job/cola por mensaje — hay backing parcial en `JobManager`).
8. Regen de integración (regenerar un resultado de `integrate_ref`).
9. Crear paquete de variables pegando JSON (el resto del flujo de paquetes sí opera).

---

## 3. Pendientes / follow-ups (2026-09-04 — wave de hardening R8/R9/R10 cerrada)

Wave de hardening cerrada por decisión de producto (bot privado single-owner): R8, R9 y R10
resueltos. Ver commits y cierre en `.grok/agent-memory/residuals/grokv2-rearch.md`.

| ID | Pendiente | Clase | Estado / Acción sugerida |
|---|---|---|---|
| **R8** | Botón "Regenerar" no scopeado al owner | hardening | **Resuelto** — `c25c1fe` (`owner_uid` en refs + validación en `regen`). |
| **R9** | Sin límites de uso + perímetro | hardening / decisión | **Cerrado por decisión** — sin tope de procesos ni cuota horaria (`2bbfdd3`, `5900809`); gate SOLO chat privado + aviso de allowlist abierta (`5e9e88b`). |
| **R10** | Tope 50MB duplicado; URL firmada | hardening / decisión | **Resuelto** — `096dd0d` (tope único `MAX_MEDIA_BYTES`); la URL se muestra: camino de recuperación por decisión del owner. |
| **R4** | 9 flujos degradados D8 (§2) | follow-up de producto (wave 2) | cablear use cases/datos por flujo. Degradaciones user-safe activas. |
| **Deploy** | Fijar `ALLOWED_TELEGRAM_IDS=<tu ID numérico>` | decisión de deploy (owner) | Hoy: SOLO chat privado, pero con allowlist vacía CUALQUIER user en DM puede usar el bot (costo real). El boot avisa. |
| **Smoke live** | Probar con infraestructura real | follow-up (owner) | Lista de credenciales y pasos en §4.3. |
| R1/R2 | Baseline sucio de `grok/**` + DeprecationWarnings pytest-asyncio (Py3.14) | out-of-scope | cosmético / no tocar |

Registro detallado con origen y archivos: `.grok/agent-memory/residuals/grokv2-rearch.md`
(proceso, no commiteado en el detalle por convención de artefactos de pool).

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

- **Perímetro (R9)**: el bot responde SOLO en chat privado (grupos/canales reciben deny).
- **`ALLOWED_TELEGRAM_IDS`**: con allowlist vacía el bot queda **abierto a quien te escriba en
  privado** (aviso en el boot). Para dejarlo solo para tu cuenta, fíjalo a tu ID numérico de
  Telegram (con @userinfobot lo ves). Es la ÚNICA pieza de identidad que falta por config.
- **`GROK_DATA_DIR`**: default `./data` relativo al cwd → correr desde la raíz del repo o
  setear ruta absoluta en deploy.
- Sin webhook (D9): solo polling. Sin límites de uso (por decisión): sin tope de procesos
  activos ni cuota horaria.

### 3. Smoke live pendiente — qué se necesita exactamente (el pool no lo hizo)

Los tests son **0-red por política**: ningún endpoint real ni el envío real a Telegram se
tocó en vivo. La suite offline NO reemplaza este smoke; para probar con la infraestructura
real hacen falta **tus credenciales y pasos interactivos** (no se pueden hacer con fakes ni
mocks). Lo que se necesita, pieza por pieza:

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

**b) Pasos interactivos en Telegram (los hace el humano; no son automatizables de forma
fiable porque hay botones y media de ida y vuelta):**
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
(eso también es parte del smoke: ver que el error llega limpio).

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
- Artefactos de proceso (sin commit, untracked por convención del pool):
  `.planning/quick/20260903-grokv2-rearch/CLOSURE.md` (resultado + learnings),
  `.grok/agent-memory/review/grokv2-rearch-poolclose.md` (review completo),
  `.grok/agent-memory/residuals/grokv2-rearch.md` (registry final, sí trackeado).
