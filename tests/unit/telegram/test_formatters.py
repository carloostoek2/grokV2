"""Tests del copy puro (formatters.py) — strings byte a byte de grok.

Sin red ni Telegram. Verifica truncado a 1024 por binary search, labels de
modelos/video/comfyui, resúmenes de batch, mensajes de status de video y el
dict ``model_display`` para mostrar (nunca resuelve providers).
"""

from __future__ import annotations

from grokbot.domain.user_config import ComfyUIConfig, UserConfig, VideoConfig
from grokbot.telegram import formatters as f


def test_escape():
    assert f.escape("<b>hola</b> & amigo") == "&lt;b&gt;hola&lt;/b&gt; &amp; amigo"


def test_prov_label():
    assert f.prov_label("xai") == "xAI"
    assert f.prov_label("replicate") == "Replicate"
    assert f.prov_label("kie") == "Kie.ai"
    assert f.prov_label("otro") == "otro"


def test_format_elapsed():
    assert f.format_elapsed(None) == "…"
    assert f.format_elapsed(45) == "45s"
    assert f.format_elapsed(185) == "3m 05s"


def test_append_prompt_to_caption_appends():
    caption = "<b>Imagen:</b> 45s"
    out = f.append_prompt_to_caption(caption, "un retrato")
    assert out == f"{caption}\n<b>Prompt:</b> un retrato"


def test_append_prompt_to_caption_truncates_under_1024():
    prompt = "x" * 5000
    caption = "<b>Imagen:</b> …"
    out = f.append_prompt_to_caption(caption, prompt)
    assert len(out) <= 1024
    assert out.endswith("…")
    assert "\n<b>Prompt:</b> " in out


def test_append_prompt_to_caption_no_prompt_returns_caption():
    caption = "solo"
    assert f.append_prompt_to_caption(caption, "") == caption


def test_format_model_caption_comfyui():
    # El caption muestra el FLUJO (Grok Style), sin fila de LoRA (dormido).
    model = {"comfyui_model": "grok_style"}
    out = f.format_model_caption(model, None, prompt="perro")
    assert "<b>Modelo:</b> Grok Style" in out
    assert "<b>LoRA:</b>" not in out
    assert "<b>Tiempo:</b> …" in out
    assert "<b>Prompt:</b> perro" in out


def test_format_model_caption_plain_model():
    model = {"name": "Seedream 5.0"}
    out = f.format_model_caption(model, 90)
    assert "<b>Modelo:</b> Seedream 5.0" in out
    assert "1m 30s" in out


def test_format_result_caption_variant():
    out = f.format_result_caption("Imagen generada", 12, variant="1/4")
    assert out == "<b>Imagen generada (1/4):</b> 12s"


def test_format_result_caption_no_variant():
    out = f.format_result_caption("Imagen generada", None)
    assert out == "<b>Imagen generada:</b> …"


def test_model_display_grok_default():
    cfg = UserConfig.defaults()  # grok + kie + quality
    m = f.model_display(cfg)
    assert m["name"] == "Grok Imagine (Kie.ai • Alta calidad)"
    assert m["provider"] == "kie"
    assert m["imagine_provider"] == "kie"
    assert m["imagine_variant"] == "quality"


def test_model_display_grok_video_with_replicate():
    cfg = UserConfig.defaults()
    cfg = UserConfig(
        model="grok_video",
        grok_imagine_provider="replicate",
        grok_imagine_variant="quality",
        video=VideoConfig(),
        comfyui=ComfyUIConfig(),
    )
    m = f.model_display(cfg)
    assert m["name"] == "Grok Imagine Video (Replicate)"
    assert m["provider"] == "replicate"


def test_model_display_comfyui():
    cfg = UserConfig(
        model="comfyui",
        comfyui=ComfyUIConfig(model="grok_style"),
    )
    m = f.model_display(cfg)
    assert m["name"] == "Grok Style"
    assert m["comfyui_model"] == "grok_style"
    assert "Grok Style" in m["desc"]


def test_validate_prompt_rules():
    assert f.validate_prompt("ab") == "El prompt es muy corto. Dame algo mas descriptivo."
    assert f.validate_prompt("un perro") is None
    too_long = "x" * (f.TELEGRAM_MAX_TEXT_LEN + 1)
    assert f.validate_prompt(too_long) == (
        f"El prompt es demasiado largo (máximo {f.TELEGRAM_MAX_TEXT_LEN} caracteres)."
    )


def test_video_start_message_escapes():
    out = f.video_start_message("grok-imagine-video", "un <gato>")
    assert "Generando video con <b>grok-imagine-video</b>..." in out
    assert "<i>un &lt;gato&gt;</i>" in out


def test_video_status_message_detail():
    out = f.video_status_message("vid-model", "procesando", "un perro")
    assert "Generando video con <b>vid-model</b>... procesando" in out
    assert "<i>un perro</i>" in out


def test_retry_status_text():
    assert f.retry_status_text("Generando imagen", 2, 6) == (
        "Generando imagen (intento 2/6)"
    )


def test_variables_batch_summary_cases():
    assert f.format_variables_batch_summary(5, 0, 5) == "✅ Listo: 5/5 imágenes generadas."
    assert f.format_variables_batch_summary(4, 1, 5) == (
        "✅ Listo: 4/5 imágenes generadas (1 error)."
    )
    assert f.format_variables_batch_summary(0, 3, 5) == (
        "⚠️ Listo: 0/5 imágenes generadas (3 errores)."
    )


def test_failed_item_message_escapes_prompt():
    out = f.format_failed_item_message("Edición", 2, 5, "a <b>prompt</b>")
    assert out == (
        "Edición 2/5 falló con el siguiente prompt:\n\n"
        "a &lt;b&gt;prompt&lt;/b&gt;"
    )


def test_multipose_summary_numbers_combos():
    out = f.format_multipose_summary(("de pie, frontal", "sentada, perfil"))
    lines = out.splitlines()
    assert lines[0] == "<b>🎲 Multi-pose ×5</b> — poses usadas:"
    assert lines[1] == "  1. de pie, frontal"
    assert lines[2] == "  2. sentada, perfil"


def test_labels_constants_transcribed():
    assert f.VIDEO_MODEL_LABELS["grok-imagine-video"] == "Base"
    assert f.VIDEO_MODE_LABELS["spicy"] == "Spicy"
    assert f.COMFYUI_FLOW_LABELS["grok_style"] == "Grok Style"
    assert f.LIST_LABELS["poses"] == "Poses"
