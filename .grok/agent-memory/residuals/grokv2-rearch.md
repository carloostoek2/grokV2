# Residuales — pool grokv2-rearch

Pool: grokv2-rearch · Fuente: ítems del pipeline. Clasificación §5b.

## R1 — grok: cambios pre-existentes sin commitear
- Origen: gsd-executor item1 (baseline detectado al arrancar y en cada gate).
- Clase: out-of-scope (documentar). Archivos: /home/ubuntu/repos/grok/sources/6181290784.jpg (M),
  /home/ubuntu/repos/grok/variables_packages/hot.json y sexy.json (untracked). Posible trabajo en curso de la dueña.
- Acción: documentar. NO tocar grok.

## R2 — DeprecationWarnings pytest-asyncio 0.26 bajo Python 3.14
- Origen: gsd-executor item1 (tooling, afecta a todo el pool).
- Clase: out-of-scope (documentar). Cosmético, suite verde.
- Acción: documentar; posible follow-up tooling del pool (downgrade/ajuste pytest-asyncio) → deferred.

## R3 — anonimizar tests/unit/domain/test_user_config.py (ítem 1)
- Origen: arch-enforcer item3 (O5).
- Clase: in-scope-followup del pool (limpieza/privacidad, test-only).
- Detalle: contiene ID real de Telegram 6181290784 y path absoluto /home/ubuntu/repos/grok/sources/6181290784.jpg (viola regla de fixtures anonimizados del pool).
- Acción: fix en el review-loop de cierre (anonimizar con usuario 111111111 y paths dummy preservando forma).
