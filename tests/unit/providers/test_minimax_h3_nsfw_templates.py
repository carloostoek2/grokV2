"""MiniMax H3 Imagen: PE heretic y dirección erótica fijados en plantilla/perfiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "src/grokbot/providers/comfyui/workflows/templates"
PROFILES = REPO_ROOT / "src/grokbot/providers/comfyui/workflows/profiles"
PROVISION = REPO_ROOT / "scripts/provision_vast_box.sh"

PE = "p-h3-image_prompt_upsampler_heretic_w4a8.safetensors"
PE_URL = (
    "https://huggingface.co/DeepNeuralNerd/"
    "Minimax_H3_Image_Prompt_Upsampler_Uncensored_Heretic/resolve/main/"
    "p-h3-image_prompt_upsampler_heretic_w4a8.safetensors"
)
CROWN = "CROWN_MiniMax_H3_Image_Douyin_Chunyu_Yujie_V1_r16_slim.safetensors"
CROWN_URL = "https://civitai.com/api/download/models/3465545"
OLD_PE = "qwen3.5_9b_w4a8_gptq.safetensors"


@pytest.mark.parametrize(
    "nombre", ["minimax_h3_image.json", "minimax_h3_t2i.json"]
)
def test_los_dos_flujos_cargan_el_pe_heretic(nombre: str) -> None:
    template = json.loads((TEMPLATES / nombre).read_text(encoding="utf-8"))

    assert template["35"]["inputs"]["clip_name"] == PE
    assert "Standing Erotic Direction" in template["39"]["inputs"]["value"]
    assert (
        "consenting adult aged 25 or older"
        in template["36"]["inputs"]["value"]
    )


@pytest.mark.parametrize("perfil", ["a100", "l4"])
def test_perfiles_declaran_el_pe_heretic_y_el_crown(perfil: str) -> None:
    datos = json.loads((PROFILES / f"{perfil}.json").read_text(encoding="utf-8"))
    pesos = {peso["path"]: peso for peso in datos["weights"]}

    assert f"text_encoders/{PE}" in pesos
    assert pesos[f"text_encoders/{PE}"]["url"] == PE_URL
    assert pesos[f"text_encoders/{PE}"]["auth"] == "none"
    assert f"text_encoders/{OLD_PE}" not in pesos

    crown = pesos[f"loras/{CROWN}"]
    assert crown["url"] == CROWN_URL
    assert crown["auth"] == "civitai"


def test_el_script_de_provision_incluye_pe_y_crown() -> None:
    texto = PROVISION.read_text(encoding="utf-8")

    assert PE in texto
    assert CROWN in texto
    assert PE_URL in texto
    assert CROWN_URL in texto
