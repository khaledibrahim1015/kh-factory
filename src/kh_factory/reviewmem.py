"""Review memory: one ledger of findings per job, with stable ids (R1, R2, ...).

Code assigns the ids, not the model. Every review round after the first gets the open findings
and must check each one first (fixed / not_fixed / no_longer_applies, with proof). The ledger
lives in the trace, so it survives resume and rework. `review.md` in the job folder shows it.
"""

from __future__ import annotations

import json

from .envelope import Finding, ReviewOutput
from .gates import GateResult
from .pipeline import Job, Phase

LEDGER = "ReviewLedger"
REPORT = "review.md"
OPEN = ("open", "not_fixed")


def load(job: Job) -> list[dict]:
    data = job.trace.last_envelope(job.id, LEDGER)
    return data["findings"] if data else []


def save(job: Job, ph: Phase, ledger: list[dict]) -> None:
    job.trace.envelope(job.id, ph.id, LEDGER, {"findings": ledger})
    (job.dir / REPORT).write_text(render(ledger))


def open_items(ledger: list[dict]) -> list[dict]:
    return [f for f in ledger if f["status"] in OPEN]


def is_blocking(item: dict) -> bool:
    return Finding.model_validate(item).blocking


def context(ledger: list[dict]) -> str:
    """The 'Previous review' layer: open findings the reviewer must check first."""
    items = open_items(ledger)
    if not items:
        return ""
    rows = "\n".join(f"- **{f['id']}** [{f['severity']}, {f['kind']}] {f['file'] or '-'}: {f['message']}"
                     for f in items)
    return ("These findings from the previous review are still open. Check EACH one FIRST against the code "
            "(not against the builder's claims). For each id, add an entry to `prior` with status "
            "`fixed`, `not_fixed` or `no_longer_applies` and a file:line or command as `proof`.\n"
            "If any blocker is `not_fixed`: verdict CHANGES, and skip the fresh pass (the code will change "
            "again). Otherwise do the normal review and put only NEW problems in `findings`.\n\n" + rows)


def check_prior(review: ReviewOutput, ledger: list[dict]) -> GateResult:
    """Every open id has a status with proof; no unknown ids."""
    want = {f["id"] for f in open_items(ledger)}
    got = {p.id: p for p in review.prior}
    problems = []
    if missing := sorted(want - set(got)):
        problems.append(f"no `prior` entry for: {', '.join(missing)}")
    if unknown := sorted(set(got) - {f["id"] for f in ledger}):
        problems.append(f"unknown ids in `prior`: {', '.join(unknown)}")
    if no_proof := sorted(i for i, p in got.items() if i in want and not p.proof.strip()):
        problems.append(f"`proof` is empty for: {', '.join(no_proof)}")
    return GateResult("prior_checked", not problems, "; ".join(problems))


def merge(ledger: list[dict], review: ReviewOutput, round_no: int) -> list[dict]:
    """Apply prior statuses, then add new findings with the next ids."""
    by_id = {f["id"]: dict(f) for f in ledger}
    for p in review.prior:
        if p.id in by_id:
            by_id[p.id].update(status=p.status, proof=p.proof, checked_round=round_no)
    next_no = max((int(i[1:]) for i in by_id), default=0) + 1
    for f in review.findings:
        fid = f"R{next_no}"
        next_no += 1
        by_id[fid] = {"id": fid, "round": round_no, "status": "open", "proof": "", "checked_round": round_no,
                      **f.model_dump()}
    return sorted(by_id.values(), key=lambda f: int(f["id"][1:]))


def verdict_consistent(review: ReviewOutput, merged: list[dict]) -> GateResult:
    """CHANGES if and only if an open finding blocks (over-engineering and deepening never block)."""
    blocking = [f["id"] for f in open_items(merged) if is_blocking(f)]
    ok = (review.verdict == "CHANGES") == bool(blocking)
    detail = "" if ok else (f"open blockers {', '.join(blocking)} need verdict CHANGES" if blocking else
                            "verdict CHANGES needs at least one open blocker (over_engineering and "
                            "deepening never block; use should_fix or a follow-up)")
    return GateResult("verdict_consistent", ok, detail)


def for_builder(ledger: list[dict]) -> str:
    """Open findings for the revise step: blockers first, then should_fix."""
    items = sorted(open_items(ledger), key=lambda f: (not is_blocking(f), int(f["id"][1:])))
    lines = []
    for f in items:
        tag = "MUST FIX" if is_blocking(f) else "fix if small"
        lines.append(f"- {f['id']} ({tag}, {f['kind']}) {f['file'] or '-'}: {f['message']}")
    return "\n".join(lines)


def render(ledger: list[dict]) -> str:
    lines = ["# Review ledger", "", "| ID | Round | Kind | Severity | Where | Finding | Status | Proof |",
             "|---|---|---|---|---|---|---|---|"]
    for f in ledger:
        cell = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")  # noqa: E731
        lines.append(f"| {f['id']} | {f['round']} | {f['kind']} | {f['severity']} | {cell(f['file'])} | "
                     f"{cell(f['message'])} | {f['status']} | {cell(f.get('proof'))} |")
    if not ledger:
        lines.append("| - | - | - | - | - | no findings | - | - |")
    return "\n".join(lines) + "\n"


def dumps(ledger: list[dict]) -> str:
    return json.dumps(ledger, indent=2)
