"""Pi runner: `pi -p --mode json`, prompt on stdin, session pinned with --session-id."""

from __future__ import annotations

import subprocess

from ..config import pi_catalog
from .base import RunRequest, RunResult, Runner


class PiRunner(Runner):
    name = "pi"

    def command(self, req: RunRequest) -> list[str]:
        a = req.agent
        # --no-context-files: Pi must not load AGENTS.md/CLAUDE.md (repo, parents, global) on its own;
        # the context pack carries the repo rules, so context.md is exactly what the agent saw.
        cmd = [self.cfg.bin, "-p", "--mode", "json", "--no-context-files",
               "--session-dir", str(req.session_dir), "--session-id", req.session_id,
               "--model", a.model, "--thinking", a.thinking,
               "--tools", ",".join(a.tools)]
        if req.system:
            cmd += ["--append-system-prompt", req.system]
        return cmd + self.cfg.extra_args

    def parse(self, events: list[dict], session_id: str) -> RunResult:
        result = parse_pi(events, session_id)
        model = _last_model(events)
        if model:
            try:
                result.context_window = pi_catalog(self.cfg.bin).get(model, 0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return result


def _last_model(events: list[dict]) -> str:
    for ev in reversed(events):
        m = ev.get("message") or {}
        if ev.get("type") == "message_end" and m.get("role") == "assistant" and m.get("model"):
            return f"{m.get('provider', '')}/{m['model']}"
    return ""


def turn_tokens(usage: dict) -> int:
    """Tokens in the window after a turn: pi's totalTokens, else the sum of its parts (as sssf does)."""
    return int(usage.get("totalTokens") or sum(int(usage.get(k) or 0)
                                               for k in ("input", "output", "cacheRead", "cacheWrite")))


def parse_pi(events: list[dict], session_id: str) -> RunResult:
    """The final answer is the last assistant `message_end`. stopReason 'error' = failed."""
    last = None
    cost = 0.0
    context = 0
    for ev in events:
        if ev.get("type") == "session" and ev.get("id"):
            session_id = ev["id"]
        if ev.get("type") == "message_end" and (ev.get("message") or {}).get("role") == "assistant":
            last = ev["message"]
            usage = last.get("usage") or {}
            cost += (usage.get("cost") or {}).get("total") or 0.0
            # an errored or aborted turn reports usage you cannot trust: keep the last good reading
            if last.get("stopReason") not in ("error", "aborted") and turn_tokens(usage):
                context = turn_tokens(usage)
    if last is None:
        notes = " | ".join(e.get("text", "") for e in events if e.get("type") == "stderr")[-500:]
        return RunResult("", session_id, True, f"RUNNER_ERROR: pi returned no assistant message. {notes}", cost)
    if last.get("stopReason") in ("error", "aborted"):
        return RunResult("", session_id, True, f"RUNNER_ERROR: {last.get('errorMessage', last.get('stopReason'))}",
                         cost, context_tokens=context)
    text = "\n".join(c.get("text", "") for c in last.get("content") or [] if c.get("type") == "text")
    return RunResult(text, session_id, False, "", cost, context_tokens=context)
