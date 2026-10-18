"""Fake runner for tests: a Python function plays the agent. No process, no model."""

from __future__ import annotations

from typing import Callable

from .base import EventSink, RunRequest, RunResult, Runner

Script = Callable[[RunRequest], str]


class FakeRunner(Runner):
    name = "fake"

    def __init__(self, script: Script, context: tuple[int, int] = (0, 0)):
        self.script = script
        self.context = context  # (tokens, window) reported for every call
        self.calls: list[RunRequest] = []

    def run(self, req: RunRequest, on_event: EventSink | None = None) -> RunResult:
        self.calls.append(req)
        if on_event:
            on_event({"type": "fake_call", "agent": req.agent.name, "resume": req.resume})
        try:
            text = self.script(req)
        except RuntimeError as e:
            return RunResult("", req.session_id, True, f"RUNNER_ERROR: {e}")
        return RunResult(text, req.session_id, False, "", 0.0, 1,
                         context_tokens=self.context[0], context_window=self.context[1])
