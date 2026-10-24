# Diagnóstico: síntoma → causa → palanca

Orden de los arreglos: de menor a mayor riesgo. Etiquetas [V]/[H] como en el resto del skill.

## Tabla de contenidos
1. Piel y rostro
2. Look general "IA"
3. Nitidez, lavado y borrosidad
4. Upscale y refinado
5. Composición y anatomía
6. Consistencia e identidad
7. Rendimiento

## 1. Piel y rostro

**Piel plástica, cerosa, "airbrushed"**
Causas probables: prompt sin fuente de luz ni textura; CFG alto en RAW; estética pulida del modelo base; ausencia de LoRA de piel.
Arreglos, en orden:
1. Prompt: luz concreta (fuente, dirección, caída), poros visibles, vello fino, imperfecciones; evita "perfect skin", "flawless", "8k masterpiece".
2. LoRA de piel (p. ej. Inline Skin LoRA con su trigger) a 0.6–1.0 [H].
3. RAW: bajar CFG hacia 2.5–3.0 y añadir negativos (airbrushed, plastic skin, waxy, smooth featureless skin, cgi) [V que RAW admite negativos].
4. Pasada de detalle facial con denoise 0.30–0.40 (ver pipelines) [H].
5. Grano fino al final: enmascara la suavidad residual [H].

**Piel con brillo excesivo / especular**
Prompt: pedir acabado mate o piel con brillo natural moderado, luz difusa. Evitar flash directo duro si el LoRA lo suaviza. Bajar CFG en RAW.

**Ojos muertos o sin catchlight**
Prompt: describir la fuente de luz y un catchlight por ojo. Pasada de detalle con máscara de ojos, denoise 0.25–0.35 [H].

**Dientes o labios raros**
Pasada local con máscara, denoise 0.35–0.45, prompt recortado a esa zona [H].

**Pelo "casco" o pegado**
Prompt: mechones sueltos, vello fino, luz de contorno. Si persiste, detalle local con denoise 0.30–0.40 [H].

## 2. Look general "IA"

**Brillo editorial, colores demasiado limpios**
Causa: el modelo base es "aesthetic first" y tiende a pulir (descrito en la ficha de CyberRealistic).
Arreglos:
1. Lenguaje de cámara en 2–3 señales compatibles (formato, lente, apertura, tipo de luz) en vez de adjetivos sueltos.
2. Imperfección específica y plausible: ropa arrugada, reflejos parásitos, fondo no ordenado, luz disponible.
3. Finetune orientado a realismo, o LoRA de realismo [V que existen; calidad según ficha].
4. Etapa de acabado: grano, viñeta sutil, curva de color moderada [H].

**Todo parece render 3D**
Quitar términos de "8k", "ultra detailed", "masterpiece"; describir material y textura concretos; revisar que no haya CFG alto en RAW.

## 3. Nitidez, lavado y borrosidad

**Imagen lavada o sobre-suavizada (Turbo)**
CFG distinto de 1.0 [V]. Corregir a 1.0.

**Imagen borrosa o inacabada (Turbo)**
Steps distintos de 8, scheduler incorrecto o ajustes de RAW [V]. Usar 8 steps, euler + simple.

**Sobresaturado y contrastado (RAW)**
CFG alto. Bajar hacia 3.0–3.5; revisar fuerza de LoRA [H].

**Falta microdetalle aunque el resto es correcto**
1. Subir precisión del modelo (FP8 → BF16) si hay VRAM [H].
2. Pasada de refinado tileada con denoise 0.20–0.30 [H].
3. Evitar cuantizaciones muy bajas (Q3/Q4) para el render final [H].

## 4. Upscale y refinado

**Textura pastosa o "óleo" tras upscale**
Causa: upscale de latente agresivo o denoise demasiado alto/bajo con modelo de upscale suave.
Arreglo: upscale por modelo, reescalar al factor final, pasada tileada con denoise 0.20–0.30 [H].

**Costuras o cuadrícula visible**
Subir overlap de tiles (64–128), usar feather en máscara, decodificación VAE tileada con más overlap [H].

**El refinado cambia la cara o la composición**
Denoise demasiado alto. Bajar a ≤0.30; mantener el prompt original o recortado; fijar seed del refinado [H].

**El refinado deja la imagen "a medias"**
Pocos steps efectivos. Subir steps a 10–12 antes de subir denoise [H].

## 5. Composición y anatomía

**Sujetos duplicados o cuerpo alargado a alta resolución**
Resolución nativa por encima del rango entrenado [V rango; H causa]. Generar a 1–1.5 MP y escalar por etapas.

**Manos o anatomía incorrectas**
1. Variar seed en lote y elegir el mejor.
2. Prompt que describa la acción de las manos con claridad (qué sostienen, posición de dedos).
3. Plano que no dependa de manos complejas si no es necesario.
4. Pasada local con máscara y denoise 0.40–0.50 [H].

**Composición no controlada**
Prompt con plan espacial: tamaño de plano, altura y ángulo de cámara, ubicación del sujeto, espacio negativo. Si necesita control estricto, evaluar guía estructural (comprobar qué ControlNets existen para Krea 2 antes de recomendar; no asumas).

**Luz incoherente**
Una fuente principal, su dirección, caída de sombras y, si hace falta, una luz de relleno o contorno. Evita pedir "lighting cinematic" sin fuente.

## 6. Consistencia e identidad

**La misma cara en todas las imágenes**
LoRA con fuerza alta o apilada. Bajar fuerza o diversificar descripción física en el prompt [H].

**Necesito el mismo personaje en varias imágenes**
Entrenar un LoRA de personaje sobre RAW y ejecutarlo sobre Turbo [V recomendación oficial]. Dataset variado en ángulos, luz y expresión.

**El estilo de una referencia se copia entero (incluida composición)**
Usar el workflow de style reference con 1–2 imágenes y declarar sujeto, distancia de cámara y ubicación antes del estilo en el prompt [H].

## 7. Rendimiento

**Sin VRAM**
FP8 scaled → INT8 → GGUF Q6/Q5/Q4 (ComfyUI-GGUF). Text encoder en FP8. Decodificación VAE tileada. Reducir MP base y compensar con upscale por etapas.

**Tiempos largos**
Explorar con Turbo a ~1 MP; reservar RAW para casos donde necesitas control de CFG/negativos; limitar refinado a regiones (máscaras) en lugar de toda la imagen.
