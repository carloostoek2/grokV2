# Arquitecturas de pipeline por etapas

Principio: cada etapa optimiza **una** cosa y debe ser lo bastante suave (denoise bajo) para no destruir lo que logró la anterior. Si una etapa intenta arreglar dos problemas, sepárala.

Los nombres de nodos son orientativos. Antes de recomendar uno concreto, confirma que existe en la instalación del usuario (nativos de ComfyUI vs. paquetes como Impact Pack, Ultimate SD Upscale, KJNodes, ComfyUI-GGUF).

## Tabla de contenidos
1. Vocabulario de nodos
2. Pipeline A: rápido (Turbo directo)
3. Pipeline B: calidad en 5 etapas
4. Pipeline C: híbrido RAW → Turbo (hipótesis a probar)
5. Pipeline D: personaje consistente
6. Pipeline E: referencia de estilo
7. Reglas de traspaso entre etapas
8. Cómo parchear un workflow existente

## 1. Vocabulario de nodos
- Nativos: KSampler / KSamplerAdvanced, VAEEncode, VAEDecode, VAEDecodeTiled, ImageScaleBy, ImageScale, UpscaleModelLoader, ImageUpscaleWithModel, LoraLoader / LoraLoaderModelOnly, LoadImage, SaveImage.
- Paquetes habituales: Impact Pack (FaceDetailer, detectores, SAM), Ultimate SD Upscale (upscale tileado), KJNodes (utilidades), ComfyUI-GGUF (carga de GGUF).
- Plantilla oficial Krea 2: subgrafo "Text to Image (Krea-2 Turbo)", ResolutionSelector, CustomCombo de LoRA. Ábrela como punto de partida.

## 2. Pipeline A: rápido (Turbo directo)
Uso: iterar ideas, resultados finales aceptables sin posproceso pesado.
1. Turbo FP8, 8 steps, cfg 1.0, euler/simple, 1.5–2 MP [V rangos].
2. LoRA de piel a 0.6–1.0 si hay rostros [H].
3. Acabado ligero (grano, curva).
Optimiza: velocidad y exploración.

## 3. Pipeline B: calidad en 5 etapas

**Etapa 0: Exploración.** Turbo, ~1 MP, lote de 8–16 seeds, prompt corto o medio. Solo sirve para elegir composición. Resultado: seed ganador.

**Etapa 1: Composición definitiva.** Mismo seed, prompt largo con plan espacial, luz y material. Turbo a 1.5–2 MP, o RAW a ≤1 MP si necesitas CFG y negativos [V]. Optimiza: encuadre, luz, anatomía general. Aquí se decide la imagen; las etapas siguientes solo la refinan.

**Etapa 2: Upscale base.** Upscale por modelo y reescalado al factor deseado (×1.5–×2 sobre la imagen de la etapa 1) [H]. Optimiza: píxeles disponibles. Aún no agregues textura aquí.

**Etapa 3: Detalle por zonas.** Detector de cara (y ojos/manos si hace falta) → recorte → img2img con Turbo, denoise 0.30–0.40, prompt recortado a la zona, máscara con feather → pegado [H]. Optimiza: piel, ojos, pelo, dientes. Hacerlo después del upscale da más píxeles al rostro.

**Etapa 4: Textura global tileada.** Pasada tileada con denoise 0.20–0.30, tiles 768–1024 con overlap 64–128 [H]. Optimiza: microtextura de tela, entorno y piel del cuerpo. Si cambia rasgos, bajar denoise.

**Etapa 5: Acabado.** Grano fino, aberración/halación/viñeta sutiles, curva de color, unsharp suave [H]. Optimiza: percepción de "foto real". Siempre último paso.

## 4. Pipeline C: híbrido RAW → Turbo (hipótesis a probar)
Idea: usar RAW como "compositor" con control de CFG/negativos y Turbo como "refinador" rápido. Comparten arquitectura, codificador de texto y VAE, y los LoRAs de RAW funcionan en Turbo [V], por lo que el traspaso tiene base técnica; que mejore el resultado es [H] y requiere A/B.
1. RAW, ≤1 MP, 52 steps, cfg 3.5, negativos activos.
2. Decodificar → upscale por modelo ×1.5–×2 (traspaso por píxeles, más seguro que por latente).
3. Turbo, 8–12 steps, cfg 1.0, denoise 0.35–0.50, prompt positivo igual.
4. Detalle facial y acabado como en el pipeline B.
Experimento de validación: mismo seed, comparar contra Turbo directo a la misma resolución final; mirar piel y bordes al 100%.

## 5. Pipeline D: personaje consistente
1. Dataset variado (ángulos, luz, expresión) → entrenar LoRA sobre **RAW** [V recomendación oficial].
2. Inferencia con ese LoRA sobre Turbo, fuerza 0.7–1.0 [H].
3. Pipeline B para calidad final. Fuerza del LoRA más baja en etapas de refinado (0.5–0.8) para no sobreactuar identidad [H].
4. Si hay deriva de rasgos entre imágenes, repetir descripción física clave en el prompt.

## 6. Pipeline E: referencia de estilo
1. Workflow oficial de style reference (Turbo INT8 convrot + LoRA `krea2_style_reference`), 1–2 imágenes [V].
2. En el prompt, declarar primero sujeto, distancia de cámara y ubicación, y después estilo, para evitar que copie la composición de la referencia [H].
3. Pipeline B desde la etapa 3 para realismo final.

## 7. Reglas de traspaso entre etapas
- **Píxeles vs. latente**: traspaso por píxeles es más seguro y permite cambiar de modelo. Traspaso por latente solo con mismo modelo y VAE, y suele cambiar menos [H].
- **Un cambio de resolución por etapa.** Escalar y refinar en la misma etapa dificulta aislar fallos.
- **Mismo seed por etapa durante pruebas**, y se anota.
- **Denoise acumulado**: cada etapa de refinado resta fidelidad a la anterior. Si pasas de 3 etapas, usa denoise ≤0.30 en las posteriores [H].
- **Guarda un checkpoint de imagen tras cada etapa** (SaveImage con prefijo por etapa) para comparar y para reanudar sin repetir.

## 8. Cómo parchear un workflow existente
1. Identifica el formato: JSON de interfaz (claves `nodes` y `links`) o formato API (nodos por id con `class_type` e `inputs`).
2. Resume la cadena actual en una línea: Loader → Encode → KSampler → Decode → …
3. Propón el parche como lista de cambios: `nodo (id/tipo) → widget: antes → después` y los nodos nuevos con sus conexiones (de qué salida a qué entrada).
4. Señala cualquier nodo que dependa de un paquete que quizá falte.
5. Si el usuario quiere el JSON modificado, edítalo con script en el contenedor, conserva ids y enlaces existentes, y valida que no queden entradas sin conectar.
