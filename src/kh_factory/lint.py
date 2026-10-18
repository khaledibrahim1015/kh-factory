"""Task lint: check that a task is clear enough to build, and that its context pack will be good.

Errors block `factory run` (unless --no-lint). Warnings are printed. All checks are code, so the
same task always gets the same result; judgment checks (two meanings, title vs body) stay with
the human or the /factory skill at the keyboard.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import context
from .config import Config
from .tasks import Task

PLACEHOLDERS = ("(write what must be true", "(which tests prove it)")
# Unmeasurable or open-ended words (super-board lint criteria 3, 5, 8). Whole words only.
VAGUE = ["fast", "faster", "quick", "quickly", "snappy", "snappier", "smooth", "polished", "modern", "nice",
         "nicer", "better", "improve", "improved", "clean", "cleaner", "intuitive", "user-friendly", "robust",
         "many", "some", "several", "various", "etc", "tbd", "todo", "and so on", "as needed", "if possible",
         "appropriate", "properly", "correctly"]
VAGUE_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in VAGUE) + r")(?![\w-])", re.I)
FAKE_RE = re.compile(r"\b(fake|stub|mock|in-memory|no database|no network|hard-?coded)\b", re.I)
ENV_RE = re.compile(r"\b([A-Z][A-Z0-9]*_(?:KEY|TOKEN|SECRET|PASSWORD|URL|DSN|ID))\b")
MAX_ACCEPTANCE = 6
RULES_PLACEHOLDER = "<!-- kh-factory: fill in -->"


@dataclass
class Issue:
    level: str   # error | warn
    code: str
    message: str


def _bullets(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+)$", text, re.M)]


def lint_task(cfg: Config, root: Path, task: Task, all_tasks: list[Task]) -> list[Issue]:
    out: list[Issue] = []
    add = lambda level, code, msg: out.append(Issue(level, code, msg))  # noqa: E731

    objective = task.section("Objective")
    acceptance = task.section("Acceptance")
    tests_text = task.section("Tests")

    # ---- clarity: can a builder and a reviewer tell when it is done? ----
    if not objective.strip() or objective.strip() == task.title.strip():
        add("warn", "THIN_OBJECTIVE", "`## Objective` only repeats the title; say what changes and where")
    if not acceptance.strip() or any(p in acceptance for p in PLACEHOLDERS):
        add("error", "NO_ACCEPTANCE", "`## Acceptance` is missing or still the template text")
    else:
        items = _bullets(acceptance)
        if not items:
            add("warn", "ACCEPTANCE_NOT_LIST", "write acceptance as `- ` bullets, one checkable outcome each")
        if len(items) > MAX_ACCEPTANCE:
            add("warn", "TOO_BIG", f"{len(items)} acceptance items (max {MAX_ACCEPTANCE}); split the task")
        if items and not any(re.search(r"[`=<>()\d\"']|returns|raises|prints|exits|shows|error", a, re.I)
                             for a in items):
            add("warn", "NOT_CHECKABLE", "no acceptance item names an exact value, output or error a test can assert")
        if FAKE_RE.search(acceptance) and not task.notes_mention_owner():
            add("warn", "FAKE_WITHOUT_OWNER",
                "acceptance is proven with a fake/stub/mock; name the task that builds the real one in Notes")
    words = sorted({m.group(1).lower() for m in VAGUE_RE.finditer(f"{objective}\n{acceptance}")})
    if words:
        add("warn", "VAGUE_WORDS", f"unmeasurable or open words: {', '.join(words)}; replace with exact behavior")
    if not tests_text.strip() or any(p in tests_text for p in PLACEHOLDERS):
        add("warn", "NO_TESTS", "`## Tests` is empty; say where the tests go and what they prove")

    # ---- dependencies ----
    ids = {t.id for t in all_tasks}
    for d in task.depends_on:
        if d not in ids:
            add("error", "UNKNOWN_DEPENDENCY", f"depends_on names `{d}`, which is not a task")
    if cycle := _cycle(task, {t.id: t for t in all_tasks}):
        add("error", "DEPENDENCY_CYCLE", " -> ".join(cycle))

    # ---- context: will the agents get what they need? ----
    try:
        specs = context.spec_sections(root, task.spec)
    except context.ContextError as e:
        add("error", e.code, e.message)
        specs = []
    if cfg.project.context and not task.spec:
        add("warn", "NO_SPEC_REFS", f"the repo has docs ({', '.join(cfg.project.context)}) but the task names no "
                                    "`spec:` section; the agents will not see the relevant spec")
    try:
        pack = context.build(cfg, layers={"Repo rules": context.repo_rules(root),
                                          "Project facts": context.project_facts(cfg),
                                          "Learnings": context.learnings(root), "Task": context.task_text(task),
                                          "Spec sections": context.format_specs(specs),
                                          "Repo map": context.repo_map(root)})
        if context.estimate_tokens(pack) > 0.6 * cfg.project.context_budget_tokens:
            add("warn", "CONTEXT_NEAR_BUDGET", f"planner pack ≈{context.estimate_tokens(pack)} tokens of "
                                               f"{cfg.project.context_budget_tokens}; later steps add the plan and diff")
    except context.ContextError as e:
        add("error", e.code, e.message)
    for name in sorted(set(ENV_RE.findall(f"{objective}\n{acceptance}\n{tests_text}\n{task.section('Notes')}"))):
        if not os.environ.get(name):
            add("warn", "ENV_MISSING", f"`{name}` is named in the task but not set here (value never read)")
    return out


def lint_repo(cfg: Config, root: Path) -> list[Issue]:
    """Checks that apply to every task: repo rules and the test command."""
    out = []
    rules = context.repo_rules(root)
    if not rules:
        out.append(Issue("warn", "NO_REPO_RULES", "no AGENTS.md or CLAUDE.md: agents get no repo conventions "
                                                  "(`factory init` writes a starter AGENTS.md)"))
    elif RULES_PLACEHOLDER in rules:
        out.append(Issue("warn", "RULES_NOT_FILLED", "AGENTS.md still has the starter Conventions text; "
                                                     "agents get no real conventions"))
    test = cfg.project.commands.get("test", "")
    if not test:
        out.append(Issue("error", "NO_TEST_COMMAND", "set project.commands.test in factory.yaml"))
    else:
        first = _first_word(test)
        if first and not shutil.which(first) and not (root / first).exists():
            out.append(Issue("error", "TEST_COMMAND_NOT_FOUND", f"`{first}` (from the test command) is not on PATH"))
    return out


def _first_word(cmd: str) -> str:
    try:
        words = shlex.split(cmd)
    except ValueError:
        return ""
    words = [w for w in words if "=" not in w.split("/")[0]]  # skip VAR=value prefixes
    return words[0] if words and words[0] not in ("cd", "(", "env") else ""


def _cycle(task: Task, by_id: dict[str, Task]) -> list[str]:
    path: list[str] = []

    def walk(tid: str) -> list[str]:
        if tid in path:
            return path[path.index(tid):] + [tid]
        if tid not in by_id:
            return []
        path.append(tid)
        for d in by_id[tid].depends_on:
            if found := walk(d):
                return found
        path.pop()
        return []
    found = walk(task.id)
    return found if found and task.id in found else []


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]
