# Propuesta: parámetros de muestreo descubiertos desde el workflow

Análisis de la rama master, 2026-11-03. Esta propuesta no implementa todavía el editor dinámico.

## Viabilidad

Es viable integrar un flujo y descubrir sus parámetros sin registrar manualmente sus nodos. El bot ya obtiene el grafo API antes de encolarlo. ComfyUI expone `GET /object_info` (y `/object_info/{class_type}`): describe inputs requeridos/opcionales, tipos, opciones de enumeración, mínimos, máximos y pasos de los nodos instalados. El grafo aporta los valores efectivos y las conexiones; el esquema aporta validación y controles. Usar solo el JSON permite reconocer valores, pero no conocer de forma fiable sus límites ni todas las opciones disponibles.

La implementación debe combinar ambas fuentes por el túnel existente y cachear el esquema por servidor/clase. Si Colab está desconectado, se puede mostrar la última configuración conocida, pero no afirmar que esté validada para una versión distinta del nodo.

## Casos comprobados en las plantillas

- `agil_solo` / `agil_moody_hq`: un KSampler con seed, steps, cfg, sampler_name, scheduler y denoise literales. Descubrimiento y edición directos.
- `grok_style` / `ohwx_krea2`: dos KSamplers, generación y refinado, con diferentes pasos y denoise. Mostrar cada nodo por separado; no aplicar un único denoise a ambos.
- `grok_style_hq`: ambos KSamplers reciben seed del SeedNode 200. Mostrar su origen compartido; la política de semillas tiene prioridad sobre el editor de parámetros.
- `agil_edit_qwen` / `agil_edit_hq`: steps y cfg son conexiones a switches que dependen del modo Lightning/base. No son números literales. El HQ también tiene un KSampler de SeedVR2, con requisitos propios.
- `agil_moody`: KSamplerAdvanced usa noise_seed, start_at_step y end_at_step. No tiene exactamente el formulario del KSampler simple.
- `flux1_dev_t2i`: SamplerCustomAdvanced, RandomNoise, KSamplerSelect, scheduler y guider separados. Para editar el muestreo completo hay que recorrer este subgrafo.
- `donut_face`: DonutSampler con cfg_start/cfg_halfway/cfg_end y opciones propias, además de wildcard processors y un SeedNode compartido. Su esquema puede descubrir campos, pero no explica todas sus dependencias semánticas.

## Descubrimiento y presentación

1. Recorrer el grafo limpio y detectar KSampler y KSamplerAdvanced por class_type. Identificar cada instancia por id, sin asumir ids numéricos simples (hay ids como `433:3`). Conservar los títulos de nodo antes de que el resolver los descarte, solo para presentación.
2. Cruzar inputs del grafo con el esquema de ComfyUI. Generar controles para INT, FLOAT, BOOLEAN, STRING y enumeraciones. Excluir conexiones MODEL, CONDITIONING, LATENT, IMAGE y otros objetos del formulario de escalares.
3. Mostrar valor original, origen (literal o conectado) y ajuste guardado. Separar varias instancias por título/id y, cuando sea posible, por su posición respecto a las salidas.
4. Para valores conectados, conservar por defecto la conexión. Si el origen es un primitivo inequívoco, mostrar su valor y quién más lo consume. Para switches o cálculos, mostrar “controlado por el flujo”; ofrecer un override local explícito del input destino. Ese override sustituye la conexión solo en la copia enviada a generar, sin modificar el nodo origen ni otros consumidores. Restaurar elimina el override y recupera la conexión.
5. No ejecutar código remoto para evaluar un grafo ni suponer que cualquier nombre con “Sampler” tiene la misma semántica. Para clases personalizadas, permitir una ficha genérica por nodo con su esquema; el soporte especializado se registra por clase de nodo, nunca por flujo. El conjunto de campos visibles puede ser automático, pero no se puede prometer interpretar cualquier nodo de terceros sin conocer su contrato.

## Persistencia y aplicación

Guardar mediante SessionRepository una estructura por usuario, flow_id, node_id e input, con class_type y firma del esquema. Conservar solo overrides, no una copia completa del workflow. Los valores originales siguen procediendo de la plantilla vigente. Evitar colisiones entre usuarios y flujos aunque compartan los mismos ids de nodo.

En cada generación: resolver el workflow vigente → descubrir/validar → copiar grafo → aplicar overrides → aplicar prompt, fuente y política de semillas → encolar. Los valores deben conservar tipos (rechazar bool como int), límites, enumeraciones y campos permitidos. Si una plantilla cambia de clase, elimina un nodo o vuelve incompatible un campo, marcar el ajuste como obsoleto y explicar cómo restablecerlo; nunca aplicarlo a otro nodo por coincidencia de id. El control de seed/noise_seed debe quedar reservado a la política de semillas para no tener dos fuentes contradictorias.

Capturar workflow y ajustes efectivos al iniciar un lote, o fijar su versión/firma, permite evitar cambios a mitad de ejecución. La implementación de semillas captura la configuración de usuario; todavía no fija una versión del workflow remoto. Guardar en los resultados los parámetros efectivos y la versión del flujo permitiría reproducirlos y auditar qué overrides se aplicaron.

## Capas y entrega recomendada

- Provider: lectura de object_info, descubrimiento del grafo y validación contra el servidor.
- Domain: descriptores puros de parámetros y overrides tipados.
- Application: consultar/guardar/restablecer ajustes, y capturar configuración efectiva del lote.
- Repository: persistencia por usuario y flujo con lectura compatible con sesiones anteriores.
- Telegram: formulario paginado generado desde descriptores; seleccionar nodo/campo, introducir valor y restaurar valores del flujo.

Primera entrega: KSampler y KSamplerAdvanced, varios nodos, campos literales y overrides explícitos de campos conectados. Segunda: subgrafos SamplerCustomAdvanced y fichas genéricas de nodos personalizados. No se necesita un mapa manual por flujo en ninguna fase.

Validación necesaria: múltiples samplers; seeds compartidas; ids con dos puntos; switches y primitivos compartidos; esquemas cambiantes; campos fuera de rango; aislamiento por usuario/flujo; persistencia tras reinicio; restaurar conexiones; servidor desconectado; lote con configuración capturada; overrides obsoletos; política de seed con prioridad. Usar fakes y respuestas HTTP locales según AGENTS.md.
