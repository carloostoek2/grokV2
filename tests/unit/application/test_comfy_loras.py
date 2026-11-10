"""Tests del catálogo de LoRAs (puerto → caso de uso)."""

from __future__ import annotations

import pytest

from grokbot.application.comfy_loras import ListComfyLorasUseCase

from conftest import FakeLoraCatalog


@pytest.mark.asyncio
async def test_returns_the_box_catalog():
    result = await ListComfyLorasUseCase(catalog=FakeLoraCatalog(("a", "b"))).execute()

    assert result.ok is True
    assert result.names == ("a", "b")


@pytest.mark.asyncio
async def test_degrades_to_not_ok_when_the_box_is_down():
    catalog = FakeLoraCatalog()
    catalog.fail = True

    result = await ListComfyLorasUseCase(catalog=catalog).execute()

    assert result.ok is False
    assert result.names == ()
