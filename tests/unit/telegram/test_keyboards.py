"""Tests de builders puros de teclados (keyboards.py).

Fija los callback_data exactos de grok (bot.py, config_flow.py,
variables_flow.py). Sin red ni Telegram.
"""

from __future__ import annotations

from grokbot.domain.user_config import ComfyUIConfig, UserConfig, VideoConfig
from grokbot.telegram import keyboards as kb
from conftest import flat_callback_data


def test_confirmation_keyboard():
    assert flat_callback_data(kb.confirmation_keyboard()) == ["confirm:yes", "confirm:no"]


def test_cancel_job_keyboard_with_and_without_id():
    assert flat_callback_data(kb.cancel_job_keyboard("abc123")) == ["cancel_job:abc123"]
    assert flat_callback_data(kb.cancel_job_keyboard()) == ["cancel_job"]
    assert flat_callback_data(kb.cancel_job_keyboard(None)) == ["cancel_job"]


def test_image_regenerate_keyboard():
    assert flat_callback_data(kb.image_regenerate_keyboard()) == ["regen"]
    assert flat_callback_data(kb.image_regenerate_keyboard(show_cancel=True)) == [
        "regen",
        "cancel_job",
    ]


def test_refine_keyboards():
    assert flat_callback_data(kb.refine_confirm_keyboard("tok123")) == [
        "refine:tok123:yes",
        "refine:tok123:no",
    ]
    assert flat_callback_data(kb.refining_keyboard()) == ["refine_noop"]


def test_config_model_keyboard_default_marks_grok():
    cfg = UserConfig.defaults()
    data = flat_callback_data(kb.config_model_keyboard(cfg))
    for key in ("grok", "seedream", "faceswap", "grok_video", "comfyui"):
        assert f"cfg:model:{key}" in data
    assert data[-1] == "cfg:close"
    # Label del modelo activo incluye la variante resuelta.
    buttons = kb.config_model_keyboard(cfg).inline_keyboard
    first_label = buttons[0][0].text
    assert first_label == "✅ Grok Imagine (Kie.ai • Alta calidad)"


def test_config_provider_keyboard():
    cfg = UserConfig.defaults()  # kie activo
    data = flat_callback_data(kb.config_provider_keyboard(cfg))
    assert data[:3] == ["cfg:provider:kie", "cfg:provider:xai", "cfg:provider:replicate"]
    assert data[-2:] == ["cfg:back:model", "cfg:close"]
    rows = kb.config_provider_keyboard(cfg).inline_keyboard
    assert rows[0][0].text == "✅ Kie.ai"


def test_config_variant_keyboard():
    cfg = UserConfig.defaults()
    data = flat_callback_data(kb.config_variant_keyboard(cfg))
    assert data[:2] == ["cfg:variant:standard", "cfg:variant:quality"]
    assert data[-2:] == ["cfg:back:provider", "cfg:close"]
    rows = kb.config_variant_keyboard(cfg).inline_keyboard
    assert rows[1][0].text == "✅ Alta calidad"


def test_config_comfyui_keyboard_model_and_lora_rows():
    cfg = ComfyUIConfig(model="krea2", lora="none", refine="1")
    data = flat_callback_data(kb.config_comfyui_keyboard(cfg))
    assert "cfg:comfyui:model:qwen" in data
    assert "cfg:comfyui:model:wan_i2v" in data
    # krea2 no expone lightning (pertenece a qwen); sí krea_nsfw.
    assert "cfg:comfyui:lora:lightning" not in data
    assert "cfg:comfyui:lora:krea_nsfw" in data
    # multipose_batch NO pertenece a krea2.
    assert "cfg:comfyui:lora:multipose_batch" not in data
    assert "cfg:comfyui:refine:0" in data  # toggle ON → data OFF
    rows = kb.config_comfyui_keyboard(cfg).inline_keyboard
    refine_text = [b.text for r in rows for b in r if b.callback_data and "refine" in b.callback_data][0]
    assert refine_text == "✨ Refinar: ON"


def test_config_comfyui_qwen_exposes_lightning_lora():
    cfg = ComfyUIConfig(model="qwen", lora="none", refine="1")
    data = flat_callback_data(kb.config_comfyui_keyboard(cfg))
    assert "cfg:comfyui:model:qwen" in data
    assert "cfg:comfyui:lora:lightning" in data


def test_config_comfyui_wan_lora_label_override():
    cfg = ComfyUIConfig(model="wan_i2v", lora="none", refine="1")
    rows = kb.config_comfyui_keyboard(cfg).inline_keyboard
    labels = [b.text for row in rows for b in row]
    assert "✅ Full (calidad, 40 pasos)" in labels


def test_config_video_keyboard_kie_includes_modes():
    cfg = VideoConfig()  # 16:9, 720p, grok-imagine-video
    data = flat_callback_data(kb.config_video_keyboard(cfg, is_kie=True))
    assert "cfg:video:model:grok-imagine-video" in data
    assert "cfg:video:duration:5" in data
    assert "cfg:video:aspect:16:9" in data
    assert "cfg:video:aspect:4:3" not in data  # base kie no permite 4:3
    assert "cfg:video:resolution:720p" in data
    assert "cfg:video:mode:spicy" in data  # solo con kie
    assert data[-2:] == ["cfg:back:provider", "cfg:close"]


def test_config_video_keyboard_non_kie_no_mode_row():
    data = flat_callback_data(kb.config_video_keyboard(VideoConfig(), is_kie=False))
    assert "cfg:video:mode:spicy" not in data
    assert "cfg:video:aspect:4:3" in data


def test_simple_close_keyboard():
    assert flat_callback_data(kb.simple_close_keyboard()) == ["cfg:back:model", "cfg:close"]


def test_variables_menu_keyboard():
    lists = {"poses": ["de pie"], "angles": [], "actions": []}
    labels = {"poses": "Poses", "angles": "Ángulos", "actions": "Acciones"}
    data = flat_callback_data(kb.variables_menu_keyboard(lists, labels))
    assert data[:3] == ["var:open:poses", "var:open:angles", "var:open:actions"]
    assert data[3:5] == ["var:tmpl", "var:packs"]
    assert data[-1] == "var:close"


def test_variables_list_keyboard():
    data = flat_callback_data(kb.variables_list_keyboard("poses"))
    assert data == [
        "var:add:poses",
        "var:edit:poses",
        "var:del:poses",
        "var:back",
        "var:close",
    ]


def test_variables_picker_keyboard():
    data = flat_callback_data(kb.variables_picker_keyboard("poses", ["de pie", "sentada"], "edit"))
    assert data[:2] == ["var:item:edit:poses:0", "var:item:edit:poses:1"]
    assert data[-1] == "var:open:poses"
    assert flat_callback_data(kb.variables_cancel_keyboard()) == ["var:cancel"]


def test_packages_keyboards():
    data = flat_callback_data(kb.packages_keyboard(["2b_outfits"]))
    assert data == ["var:pack:view:2b_outfits", "var:pack:new", "var:pack:back"]
    view_active = flat_callback_data(kb.package_view_keyboard("2b_outfits", "2b_outfits"))
    assert view_active == ["var:pack:del:2b_outfits", "var:packs", "var:pack:back"]
    view_inactive = flat_callback_data(kb.package_view_keyboard("otro", "2b_outfits"))
    assert view_inactive == [
        "var:pack:activate:otro",
        "var:pack:del:otro",
        "var:packs",
        "var:pack:back",
    ]
