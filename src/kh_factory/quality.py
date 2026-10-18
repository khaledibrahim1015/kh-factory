"""Tests and lint are code, not an agent: run the commands from factory.yaml."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

CHECKS = ("lint", "test")  # order matters: lint is fast, test is the gate that must exist
TAIL_LINES = 150


@dataclass
class Check:
    name: str
    command: str
    passed: bool
    output: str


@dataclass
class QualityResult:
    passed: bool
    checks: list[Check] = field(default_factory=list)
    code: str = ""  # NO_TEST_COMMAND when nothing can prove the work

    def summary(self) -> str:
        if self.code:
            return self.code
        return ", ".join(f"{c.name}: {'pass' if c.passed else 'FAIL'}" for c in self.checks)

    def feedback(self) -> str:
        """Verbatim output of failed checks, for the fix step."""
        parts = []
        for c in self.checks:
            if not c.passed:
                parts.append(f"### `{c.name}` failed: `{c.command}`\n\n```\n{c.output}\n```")
        return "\n\n".join(parts) or "All checks passed."


def run(cfg: Config, cwd: Path) -> QualityResult:
    cmds = cfg.project.commands
    if not cmds.get("test"):
        return QualityResult(False, [], "NO_TEST_COMMAND: set project.commands.test in factory.yaml")
    checks = []
    for name in CHECKS:
        cmd = cmds.get(name)
        if not cmd:
            continue
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=cfg.project.command_timeout_min * 60,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            out, ok = (p.stdout + p.stderr), p.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = f"TIMEOUT after {cfg.project.command_timeout_min} min", False
        tail = "\n".join(out.splitlines()[-TAIL_LINES:])
        checks.append(Check(name, cmd, ok, tail))
    return QualityResult(all(c.passed for c in checks), checks)
