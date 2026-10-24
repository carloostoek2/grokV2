# Prompting para fotorrealismo en Krea 2

Krea 2 usa Qwen3-VL como codificador de texto. No hay CLIP: responde a lenguaje natural contextual, no a listas de etiquetas ni a pesos tipo (palabra:1.3). Las plantillas oficiales incluyen un prompt enhancer porque el modelo se entrenó con prompts cortos, medios y largos [V].

## Tabla de contenidos
1. Estructura recomendada
2. Reglas
3. Banco de señales de realismo
4. Antipatrones
5. Plantillas
6. Flujo explorar → bloquear → afinar

## 1. Estructura recomendada
Orden sugerido (coincide con la guía de varios autores y con la ficha de CyberRealistic):
sujeto → escenario → composición y cámara → luz → ánimo/estilo → material y textura.

## 2. Reglas
- Escribe frases naturales, no palabras sueltas.
- **Plan espacial**: tamaño de plano, altura y ángulo de cámara, distancia, ubicación del sujeto, espacio negativo.
- **Cámara con 2–3 señales compatibles** (formato/lente/apertura/tipo de luz), no un término aislado.
- **Luz con fuente, dirección y caída de sombra.** "Cinematic lighting" sin fuente da resultados genéricos.
- **Detalle de superficie concreto** (poros, vello, hilos de tela, rayones), pero plausible: lo imperfecto rompe el brillo genérico.
- **No pidas "calidad"**; describe lo que se ve. Los modificadores tipo "masterpiece" o "8k" no aportan y empujan al look de render.
- **Negative prompt solo en RAW** (CFG>1). En Turbo no tiene efecto [V]: reformula en positivo.
- Si usas LoRA con trigger, colócalo donde indique su ficha (muchas veces al inicio).
- Con `prompt_enhance` ON obtienes riqueza pero menos control; para realismo estricto, escribe el prompt completo tú y deja OFF [H].

## 3. Banco de señales de realismo (combinar 2–4, no todas)
- **Cámara**: retrato en 85 mm con apertura abierta; gran angular de 24 mm a la altura de los ojos; foco profundo; desenfoque de movimiento leve; grano de película.
- **Luz**: ventana lateral con luz difusa; softbox grande a la izquierda con relleno tenue; luz dura de mediodía; hora azul con luz de calle; flash directo (ojo: puede aplanar textura); luz de contorno tras los hombros.
- **Piel**: poros visibles, vello fino en mejillas, ligera imperfección de tono, brillo natural moderado, enrojecimiento en nariz y mejillas, sin maquillaje o maquillaje mínimo.
- **Ojos**: un catchlight por ojo, vasos finos en la esclerótica, pestañas desiguales.
- **Pelo**: mechones sueltos, pelo suelto con luz de contorno, humedad o frizz.
- **Tela y materiales**: arrugas de uso, hilos sueltos, desgaste en bordes, cuero con microgrietas, metal con huellas.
- **Entorno**: objetos no ordenados, reflejos parásitos, profundidad con elementos en primer plano desenfocados.

## 4. Antipatrones
- Listas tipo "woman, street, night, realistic, masterpiece, best quality, 8k, detailed skin": el modelo no sabe qué es la imagen. Describe la escena.
- "Perfect skin", "flawless", "beautiful": empuja a piel pulida.
- Pesos de énfasis (palabra:1.4): sin CLIP no funcionan como en SD/SDXL [H].
- Combinar estilos incompatibles en un solo prompt (documental y editorial de lujo).
- Prompt larguísimo sin jerarquía: la información clave (sujeto, cámara, luz) va primero.

## 5. Plantillas

**Retrato fotorrealista**
> [Sujeto: edad aproximada, rasgos, expresión, ropa] en [escenario concreto]. Plano [tamaño] a [altura/ángulo], [distancia], sujeto [ubicación en el cuadro]. Fotografiado con [lente/apertura], [tipo de luz: fuente, dirección, caída de sombra]. Piel con poros visibles y vello fino, [imperfección concreta], brillo natural moderado. [Material/ropa con detalle]. [Ánimo en una frase].

**Producto / bodegón**
> [Objeto y materiales] sobre [superficie], plano [tamaño], cámara [ángulo]. Luz [fuente y dirección], con reflejos [dónde] y sombra [cómo cae]. Textura de [material: rayones, grano, huellas]. Fondo [descripción], profundidad de campo [corta/larga].

**Escena en exterior con gente**
> [Escena y hora del día], gran angular a la altura de los ojos. Luz [dirección y calidad]. En primer plano [elemento desenfocado], sujeto principal [descripción] a [posición]. Entorno con [detalles desordenados creíbles]. Grano fino, rango dinámico natural.

## 6. Flujo explorar → bloquear → afinar
1. **Explorar**: prompt corto/medio, Turbo a ~1 MP, lote de seeds. Elegir composición.
2. **Bloquear**: fijar seed y añadir plan espacial, cámara, luz y material. Re-generar a la resolución de trabajo.
3. **Afinar**: A/B de un elemento a la vez (luz, lente, imperfección). Pasar luego a etapas de detalle.
Con Turbo, generar cinco direcciones primero y luego detallar la que gusta suele rendir más que escribir el prompt perfecto a ciegas.
