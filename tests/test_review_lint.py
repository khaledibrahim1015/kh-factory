"""Reviewer memory (stable ids, prior checks), follow-ups, and task lint."""

from __future__ import annotations

import pytest

from kh_factory import config, followups, jobs, lint, tasks
from kh_factory.trace import Trace

from conftest import PASS, PLAN, build_ok, envelope, git, script


def run(repo, fake, **kw):
    job_id, status, reason = jobs.start(repo, "T-001", runner_map={"fake": fake}, check_models=False, **kw)
    return job_id, status, reason, repo / ".factory" / "jobs" / job_id


def changes(*findings):
    return envelope({"verdict": "CHANGES", "summary": "needs work", "findings": list(findings)})


BUG = {"severity": "blocker", "kind": "bug", "file": "calc.py:2", "message": "no docstring"}
NIT = {"severity": "should_fix", "kind": "verification_miss", "file": "check.py:2", "message": "test only one case"}


def revise_ok(req):
    (req.cwd / "calc.py").write_text('def add(a, b):\n    """Sum."""\n    return a + b\n')
    return envelope({"summary": "fixed R1", "changed_files": ["calc.py"], "commit_message": "Add add()"})


# ---- reviewer memory ----

def test_round_two_gets_ids_and_must_check_them(repo):
    no_prior = envelope({"verdict": "PASS", "summary": "ok"})
    with_prior = envelope({"verdict": "PASS", "summary": "ok",
                           "prior": [{"id": "R1", "status": "fixed", "proof": "calc.py:2"},
                                     {"id": "R2", "status": "not_fixed", "proof": "check.py:2 still one case"}]})
    fake = script(planner=PLAN, builder=[build_ok, revise_ok], reviewer=[changes(BUG, NIT), no_prior, with_prior])
    job_id, status, reason, d = run(repo, fake)
    assert status == "awaiting-human", reason
    rev = [c for c in fake.calls if c.agent.name == "reviewer"]
    assert "**R1** [blocker, bug] calc.py:2" in rev[1].prompt and "**R2**" in rev[1].prompt
    assert "no `prior` entry for: R1, R2" in rev[2].prompt          # gate re-prompted round 2
    ledger = (d / "review.md").read_text()
    assert "| R1 | 1 | bug | blocker | calc.py:2 | no docstring | fixed | calc.py:2 |" in ledger
    assert "| R2 | 1 | verification_miss | should_fix |" in ledger and "not_fixed" in ledger
    revise = [c for c in fake.calls if c.agent.name == "builder"][1]
    assert "R1 (MUST FIX, bug)" in revise.prompt and "R2 (fix if small" in revise.prompt


def test_not_fixed_blocker_forces_changes(repo):
    lying_pass = envelope({"verdict": "PASS", "summary": "ok",
                           "prior": [{"id": "R1", "status": "not_fixed", "proof": "calc.py:2 unchanged"}]})
    honest = envelope({"verdict": "CHANGES", "summary": "R1 still open",
                       "prior": [{"id": "R1", "status": "not_fixed", "proof": "calc.py:2 unchanged"}]})
    fake = script(planner=PLAN, builder=[build_ok, build_ok], reviewer=[changes(BUG), lying_pass, honest])
    job_id, status, reason, d = run(repo, fake)
    assert status == "failed" and reason.startswith("REVIEW_CHANGES") and "open: R1" in reason
    rev = [c for c in fake.calls if c.agent.name == "reviewer"]
    assert "open blockers R1 need verdict CHANGES" in rev[2].prompt


def test_over_engineering_never_blocks_and_becomes_followup(repo):
    oe = {"severity": "blocker", "kind": "over_engineering", "file": "calc.py:1", "message": "factory class for add"}
    review = envelope({"verdict": "PASS", "summary": "ok", "findings": [oe],
                       "followups": [{"title": "Add subtract", "why": "users asked", "kind": "feature",
                                      "acceptance": ["subtract(5, 3) == 2"]}]})
    fake = script(planner=PLAN, builder=build_ok, reviewer=review)
    job_id, status, reason, d = run(repo, fake)
    assert status == "awaiting-human" and "+2 follow-ups" in reason, reason
    text = (d / "followups.md").read_text()
    assert "factory class for add" in text and "Add subtract" in text and "subtract(5, 3) == 2" in text


def test_memory_survives_rework(repo):
    fake = script(planner=PLAN, builder=[build_ok, revise_ok],
                  reviewer=[changes(BUG), envelope({"verdict": "PASS", "summary": "ok",
                            "prior": [{"id": "R1", "status": "fixed", "proof": "calc.py:2"}]})])
    job_id, status, *_ = run(repo, fake)
    assert status == "awaiting-human"
    def hints(req):
        (req.cwd / "calc.py").write_text('def add(a: int, b: int) -> int:\n    """Sum."""\n    return a + b\n')
        return envelope({"summary": "hints", "changed_files": ["calc.py"], "commit_message": "Type hints"})
    still = envelope({"verdict": "CHANGES", "summary": "R2 open",
                      "prior": [{"id": "R2", "status": "not_fixed", "proof": "calc.py:1"}]})
    fake2 = script(planner=PLAN, builder=hints, reviewer=[changes({**BUG, "message": "missing type hints"}), still])
    status, reason = jobs.rework(repo, job_id, "add type hints", runner_map={"fake": fake2})
    assert status == "failed" and "open: R2" in reason, reason
    ledger = (repo / ".factory" / "jobs" / job_id / "review.md").read_text()
    assert "| R1 | 1 |" in ledger and "| R2 | 3 | bug | blocker | calc.py:2 | missing type hints | not_fixed | calc.py:1 |" in ledger


def test_followups_create_tasks(repo):
    review = envelope({"verdict": "PASS", "summary": "ok",
                       "followups": [{"title": "Add subtract", "why": "next step", "kind": "feature",
                                      "acceptance": ["`subtract(5, 3)` returns 2"]}]})
    job_id, *_ = run(repo, script(planner=PLAN, builder=build_ok, reviewer=review))
    paths = followups.create(repo, job_id)
    t = tasks.parse(paths[0])
    assert t.id == "T-002" and t.depends_on == ["T-001"] and "`subtract(5, 3)` returns 2" in t.section("Acceptance")


# ---- lint ----

def write_task(repo, body: str, tid="T-009", depends="[]", spec="[]"):
    p = repo / "tasks" / f"{tid}-x.md"
    p.write_text(f"---\nid: {tid}\ntitle: Thing\ndepends_on: {depends}\nspec: {spec}\n---\n\n{body}")
    return tasks.parse(p)


def codes(repo, task):
    cfg = config.load(repo)
    return {i.code: i.level for i in lint.lint_task(cfg, repo, task, tasks.load_all(repo))}


def test_lint_good_task_is_clean(repo):
    t = write_task(repo, "## Objective\n\nAdd `mul(a, b)` to calc.py.\n\n## Acceptance\n\n- `mul(2, 3)` returns 6\n\n"
                         "## Tests\n\nAdd a case to check.py.\n", spec="[docs/SPEC.md#1]")
    assert codes(repo, t) == {}


def test_lint_flags_vague_and_missing(repo):
    t = write_task(repo, "## Objective\n\nThing\n\n## Acceptance\n\n- (write what must be true when this task is done)\n")
    c = codes(repo, t)
    assert c["NO_ACCEPTANCE"] == "error" and c["NO_TESTS"] == "warn" and c["THIN_OBJECTIVE"] == "warn"
    assert c["NO_SPEC_REFS"] == "warn"  # the repo has docs, the task names none
    t = write_task(repo, "## Objective\n\nMake it faster and nicer, etc.\n\n## Acceptance\n\n- it feels snappy\n\n"
                         "## Tests\n\nsome\n", spec="[docs/SPEC.md#1]")
    c = codes(repo, t)
    assert c["VAGUE_WORDS"] == "warn" and c["NOT_CHECKABLE"] == "warn"


def test_lint_dependencies_specs_fakes_budget(repo):
    write_task(repo, "## Objective\n\nx y\n\n## Acceptance\n\n- `a()` returns 1\n", tid="T-010", depends="[T-011]")
    t = write_task(repo, "## Objective\n\nx y\n\n## Acceptance\n\n- `b()` returns 1, proven with a fake repo\n",
                   tid="T-011", depends="[T-010, T-404]", spec="[docs/SPEC.md#9]")
    c = codes(repo, t)
    assert c["UNKNOWN_DEPENDENCY"] == "error" and c["DEPENDENCY_CYCLE"] == "error"
    assert c["SPEC_REF_NOT_FOUND"] == "error" and c["FAKE_WITHOUT_OWNER"] == "warn"
    p = repo / "factory.yaml"
    p.write_text(p.read_text().replace("  context: [docs]", "  context: [docs]\n  context_budget_tokens: 50"))
    t = write_task(repo, "## Objective\n\nx y\n\n## Acceptance\n\n- `a()` returns 1\n", tid="T-012",
                   spec="[docs/SPEC.md#1]")
    assert codes(repo, t)["CONTEXT_TOO_LARGE"] == "error"


def test_lint_repo_and_start_gate(repo, monkeypatch):
    cfg = config.load(repo)
    rc = {i.code for i in lint.lint_repo(cfg, repo)}
    assert "NO_REPO_RULES" in rc and "TEST_COMMAND_NOT_FOUND" not in rc
    p = repo / "tasks" / "T-001-add.md"
    p.write_text(p.read_text().replace("- add(2, 3) == 5", "- (write what must be true when this task is done)"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "weak task")
    with pytest.raises(jobs.JobError, match="NO_ACCEPTANCE"):
        jobs.start(repo, "T-001", runner_map={}, check_models=False)
    assert Trace(repo).jobs() == []   # nothing was started


def test_docs_followup_dropped_when_doc_update_covers_it(repo):
    upd = {"path": "docs/SPEC.md", "section": "1. Math", "reason": "r", "proposal": "## 1. Math\n\nnew\n"}

    def builder(req):
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        return envelope({"summary": "s", "changed_files": ["calc.py"], "commit_message": "m", "doc_updates": [upd]})
    review = envelope({"verdict": "PASS", "summary": "ok",
                       "followups": [{"title": "Update spec", "why": "stale", "kind": "docs", "file": "docs/SPEC.md#1"},
                                     {"title": "Add subtract", "why": "next", "kind": "feature",
                                      "acceptance": ["`subtract(5, 3)` returns 2"]}]})
    job_id, status, reason, d = run(repo, script(planner=PLAN, builder=builder, reviewer=review))
    assert "+1 doc updates" in reason and "+1 follow-ups" in reason, reason
    assert "Update spec" not in (d / "followups.md").read_text()
