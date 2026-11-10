"""Tests de UpdateUserConfigUseCase — setters de config con reset Kie (R5).

Verifica validación (fuera de VALID_* → ok=False sin save), no-op (ok=True,
changed=False), patrón load-then-save O3 (un save por mutación real) y el reset
de aspect Kie tras provider/video-model (helper de dominio D12). Sin registry.
"""

from __future__ import annotations

from dataclasses import replace

from grokbot.application.manage_config import ConfigResult, UpdateUserConfigUseCase
from grokbot.domain.user_config import ComfyUIConfig, UserConfig, VideoConfig

from conftest import USER_ID, FakeSessionRepo


def _uc(sessions):
    return UpdateUserConfigUseCase(sessions=sessions)


def _cfg(**over):
    return replace(UserConfig.defaults(), **over)


# --- model ---------------------------------------------------------------------

def test_set_model_valid_persists_and_changed(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    res = uc.set_model(USER_ID, "seedream")

    assert isinstance(res, ConfigResult)
    assert res.ok is True and res.changed is True
    assert res.field == "model"
    assert sessions.saved[-1][1].model == "seedream"
    assert len(sessions.saved) == 2  # baseline + 1 save


def test_set_model_invalid_no_save(sessions):
    uc = _uc(sessions)

    res = uc.set_model(USER_ID, "no-such-model")

    assert res.ok is False and res.changed is False
    assert sessions.saved == []


def test_set_model_noop_no_save(sessions):
    uc = _uc(sessions)

    res = uc.set_model(USER_ID, "grok")  # default ya es grok

    assert res.ok is True and res.changed is False
    assert res.aspect_reset_to is None
    assert sessions.saved == []


# --- grok imagine provider / variant: reset de aspect Kie ----------------------

def test_set_provider_kie_resets_invalid_aspect(sessions):
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="xai",
        video=VideoConfig(aspect_ratio="4:3"),  # 4:3 inválido para Kie base
    )
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions)

    res = uc.set_grok_imagine_provider(USER_ID, "kie")

    assert res.ok is True and res.changed is True
    assert res.aspect_reset_to == "16:9"
    saved = sessions.saved[-1][1]
    assert saved.grok_imagine_provider == "kie"
    assert saved.video.aspect_ratio == "16:9"


def test_set_provider_xai_no_aspect_reset(sessions):
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(aspect_ratio="4:3"),
    )
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions)

    res = uc.set_grok_imagine_provider(USER_ID, "xai")

    assert res.ok is True and res.changed is True
    assert res.aspect_reset_to is None  # xai no es Kie → sin reset
    saved = sessions.saved[-1][1]
    assert saved.grok_imagine_provider == "xai"
    assert saved.video.aspect_ratio == "4:3"  # se conserva


def test_set_grok_imagine_variant_valid_persists(sessions):
    cfg = _cfg(grok_imagine_variant="standard")
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions)

    res = uc.set_grok_imagine_variant(USER_ID, "quality")

    assert res.ok is True and res.changed is True
    assert sessions.saved[-1][1].grok_imagine_variant == "quality"


def test_set_grok_imagine_variant_invalid(sessions):
    uc = _uc(sessions)
    res = uc.set_grok_imagine_variant(USER_ID, "ultra")
    assert res.ok is False and res.changed is False
    assert sessions.saved == []


# --- video ---------------------------------------------------------------------

def test_set_video_model_15_keeps_valid_aspect(sessions):
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(model="grok-imagine-video", aspect_ratio="4:3"),
    )
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions)

    res = uc.set_video(USER_ID, model="grok-imagine-video-1.5")

    assert res.ok is True and res.changed is True
    assert res.aspect_reset_to is None  # 4:3 está permitido en el modelo 1.5
    saved = sessions.saved[-1][1]
    assert saved.video.model == "grok-imagine-video-1.5"
    assert saved.video.aspect_ratio == "4:3"


def test_set_video_model_base_resets_invalid_aspect(sessions):
    cfg = _cfg(
        model="grok_video",
        grok_imagine_provider="kie",
        video=VideoConfig(model="grok-imagine-video-1.5", aspect_ratio="4:3"),
    )
    sessions.save_config(USER_ID, cfg)
    uc = _uc(sessions)

    res = uc.set_video(USER_ID, model="grok-imagine-video")

    assert res.ok is True and res.changed is True
    assert res.aspect_reset_to == "16:9"
    saved = sessions.saved[-1][1]
    assert saved.video.model == "grok-imagine-video"
    assert saved.video.aspect_ratio == "16:9"


def test_set_video_duration_valid_and_invalid(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    ok = uc.set_video(USER_ID, duration=10)
    assert ok.ok is True and ok.changed is True
    assert sessions.saved[-1][1].video.duration == 10

    bad = uc.set_video(USER_ID, duration=7)
    assert bad.ok is False and bad.changed is False
    assert len(sessions.saved) == 2  # el inválido no persistió


def test_set_video_invalid_field_name(sessions):
    uc = _uc(sessions)
    res = uc.set_video(USER_ID, fps=30)
    assert res.ok is False and res.changed is False


# --- comfyui -------------------------------------------------------------------

def test_set_comfyui_model_valid_persists(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    # Único flujo registrado (grok_style, default): seteárselo es no-op válido.
    res = uc.set_comfyui(USER_ID, model="grok_style")

    assert res.ok is True and res.changed is False
    assert sessions.saved[-1][1].comfyui.model == "grok_style"


def test_set_comfyui_invalid_model_and_lora(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    bad_model = uc.set_comfyui(USER_ID, model="no-such")
    assert bad_model.ok is False and bad_model.changed is False

    bad_lora = uc.set_comfyui(USER_ID, lora="no-such-lora")
    assert bad_lora.ok is False and bad_lora.changed is False

    assert len(sessions.saved) == 1  # solo el baseline


def test_set_comfyui_dormant_fields_rejected(sessions):
    """lora/refine quedaron dormidos: setearlos se rechaza (no se ofrecen)."""
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    for field in ({"refine": "0"}, {"refine": "2"}, {"lora": "none"}):
        res = uc.set_comfyui(USER_ID, **field)
        assert res.ok is False and res.changed is False
    assert len(sessions.saved) == 1  # solo el baseline


def test_save_uses_seeded_fake_repo_persistence(sessions):
    """El get→replace→save persiste; un no-op no pisa ni añade un save."""
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    uc.set_model(USER_ID, "comfyui")
    cfg = sessions.get_config(USER_ID)
    assert cfg.model == "comfyui"
    assert cfg.comfyui.model == "grok_style"  # flujo default
    # Setear el mismo flujo (único registrado) no añade save.
    uc.set_comfyui(USER_ID, model="grok_style")
    assert len(sessions.saved) == 2  # baseline + model


def test_saved_records_are_distinct_copies():
    """FakeSessionRepo no muta el UserConfig original (frozen + replace)."""
    sessions = FakeSessionRepo()
    original = _cfg()
    sessions.save_config(USER_ID, original)
    uc = _uc(sessions)
    uc.set_model(USER_ID, "seedream")
    assert original.model == "grok"  # no se mutó
    assert sessions.saved[0][1] is not sessions.saved[1][1]


# --- ComfyUI seed policy ------------------------------------------------------

def test_set_comfyui_seed_modes_keep_last_fixed_seed(sessions):
    sessions.save_config(
        USER_ID, _cfg(comfyui=ComfyUIConfig(seed_mode="fixed", seed=999))
    )
    uc = _uc(sessions)

    res = uc.set_comfyui_seed(USER_ID, "batch")
    assert res.ok is True and res.changed is True
    cc = sessions.get_config(USER_ID).comfyui
    assert (cc.seed_mode, cc.seed) == ("batch", 999)

    res = uc.set_comfyui_seed(USER_ID, "random")
    assert res.ok is True and res.changed is True
    cc = sessions.get_config(USER_ID).comfyui
    assert (cc.seed_mode, cc.seed) == ("random", 999)


def test_set_comfyui_seed_noop_does_not_save(sessions):
    sessions.save_config(
        USER_ID, _cfg(comfyui=ComfyUIConfig(seed_mode="batch", seed=999))
    )
    uc = _uc(sessions)

    res = uc.set_comfyui_seed(USER_ID, "batch")

    assert res.ok is True and res.changed is False
    assert len(sessions.saved) == 1  # solo el baseline


def test_set_comfyui_seed_rejects_invalid_mode_and_seed(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    for mode, seed in (
        ("bogus", None),
        ("fixed", -1),
        ("fixed", 1 << 63),
        ("fixed", True),
        ("fixed", "1"),
    ):
        res = uc.set_comfyui_seed(USER_ID, mode, seed)
        assert res.ok is False and res.changed is False

    assert len(sessions.saved) == 1  # ningún save inválido


def test_set_nano_banana_provider_and_variant(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    res = uc.set_nano_banana_provider(USER_ID, "replicate")
    assert res.ok is True and res.changed is True
    assert sessions.saved[-1][1].nano_banana_provider == "replicate"

    res2 = uc.set_nano_banana_variant(USER_ID, "pro")
    assert res2.ok is True and res2.changed is True
    assert sessions.saved[-1][1].nano_banana_variant == "pro"

    bad = uc.set_nano_banana_provider(USER_ID, "xai")
    assert bad.ok is False


# --- comfyui LoRA stack --------------------------------------------------------

def test_add_comfyui_lora_appends_with_default_strength(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    res = uc.add_comfyui_lora(USER_ID, "grok_style", "krea2/style_a.safetensors")

    assert res.ok is True and res.changed is True
    entries = sessions.get_config(USER_ID).comfyui.loras_for("grok_style")
    assert [entry.name for entry in entries] == ["krea2/style_a.safetensors"]
    assert entries[0].strength == 0.8
    assert "krea2/style_a.safetensors" not in sessions.get_config(USER_ID).comfyui.loras_for("agil_solo")


def test_add_comfyui_lora_is_idempotent_by_name(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")
    saves = len(sessions.saved)

    res = uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")

    assert res.ok is True and res.changed is False
    assert len(sessions.saved) == saves
    assert len(sessions.get_config(USER_ID).comfyui.loras_for("grok_style")) == 1


def test_lora_stacks_are_isolated_per_flow(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")
    uc.add_comfyui_lora(USER_ID, "agil_solo", "b.safetensors")

    cc = sessions.get_config(USER_ID).comfyui
    assert [e.name for e in cc.loras_for("grok_style")] == ["a.safetensors"]
    assert [e.name for e in cc.loras_for("agil_solo")] == ["b.safetensors"]


def test_lora_stack_caps_at_max_per_flow(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    for index in range(5):
        assert uc.add_comfyui_lora(
            USER_ID, "grok_style", f"lora_{index}.safetensors"
        ).ok

    res = uc.add_comfyui_lora(USER_ID, "grok_style", "lora_5.safetensors")

    assert res.ok is False and res.changed is False
    assert len(sessions.get_config(USER_ID).comfyui.loras_for("grok_style")) == 5


def test_shift_lora_strength_clamps_and_is_noop_at_bounds(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")

    assert uc.shift_comfyui_lora_strength(USER_ID, "grok_style", 0, 0.1).changed
    assert sessions.get_config(USER_ID).comfyui.loras_for("grok_style")[0].strength == 0.9

    assert uc.shift_comfyui_lora_strength(USER_ID, "grok_style", 0, -5).changed
    assert sessions.get_config(USER_ID).comfyui.loras_for("grok_style")[0].strength == 0.0

    res = uc.shift_comfyui_lora_strength(USER_ID, "grok_style", 0, -0.1)
    assert res.ok is True and res.changed is False


def test_remove_and_clear_lora_stack(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")
    uc.add_comfyui_lora(USER_ID, "grok_style", "b.safetensors")

    assert uc.remove_comfyui_lora(USER_ID, "grok_style", 0).changed
    assert [e.name for e in sessions.get_config(USER_ID).comfyui.loras_for("grok_style")] == ["b.safetensors"]

    assert uc.clear_comfyui_loras(USER_ID, "grok_style").changed
    assert sessions.get_config(USER_ID).comfyui.loras_for("grok_style") == ()


def test_invalid_flow_index_or_entry_is_rejected(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    assert uc.add_comfyui_lora(USER_ID, "no-such-flow", "a.safetensors").ok is False
    assert uc.add_comfyui_lora(USER_ID, "grok_style", "").ok is False
    assert uc.add_comfyui_lora(USER_ID, "grok_style", "a", 9.0).ok is False
    uc.add_comfyui_lora(USER_ID, "grok_style", "a.safetensors")
    assert uc.remove_comfyui_lora(USER_ID, "grok_style", 7).ok is False
    assert uc.shift_comfyui_lora_strength(USER_ID, "grok_style", "0", 0.1).ok is False
