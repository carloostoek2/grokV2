"""ComfyUI providers (SSH to a Vast box running ``gen_comfy.py``)."""

from grokbot.providers.comfyui.provider import ComfyUIProvider
from grokbot.providers.comfyui.ssh_client import SshClient

__all__ = ["SshClient", "ComfyUIProvider"]
