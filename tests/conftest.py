from __future__ import annotations

import json
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest

from kh_factory.runners.fake import FakeRunner

FAKE_YAML = """
defaults:
  runner: fake
  model: fake-model
  tools: [read, bash, edit, write]
  protected_files: [factory.yaml, .factory/, tasks/, .claude/, AGENTS.md, CLAUDE.md]
agents:
  planner:  {{writes: [], prompts: {{system: .factory/prompts/planner/system.md, user: .factory/prompts/planner/user.md}}}}
  builder:  {{writes: ["**"], prompts: {{system: .factory/prompts/builder/system.md, user: .factory/prompts/builder/user.md}}}}
  reviewer: {{writes: [], prompts: {{system: .factory/prompts/reviewer/system.md, user: .factory/prompts/reviewer/user.md}}}}
pipeline: {{max_fix_loops: 3, max_review_loops: 2}}
project:
  stack: python
  commands: {{test: "{py} check.py"}}
  context: [docs]
"""

TASK = """---
id: T-001
title: Add function
depends_on: []
spec: {spec}
---

## Objective

Add `add(a, b)` to calc.py.

## Acceptance

- add(2, 3) == 5
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "factory.yaml").write_text(FAKE_YAML.format(py=sys.executable))
    tpl = resources.files("kh_factory") / "templates" / "prompts"
    for agent in ("planner", "builder", "reviewer"):
        for kind in ("system", "user"):
            p = root / ".factory" / "prompts" / agent / f"{kind}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text((tpl / agent / f"{kind}.md").read_text())
    (root / "docs").mkdir()
    (root / "docs" / "SPEC.md").write_text("# Spec\n\n## 1. Math\n\nadd must be pure.\n\n## 2. Other\n\nx\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "T-001-add.md").write_text(TASK.format(spec='["docs/SPEC.md#1"]'))
    (root / "calc.py").write_text("")
    (root / "check.py").write_text("from calc import add\nassert add(2, 3) == 5, 'add(2,3) != 5'\nprint('ok')\n")
    (root / ".gitignore").write_text(".factory/jobs/\n.factory/trace.db*\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


def envelope(data: dict) -> str:
    return f"Done.\n\n```json\n{json.dumps(data)}\n```"


PLAN = envelope({"summary": "add add()", "plan": "1. write add in calc.py", "files_to_touch": ["calc.py"]})
PASS = envelope({"verdict": "PASS", "summary": "good", "findings": []})


def build_ok(req) -> str:
    (req.cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return envelope({"summary": "added add", "changed_files": ["calc.py"], "commit_message": "Add add()"})


def build_bad(req) -> str:
    (req.cwd / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    return envelope({"summary": "added add", "changed_files": ["calc.py"], "commit_message": "Add add()"})


def script(**by_agent):
    """FakeRunner whose answer depends on the agent name. Values: str, callable, or list (one per call)."""
    calls: dict[str, int] = {}

    def run(req):
        name = req.agent.name
        n = calls.get(name, 0)
        calls[name] = n + 1
        v = by_agent[name]
        if isinstance(v, list):
            v = v[min(n, len(v) - 1)]
        return v(req) if callable(v) else v

    return FakeRunner(run)
