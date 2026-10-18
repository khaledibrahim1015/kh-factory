"""End-to-end flow tests with a fake runner in a real temporary git repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from kh_factory import jobs
from kh_factory.trace import Trace

from conftest import PASS, PLAN, build_bad, build_ok, envelope, git, script


def start(repo, fake, task="T-001"):
    return jobs.start(repo, task, runner_map={"fake": fake}, check_models=False)


def phases(repo, job_id):
    return [(p["name"], p["status"]) for p in
            Trace(repo).query("SELECT * FROM phases WHERE job_id = ? ORDER BY id", (job_id,))]


def test_happy_path_then_approve(repo):
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    names = [n for n, _ in phases(repo, job_id)]
    assert names == ["context", "plan", "build", "test_1", "review_1", "commit"]
    assert all(s == "passed" for _, s in phases(repo, job_id))
    job_dir = repo / ".factory" / "jobs" / job_id
    assert (job_dir / "plan.md").read_text() == "1. write add in calc.py"
    # reviewer saw the diff and the spec section, not the builder's reasoning
    review_ctx = next(job_dir.glob("steps/*review_1/context.md")).read_text()
    assert "+def add(a, b):" in review_ctx and "add must be pure" in review_ctx
    assert "added add" not in review_ctx
    # main branch untouched until approve
    assert "def add" not in (repo / "calc.py").read_text()
    jobs.approve(repo, job_id)
    assert "def add" in (repo / "calc.py").read_text()
    assert Trace(repo).task_status("T-001") == "done"
    assert not Path(Trace(repo).job(job_id)["worktree"]).exists()


def test_fix_loop_exhausted(repo):
    fake = script(planner=PLAN, builder=build_bad, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("TESTS_FAILED")
    names = [n for n, _ in phases(repo, job_id)]
    assert names.count("fix_1") + names.count("fix_2") + names.count("fix_3") == 3
    assert "test_4" in names and "review_1" not in names
    # fix steps resume the builder session
    builder_calls = [c for c in fake.calls if c.agent.name == "builder"]
    assert [c.resume for c in builder_calls] == [False, True, True, True]
    assert len({c.session_id for c in builder_calls}) == 1
    assert "add(2,3) != 5" in builder_calls[1].prompt  # verbatim test output reached the fixer


def test_fix_loop_recovers(repo):
    fake = script(planner=PLAN, builder=[build_bad, build_ok], reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    assert ("fix_1", "passed") in phases(repo, job_id) and ("test_2", "passed") in phases(repo, job_id)


def test_reviewer_cannot_write(repo):
    def sneaky(req):
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return 42\n")
        return PASS
    fake = script(planner=PLAN, builder=build_ok, reviewer=sneaky)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("WRITES_BOUNDARY")
    wt = Path(Trace(repo).job(job_id)["worktree"])
    assert "return a + b" in (wt / "calc.py").read_text()  # reviewer's edit was reverted


def test_builder_cannot_touch_protected(repo):
    def edits_task(req):
        (req.cwd / "tasks" / "T-001-add.md").write_text("hacked")
        return build_ok(req)
    fake = script(planner=PLAN, builder=edits_task, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and "tasks/T-001-add.md" in reason


def test_unclaimed_change_is_reprompted(repo):
    def forgetful(req):
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (req.cwd / "extra.py").write_text("x = 1\n")
        return envelope({"summary": "s", "changed_files": ["calc.py"], "commit_message": "m"})
    honest = envelope({"summary": "s", "changed_files": ["calc.py", "extra.py"], "commit_message": "m"})
    fake = script(planner=PLAN, builder=[forgetful, honest], reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    second = [c for c in fake.calls if c.agent.name == "builder"][1]
    assert second.resume and "extra.py" in second.prompt


def test_bad_json_is_reprompted_then_fails(repo):
    fake = script(planner="I refuse to answer in JSON", builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("ENVELOPE_INVALID")
    assert len([c for c in fake.calls if c.agent.name == "planner"]) == 3  # 1 + max_envelope_retries


def test_review_changes_then_revise_then_retest(repo):
    changes = envelope({"verdict": "CHANGES", "summary": "needs docstring",
                        "findings": [{"severity": "blocking", "file": "calc.py", "message": "add a docstring"}]})

    def revise(req):
        (req.cwd / "calc.py").write_text('def add(a, b):\n    """Sum."""\n    return a + b\n')
        return envelope({"summary": "docstring", "changed_files": ["calc.py"], "commit_message": "Add add()"})
    fake = script(planner=PLAN, builder=[build_ok, revise], reviewer=[changes, PASS])
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    names = [n for n, _ in phases(repo, job_id)]
    assert names[-4:] == ["revise_1", "review_2", "retest", "commit"]
    reviewer_sessions = {c.session_id for c in fake.calls if c.agent.name == "reviewer"}
    assert len(reviewer_sessions) == 2  # fresh session each round


def test_planner_questions_stop_then_rework_answers(repo):
    ask = envelope({"summary": "?", "plan": "tbd", "questions": ["Integers only?"]})
    fake = script(planner=[ask, PLAN], builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human" and "Integers only?" in reason
    with pytest.raises(jobs.JobError, match="no commit"):
        jobs.approve(repo, job_id)
    status, reason = jobs.rework(repo, job_id, "Yes, integers only.", runner_map={"fake": fake})
    assert status == "awaiting-human" and reason.startswith("ready"), reason
    planner_calls = [c for c in fake.calls if c.agent.name == "planner"]
    assert planner_calls[1].resume and "integers only" in planner_calls[1].prompt


def test_rework_resumes_builder_with_note(repo):
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    job_id, status, _ = start(repo, fake)

    def rename(req):
        (req.cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\nplus = add\n")
        return envelope({"summary": "alias", "changed_files": ["calc.py"], "commit_message": "Add plus alias"})
    fake2 = script(planner=PLAN, builder=rename, reviewer=PASS)
    status, reason = jobs.rework(repo, job_id, "Also add a `plus` alias.", runner_map={"fake": fake2})
    assert status == "awaiting-human", reason
    assert not any(c.agent.name == "planner" for c in fake2.calls)  # plan reused
    b = [c for c in fake2.calls if c.agent.name == "builder"][0]
    assert b.resume and "plus" in b.prompt
    wt = Path(Trace(repo).job(job_id)["worktree"])
    assert git(wt, "rev-list", "--count", "main..HEAD").strip() == "2"


def test_bad_spec_ref_fails_before_any_model_call(repo):
    p = repo / "tasks" / "T-001-add.md"
    p.write_text(p.read_text().replace("docs/SPEC.md#1", "docs/SPEC.md#9"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "bad ref")
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("SPEC_REF_NOT_FOUND")
    assert fake.calls == []


def test_no_test_command(repo):
    p = repo / "factory.yaml"
    p.write_text(p.read_text().replace('commands: {test:', 'commands: {lint:'))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "no test")
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("NO_TEST_COMMAND")


def test_runner_error_fails_job(repo):
    def boom(req):
        raise RuntimeError("model unavailable")
    fake = script(planner=boom, builder=build_ok, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "failed" and reason.startswith("RUNNER_ERROR") and "model unavailable" in reason


def test_dependencies_and_next(repo):
    (repo / "tasks" / "T-002-next.md").write_text(
        "---\nid: T-002\ntitle: Next\ndepends_on: [T-001]\nspec: []\n---\n\n## Objective\n\nx\n")
    assert jobs.next_task(repo).id == "T-001"
    with pytest.raises(jobs.JobError, match="depends on unfinished"):
        jobs.start(repo, "T-002", runner_map={}, check_models=False)


def test_reject_removes_worktree(repo):
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    job_id, _, _ = start(repo, fake)
    wt = Path(Trace(repo).job(job_id)["worktree"])
    jobs.reject(repo, job_id)
    assert not wt.exists()
    assert Trace(repo).task_status("T-001") == "rejected"
    assert "factory/" not in git(repo, "branch")


def test_context_use_is_recorded_and_warns(repo):
    fake = script(planner=PLAN, builder=build_ok, reviewer=PASS)
    fake.context = (90_000, 100_000)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    t = Trace(repo)
    rows = t.query("SELECT name, context_tokens, context_window FROM phases WHERE job_id = ? AND kind = 'agent'",
                   (job_id,))
    assert rows and all(r["context_tokens"] == 90_000 and r["context_window"] == 100_000 for r in rows)
    assert t.job(job_id)["max_context_pct"] == 90.0
    warnings = t.query("SELECT data FROM events WHERE job_id = ? AND type = 'warning'", (job_id,))
    assert any("90.0% of its context window" in w["data"] for w in warnings)


def test_big_diff_is_cut_for_reviewer(repo):
    p = repo / "factory.yaml"
    p.write_text(p.read_text().replace("  context: [docs]", "  context: [docs]\n  max_diff_lines: 5"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "small diff limit")

    def build_big(req):
        body = "".join(f"# line {i}\n" for i in range(20))
        (req.cwd / "calc.py").write_text(body + "def add(a, b):\n    return a + b\n")
        return envelope({"summary": "s", "changed_files": ["calc.py"], "commit_message": "Add add()"})
    fake = script(planner=PLAN, builder=build_big, reviewer=PASS)
    job_id, status, reason = start(repo, fake)
    assert status == "awaiting-human", reason
    job_dir = repo / ".factory" / "jobs" / job_id
    ctx = next(job_dir.glob("steps/*review_1/context.md")).read_text()
    patch = job_dir / "diff-review_1.patch"
    assert patch.is_file() and "def add" in patch.read_text()
    assert f"Diff cut at 5 of" in ctx and str(patch) in ctx
    assert "calc.py |" in ctx            # the stat block lists every changed file
    assert "# line 19" not in ctx        # cut part is not in the pack
