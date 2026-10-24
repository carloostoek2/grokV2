# Mapa parámetro → efecto

Etiquetas: [V] verificado en docs oficiales, [H] heurística a validar con A/B. Los rangos [H] son puntos de partida.

## Tabla de contenidos
1. Muestreo
2. Resolución y composición
3. Etapas de refinado (denoise)
4. LoRAs
5. Precisión y VAE
6. Posproceso
7. Protocolo de A/B

## 1. Muestreo

| Parámetro | Subir | Bajar | Notas |
|---|---|---|---|
| CFG en RAW | Más contraste y saturación, piel rígida/cerosa, "quemado", más obediencia | Más suave, más natural, menos obediencia, más deriva creativa | [V] 3.5 oficial. Probar 2.5–4.5 [H] |
| CFG en Turbo | Lavado, sobre-suavizado, imagen deshecha | — | [V] dejar en 1.0 |
| Steps en RAW | Más detalle fino y coherencia | Borrador más rápido, pierde microdetalle | [V] 52 oficial. Para borradores 28–36 [H] |
| Steps en Turbo | Sin mejora esperable | Borroso e inacabado | [V] 8. Refinado con denoise bajo: probar 8–12 [H] |
| Sampler | — | — | [V] euler + simple en Turbo. Otros solo como experimento |
| mu (shift) | Más tiempo en estructura global | Más tiempo en detalle fino | [V] Turbo fijo en 1.15; RAW dependiente de resolución. La interpretación del efecto es [H]; no tocar salvo experimento |
| Seed | Cambia la composición | — | Explorar en lote y bloquear el ganador |
| Batch | Más candidatos | — | Con Turbo, 8–16 por tanda a ~1 MP |

## 2. Resolución y composición

| Decisión | Efecto | Notas |
|---|---|---|
| Megapíxeles base | Turbo 1–2 MP [V]. RAW hasta ~1 MP [V] | Más allá: sujetos duplicados, anatomía rota [H] |
| Relación de aspecto | Retrato 3:4 o 4:5 favorece rostro y torso | Panorámicas extremas elevan errores de anatomía [H] |
| Tamaño del sujeto en cuadro | Rostros pequeños pierden detalle de piel | Para planos amplios, pasada de detalle facial (ver pipelines) |
| prompt_enhance | ON: más rico, más "estética Krea" | OFF: control exacto. Para realismo estricto suele convenir OFF y prompt propio [H] |

## 3. Etapas de refinado (img2img / hires)

Guía de denoise [H], sobre una imagen ya buena:

| Denoise | Qué hace | Cuándo usarlo |
|---|---|---|
| 0.10–0.20 | Afina nitidez y micro-textura sin cambiar contenido | Último pulido tras upscale |
| 0.20–0.30 | Reconstruye textura de piel/tela tras upscale | Pasada tileada de upscale |
| 0.30–0.45 | Re-renderiza detalle fino; puede suavizar rasgos | Detalle facial, ojos, pelo |
| 0.45–0.60 | Cambia rasgos y objetos | Corrección de errores locales |
| >0.60 | Casi regenera | Rara vez en una cadena de realismo |

Reglas de oficio [H]:
- Con denoise bajo se ejecutan pocos steps efectivos: si el resultado queda "a medias", sube steps a 10–12 antes de subir denoise.
- Mantén el mismo prompt base o uno recortado al área tratada. Un prompt distinto en refinado cambia la imagen.
- Máscara con feather/blur de bordes suaves para que el pegado no deje costuras.

Upscale:
- Upscale por modelo (UpscaleModelLoader + ImageUpscaleWithModel) y luego escala de vuelta a ×1.5–×2 final suele dar más naturalidad que escalar el latente de golpe [H].
- Tiles de 768–1024 con overlap de 64–128 reducen costuras [H]. Costuras visibles → subir overlap o feather.

## 4. LoRAs

| Parámetro | Efecto | Notas |
|---|---|---|
| Fuerza 0.6–1.0 | Rango seguro para LoRAs de piel/realismo | [H]; estilos oficiales: 1.0 [V] |
| Fuerza >1.2 | Artefactos, misma cara en todo, pérdida de variedad | [H]; algunos autores recomiendan más, léelo en la ficha |
| Apilar varios | Suma de fuerzas por encima de ~1.5–2.0 suele chocar | [H]; baja cada una al apilar |
| Orden de aplicación | Con modelos aditivos, poco impacto; el conflicto está en la fuerza total | [H] |
| LoRA entrenado en RAW usado en Turbo | Funciona; puede expresarse con más fuerza | [V] funciona. Ajusta fuerza a la baja si sobreactúa |
| Trigger word | Colócala al inicio del prompt si la ficha lo indica | Ver ficha del LoRA |

## 5. Precisión y VAE

- FP8 scaled: recomendado para la mayoría [V]. BF16 puede conservar algo más de microdetalle y evitar banding [H]; compáralo con A/B si tienes VRAM.
- INT8/NVFP4/GGUF bajos (Q3–Q4): ahorran VRAM; vigila pérdida de textura fina en piel y bordes de pelo [H].
- Decodificación tileada del VAE (VAEDecodeTiled) para imágenes grandes; si aparecen líneas de tile, sube el overlap [H].

## 6. Posproceso (etapa de acabado)

El realismo percibido depende mucho de lo que la cámara real deja y la IA limpia. Palancas [H]:
- **Grano**: leve y de tamaño pequeño; demasiado grano uniforme delata CGI. Aplicar al final, tras cualquier escalado.
- **Aberración cromática / halación / viñeta**: sutiles; subirlos evita el "look limpio de render".
- **Curva de color y rango dinámico**: contraste moderado, negros no aplastados.
- **Nitidez**: unsharp suave al final; sobreafilar crea halos.

Verifica en el Manager qué paquete de nodos de posproceso tiene el usuario antes de nombrar nodos concretos.

## 7. Protocolo de A/B

1. Seed fijo, mismo prompt, misma resolución.
2. Cambia **un** parámetro por tanda, 3 valores (bajo, actual, alto).
3. Compara recortes al 100%: ojo (catchlight, pestañas), piel (poros, brillo), borde del pelo, tela.
4. Anota en tabla: parámetro, valor, efecto observado, veredicto.
5. Repite en 2–3 seeds distintos antes de adoptar el valor (un seed afortunado engaña).
