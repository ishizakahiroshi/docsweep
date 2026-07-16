"""inject/eject の公開 API と後方互換 re-export。"""

from __future__ import annotations

import sys
from types import ModuleType

from .. import config as _config
from . import api as _api
from . import blocks as _blocks
from . import manifest as _manifest
from .agent_codex import resolve_global_target
from .api import (
    DEFAULT_TARGETS,
    GUIDANCE_IMPORT,
    GUIDANCE_PATH,
    GUIDANCE_VERSION,
    SUPPORTED_GLOBAL_AGENTS,
    EjectResult,
    InjectResult,
    docsweep_command,
    eject,
    eject_global,
    generate_due_block,
    generate_guidance_block,
    generate_label_block,
    generate_managed_block,
    generate_okf_block,
    inject,
    inject_global,
    list_injected,
    preview_global,
    preview_inject,
    write_guidance_file,
)
from .blocks import MARK_END, MARK_START
from .manifest import MANIFEST_PATH, load_manifest, save_manifest

GLOBAL_CONFIG_PATH = _config.GLOBAL_CONFIG_PATH


class _CompatModule(ModuleType):
    """従来の module-level 定数 monkeypatch を分割先へ伝播する。"""

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        for module in (_api, _blocks, _manifest, _config):
            if hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _CompatModule

__all__ = [
    "DEFAULT_TARGETS", "EjectResult", "GLOBAL_CONFIG_PATH", "GUIDANCE_IMPORT",
    "GUIDANCE_PATH", "GUIDANCE_VERSION", "InjectResult", "MANIFEST_PATH",
    "MARK_END", "MARK_START", "SUPPORTED_GLOBAL_AGENTS", "docsweep_command",
    "eject", "eject_global", "generate_due_block", "generate_guidance_block",
    "generate_label_block", "generate_managed_block", "generate_okf_block", "inject",
    "inject_global", "list_injected", "load_manifest", "preview_global",
    "preview_inject", "resolve_global_target", "save_manifest", "write_guidance_file",
]
