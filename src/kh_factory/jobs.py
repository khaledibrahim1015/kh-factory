"""Job lifecycle: start, resume, rework, approve, reject. SQLite holds the state.

    queued -> running -> awaiting-human -> approved | rejected
                      -> failed (resume or rework) ; running + dead process = stalled
"""

from __future__ import annotations

import traceback
import uuid
from pathlib import Path

from . import config, flows, lint, runners, tasks, worktree
from .pipeline import Job, PhaseFailed, StopForHuman, heartbeat
from .runners.base import Runner
from .trace import Trace


class JobError(Exception):
    pass


def _cfg(root: Path, check_models: bool = True) -> config.Config:
    cfg = config.load(root)
    problems = config.check(cfg, check_models=check_models)
    if problems:
        raise JobError("factory.yaml has problems (run `factory config check`):\n  - " + "\n  - ".join(problems))
    return cfg


def _job(root: Path, trace: Trace, row: dict, cfg: config.Config, runner_map: dict[str, Runner]) -> Job:
    task = tasks.find(root, row["task_id"])
    return Job(id=row["id"], task=task, cfg=cfg, trace=trace, runners=runner_map, root=root,
               wt=Path(row["worktree"]), branch=row["branch"], base_sha=row["base_sha"])


def _get(trace: Trace, job_id: str) -> dict:
    row = trace.job(job_id)
    if row is None:
        raise JobError(f"no single job matches '{job_id}' (see `factory status`)")
    return row


def deps_done(trace: Trace, task: tasks.Task) -> list[str]:
    """Return the dependencies that are not done yet."""
    return [d for d in task.depends_on if trace.task_status(d) != "done"]


def lint_errors(cfg: config.Config, root: Path, task: tasks.Task) -> list[lint.Issue]:
    return lint.errors(lint.lint_repo(cfg, root) + lint.lint_task(cfg, root, task, tasks.load_all(root)))


def start(root: Path, task_id: str, *, force: bool = False, run_lint: bool = True,
          runner_map: dict[str, Runner] | None = None, check_models: bool = True) -> tuple[str, str, str]:
    cfg = _cfg(root, check_models)
    trace = Trace(root)
    task = tasks.find(root, task_id)
    if run_lint and (errs := lint_errors(cfg, root, task)):
        raise JobError(f"task {task.id} fails lint (fix it, or use --no-lint):\n  - "
                       + "\n  - ".join(f"{e.code}: {e.message}" for e in errs))
    status = trace.task_status(task.id)
    if status in ("running", "awaiting-human", "stalled", "done") and not force:
        raise JobError(f"task {task.id} is already {status}. Use --force to start another job.")
    missing = deps_done(trace, task)
    if missing and not force:
        raise JobError(f"task {task.id} depends on unfinished task(s): {', '.join(missing)}")
    if not worktree.has_commits(root):
        raise JobError("the repo has no commits yet. Commit once (e.g. factory.yaml and tasks/) first.")
    worktree.ensure_excludes(root)
    job_id = f"{task.id.lower()}-{uuid.uuid4().hex[:6]}"
    base_branch, base_sha = worktree.current_branch(root), worktree.rev(root)
    wt, branch = worktree.create(root, job_id, base_sha)
    trace.create_job(job_id, task.id, task.title, branch=branch, worktree=str(wt),
                     base_branch=base_branch, base_sha=base_sha)
    row = trace.job(job_id)
    return job_id, *execute(_job(root, trace, row, cfg, runner_map or runners.build(cfg)))


def execute(job: Job, rework_note: str | None = None) -> tuple[str, str]:
    """Run the configured flow. Returns (status, reason). Never raises for flow failures."""
    t = job.trace
    t.update_job(job.id, status="running", reason="")
    t.heartbeat(job.id)
    flow = flows.FLOWS[job.cfg.pipeline.flow]
    try:
        with heartbeat(job):
            reason = flow(job, rework_note=rework_note)
        status = "awaiting-human"
    except StopForHuman as e:
        status, reason = "awaiting-human", f"STOPPED: {e}"
    except PhaseFailed as e:
        status, reason = "failed", str(e)
    except Exception as e:  # a bug or an environment problem: keep the traceback for the human
        (job.dir / "error.txt").write_text(traceback.format_exc())
        status, reason = "failed", f"INTERNAL_ERROR: {e} (see {job.dir / 'error.txt'})"
    t.update_job(job.id, status=status, reason=reason)
    return status, reason


def resume(root: Path, job_id: str, runner_map: dict[str, Runner] | None = None) -> tuple[str, str]:
    cfg, trace = _cfg(root), Trace(root)
    row = _get(trace, job_id)
    if row["status"] not in ("failed", "stalled"):
        raise JobError(f"job {row['id']} is {row['status']}; only failed or stalled jobs can be resumed")
    return execute(_job(root, trace, row, cfg, runner_map or runners.build(cfg)))


def rework(root: Path, job_id: str, note: str, runner_map: dict[str, Runner] | None = None) -> tuple[str, str]:
    cfg, trace = _cfg(root), Trace(root)
    row = _get(trace, job_id)
    if row["status"] not in ("awaiting-human", "failed", "stalled"):
        raise JobError(f"job {row['id']} is {row['status']}; rework needs awaiting-human, failed or stalled")
    return execute(_job(root, trace, row, cfg, runner_map or runners.build(cfg)), rework_note=note)


def approve(root: Path, job_id: str, code_only: bool = False) -> str:
    trace = Trace(root)
    row = _get(trace, job_id)
    if row["status"] != "awaiting-human":
        raise JobError(f"job {row['id']} is {row['status']}; only awaiting-human jobs can be approved")
    if worktree.commits_ahead(root, row["base_sha"], row["branch"]) == 0:
        raise JobError(f"job {row['id']} has no commit to merge ({row['reason']})")
    if worktree.current_branch(root) != row["base_branch"]:
        raise JobError(f"check out '{row['base_branch']}' in {root} first; the job started from it")
    target, what = row["branch"], ""
    if code_only:
        if not row.get("code_sha"):
            raise JobError(f"job {row['id']} has no recorded code commit; approve without --code-only")
        target, what = row["code_sha"], " (code only, docs commit dropped)"
    sha = worktree.merge(root, target, f"Merge {row['branch']}{what}: {row['task_id']} {row['title']}")
    worktree.remove(root, Path(row["worktree"]), row["branch"])
    trace.update_job(row["id"], status="approved", reason=f"merged as {sha[:8]}{what}")
    return sha


def reject(root: Path, job_id: str) -> None:
    trace = Trace(root)
    row = _get(trace, job_id)
    if row["status"] in ("approved", "rejected"):
        raise JobError(f"job {row['id']} is already {row['status']}")
    if row["status"] == "running":
        raise JobError(f"job {row['id']} is still running")
    worktree.remove(root, Path(row["worktree"]), row["branch"])
    trace.update_job(row["id"], status="rejected", reason="rejected by human")


def next_task(root: Path) -> tasks.Task | None:
    trace = Trace(root)
    for t in tasks.load_all(root):
        if trace.task_status(t.id) in ("todo", "rejected") and not deps_done(trace, t):
            return t
    return None
