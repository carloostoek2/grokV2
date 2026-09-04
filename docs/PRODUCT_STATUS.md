# grokV2 — Estado de producto (consolidado)

> Fuente única de estado del producto `grokbot` (@grokV2). Arquitectura objetivo:
> `docs/SPEC_REFACTOR.md`. Fecha de consolidación: 2026-09-04.
> HEAD verificado: `72e3a6f` — suite completa **513 passed** · baseline `grok/**` intacto.

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
| `application/` | Use cases como generadores de **10 eventos frozen**; sin I/O |
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
- **Calidad**: **513 tests passed**, política **0-red / 0-mock** — el bot se prueba 100%
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

## 3. Pendientes / follow-ups diferidos (ninguno bloquea arranque)

| ID | Pendiente | Clase | Acción sugerida |
|---|---|---|---|
| **R4** | 9 flujos degradados (§2) | follow-up de producto | cablear use cases/datos por flujo |
| **R8** | Botón "Regenerar" no scopeado al owner (paridad grok) | hardening | persistir `owner_uid` en `generation_refs` y validarlo en el callback `regen` |
| **R9** | Bot abierto por default + sin rate limit + sesiones sin TTL | hardening de deploy | fijar `ALLOWED_TELEGRAM_IDS`; evaluar rate limit y expiración de sesión |
| **R10** | Video remoto rechazado publica la URL firmada; rama ">50MB → texto" muerta | hardening | mover tope 50MB a config única; ocultar URL tras comando autenticado |
| R1/R2 | Baseline sucio de `grok/**` + DeprecationWarnings pytest-asyncio (Py3.14) | out-of-scope | cosmético / no tocar |

Registro detallado con origen y archivos: `.grok/agent-memory/residuals/grokv2-rearch.md`
(proceso, no commiteado en el detalle por convención de artefactos de pool).

---

## 4. ¿Listo para arrancar?

**Código: sí.** El entrypoint `grokbot` (`main.run()`) hace polling con fail-fast claro y
arranque verificado por 513 tests offline. Para levantarlo **en vivo** se necesita:

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

- **`ALLOWED_TELEGRAM_IDS`**: por defecto el bot es **abierto** (allowlist vacía = todos).
  Fijarla si no se quiere permitir cualquier usuario (ver R9).
- **`GROK_DATA_DIR`**: default `./data` relativo al cwd → correr desde la raíz del repo o
  setear ruta absoluta en deploy.
- Sin webhook (D9): solo polling.

### 3. Smoke live pendiente (el pool no lo hizo)

Los tests fueron **0-red por política**: ningún endpoint real de xai/replicate/kie/comfyui
se tocó en vivo durante el pool. Antes de producción conviene un smoke real por proveedor
(un job de imagen, uno de video y un refine ComfyUI contra `COMFYUI_HOST` real).

---

## 5. Cómo se construyó (trazabilidad corta)

- Pipeline hardener-agile: las 6 fases SPEC como **ítems del pool** (cada una con
  impacto→planner→executor→arch→test-guardian→tests+commit-gate) y el **review-loop de
  3 reviewers únicamente al cierre** sobre el diff acumulado de los 6 ítems.
- Review de cierre: 18 hallazgos crudos → **14 únicos (C1–C15)**: 12 fixed + 3 wontfix→
  residuales (R8/R9/R10) + 1 nit re-abierto (C15) → fixed en `2f7ab39`. **0 issues en
  Round 3**.
- Progresión de suite por gate: 48→204→300→302→304→482→487→493→502→503→511→**513**.
- Artefactos de proceso (sin commit, untracked por convención del pool):
  `.planning/quick/20260903-grokv2-rearch/CLOSURE.md` (resultado + learnings),
  `.grok/agent-memory/review/grokv2-rearch-poolclose.md` (review completo),
  `.grok/agent-memory/residuals/grokv2-rearch.md` (registry final, sí trackeado).
