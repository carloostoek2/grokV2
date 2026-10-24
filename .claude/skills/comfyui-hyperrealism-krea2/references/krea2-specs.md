# Krea 2: especificaciones y ecosistema

Contenido de referencia. Todo lo marcado [V] viene de documentación oficial (docs.comfy.org, repo krea-ai/krea-2, model cards de Hugging Face). Verifica novedades con búsqueda web: el ecosistema cambia rápido.

## Tabla de contenidos
1. Arquitectura
2. Variantes y parámetros oficiales
3. Archivos y cuantizaciones
4. Prompt enhancer y referencias de estilo
5. API (partner nodes)
6. LoRAs y finetunes de la comunidad
7. Requisitos y licencia
8. Fuentes

## 1. Arquitectura [V]
- DiT denso de ~12B parámetros, entrenado desde cero, enfoque estético y de diversidad.
- Codificador de texto: Qwen3-VL-4B-Instruct (agrega características de varias capas). **No hay CLIP.**
- VAE: Qwen Image VAE (`qwen_image_vae.safetensors`).
- Muestreo: flow matching con shift de timestep dependiente de la resolución.

## 2. Variantes y parámetros oficiales [V]

| | RAW | Turbo |
|---|---|---|
| Naturaleza | Base sin destilar ni post-entrenar | Destilado a 8 steps |
| Steps | 52 | 8 |
| CFG | 3.5 | 0 (ComfyUI: cfg 1.0) |
| mu (shift) | Interpolado por resolución (y1=0.5 en resolución mínima, y2=1.15 en máxima) | Fijo 1.15 |
| Resolución | Entrenado hasta ~1K | 1K a 2K (referencia oficial 2048x2048) |
| Sampler en plantillas | euler | euler, scheduler simple |
| Negative prompt | Funciona (CFG>1) | Sin efecto con CFG 1 |
| Rol | Entrenar LoRAs y componer con control | Inferencia rápida y final |

Regla de oro oficial: **entrena LoRAs sobre RAW, ejecútalas sobre Turbo.** Los LoRAs de RAW se aplican bien a Turbo.

Errores típicos al mezclar reglas (fuente: ficha GGUF de Turbo):
- Imagen lavada, sobre-suavizada o desordenada → CFG activo en Turbo. Poner cfg=1.0.
- Imagen borrosa o inacabada → pocos steps o ajustes de RAW aplicados a Turbo. Usar 8 steps con scheduler simple.

## 3. Archivos y cuantizaciones
- Carpeta Comfy-Org/Krea-2 en Hugging Face: diffusion models en FP8 scaled (recomendado para la mayoría), BF16, NVFP4, MXFP8, INT8 convrot.
- Text encoder: `qwen3vl_4b_fp8_scaled.safetensors`.
- GGUF de Turbo (ByteShape): de Q3_K_M (~6.3 GB) a Q8_0 (~14.3 GB). Requiere ComfyUI-GGUF (loader Unet GGUF).
- Rutas: `models/diffusion_models`, `models/text_encoders`, `models/vae`, `models/loras`.
- Requiere ComfyUI actualizado (el blog oficial indica 0.26.0 al lanzar el modelo abierto). Si faltan nodos, actualizar.

## 4. Prompt enhancer y referencias de estilo [V]
- Las plantillas oficiales traen un subgrafo con `prompt_enhance` (ON/OFF) y `LLM_max_token`, usando un system prompt del equipo de Krea.
- Controles del subgrafo: prompt de usuario, prompt_enhance, LLM_max_token, ancho/alto (ResolutionSelector, megapixels hasta 2.0), seed, enable_lora, fuerza de LoRA, trigger word.
- Workflow de style reference: Turbo INT8 convrot + `krea2_style_reference.safetensors`, 1–2 imágenes de referencia.
- Nueve LoRAs de estilo oficiales (darkbrush, dotmatrix, kidsdrawing, neondrip, rainywindow, retroanime, softwatercolor, sunsetblur, vintagetarot), fuerza recomendada 1.0 con su trigger word. Son artísticos, no de realismo.

## 5. API / partner nodes [V]
- Krea 2 Medium: ilustración y estilos pictóricos. Krea 2 Large: fotorrealismo, con estética de cámara (grano, desenfoque de movimiento, rango dinámico natural).
- El nodo de imagen admite hasta 10 imágenes de referencia de estilo y un `moodboard_id` (UUID de un moodboard de Krea).
- Parámetro `creativity` en algunos proveedores: controla cuánto expande el prompt.
- No hay control de sampler/CFG/steps.

## 6. Comunidad (verificar licencia y calidad antes de recomendar)
- **Inline Skin LoRA (RAW)**: textura de piel fotorrealista. Trigger al inicio del prompt: `inline-skin-lora, detailed skin texture`. Funciona sobre RAW y también sobre Turbo. Ficha sugiere negativos (airbrushed, plastic skin, waxy...) que aplican en RAW. Limitaciones según la ficha: tiende a envejecer menos de lo pedido, y flash directo duro puede suavizarse hacia luz de estudio.
- **CyberRealistic Krea 2**: finetune completo de Turbo, más piel natural y mayor respuesta a cámara, lente, luz y materiales; guidance 0.0. Estructura de prompt que recomienda: sujeto → escenario → composición/cámara → luz → ánimo/estilo → material y textura.
- **Krea2-realism-V2** (LoRA): su autor reporta que funciona a 1.0 pero prefiere 1.5–2.0; trátalo como dato de autor, no como norma.
- Hay muchos LoRAs de estilo en la colección de Krea en Hugging Face.

## 7. Licencia
Krea AI Community License. Uso comercial por encima de ciertos umbrales requiere licencia de empresa. Remitir siempre al texto oficial (krea.ai/krea-2-licensing).

## 8. Fuentes
- https://docs.comfy.org/tutorials/image/krea/krea-2
- https://github.com/krea-ai/krea-2
- https://huggingface.co/krea/Krea-2-Turbo
- https://blog.comfy.org/p/krea-2-open-source-models-are-now
- https://huggingface.co/byteshape/Krea-2-Turbo-GGUF
- https://huggingface.co/inlineresearch/skin-lora-krea-2-raw
