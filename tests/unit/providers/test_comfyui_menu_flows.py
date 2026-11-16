"""COMFYUI_MENU_FLOWS: el provider debe acotar el menú del resolver."""

from __future__ import annotations

import pytest

from grokbot.providers.comfyui import provider as provider_mod
from grokbot.providers.comfyui.provider import ComfyUIProvider
from grokbot.providers.comfyui.workflows import resolver as resolver_mod


@pytest.fixture(autouse=True)
def _reset_source():
    yield
    resolver_mod.reset_workflow_source()


def test_provider_sin_menu_no_filtra():
    provider_mod.ComfyUIProvider(host="")

    assert resolver_mod.active_flow_ids() is None


def test_provider_pasa_allowlist_al_resolver():
    provider_mod.ComfyUIProvider(host="", menu_flows="agil_solo,agil_solo_edit")

    assert resolver_mod.active_flow_ids() == frozenset(
        {"agil_solo", "agil_solo_edit"}
    )
