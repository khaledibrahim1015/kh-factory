"""Doc updates: agents propose, code records and limits, the human decides.

The builder proposes `doc_updates`, the reviewer adds `doc_drift` and can reject builder
proposals (`doc_rejects`). Code keeps only proposals for existing doc files, writes
doc-updates.md, and (in `apply` mode) lets the docs step change exactly those files.
"""

from __future__ import annotations

from .config import Config
from .context import cut_section
from .envelope import BuildOutput, DocUpdate, ReviewOutput
from .gates import matches
from .pipeline import Job

REPORT = "doc-updates.md"


def is_doc_path(cfg: Config, path: str) -> bool:
    """Inside a project.context folder, or a top-level *.md file. Never code, config or tasks."""
    path = path.removeprefix("./")
    if "/" not in path and path.endswith(".md"):
        return True
    return any(path.startswith(c.rstrip("/") + "/") for c in cfg.project.context)


def collect(job: Job) -> tuple[list[DocUpdate], list[str]]:
    """All proposals of this job from the trace. Returns (kept, dropped reasons).

    The latest builder proposal per path#section wins. Reviewer drift is added when the builder
    did not cover that section. The last code review's doc_rejects remove proposals.
    """
    rows = job.trace.query("SELECT type, data FROM envelopes WHERE job_id = ? AND type IN (?, ?) ORDER BY id",
                           (job.id, "BuildOutput", "ReviewOutput"))
    builder: dict[str, DocUpdate] = {}
    drift: dict[str, DocUpdate] = {}
    rejects: set[str] = set()
    for r in rows:
        if r["type"] == "BuildOutput":
            for u in BuildOutput.model_validate_json(r["data"]).doc_updates:
                builder[u.key] = u
        else:
            review = ReviewOutput.model_validate_json(r["data"])
            for u in review.doc_drift:
                drift[u.key] = u
            rejects = {_norm(x) for x in review.doc_rejects}
    merged = {**drift, **builder}
    kept, dropped = [], []
    for key, u in merged.items():
        if key in rejects or key.rstrip("#") in rejects:
            dropped.append(f"{key}: rejected by the reviewer")
        elif not is_doc_path(job.cfg, u.path):
            dropped.append(f"{key}: not a doc path (must be in project.context or a top-level *.md)")
        elif not (job.wt / u.path).is_file():
            dropped.append(f"{key}: file does not exist")
        else:
            kept.append(u)
    return kept, dropped


def _norm(ref: str) -> str:
    """'./docs/A.md#Errors ' -> 'docs/A.md#errors' (path exact, section without case), like DocUpdate.key."""
    path, _, section = ref.strip().partition("#")
    return f"{path.removeprefix('./')}#{section.strip().lower()}"


def split(cfg: Config, updates: list[DocUpdate]) -> tuple[list[DocUpdate], list[DocUpdate]]:
    """(may be applied, proposal only). ADRs and other `docs_never_apply` paths are never applied."""
    never = cfg.pipeline.docs_never_apply
    return [u for u in updates if not matches(u.path, never)], [u for u in updates if matches(u.path, never)]


def brief(job: Job, updates: list[DocUpdate]) -> str:
    """Proposals with the current text of each section, for the docs agent."""
    parts = []
    for u in updates:
        text = (job.wt / u.path).read_text()
        current = cut_section(text, u.section) if u.section else text
        parts.append(f"### {u.path} — {u.section or '(whole file)'}\n\nReason: {u.reason}\n\n"
                     f"Current text:\n\n```markdown\n{current or '(section not found: add it)'}\n```\n\n"
                     f"Proposed text:\n\n```markdown\n{u.proposal}\n```")
    return "\n\n".join(parts)


def report(updates: list[DocUpdate], applied: list[DocUpdate], dropped: list[str], note: str = "") -> str:
    applied_keys = {u.key for u in applied}
    lines = ["# Doc updates", ""]
    if note:
        lines += [note, ""]
    for u in updates:
        state = "APPLIED" if u.key in applied_keys else "PROPOSED"
        lines += [f"## [{state}] {u.path} — {u.section or '(whole file)'}", "", f"Reason: {u.reason}", "",
                  "```markdown", u.proposal, "```", ""]
    if dropped:
        lines += ["## Dropped", ""] + [f"- {d}" for d in dropped] + [""]
    return "\n".join(lines)
