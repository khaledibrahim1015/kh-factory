"""factory — command line entry point."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import config, jobs, tasks, worktree
from .runners import build as build_runners
from .runners.base import RunRequest
from .trace import Trace

DETECT = [  # (marker file, stack, test, lint)
    ("pyproject.toml", "Python (uv)", "uv run pytest -q", ""),
    ("package.json", "Node.js", "npm test --silent", ""),
    ("go.mod", "Go", "go test ./...", "go vet ./..."),
    ("Cargo.toml", "Rust", "cargo test", "cargo clippy -q"),
]


def _root() -> Path:
    return worktree.repo_root(Path.cwd())


def _template(rel: str) -> str:
    return (resources.files("kh_factory") / "templates" / rel).read_text()


def cmd_init(a) -> int:
    root = _root()
    stack, test, lint = "", "", ""
    for marker, s, t, l in DETECT:
        if (root / marker).exists():
            stack, test, lint = s, t, l
            break
    commands = "\n".join(f"    {k}: \"{v}\"" for k, v in (("test", test), ("lint", lint)) if v) \
        or "    {}   # add `test: \"<your test command>\"` here: required before any job can pass"
    files = {
        "factory.yaml": _template("factory.yaml").replace("{{stack}}", stack).replace("{{commands}}", commands),
        ".factory/learnings.md": _template("learnings.md"),
    }
    for agent in ("planner", "builder", "reviewer"):
        for kind in ("system", "user"):
            files[f".factory/prompts/{agent}/{kind}.md"] = _template(f"prompts/{agent}/{kind}.md")
    for rel, text in files.items():
        p = root / rel
        if p.exists() and not a.force:
            print(f"  keep    {rel} (exists; --force to overwrite)")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        print(f"  write   {rel}")
    (root / tasks.TASKS_DIR).mkdir(exist_ok=True)
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    add = [x for x in (".factory/jobs/", ".factory/trace.db*") if x not in lines]
    if add:
        gi.write_text("\n".join(lines + ["# kh-factory runtime", *add]) + "\n")
        print("  update  .gitignore")
    worktree.ensure_excludes(root)
    if a.with_skill:
        code, msg = install_skill(root / SKILL_REL, link=False, force=a.force)
        print(f"  skill   {msg}")
    print(f"\nDetected stack: {stack or 'none'}; test command: {test or 'NOT SET (edit factory.yaml)'}")
    print("Next: edit factory.yaml, run `factory config check`, add a task with `factory add \"...\"`, "
          "commit, then `factory next`.")
    return 0


def cmd_config_check(a) -> int:
    root = _root()
    cfg = config.load(root)
    problems = config.check(cfg, check_models=not a.no_models)
    for name, ag in cfg.agents.items():
        print(f"  {name:<10} runner={ag.runner:<7} model={ag.model:<28} thinking={ag.thinking:<7} "
              f"writes={ag.writes or 'read-only'}")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK")
    return 0


def cmd_add(a) -> int:
    path = tasks.add(_root(), a.title)
    print(f"created {path.relative_to(_root())} — fill in Objective and Acceptance before running it")
    return 0


def cmd_import(a) -> int:
    root = _root()
    src = Path(a.file).resolve()
    texts = tasks.parse_cards(src, root)
    d = root / tasks.TASKS_DIR
    d.mkdir(exist_ok=True)
    written = 0
    for text in texts:
        tid = text.split("id: ", 1)[1].split("\n", 1)[0].strip().strip("'\"")
        title = text.split("title: ", 1)[1].split("\n", 1)[0].strip().strip("'\"")
        p = d / f"{tid}-{tasks.slug(title)}.md"
        if p.exists() and not a.force:
            continue
        p.write_text(text)
        written += 1
    print(f"imported {written} of {len(texts)} task card(s) into {d.relative_to(root)}/")
    return 0


def _ago(ts: float | None) -> str:
    if not ts:
        return "-"
    s = int(time.time() - ts)
    return f"{s}s" if s < 120 else f"{s // 60}m" if s < 7200 else f"{s // 3600}h"


def cmd_status(a) -> int:
    root = _root()
    trace = Trace(root)
    all_tasks = tasks.load_all(root)
    print("TASKS")
    for t in all_tasks:
        st = trace.task_status(t.id)
        blocked = jobs.deps_done(trace, t)
        extra = f"  (waits for {', '.join(blocked)})" if blocked and st == "todo" else ""
        print(f"  {t.id:<10} {st:<15} {t.title}{extra}")
    if not all_tasks:
        print("  (none — `factory add \"<title>\"`)")
    print("\nJOBS")
    for j in trace.jobs()[: a.limit]:
        print(f"  {j['id']:<18} {j['status']:<15} {j['task_id']:<10} updated {_ago(j['updated_at']):>4} ago"
              f"  ${j['cost_usd'] or 0:.3f}")
        if j["reason"]:
            print(f"      {j['reason'][:300]}")
    return 0


def _report(job_id: str, status: str, reason: str) -> int:
    print(f"\njob {job_id}: {status}\n  {reason}")
    print(f"  details: factory show {job_id}   |   UI: factory ui")
    return 0 if status == "awaiting-human" and not reason.startswith("STOPPED") else 1


def cmd_run(a) -> int:
    root = _root()
    print(f"starting task {a.task} …")
    job_id, status, reason = jobs.start(root, a.task, force=a.force)
    return _report(job_id, status, reason)


def cmd_next(a) -> int:
    root = _root()
    t = jobs.next_task(root)
    if t is None:
        print("no runnable task (all done, running, waiting for you, or blocked by dependencies)")
        return 0
    print(f"next task: {t.id} — {t.title}")
    job_id, status, reason = jobs.start(root, t.id)
    return _report(job_id, status, reason)


def cmd_resume(a) -> int:
    status, reason = jobs.resume(_root(), a.job)
    return _report(a.job, status, reason)


def cmd_rework(a) -> int:
    status, reason = jobs.rework(_root(), a.job, a.note)
    return _report(a.job, status, reason)


def cmd_approve(a) -> int:
    sha = jobs.approve(_root(), a.job, code_only=a.code_only)
    print(f"merged job {a.job} as {sha[:8]}; worktree and branch removed")
    return 0


def cmd_reject(a) -> int:
    jobs.reject(_root(), a.job)
    print(f"rejected job {a.job}; worktree and branch removed, trace kept")
    return 0


def _k(n: int) -> str:
    return f"{n / 1e6:.1f}M".replace(".0M", "M") if n >= 1e6 else f"{round(n / 1e3)}k"


def _ctx(tokens: int | None, window: int | None) -> str:
    """'ctx 38k/1M (4%)', 'ctx 38k', or '' when nothing was measured."""
    if not tokens:
        return ""
    if not window:
        return f"ctx {_k(tokens)}"
    return f"ctx {_k(tokens)}/{_k(window)} ({100 * tokens / window:.0f}%)"


def cmd_show(a) -> int:
    root = _root()
    trace = Trace(root)
    j = trace.job(a.job)
    if not j:
        print(f"no single job matches '{a.job}'")
        return 1
    print(f"{j['id']}  {j['status']}  task {j['task_id']}: {j['title']}")
    print(f"  branch {j['branch']}  worktree {j['worktree']}  base {j['base_branch']}@{(j['base_sha'] or '')[:8]}")
    print(f"  cost ${j['cost_usd'] or 0:.3f}  reason: {j['reason']}")
    print(f"  files: {root / '.factory' / 'jobs' / j['id']}")
    report = root / ".factory" / "jobs" / j["id"] / "doc-updates.md"
    if report.exists():
        print(f"  doc updates: {report}")
    for p in trace.query("SELECT * FROM phases WHERE job_id = ? ORDER BY id", (j["id"],)):
        dur = f"{(p['ended_at'] or time.time()) - p['started_at']:.0f}s"
        ctx = _ctx(p.get("context_tokens"), p.get("context_window"))
        print(f"  [{p['status']:<7}] {p['name']:<10} {p['kind']:<6} {p['owner'] or '':<10} {dur:>6}  "
              f"{ctx:<16} {(p['detail'] or '')[:110]}")
    return 0


def cmd_try(a) -> int:
    """Smoke test one agent in the current directory, with no worktree and no gates."""
    root = _root()
    cfg = config.load(root)
    agent = cfg.agents[a.agent]
    runner = build_runners(cfg)[agent.runner]
    d = root / ".factory" / "jobs" / f"try-{datetime.now():%Y%m%d-%H%M%S}"
    req = RunRequest(agent=agent, prompt=a.prompt, system="", cwd=root, session_id=str(uuid.uuid4()),
                     resume=False, session_dir=d / "sessions", log_path=d / "raw.jsonl")
    r = runner.run(req)
    print(r.error if r.is_error else r.text)
    print(f"\n-- runner={agent.runner} model={agent.model} session={r.session_id} events={r.events} "
          f"cost={r.cost_usd} {_ctx(r.context_tokens, r.context_window) or 'ctx unknown'} log={req.log_path}")
    return 1 if r.is_error else 0


SKILL_REL = Path(".claude") / "skills" / "factory" / "SKILL.md"


def _skill_source() -> Path:
    return Path(str(resources.files("kh_factory") / "templates" / "skill" / "SKILL.md"))


def install_skill(dest: Path, *, link: bool, force: bool) -> tuple[int, str]:
    """User level: a symlink to the template, so skill updates arrive with kh-factory.
    Project level: a copy, so it can be committed and shared."""
    src = _skill_source()
    if link and dest.is_symlink() and dest.resolve() == src.resolve():
        return 0, f"up to date: {dest} -> {src}"
    if not link and dest.is_file() and not dest.is_symlink() and dest.read_text() == src.read_text():
        return 0, f"up to date: {dest}"
    if (dest.exists() or dest.is_symlink()) and not force:
        return 2, f"error: {dest} exists and differs; use --force to replace it"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    if link:
        dest.symlink_to(src)
        return 0, f"linked: {dest} -> {src}"
    dest.write_text(src.read_text())
    return 0, f"copied: {dest}"


def cmd_install_skill(a) -> int:
    """Install the /factory Claude Code skill: ~/.claude (symlink, all repos) or <repo>/.claude (copy)."""
    if a.project:
        dest, link = _root() / SKILL_REL, False
    else:
        dest, link = Path.home() / SKILL_REL, not a.copy
    code, msg = install_skill(dest, link=link, force=a.force)
    print(msg, file=sys.stderr if code else sys.stdout)
    if code == 0 and not msg.startswith("up to date"):
        print("In Claude Code, type /factory (start a new session if it is not listed).")
        if a.project:
            print(f"Commit it to share it: git add {SKILL_REL} && git commit -m 'Add /factory skill' -- {SKILL_REL}")
    return code


def cmd_ui(a) -> int:
    from .ui.server import serve
    serve(_root(), a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="factory", description="kh-factory: agent proposes, code disposes.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="install the factory into the current git repo")
    p.add_argument("--force", action="store_true")
    p.add_argument("--with-skill", action="store_true", help="also copy the /factory skill into <repo>/.claude/skills")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("config", help="config commands")
    cs = p.add_subparsers(dest="sub", required=True)
    c = cs.add_parser("check", help="validate factory.yaml")
    c.add_argument("--no-models", action="store_true", help="skip the `pi --list-models` check")
    c.set_defaults(fn=cmd_config_check)

    p = sub.add_parser("add", help="create a task file")
    p.add_argument("title")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("import", help="convert '### ID — Title' task cards into task files")
    p.add_argument("file")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("status", help="tasks and jobs")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("run", help="run one task")
    p.add_argument("task")
    p.add_argument("--force", action="store_true", help="ignore dependency and duplicate checks")
    p.set_defaults(fn=cmd_run)

    sub.add_parser("next", help="run the next runnable task").set_defaults(fn=cmd_next)

    p = sub.add_parser("approve", help="merge a job's branch locally")
    p.add_argument("job")
    p.add_argument("--code-only", action="store_true", help="merge the code commit only, not the docs commit")
    p.set_defaults(fn=cmd_approve)

    for name, fn, hlp in (("resume", cmd_resume, "continue a failed or stalled job"),
                          ("reject", cmd_reject, "drop a job's worktree and branch"),
                          ("show", cmd_show, "phases and files of one job")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("job")
        p.set_defaults(fn=fn)

    p = sub.add_parser("rework", help="send a job back to the builder (or planner) with a note")
    p.add_argument("job")
    p.add_argument("note")
    p.set_defaults(fn=cmd_rework)

    p = sub.add_parser("try", help="smoke test one agent with a prompt (no worktree, no gates)")
    p.add_argument("agent")
    p.add_argument("prompt")
    p.set_defaults(fn=cmd_try)

    p = sub.add_parser("install-skill", help="install the /factory skill for Claude Code")
    p.add_argument("--project", action="store_true", help="copy into <repo>/.claude/skills (to commit and share)")
    p.add_argument("--copy", action="store_true", help="user level: copy instead of symlink")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_install_skill)

    p = sub.add_parser("ui", help="web UI over the trace (read-only)")
    p.add_argument("--port", type=int, default=4600)
    p.set_defaults(fn=cmd_ui)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (config.ConfigError, tasks.TaskError, jobs.JobError, worktree.GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — the job shows as stalled; continue with `factory resume <job>`", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
