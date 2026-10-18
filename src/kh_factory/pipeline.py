"""Shared step runner used by every flow: phases, agent calls, envelopes, gates, trace.

Every phase defaults to failed. Code marks success.
"""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from pydantic import BaseModel

from . import envelope, gates
from .config import Config
from .runners.base import RunRequest, Runner
from .tasks import Task
from .trace import CONTEXT_WARN_PCT, Trace

T = TypeVar("T", bound=BaseModel)
SoftGates = Callable[[BaseModel, gates.Snapshot], list[gates.GateResult]]


class PhaseFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class StopForHuman(Exception):
    """The job needs a decision from the human (questions, spec conflict)."""


@dataclass
class Job:
    id: str
    task: Task
    cfg: Config
    trace: Trace
    runners: dict[str, Runner]
    root: Path        # main repo
    wt: Path          # this job's worktree
    branch: str
    base_sha: str
    dir: Path = field(init=False)  # runtime folder: .factory/jobs/<id>/
    step_no: int = 0

    def __post_init__(self):
        self.dir = self.root / ".factory" / "jobs" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)

    def step_dir(self, name: str) -> Path:
        self.step_no += 1
        d = self.dir / "steps" / f"{self.step_no:02d}-{name}"
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass
class Phase:
    job: Job
    id: int
    name: str
    status: str = "failed"
    detail: str = ""

    def succeed(self, detail: str = "") -> None:
        self.status, self.detail = "passed", detail

    def log(self, **data) -> None:
        self.job.trace.event(self.job.id, self.id, {"type": "log", **data})


@contextmanager
def phase(job: Job, name: str, kind: str, owner: str) -> Iterator[Phase]:
    ph = Phase(job, job.trace.start_phase(job.id, name, kind, owner), name)
    job.trace.heartbeat(job.id)
    try:
        yield ph
    except Exception as e:
        ph.status, ph.detail = "failed", str(e)
        raise
    finally:
        job.trace.end_phase(ph.id, ph.status, ph.detail)


@contextmanager
def heartbeat(job: Job, every_s: float = 15) -> Iterator[None]:
    stop = threading.Event()

    def beat():
        while not stop.wait(every_s):
            job.trace.heartbeat(job.id)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def render(template: str, values: dict[str, str]) -> str:
    """Prompt templates use {{name}} placeholders, so JSON braces stay literal."""
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def call_agent(job: Job, ph: Phase, agent_name: str, *, context: str, output_type: type[T],
               values: dict[str, str] | None = None, soft_gates: SoftGates | None = None,
               resume: bool = False, writes: list[str] | None = None,
               protected: list[str] | None = None) -> T:
    """Run one agent step until it returns a valid envelope that passes its gates.

    resume=True continues this agent's earlier session for the job (fix and revise steps).
    writes / protected override the agent's write rules for this one step (the docs step).
    """
    cfg, agent = job.cfg, job.cfg.agents[agent_name]
    runner = job.runners.get(agent.runner)
    if runner is None:
        raise PhaseFailed("RUNNER_ERROR", f"runner '{agent.runner}' is not available")
    step = job.step_dir(ph.name)
    (step / "context.md").write_text(context)

    session_id = job.trace.session(job.id, agent_name) if resume else None
    is_resume = session_id is not None
    if session_id is None:
        session_id = str(uuid.uuid4())
        job.trace.set_session(job.id, agent_name, agent.runner, session_id)

    system = (cfg.root / agent.prompts.system).read_text()
    user = render((cfg.root / agent.prompts.user).read_text(),
                  {"context": context, "task_id": job.task.id, "task_title": job.task.title,
                   **(values or {})}) + envelope.instructions(output_type)
    snap = gates.snapshot(job.wt)
    prompt = user
    retries = cfg.pipeline.max_envelope_retries

    for attempt in range(retries + 1):
        (step / f"prompt-{attempt}.md").write_text(prompt)
        req = RunRequest(agent=agent, prompt=prompt, system=system, cwd=job.wt, session_id=session_id,
                         resume=is_resume, session_dir=job.dir / "sessions",
                         log_path=step / "raw.jsonl")
        ph.log(agent=agent_name, runner=agent.runner, model=agent.model, attempt=attempt, resume=is_resume)
        result = runner.run(req, on_event=lambda ev: job.trace.event(job.id, ph.id, ev))
        job.trace.add_cost(job.id, result.cost_usd)
        if result.context_tokens:
            pct = job.trace.set_context(job.id, ph.id, result.context_tokens, result.context_window)
            if pct >= CONTEXT_WARN_PCT:
                job.trace.event(job.id, ph.id, {"type": "warning", "text": f"agent '{agent_name}' used {pct}% of "
                                f"its context window ({result.context_tokens}/{result.context_window} tokens)"})
        is_resume = True  # any later attempt continues the same session
        (step / f"answer-{attempt}.md").write_text(result.text or result.error)

        rules = agent if writes is None else agent.model_copy(update={"writes": writes})
        boundary = gates.writes_boundary(job.wt, snap, rules, cfg.protected if protected is None else protected)
        job.trace.gate(job.id, ph.id, boundary.name, boundary.passed, boundary.detail)
        if not boundary.passed:
            raise PhaseFailed("WRITES_BOUNDARY", boundary.detail)
        if result.is_error:
            raise PhaseFailed("RUNNER_ERROR", result.error)

        try:
            env = envelope.parse(result.text, output_type)
        except ValueError as e:
            job.trace.gate(job.id, ph.id, "envelope", False, str(e))
            prompt = (f"Your last answer could not be used: {e}\n"
                      f"Reply again. End with one ```json block that matches {output_type.__name__}.")
            continue
        job.trace.gate(job.id, ph.id, "envelope", True, output_type.__name__)

        results = soft_gates(env, snap) if soft_gates else []
        for g in results:
            job.trace.gate(job.id, ph.id, g.name, g.passed, g.detail)
        failed = [g for g in results if not g.passed]
        if not failed:
            data = env.model_dump()
            job.trace.envelope(job.id, ph.id, output_type.__name__, data)
            (step / "envelope.json").write_text(json.dumps(data, indent=2))
            return env
        prompt = ("Code checks failed on your last answer:\n"
                  + "\n".join(f"- {g.name}: {g.detail}" for g in failed)
                  + f"\nFix this, then reply again ending with one ```json block ({output_type.__name__}).")

    raise PhaseFailed("ENVELOPE_INVALID", f"agent '{agent_name}' gave no valid answer after {retries + 1} attempts")
