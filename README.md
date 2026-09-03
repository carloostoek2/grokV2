# grokbot (grokV2)

Re-implementación por capas del bot de Telegram `@grok` (generación de imagen/video
con xAI Grok Imagine, Replicate, Kie.ai y ComfyUI remoto). Ver `docs/SPEC_REFACTOR.md`
para la arquitectura objetivo (domain → application → providers/repositories → telegram).

## Layout

```
src/grokbot/
├── settings.py      # Settings (pydantic-settings) + get_settings() cacheado
└── domain/          # entidades puras (stdlib, sin I/O)
tests/
├── conftest.py      # aislante de env + caché de settings
└── unit/            # tests de dominio y settings
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Tests

```bash
.venv/bin/pytest tests/unit -q
```
