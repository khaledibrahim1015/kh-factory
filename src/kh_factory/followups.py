"""Follow-ups: work found during review that is outside this task.

Sources: the reviewer's explicit `followups`, plus findings still open when the review passed
(should_fix, deepening, over_engineering; nits are not filed). Code writes followups.md.
`factory followups <job> --create` turns them into task files; nothing is created on its own.
"""

from __future__ import annotations

from pathlib import Path

from . import reviewmem, tasks
from .envelope import FollowUp, ReviewOutput
from .pipeline import Job, Phase
from .trace import Trace

ENVELOPE = "FollowUps"
REPORT = "followups.md"
FILED_SEVERITIES = ("should_fix", "deepening")


def gather(review: ReviewOutput, ledger: list[dict], doc_paths: set[str] = frozenset()) -> list[FollowUp]:
    """doc_paths: files already covered by doc updates; a docs follow-up for them is a duplicate."""
    items: dict[str, FollowUp] = {}
    for f in reviewmem.open_items(ledger):
        if f["severity"] in FILED_SEVERITIES or f["kind"] == "over_engineering":
            items[f"{f['file']}|{f['message']}"] = FollowUp(
                title=f"{f['id']}: {f['message'][:70]}", why=f["message"], file=f["file"],
                kind="refactor" if f["kind"] in ("over_engineering",) or f["severity"] == "deepening" else
                "test" if f["kind"] == "verification_miss" else "bug")
    for fu in review.followups:
        if fu.kind == "docs" and fu.file.split("#")[0].split(":")[0].removeprefix("./") in doc_paths:
            continue  # the doc-updates step owns it (applied or proposed in doc-updates.md)
        items[f"{fu.file}|{fu.title}"] = fu
    return list(items.values())


def record(job: Job, ph: Phase, out: list[FollowUp]) -> None:
    job.trace.envelope(job.id, ph.id, ENVELOPE, {"task": job.task.id, "items": [x.model_dump() for x in out]})
    if out:
        (job.dir / REPORT).write_text(render(job.task.id, out))


def render(task_id: str, items: list[FollowUp]) -> str:
    lines = [f"# Follow-ups from task {task_id}", "",
             "Create task files with: `factory followups <job-id> --create`", ""]
    for i, f in enumerate(items, 1):
        lines += [f"## {i}. {f.title}", "", f"- Kind: {f.kind}", f"- Where: {f.file or '-'}", f"- Why: {f.why}"]
        if f.acceptance:
            lines += ["- Acceptance:"] + [f"  - {a}" for a in f.acceptance]
        else:
            lines += ["- Acceptance: (none yet: write it before running the task; `factory lint` flags it)"]
        lines.append("")
    return "\n".join(lines)


def load(root: Path, job_id: str) -> tuple[str, list[FollowUp]]:
    t = Trace(root)
    row = t.job(job_id)
    if row is None:
        raise tasks.TaskError(f"no single job matches '{job_id}'")
    data = t.last_envelope(row["id"], ENVELOPE)
    return row["task_id"], [FollowUp.model_validate(x) for x in (data or {}).get("items", [])]


def create(root: Path, job_id: str) -> list[Path]:
    """Write one task file per follow-up, depending on the original task."""
    task_id, items = load(root, job_id)
    paths = []
    for fu in items:
        tid = tasks.next_id(root)
        path = root / tasks.TASKS_DIR / f"{tid}-{tasks.slug(fu.title)}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tasks.render(tid, fu.title, objective=f"{fu.why}\n\nWhere: {fu.file or '-'}",
                                     acceptance=fu.acceptance or None, depends_on=[task_id],
                                     notes=f"Follow-up ({fu.kind}) from job {job_id}."))
        paths.append(path)
    return paths
