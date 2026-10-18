"""Context pack: code builds a small, exact context for each agent step.

Layers: repo rules, project facts, task, spec sections, repo map, previous step,
learnings. The agent can read more with its tools. The pack is saved as
context.md in the step folder, so you can see exactly what the agent saw.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import Config
from .tasks import Task

LEARNINGS = ".factory/learnings.md"


class ContextError(Exception):
    """code is SPEC_REF_NOT_FOUND or CONTEXT_TOO_LARGE."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def repo_rules(root: Path) -> str:
    parts = []
    seen = set()
    for name in ("AGENTS.md", "CLAUDE.md"):
        p = root / name
        if p.exists():
            text = p.read_text().strip()
            if text in seen or re.fullmatch(r"@AGENTS\.md", text):
                continue
            seen.add(text)
            parts.append(f"### {name}\n\n{text}")
    return "\n\n".join(parts)


def project_facts(cfg: Config) -> str:
    p = cfg.project
    lines = []
    if p.stack:
        lines.append(f"- Stack: {p.stack}")
    for k, v in p.commands.items():
        lines.append(f"- `{k}` command: `{v}`")
    for r in p.rules:
        lines.append(f"- Rule: {r}")
    if cfg.protected:
        lines.append("- Protected paths (you must not change them): " + ", ".join(f"`{x}`" for x in cfg.protected))
    return "\n".join(lines)


def task_text(task: Task) -> str:
    meta = f"- id: {task.id}\n- title: {task.title}"
    if task.depends_on:
        meta += f"\n- depends on: {', '.join(task.depends_on)}"
    if task.spec:
        meta += f"\n- spec: {', '.join(task.spec)}"
    return f"{meta}\n\n{task.body.strip()}"


HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def cut_section(text: str, anchor: str) -> str | None:
    """Return the section whose heading matches the anchor, up to the next heading of the same or higher level.

    Matches '§6', '6.', '6 ', '6)' style numbers, or a heading that contains the anchor text.
    """
    a = anchor.strip().lstrip("§").lower()
    headings = list(HEADING.finditer(text))
    for i, h in enumerate(headings):
        title = h.group(2).strip().lower().lstrip("§")
        numbered = re.match(rf"^{re.escape(a)}(?:[.)\s:—–-]|$)", title)
        if numbered or (not re.fullmatch(r"[\d.]+", a) and a in title):
            level = len(h.group(1))
            end = len(text)
            for h2 in headings[i + 1:]:
                if len(h2.group(1)) <= level:
                    end = h2.start()
                    break
            return text[h.start():end].strip()
    return None


def spec_sections(root: Path, refs: list[str]) -> list[tuple[str, str]]:
    out = []
    for ref in refs:
        path, _, anchor = ref.partition("#")
        p = root / path
        if not p.is_file():
            raise ContextError("SPEC_REF_NOT_FOUND", f"'{ref}': file {path} does not exist")
        text = p.read_text()
        if anchor:
            sec = cut_section(text, anchor)
            if sec is None:
                raise ContextError("SPEC_REF_NOT_FOUND", f"'{ref}': no heading matching '{anchor}' in {path}")
            out.append((ref, sec))
        else:
            out.append((ref, text.strip()))
    return out


def repo_map(root: Path, depth: int = 3, max_entries: int = 300) -> str:
    try:
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root,
                               capture_output=True, text=True, check=True).stdout.splitlines()
    except (subprocess.CalledProcessError, OSError):
        return ""
    entries = set()
    for f in files:
        parts = f.split("/")
        for d in range(1, min(len(parts), depth) + 1):
            p = "/".join(parts[:d])
            entries.add(p + ("/" if d < len(parts) else ""))
    lines = sorted(entries)
    more = len(lines) - max_entries
    lines = lines[:max_entries]
    return "\n".join(lines) + (f"\n... ({more} more)" if more > 0 else "")


def learnings(root: Path) -> str:
    p = root / LEARNINGS
    return p.read_text().strip() if p.exists() else ""


def build(cfg: Config, *, layers: dict[str, str]) -> str:
    """Join non-empty layers into context.md and enforce the token budget."""
    order = ["Repo rules", "Project facts", "Learnings", "Task", "Spec sections", "Plan",
             "Previous review", "Previous step", "Diff", "Test result", "Repo map"]
    blocks = [f"## {name}\n\n{layers[name].strip()}" for name in order if layers.get(name, "").strip()]
    text = "# Context pack\n\n" + "\n\n".join(blocks) + "\n"
    tokens = estimate_tokens(text)
    if tokens > cfg.project.context_budget_tokens:
        sizes = ", ".join(f"{n}≈{estimate_tokens(layers[n])}" for n in order if layers.get(n))
        raise ContextError("CONTEXT_TOO_LARGE",
                           f"pack is ≈{tokens} tokens, budget is {cfg.project.context_budget_tokens} ({sizes}). "
                           f"Narrow the task's spec refs or raise project.context_budget_tokens.")
    return text


def format_specs(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"### {ref}\n\n{text}" for ref, text in sections)
