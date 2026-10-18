"""Doc updates inside the sdlc flow: propose, record, apply (separate commit), approve --code-only."""

from __future__ import annotations

from pathlib import Path

from kh_factory import jobs
from kh_factory.trace import Trace

from conftest import PASS, PLAN, envelope, git, script

SPEC_UPDATE = {"path": "docs/SPEC.md", "section": "1. Math", "reason": "add() now exists",
               "proposal": "## 1. Math\n\nadd(a, b) returns a + b and must be pure.\n"}


def set_mode(repo: Path, mode: str) -> None:
    p = repo / "factory.yaml"
    p.write_text(p.read_text().replace("pipeline: {max_fix_loops: 3, max_review_loops: 2}",
                                       f"pipeline: {{max_fix_loops: 3, max_review_loops: 2, doc_updates: {mode}}}"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", f"doc mode {mode}")


def builder(updates, docs_step=None):
    """Fake builder: implements add(); proposes `updates`; from the docs step on runs `docs_step(req)`."""
    state = {"docs": False}

    def run(req):
        state["docs"] = state["docs"] or "Docs step" in req.prompt
        if state["docs"]:
            return docs_step(req)
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        return envelope({"summary": "added add", "changed_files": ["calc.py"], "commit_message": "Add add()",
                         "doc_updates": updates})
    return run


def apply_spec(req):
    (req.cwd / "docs" / "SPEC.md").write_text(
        "# Spec\n\n## 1. Math\n\nadd(a, b) returns a + b and must be pure.\n\n## 2. Other\n\nx\n")
    return envelope({"summary": "spec updated", "changed_files": ["docs/SPEC.md"], "commit_message": "Docs: add()"})


def run_job(repo, fake):
    job_id, status, reason = jobs.start(repo, "T-001", runner_map={"fake": fake}, check_models=False)
    return job_id, status, reason, repo / ".factory" / "jobs" / job_id


def ahead(repo, job_id) -> int:
    row = Trace(repo).job(job_id)
    return int(git(repo, "rev-list", "--count", f"{row['base_sha']}..{row['branch']}").strip())


def test_propose_mode_records_only(repo):
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE]), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert status == "awaiting-human" and "+1 doc updates: 1 proposed" in reason, reason
    report = (d / "doc-updates.md").read_text()
    assert "[PROPOSED] docs/SPEC.md — 1. Math" in report and "must be pure" in report
    assert ahead(repo, job_id) == 1  # code commit only
    assert not any(c.prompt.startswith("Docs step") for c in fake.calls)


def test_non_doc_paths_are_dropped(repo):
    bad = [{**SPEC_UPDATE, "path": "calc.py"}, {**SPEC_UPDATE, "path": "factory.yaml"},
           {**SPEC_UPDATE, "path": "docs/NOPE.md"}]
    fake = script(planner=PLAN, builder=builder(bad), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert status == "awaiting-human" and "doc updates" not in reason, reason
    report = (d / "doc-updates.md").read_text()
    assert "calc.py#1. math: not a doc path" in report and "docs/NOPE.md#1. math: file does not exist" in report
    warnings = Trace(repo).query("SELECT data FROM events WHERE job_id = ? AND type = 'warning'", (job_id,))
    assert len(warnings) == 3


def test_apply_mode_separate_docs_commit_and_code_only_approve(repo):
    set_mode(repo, "apply")
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE], apply_spec), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert status == "awaiting-human" and "1 applied in" in reason, reason
    assert ahead(repo, job_id) == 2
    docs_call = [c for c in fake.calls if "Docs step" in c.prompt][0]
    assert docs_call.resume and "Current text:" in docs_call.prompt  # builder session, sees the section
    assert "[APPLIED] docs/SPEC.md" in (d / "doc-updates.md").read_text()
    jobs.approve(repo, job_id, code_only=True)
    assert "def add" in (repo / "calc.py").read_text()
    assert "must be pure.\n\n## 2" not in (repo / "docs" / "SPEC.md").read_text().replace("add must be pure", "")
    assert "add(a, b) returns" not in (repo / "docs" / "SPEC.md").read_text()  # docs commit dropped


def test_apply_mode_full_approve_merges_docs(repo):
    set_mode(repo, "apply")
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE], apply_spec), reviewer=PASS)
    job_id, *_ = run_job(repo, fake)
    jobs.approve(repo, job_id)
    assert "add(a, b) returns a + b" in (repo / "docs" / "SPEC.md").read_text()


def test_docs_step_cannot_touch_code(repo):
    set_mode(repo, "apply")

    def sneaky(req):
        apply_spec(req)
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return 0\n")
        return envelope({"summary": "s", "changed_files": ["docs/SPEC.md", "calc.py"], "commit_message": "Docs"})
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE], sneaky), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert status == "awaiting-human" and "docs step failed" in reason, reason
    assert ahead(repo, job_id) == 1  # code commit kept, no docs commit
    wt = Path(Trace(repo).job(job_id)["worktree"])
    assert "return a + b" in (wt / "calc.py").read_text()
    assert git(wt, "status", "--porcelain").strip() == ""
    assert "WRITES_BOUNDARY" in (d / "doc-updates.md").read_text()


def test_adr_is_never_applied(repo):
    adr = repo / "docs" / "decisions" / "ADR-001.md"
    adr.parent.mkdir()
    adr.write_text("# ADR-001\n\n## Decision\n\nUse ints.\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "adr")
    set_mode(repo, "apply")
    adr_update = {"path": "docs/decisions/ADR-001.md", "section": "Decision", "reason": "floats too",
                  "proposal": "## Decision\n\nUse floats.\n"}
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE, adr_update], apply_spec), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert "1 applied" in reason and "1 proposed" in reason, reason
    report = (d / "doc-updates.md").read_text()
    assert "[PROPOSED] docs/decisions/ADR-001.md" in report and "[APPLIED] docs/SPEC.md" in report
    docs_call = [c for c in fake.calls if "Docs step" in c.prompt][0]
    assert "ADR-001" not in docs_call.prompt


def test_reviewer_rejects_and_adds_drift(repo):
    drift = {"path": "README.md", "section": "", "reason": "mention add()", "proposal": "# proj\n\nHas add().\n"}
    (repo / "README.md").write_text("# proj\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "readme")
    review = envelope({"verdict": "PASS", "summary": "ok", "findings": [], "doc_drift": [drift],
                       "doc_rejects": ["docs/SPEC.md#1. Math"]})
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE]), reviewer=review)
    job_id, status, reason, d = run_job(repo, fake)
    report = (d / "doc-updates.md").read_text()
    assert "[PROPOSED] README.md" in report
    assert "docs/SPEC.md#1. math: rejected by the reviewer" in report
    assert "+1 doc updates" in reason


def test_docs_edit_must_keep_spec_refs(repo):
    set_mode(repo, "apply")
    attempts = []

    def breaks_heading_first(req):
        attempts.append(req.prompt)
        if len(attempts) == 1:
            (req.cwd / "docs" / "SPEC.md").write_text("# Spec\n\n## Math\n\nadd(a, b) returns a + b.\n")
            return envelope({"summary": "s", "changed_files": ["docs/SPEC.md"], "commit_message": "Docs"})
        return apply_spec(req)
    fake = script(planner=PLAN, builder=builder([SPEC_UPDATE], breaks_heading_first), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert "1 applied" in reason, reason
    assert len(attempts) == 2 and "spec_refs_resolve" in attempts[1]


def test_rules_file_update_is_proposal_only(repo):
    (repo / "AGENTS.md").write_text("# Rules\n\n## Testing\n\nRun check.py.\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "rules")
    set_mode(repo, "apply")
    rules_update = {"path": "AGENTS.md", "section": "Testing", "reason": "new command",
                    "proposal": "## Testing\n\nRun check.py and pytest.\n"}
    fake = script(planner=PLAN, builder=builder([rules_update], apply_spec), reviewer=PASS)
    job_id, status, reason, d = run_job(repo, fake)
    assert "1 proposed" in reason and "applied" not in reason, reason
    assert "[PROPOSED] AGENTS.md" in (d / "doc-updates.md").read_text()
