from __future__ import annotations

from ..config import Config
from .base import RunRequest, RunResult, Runner
from .claude import ClaudeRunner
from .pi import PiRunner

__all__ = ["RunRequest", "RunResult", "Runner", "build"]


def build(cfg: Config) -> dict[str, Runner]:
    """One runner instance per configured runner name. 'fake' is injected by tests only."""
    out: dict[str, Runner] = {}
    for name, rc in cfg.runners.items():
        if name == "pi":
            out[name] = PiRunner(rc)
        elif name == "claude":
            out[name] = ClaudeRunner(rc)
    return out
