"""Claude Code runner: `claude -p --output-format stream-json --verbose`, prompt on stdin.

Note: `--allowedTools` takes a variable number of values, so the prompt must not
be a positional argument after it. That is why the prompt goes on stdin.
"""

from __future__ import annotations

from .base import RunRequest, RunResult, Runner

# factory tool names -> Claude Code tool names
TOOLS = {"read": "Read", "edit": "Edit", "write": "Write", "bash": "Bash",
         "grep": "Grep", "find": "Glob", "ls": "Glob"}

# Assumed: Claude Code reads MAX_THINKING_TOKENS from the environment.
THINKING_TOKENS = {"off": "0", "minimal": "1024", "low": "4000", "medium": "10000",
                   "high": "32000", "xhigh": "48000", "max": "63999"}


class ClaudeRunner(Runner):
    name = "claude"

    def command(self, req: RunRequest) -> list[str]:
        a = req.agent
        tools = sorted({TOOLS.get(t, t) for t in a.tools})
        # --safe-mode: no CLAUDE.md auto-discovery (and no user skills, plugins, hooks, output styles);
        # auth, models, built-in tools and permissions work normally (verified 2026-10-18).
        # The context pack carries the repo rules. (--bare would also do it, but needs an API key.)
        cmd = [self.cfg.bin, "-p", "--safe-mode", "--output-format", "stream-json", "--verbose",
               "--model", a.model, "--permission-mode", "acceptEdits"]
        cmd += ["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]
        if req.system:
            cmd += ["--append-system-prompt", req.system]
        cmd += self.cfg.extra_args
        cmd += ["--allowedTools", ",".join(tools)]  # last: it is variadic
        return cmd

    def env(self, req: RunRequest) -> dict[str, str]:
        env = super().env(req)
        env["MAX_THINKING_TOKENS"] = THINKING_TOKENS[req.agent.thinking]
        return env

    def parse(self, events: list[dict], session_id: str) -> RunResult:
        return parse_claude(events, session_id)


def parse_claude(events: list[dict], session_id: str) -> RunResult:
    """The final answer is the `result` event. `is_error` decides; `subtype` can say success on errors."""
    context = _context_tokens(events)
    for ev in reversed(events):
        if ev.get("type") == "result":
            sid = ev.get("session_id") or session_id
            cost = ev.get("total_cost_usd")
            window = max((int(m.get("contextWindow") or 0) for m in (ev.get("modelUsage") or {}).values()),
                         default=0)
            if ev.get("is_error"):
                status = ev.get("api_error_status")
                return RunResult("", sid, True, f"RUNNER_ERROR: {ev.get('result', '')}"
                                 + (f" (api status {status})" if status else ""), cost,
                                 context_tokens=context, context_window=window)
            return RunResult(ev.get("result") or "", sid, False, "", cost,
                             context_tokens=context, context_window=window)
    notes = " | ".join(e.get("text", "") for e in events if e.get("type") == "stderr")[-500:]
    return RunResult("", session_id, True, f"RUNNER_ERROR: claude returned no result event. {notes}")


def _context_tokens(events: list[dict]) -> int:
    """Tokens in the window after the last assistant turn: input + cache writes + cache reads + output."""
    for ev in reversed(events):
        if ev.get("type") == "assistant":
            u = (ev.get("message") or {}).get("usage") or {}
            total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens", "output_tokens"))
            if total:
                return total
    return 0
