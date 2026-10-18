"""Load and check factory.yaml.

Pipeline steps name agents, never models. Each agent inherits from `defaults:`
and overrides only what it sets.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

CONFIG_NAME = "factory.yaml"
RUNNER_NAMES = ("pi", "claude", "fake")
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


class Prompts(BaseModel):
    system: str
    user: str


class Agent(BaseModel):
    name: str
    runner: Literal["pi", "claude", "fake"]
    model: str
    thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] = "medium"
    timeout_min: float = 20
    tools: list[str]
    # Paths this agent may change in the repo. [] = read-only. "**" = everything
    # except protected files. The job's runtime folder is always writable.
    writes: list[str] = Field(default_factory=list)
    prompts: Prompts


class RunnerCfg(BaseModel):
    bin: str
    extra_args: list[str] = Field(default_factory=list)


class Pipeline(BaseModel):
    flow: str = "sdlc"
    plan: str = "planner"
    build: str = "builder"
    fix: str = "builder"
    review: str = "reviewer"
    max_fix_loops: int = 3
    max_review_loops: int = 2
    max_envelope_retries: int = 2
    doc_updates: Literal["propose", "apply", "off"] = "propose"
    docs: str = "builder"                      # agent for the docs step (apply mode)
    # ADRs and the repo rules files: proposal only, never applied by an agent
    docs_never_apply: list[str] = Field(default_factory=lambda: ["docs/decisions/", "AGENTS.md", "CLAUDE.md"])


class Project(BaseModel):
    stack: str = ""
    commands: dict[str, str] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    context_budget_tokens: int = 40000
    command_timeout_min: float = 10
    max_diff_lines: int = 2000       # reviewer sees at most this many diff lines (sssf default)


class Config(BaseModel):
    root: Path
    protected_files: list[str]
    runners: dict[str, RunnerCfg]
    agents: dict[str, Agent]
    pipeline: Pipeline
    project: Project

    @property
    def protected(self) -> list[str]:
        """Protected paths, plus doc folders: agents never edit specs silently."""
        out = list(self.protected_files)
        for p in self.project.context:
            out.append(p.rstrip("/") + "/")
        return out


class ConfigError(Exception):
    pass


DEFAULT_PROTECTED = ["factory.yaml", ".factory/", "tasks/", ".claude/", "AGENTS.md", "CLAUDE.md"]
DEFAULT_RUNNERS = {"pi": {"bin": "pi"}, "claude": {"bin": "claude"}, "fake": {"bin": "fake"}}


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    if not path.exists():
        raise ConfigError(f"{path} not found. Run `factory init` first.")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    return parse(raw, root)


def parse(raw: dict, root: Path) -> Config:
    defaults = dict(raw.get("defaults") or {})
    protected = defaults.pop("protected_files", DEFAULT_PROTECTED)
    agents = {}
    errors = []
    for name, spec in (raw.get("agents") or {}).items():
        merged = {**defaults, **(spec or {}), "name": name}
        try:
            agents[name] = Agent.model_validate(merged)
        except ValidationError as e:
            errors.append(f"agents.{name}: {_short(e)}")
    runners_raw = {**DEFAULT_RUNNERS, **(raw.get("runners") or {})}
    try:
        cfg = Config(
            root=root,
            protected_files=protected,
            runners={k: RunnerCfg.model_validate(v) for k, v in runners_raw.items()},
            agents=agents,
            pipeline=Pipeline.model_validate(raw.get("pipeline") or {}),
            project=Project.model_validate({k: v for k, v in (raw.get("project") or {}).items()
                                            if v is not None}),
        )
    except ValidationError as e:
        errors.append(_short(e))
    if errors:
        raise ConfigError("invalid factory.yaml:\n  - " + "\n  - ".join(errors))
    return cfg


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())


def check(cfg: Config, check_models: bool = True) -> list[str]:
    """Return every problem found. Empty list = config is usable."""
    from . import flows

    problems = []
    for step in ("plan", "build", "fix", "review", "docs"):
        agent = getattr(cfg.pipeline, step)
        if agent not in cfg.agents:
            problems.append(f"pipeline.{step}: agent '{agent}' is not defined under agents:")
    if cfg.pipeline.flow not in flows.FLOWS:
        problems.append(f"pipeline.flow: no flow '{cfg.pipeline.flow}' (have: {', '.join(flows.FLOWS)})")
    for a in cfg.agents.values():
        if a.runner not in cfg.runners:
            problems.append(f"agents.{a.name}.runner: no runner '{a.runner}' under runners:")
        for kind in ("system", "user"):
            p = cfg.root / getattr(a.prompts, kind)
            if not p.exists():
                problems.append(f"agents.{a.name}.prompts.{kind}: file not found: {p}")
        for w in a.writes:
            for prot in cfg.protected:
                if w not in ("**", "*") and (w.rstrip("/") == prot.rstrip("/") or w.startswith(prot)
                                             or prot.startswith(w.rstrip("/") + "/")):
                    problems.append(f"agents.{a.name}.writes: '{w}' overlaps protected path '{prot}'")
    if check_models:
        problems += _check_pi_models(cfg)
    return problems


def parse_size(text: str) -> int:
    """'200K' -> 200000, '1.0M' -> 1000000, '262.1K' -> 262100. Unknown -> 0."""
    m = re.fullmatch(r"([\d.]+)\s*([KMG]?)", text.strip().upper())
    if not m:
        return 0
    return int(round(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]))


@lru_cache(maxsize=4)
def pi_catalog(bin_: str = "pi") -> dict[str, int]:
    """{provider/id: context window} from `pi --list-models`. Raises OSError / TimeoutExpired."""
    out = subprocess.run([bin_, "--list-models"], capture_output=True, text=True, timeout=60).stdout
    catalog = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] != "provider":
            catalog[f"{parts[0]}/{parts[1]}"] = parse_size(parts[2])
    return catalog


def _check_pi_models(cfg: Config) -> list[str]:
    pi_agents = [a for a in cfg.agents.values() if a.runner == "pi"]
    if not pi_agents:
        return []
    bin_ = cfg.runners["pi"].bin
    try:
        known = pi_catalog(bin_)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"runners.pi: cannot run `{bin_} --list-models`: {e}"]
    problems = []
    for a in pi_agents:
        model = a.model.split(":")[0]
        if model not in known:
            problems.append(f"agents.{a.name}.model: '{a.model}' not in `pi --list-models` "
                            f"(use provider/id, e.g. opencode-go/kimi-k3)")
    return problems
