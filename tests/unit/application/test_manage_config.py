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

    res = uc.set_comfyui(USER_ID, model="qwen")

    assert res.ok is True and res.changed is True
    assert sessions.saved[-1][1].comfyui.model == "qwen"


def test_set_comfyui_invalid_model_and_lora(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    bad_model = uc.set_comfyui(USER_ID, model="no-such")
    assert bad_model.ok is False and bad_model.changed is False

    bad_lora = uc.set_comfyui(USER_ID, lora="no-such-lora")
    assert bad_lora.ok is False and bad_lora.changed is False

    assert len(sessions.saved) == 1  # solo el baseline


def test_set_comfyui_refine_valid_invalid(sessions):
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)

    ok = uc.set_comfyui(USER_ID, refine="0")
    assert ok.ok is True and ok.changed is True
    assert sessions.saved[-1][1].comfyui.refine == "0"

    bad = uc.set_comfyui(USER_ID, refine="2")
    assert bad.ok is False and bad.changed is False
    assert len(sessions.saved) == 2


def test_save_uses_seeded_fake_repo_persistence(sessions):
    """El get→replace→save persiste y un segundo set no pisa el anterior."""
    sessions.save_config(USER_ID, _cfg())
    uc = _uc(sessions)
    uc.set_model(USER_ID, "comfyui")
    uc.set_comfyui(USER_ID, model="qwen")
    cfg = sessions.get_config(USER_ID)
    assert cfg.model == "comfyui"
    assert cfg.comfyui.model == "qwen"
    # baseline + model + comfyui = 3 saves.
    assert len(sessions.saved) == 3


def test_saved_records_are_distinct_copies():
    """FakeSessionRepo no muta el UserConfig original (frozen + replace)."""
    sessions = FakeSessionRepo()
    original = _cfg()
    sessions.save_config(USER_ID, original)
    uc = _uc(sessions)
    uc.set_model(USER_ID, "seedream")
    assert original.model == "grok"  # no se mutó
    assert sessions.saved[0][1] is not sessions.saved[1][1]
