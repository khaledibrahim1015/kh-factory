"""Runner interface: start one agent CLI process, stream its JSON events, return the final text.

Both CLIs exit 0 on model errors (verified 2026-10-18), so a runner never trusts
the exit code. It reads the error fields of the event stream instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Agent, RunnerCfg

EventSink = Callable[[dict], None]


@dataclass
class RunResult:
    text: str
    session_id: str
    is_error: bool = False
    error: str = ""
    cost_usd: float | None = None
    events: int = 0
    context_tokens: int = 0   # tokens in the model's window after the last valid turn (as sssf measures it)
    context_window: int = 0   # the model's limit; 0 = unknown


@dataclass
class RunRequest:
    agent: Agent
    prompt: str
    system: str
    cwd: Path
    session_id: str
    resume: bool          # True = continue this session; False = start it
    session_dir: Path     # where the runner may keep its own session files
    log_path: Path        # raw stream is copied here


class Runner:
    name = "base"

    def __init__(self, cfg: RunnerCfg):
        self.cfg = cfg

    def command(self, req: RunRequest) -> list[str]:
        raise NotImplementedError

    def env(self, req: RunRequest) -> dict[str, str]:
        return dict(os.environ)

    def parse(self, events: list[dict], session_id: str) -> RunResult:
        raise NotImplementedError

    def run(self, req: RunRequest, on_event: EventSink | None = None) -> RunResult:
        cmd = self.command(req)
        req.log_path.parent.mkdir(parents=True, exist_ok=True)
        events: list[dict] = []
        try:
            proc = subprocess.Popen(cmd, cwd=req.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=self.env(req), bufsize=1)
        except OSError as e:
            return RunResult("", req.session_id, True, f"RUNNER_ERROR: cannot start {cmd[0]}: {e}")

        def pump():
            with req.log_path.open("a") as log:
                for line in proc.stdout:
                    log.write(line)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if not isinstance(ev, dict):
                            ev = {"type": "text", "text": line}
                    except json.JSONDecodeError:
                        ev = {"type": "stderr", "text": line}
                    events.append(ev)
                    if on_event:
                        try:
                            on_event(ev)
                        except Exception:  # tracing must never kill a run
                            pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            proc.stdin.write(req.prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        deadline = time.monotonic() + req.agent.timeout_min * 60
        timed_out = False
        while proc.poll() is None:
            if time.monotonic() > deadline:
                timed_out = True
                proc.kill()
                break
            time.sleep(0.2)
        proc.wait()
        reader.join(timeout=10)
        if timed_out:
            return RunResult("", req.session_id, True,
                             f"RUNNER_ERROR: TIMEOUT after {req.agent.timeout_min} min", events=len(events))
        result = self.parse(events, req.session_id)
        result.events = len(events)
        if not result.is_error and proc.returncode != 0 and not result.text:
            tail = " | ".join(e.get("text", "") for e in events[-5:] if e.get("type") == "stderr")
            result.is_error, result.error = True, f"RUNNER_ERROR: exit {proc.returncode}: {tail}"
        return result
