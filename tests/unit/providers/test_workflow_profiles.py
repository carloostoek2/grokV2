"""Perfiles de pesos por GPU: manifiestos, overlay y resolución del bot."""

from __future__ import annotations

from pathlib import Path

import pytest

from grokbot.providers.comfyui.workflows.profiles import (
    apply_profile,
    flow_graphs,
    load_profiles,
    model_filenames,
    profile_for_device,
    template_index,
)
from grokbot.providers.comfyui.workflows.resolver import (
    active_profile,
    flows,
    get_flow,
    reset_workflow_source,
    set_active_profile,
)

PROFILES = load_profiles()


def _unet_nodes(graph: dict) -> list[dict]:
    return [
        node
        for node in graph.values()
        if isinstance(node, dict)
        and node.get("class_type") in ("UNETLoader", "UnetLoaderGGUF")
    ]


def test_los_tres_perfiles_existen_y_su_default_es_valido():
    assert set(PROFILES) == {"a100", "l4", "t4"}
    for profile in PROFILES.values():
        assert profile.default_flow in profile.flows
        assert profile.weights


def test_a100_usa_bf16_para_krea():
    graph = flow_graphs(PROFILES["a100"])["grok_edit"]
    unet = _unet_nodes(graph)[0]
    assert unet["class_type"] == "UNETLoader"
    assert unet["inputs"]["unet_name"] == "krea2_turbo_bf16.safetensors"
    clips = [
        node["inputs"]["clip_name"]
        for node in graph.values()
        if isinstance(node, dict) and node.get("class_type") == "CLIPLoader"
    ]
    assert clips == ["qwen3vl_4b_bf16.safetensors"]


def test_t4_cambia_el_unet_a_gguf_y_quita_weight_dtype():
    graph = flow_graphs(PROFILES["t4"])["grok_edit"]
    unet = _unet_nodes(graph)[0]
    assert unet["class_type"] == "UnetLoaderGGUF"
    assert unet["inputs"] == {"unet_name": "Krea-2-Turbo-Q4_K_M.gguf"}


def test_l4_usa_qwen_edit_gguf_pero_krea_sigue_en_fp8():
    graphs = flow_graphs(PROFILES["l4"])
    qwen = _unet_nodes(graphs["agil_edit_qwen"])[0]
    assert qwen["class_type"] == "UnetLoaderGGUF"
    assert qwen["inputs"]["unet_name"] == "qwen-image-edit-2511-Q4_K_M.gguf"
    krea = _unet_nodes(graphs["grok_edit"])[0]
    assert krea["class_type"] == "UNETLoader"
    assert krea["inputs"]["unet_name"] == "krea2_turbo_fp8_scaled.safetensors"


def test_apply_profile_no_toca_un_safetensors():
    graph = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "otro.safetensors", "weight_dtype": "default"},
        }
    }
    salida = apply_profile(graph, PROFILES["t4"])
    assert salida["1"]["class_type"] == "UNETLoader"
    assert salida["1"]["inputs"]["weight_dtype"] == "default"


@pytest.mark.parametrize(
    ("nombre", "vram", "esperado"),
    [
        ("NVIDIA A100-SXM4-80GB", 80.0, "a100"),
        ("NVIDIA H100 80GB HBM3", 80.0, "a100"),
        ("Tesla L4", 24.0, "l4"),
        ("Tesla T4", 15.0, "t4"),
        ("GPU desconocida", 48.0, "a100"),
        ("GPU desconocida", 24.0, "l4"),
        ("GPU desconocida", 0.0, "t4"),
    ],
)
def test_profile_for_device(nombre, vram, esperado):
    assert profile_for_device(nombre, vram, PROFILES).id == esperado


@pytest.mark.parametrize("perfil_id", ["a100", "l4", "t4"])
def test_todo_peso_referenciado_esta_en_el_manifiesto(perfil_id):
    """Test cruzado: ninguna plantilla pide un archivo que el perfil no baja."""
    perfil = PROFILES[perfil_id]
    rutas = [str(peso["path"]) for peso in perfil.weights]

    def disponible(nombre: str) -> bool:
        # Las plantillas usan la ruta relativa a models/<categoria>/ (p. ej.
        # ``krea2/loRA.safetensors``), no la ruta completa del manifiesto.
        return any(
            ruta == nombre
            or ruta.endswith("/" + nombre)
            or Path(ruta).name == nombre
            for ruta in rutas
        )

    faltantes: dict[str, set[str]] = {}
    for flow_id, graph in flow_graphs(perfil).items():
        ausentes = {n for n in model_filenames(graph) if not disponible(n)}
        if ausentes:
            faltantes[flow_id] = ausentes
    assert not faltantes, f"perfil {perfil_id} sin pesos para: {faltantes}"


def test_resolver_filtra_y_adapta_por_perfil():
    reset_workflow_source()
    try:
        set_active_profile("t4")
        assert active_profile() is not None
        assert active_profile().id == "t4"
        assert [f.id for f in flows()] == list(PROFILES["t4"].flows)
        flow = get_flow("grok_edit")
        assert flow is not None
        assert flow.graph["2"]["class_type"] == "UnetLoaderGGUF"
        # Flujo de A100 que no cabe en la T4: no existe para el bot.
        assert get_flow("grok_style_hq") is None
    finally:
        reset_workflow_source()


def test_resolver_sin_perfil_devuelve_todas_las_plantillas():
    reset_workflow_source()
    try:
        ids = {flow.id for flow in flows()}
        assert "grok_style_hq" in ids
        assert "grok_edit" in ids
        assert get_flow("grok_style_hq") is not None
    finally:
        reset_workflow_source()


class _FakeTransport:
    is_configured = True
    base_url = "http://127.0.0.1:19000"
    ensure_calls = 0

    async def ensure(self) -> str:
        type(self).ensure_calls += 1
        return self.base_url


class _StatsClient:
    def __init__(self, stats: dict) -> None:
        self._stats = stats

    async def system_stats(self) -> dict:
        return self._stats

    async def aclose(self) -> None:
        return None


async def test_refresh_profile_detecta_t4_y_no_abre_tunel():
    from grokbot.providers.comfyui.provider import ComfyUIProvider

    stats = {
        "devices": [
            {"name": "Tesla T4", "vram_total": 15 * 1024**3},
        ]
    }
    prov = ComfyUIProvider(
        "box",
        transport=_FakeTransport(),
        workflow_source="embed",
        weight_profile="auto",
        client_factory=lambda _url: _StatsClient(stats),
    )
    reset_workflow_source()
    try:
        await prov.refresh_profile(force=True)
        assert active_profile() is not None
        assert active_profile().id == "t4"
        # La detección consume el túnel ya activo, no lo abre.
        assert _FakeTransport.ensure_calls == 0
        assert get_flow("agil_edit_qwen") is None
    finally:
        reset_workflow_source()


# --- VRAM mínima por flujo dentro de un perfil (follow-up de 237b0fc) -------

# Lo que reporta ``/system_stats`` (GiB) en cada tarjeta: una L4 de 24 GB queda
# por debajo de 23 GiB utilizables; una RTX 5000 Ada de 32 GB, cerca de 32.
VRAM_L4 = 22.5
VRAM_32 = 31.5


def test_l4_condiciona_agil_edit_nsfw_por_vram():
    l4 = PROFILES["l4"]
    assert "agil_edit_nsfw" in l4.flows
    assert l4.min_vram_gb == {"agil_edit_nsfw": 28.0}
    assert "agil_edit_nsfw" not in l4.for_vram(VRAM_L4).flows
    assert "agil_edit_nsfw" in l4.for_vram(VRAM_32).flows
    # VRAM desconocida (perfil forzado o sin dato): nunca se ofrece.
    assert "agil_edit_nsfw" not in l4.for_vram(None).flows
    assert "agil_edit_nsfw" not in l4.for_vram(0.0).flows


@pytest.mark.parametrize("perfil_id", ["a100", "t4"])
def test_perfiles_sin_requisitos_no_cambian_con_la_vram(perfil_id):
    perfil = PROFILES[perfil_id]
    assert perfil.for_vram(None) is perfil
    assert perfil.for_vram(VRAM_L4) is perfil


def test_min_vram_gb_de_un_flujo_fuera_del_perfil_falla(tmp_path):
    (tmp_path / "x.json").write_text(
        '{"id": "x", "flows": ["grok_style"], "min_vram_gb": {"agil_edit_nsfw": 28}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no está en flows"):
        load_profiles(tmp_path)


def test_min_vram_gb_en_el_default_flow_falla(tmp_path):
    # Si el default quedara oculto, el fallback del provider no tendría flujo.
    (tmp_path / "x.json").write_text(
        '{"id": "x", "flows": ["grok_style"], "min_vram_gb": {"grok_style": 28}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="default_flow"):
        load_profiles(tmp_path)


def test_resolver_oculta_agil_edit_nsfw_en_una_l4_de_24_gb():
    reset_workflow_source()
    try:
        set_active_profile("l4", vram_gb=VRAM_L4)
        assert get_flow("agil_edit_nsfw") is None
        assert "agil_edit_nsfw" not in [f.id for f in flows()]
        set_active_profile("l4", vram_gb=VRAM_32)
        assert get_flow("agil_edit_nsfw") is not None
        assert "agil_edit_nsfw" in [f.id for f in flows()]
        # Perfil forzado sin detección (COMFYUI_PERFIL=l4): oculto.
        set_active_profile("l4")
        assert get_flow("agil_edit_nsfw") is None
    finally:
        reset_workflow_source()


async def test_refresh_profile_reacciona_a_la_vram_dentro_de_l4():
    from grokbot.providers.comfyui.provider import ComfyUIProvider

    stats = {"devices": [{"name": "NVIDIA L4", "vram_total": int(VRAM_L4 * 1024**3)}]}
    prov = ComfyUIProvider(
        "box",
        transport=_FakeTransport(),
        workflow_source="embed",
        weight_profile="auto",
        client_factory=lambda _url: _StatsClient(stats),
    )
    reset_workflow_source()
    try:
        assert await prov.refresh_profile(force=True) is True
        assert active_profile().id == "l4"
        assert get_flow("agil_edit_nsfw") is None
        # Mismo perfil, más VRAM (otra tarjeta de 20–39 GB): cambia el set.
        stats["devices"][0] = {
            "name": "NVIDIA RTX 5000 Ada Generation",
            "vram_total": int(VRAM_32 * 1024**3),
        }
        assert await prov.refresh_profile(force=True) is True
        assert active_profile().id == "l4"
        assert get_flow("agil_edit_nsfw") is not None
        # Sin cambios: no pide re-resolver.
        assert await prov.refresh_profile(force=True) is False
    finally:
        reset_workflow_source()


# --- Plantillas y conteo de flujos visibles ---------------------------------

# Flujos registrados en COMFYUI_FLOWS que hoy NO tienen plantilla en
# ``templates/``. Solo se aceptan si no se ofrecen: ocultos del menú y fuera de
# todo perfil (así ``get_flow`` devuelve ``None`` y nadie los puede elegir).
FLOWS_SIN_PLANTILLA = {
    # MiniMax H3 I2V: el id se conserva para no invalidar configs guardadas,
    # pero su plantilla nunca se agregó al repo (oculto desde 737944c).
    "minimax_h3_i2v",
}


def test_todo_flujo_registrado_tiene_plantilla_salvo_la_lista_conocida():
    from grokbot.domain.user_config import VALID_COMFYUI_MODELS

    indice = template_index()
    sin_plantilla = {f for f in VALID_COMFYUI_MODELS if f not in indice}
    # Igualdad exacta: si aparece la plantilla, hay que sacarlo de la lista.
    assert sin_plantilla == FLOWS_SIN_PLANTILLA


def test_flujos_sin_plantilla_no_se_ofrecen():
    from grokbot.domain.user_config import COMFYUI_MENU_HIDDEN_FLOWS

    assert FLOWS_SIN_PLANTILLA <= COMFYUI_MENU_HIDDEN_FLOWS
    for perfil in PROFILES.values():
        assert not FLOWS_SIN_PLANTILLA & set(perfil.flows), perfil.id
    reset_workflow_source()
    try:
        for flow_id in FLOWS_SIN_PLANTILLA:
            assert get_flow(flow_id) is None
    finally:
        reset_workflow_source()


@pytest.mark.parametrize("perfil_id", ["a100", "l4", "t4"])
def test_todo_flujo_de_un_perfil_tiene_plantilla(perfil_id):
    indice = template_index()
    assert [f for f in PROFILES[perfil_id].flows if f not in indice] == []


def _flujos_del_menu() -> list[str]:
    from grokbot.domain.user_config import ComfyUIConfig
    from grokbot.telegram.keyboards import config_comfyui_keyboard

    prefijo = "cfg:comfyui:flow:"
    teclado = config_comfyui_keyboard(ComfyUIConfig(model="grok_style"))
    return [
        boton.callback_data[len(prefijo):]
        for fila in teclado.inline_keyboard
        for boton in fila
        if (boton.callback_data or "").startswith(prefijo)
    ]


@pytest.mark.parametrize(
    ("perfil_id", "vram", "visibles"),
    [
        (None, None, 13),  # sin perfil: catálogo completo menos ocultos
        ("a100", 80.0, 13),
        ("l4", VRAM_L4, 12),  # L4 24 GB: sin agil_edit_nsfw
        ("l4", None, 12),  # COMFYUI_PERFIL=l4 forzado: VRAM desconocida
        ("l4", VRAM_32, 13),  # tarjeta l4 de 32 GB: con agil_edit_nsfw
        ("t4", 15.0, 4),
    ],
)
def test_conteo_de_flujos_visibles_por_perfil(perfil_id, vram, visibles):
    reset_workflow_source()
    try:
        set_active_profile(perfil_id, vram_gb=vram)
        menu = _flujos_del_menu()
        assert len(menu) == visibles, menu
        # Todo botón del menú resuelve a un flujo con plantilla.
        assert all(get_flow(flow_id) is not None for flow_id in menu)
    finally:
        reset_workflow_source()
