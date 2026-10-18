"""Flow `sdlc` — plan, build, test, fix, review, revise, retest, commit. Then stop for the human.

    plan (agent) -> build (agent) -> test (code) <-> fix (agent, max N)
      -> review (agent, new session each round) <-> revise (agent, max M) -> retest (code)
      -> commit on factory/<job-id>
      -> doc updates: record proposals; in `apply` mode a docs step edits only the proposed
         doc files and lands as a second commit -> awaiting-human

Tests are code: a known command needs no agent. The reviewer asks a different
question ("is this what was asked?") and never sees the builder's reasoning.
A rework from the human resumes the builder's session with the human's note.
"""

from __future__ import annotations

from .. import context, docupdates, followups, gates, quality, reviewmem, tasks, worktree
from ..envelope import BuildOutput, DocsOutput, DocsReviewOutput, PlanOutput, ReviewOutput
from ..pipeline import Job, PhaseFailed, StopForHuman, call_agent, phase


def run(job: Job, rework_note: str | None = None) -> str:
    cfg, P, wt = job.cfg, job.cfg.pipeline, job.wt

    with phase(job, "context", "code", "factory") as ph:
        try:
            task_specs = context.spec_sections(wt, job.task.spec)
        except context.ContextError as e:
            raise PhaseFailed(e.code, e.message) from e
        base = {"Repo rules": context.repo_rules(wt), "Project facts": context.project_facts(cfg),
                "Learnings": context.learnings(job.root), "Task": context.task_text(job.task)}
        ph.succeed(f"{len(task_specs)} spec section(s)")

    def pack(**layers: str) -> str:
        try:
            return context.build(cfg, layers={**base, **layers})
        except context.ContextError as e:
            raise PhaseFailed(e.code, e.message) from e

    # ---- plan (skipped on resume/rework when a usable plan exists) ----
    saved = job.trace.last_envelope(job.id, "PlanOutput")
    plan = PlanOutput.model_validate(saved) if saved else None
    if plan is None or (plan.questions and rework_note):
        with phase(job, "plan", "agent", P.plan) as ph:
            answers = f"The human answered your questions:\n\n{rework_note}" if plan and rework_note else ""
            plan = call_agent(job, ph, P.plan, output_type=PlanOutput, resume=bool(answers),
                              context=pack(**{"Spec sections": context.format_specs(task_specs),
                                              "Previous step": answers,
                                              "Repo map": context.repo_map(wt)}),
                              soft_gates=lambda e, _s: [_plan_non_empty(e)])
            (job.dir / "plan.md").write_text(plan.plan)
            ph.succeed(plan.summary)
        rework_note = None  # the note was consumed by the planner
        if plan.questions:
            (job.dir / "questions.md").write_text("\n".join(f"- {q}" for q in plan.questions))
            raise StopForHuman("planner has questions: " + " | ".join(plan.questions)
                               + f"  → answer with: factory rework {job.id} \"<answers>\"")

    specs = context.format_specs(task_specs + _plan_specs(job, plan, task_specs))
    claims = lambda e, snap: [gates.claims(wt, snap, e.changed_files)]  # noqa: E731

    # ---- build (or rework from the human) ----
    resuming = job.trace.session(job.id, P.build) is not None
    name = "rework" if rework_note else "build"
    with phase(job, name, "agent", P.build) as ph:
        note = f"Human rework note:\n\n{rework_note}" if rework_note else (
            "You were interrupted. Check the worktree state and finish the plan." if resuming else "")
        build = call_agent(job, ph, P.build, output_type=BuildOutput, resume=resuming,
                           values={"plan_path": str(job.dir / "plan.md")},
                           context=pack(**{"Spec sections": specs, "Plan": plan.plan, "Previous step": note}),
                           soft_gates=claims)
        _stop_on_conflict(job, build)
        ph.succeed(build.summary)

    # ---- test <-> fix ----
    test = _test(job, "test_1")
    for i in range(1, P.max_fix_loops + 1):
        if test.passed:
            break
        if test.code:
            raise PhaseFailed("NO_TEST_COMMAND", test.code)
        with phase(job, f"fix_{i}", "agent", P.fix) as ph:
            build = call_agent(job, ph, P.fix, output_type=BuildOutput, resume=True,
                               context=pack(**{"Plan": plan.plan, "Test result": test.feedback()}),
                               soft_gates=claims)
            _stop_on_conflict(job, build)
            ph.succeed(build.summary)
        test = _test(job, f"test_{i + 1}")
    if not test.passed:
        raise PhaseFailed(test.code.split(":")[0] if test.code else "TESTS_FAILED",
                          f"tests still fail after {P.max_fix_loops} fix round(s): {test.summary()}")

    # ---- review <-> revise (the ledger keeps findings and their ids across rounds and reworks) ----
    ledger = reviewmem.load(job)
    first_round = max((max(f["round"], f.get("checked_round", 0)) for f in ledger), default=0) + 1
    review, revised = None, False
    for i in range(1, P.max_review_loops + 1):
        round_no = first_round + i - 1
        with phase(job, f"review_{i}", "agent", P.review) as ph:
            job.trace.clear_session(job.id, P.review)  # fresh eyes every round; memory comes from the ledger

            def review_gates(e, _s, ledger=ledger, round_no=round_no):
                checks = [reviewmem.check_prior(e, ledger)] if reviewmem.open_items(ledger) else []
                return checks + [reviewmem.verdict_consistent(e, reviewmem.merge(ledger, e, round_no))]
            review = call_agent(job, ph, P.review, output_type=ReviewOutput,
                                context=pack(**{"Spec sections": specs, "Plan": plan.plan,
                                                "Previous review": reviewmem.context(ledger),
                                                "Diff": _review_diff(job, f"review_{i}"),
                                                "Test result": test.summary()}),
                                soft_gates=review_gates)
            ledger = reviewmem.merge(ledger, review, round_no)
            reviewmem.save(job, ph, ledger)
            ph.succeed(f"{review.verdict}: {review.summary}")
        if review.verdict == "PASS" or i == P.max_review_loops:
            break
        with phase(job, f"revise_{i}", "agent", P.build) as ph:
            build = call_agent(job, ph, P.build, output_type=BuildOutput, resume=True,
                               context=pack(**{"Plan": plan.plan,
                                               "Previous step": "Reviewer findings to close (keep the ids in your "
                                                                "summary):\n\n" + reviewmem.for_builder(ledger)}),
                               soft_gates=claims)
            _stop_on_conflict(job, build)
            ph.succeed(build.summary)
            revised = True
    if review is None or review.verdict != "PASS":
        open_ids = ", ".join(f["id"] for f in reviewmem.open_items(ledger) if reviewmem.is_blocking(f))
        raise PhaseFailed("REVIEW_CHANGES", f"review still asks for changes after {P.max_review_loops} round(s)"
                                            f" (open: {open_ids}; see {reviewmem.REPORT})")

    if revised:  # a revision changed code after the last green run
        test = _test(job, "retest")
        if not test.passed:
            raise PhaseFailed("TESTS_FAILED", f"retest after revision failed: {test.summary()}")

    # ---- commit on the job branch; the human merges ----
    with phase(job, "commit", "code", "git") as ph:
        msg = f"{build.commit_message}\n\nfactory-job: {job.id}\ntask: {job.task.id}"
        sha = worktree.commit_all(wt, msg)
        if sha is None:
            raise PhaseFailed("NO_CHANGES", "nothing to commit")
        job.trace.update_job(job.id, code_sha=sha)
        ph.succeed(f"{sha[:8]} on {job.branch}")
    docs_note = _doc_updates(job, pack) if P.doc_updates != "off" else ""
    follow_note = _followups(job, review, ledger)
    return f"ready: {sha[:8]} on {job.branch}{docs_note}{follow_note} — factory approve {job.id}"


def _followups(job: Job, review: ReviewOutput, ledger: list[dict]) -> str:
    docs = job.trace.last_envelope(job.id, "DocUpdates") or {}
    doc_paths = {u["path"].removeprefix("./") for u in docs.get("updates", [])}
    items = followups.gather(review, ledger, doc_paths)
    if not items:
        return ""
    with phase(job, "followups", "code", "factory") as ph:
        followups.record(job, ph, items)
        ph.succeed(f"{len(items)} follow-up(s)")
    return f" (+{len(items)} follow-ups → {followups.REPORT})"


def _doc_updates(job: Job, pack) -> str:
    """Record doc proposals; apply them in `apply` mode. Returns the suffix for the job reason.

    A failed docs step never loses the code commit: its changes are dropped and the job still
    waits for the human with the code commit.
    """
    P, wt = job.cfg.pipeline, job.wt
    updates, dropped = docupdates.collect(job)
    if not updates and not dropped:
        return ""
    with phase(job, "doc_updates", "code", "factory") as ph:
        for d in dropped:
            job.trace.event(job.id, ph.id, {"type": "warning", "text": f"doc update dropped: {d}"})
        job.trace.envelope(job.id, ph.id, "DocUpdates", {"updates": [u.model_dump() for u in updates],
                                                         "dropped": dropped})
        ph.succeed(f"{len(updates)} proposed, {len(dropped)} dropped")
    if not updates:
        if dropped:
            (job.dir / docupdates.REPORT).write_text(docupdates.report([], [], dropped))
        return ""
    can_apply, never = docupdates.split(job.cfg, updates)
    applied, note, docs_sha = [], "", None
    if P.doc_updates == "apply" and can_apply:
        try:
            docs_sha = _apply_docs(job, pack, can_apply)
            applied = can_apply if docs_sha else []
        except PhaseFailed as e:
            worktree.reset_hard(wt)  # HEAD is the code commit
            note = f"Docs step failed, nothing applied: {e}"
    (job.dir / docupdates.REPORT).write_text(docupdates.report(updates, applied, dropped, note))
    proposed = len(updates) - len(applied)
    parts = [f"{len(applied)} applied in {docs_sha[:8]}"] if applied else []
    if proposed:
        parts.append(f"{proposed} proposed")
    if note:
        parts.append("docs step failed")
    return f" (+{len(updates)} doc updates: {', '.join(parts)} → {docupdates.REPORT})"


def _apply_docs(job: Job, pack, updates) -> str | None:
    P, cfg, wt = job.cfg.pipeline, job.cfg, job.wt
    files = sorted({u.path.removeprefix("./") for u in updates})
    context_dirs = {c.rstrip("/") + "/" for c in cfg.project.context}
    protected = [p for p in cfg.protected if p not in context_dirs]   # docs open; config/tasks/skill stay closed
    code_head = worktree.rev(wt)
    instructions = ("Docs step. The code is committed and reviewed. Apply these doc updates now.\n"
                    f"Change ONLY these files: {', '.join(files)}. Any other change is reverted.\n"
                    "Keep the headings that tasks reference (spec refs must still resolve).\n\n"
                    + docupdates.brief(job, updates))

    def gates_(e, snap):
        return [gates.claims(wt, snap, e.changed_files), _spec_refs_resolve(job)]

    with phase(job, "docs", "agent", P.docs) as ph:
        out = call_agent(job, ph, P.docs, output_type=DocsOutput,
                         resume=job.trace.session(job.id, P.docs) is not None,
                         context=pack(**{"Previous step": instructions}),
                         writes=files, protected=protected, soft_gates=gates_)
        ph.succeed(out.summary)
    with phase(job, "docs_review", "agent", P.review) as ph:
        job.trace.clear_session(job.id, P.review)
        diff = worktree.diff(wt, code_head)
        review = call_agent(job, ph, P.review, output_type=DocsReviewOutput,
                            context=pack(**{"Previous step": "Review ONLY this doc change. Does it match the code "
                                                             "on this branch and the proposals below?\n\n"
                                                             + docupdates.brief(job, updates),
                                            "Diff": f"```diff\n{diff}\n```"}),
                            soft_gates=lambda e, _s: [_verdict_consistent(e)])
        ph.succeed(f"{review.verdict}: {review.summary}")
    if review.verdict != "PASS":
        with phase(job, "docs_revise", "agent", P.docs) as ph:
            findings = "\n".join(f"- [{f.severity}, {f.kind}] {f.file}: {f.message}" for f in review.findings)
            out = call_agent(job, ph, P.docs, output_type=DocsOutput, resume=True,
                             context=pack(**{"Previous step": f"Docs reviewer findings to close:\n\n{findings}\n\n"
                                                              f"Change ONLY these files: {', '.join(files)}."}),
                             writes=files, protected=protected, soft_gates=gates_)
            ph.succeed(out.summary)
    with phase(job, "commit_docs", "code", "git") as ph:
        sha = worktree.commit_all(wt, f"{out.commit_message}\n\nfactory-job: {job.id}\nfactory-docs: {job.task.id}")
        ph.succeed(f"{sha[:8]} on {job.branch}" if sha else "no doc changes")
    return sha


def _spec_refs_resolve(job: Job) -> gates.GateResult:
    """Every task's spec refs still resolve after a doc edit (headings not broken)."""
    broken = []
    for t in tasks.load_all(job.wt):
        try:
            context.spec_sections(job.wt, t.spec)
        except context.ContextError as e:
            broken.append(f"{t.id}: {e.message}")
    return gates.GateResult("spec_refs_resolve", not broken, "; ".join(broken))


def _test(job: Job, name: str) -> quality.QualityResult:
    with phase(job, name, "code", "quality") as ph:
        result = quality.run(job.cfg, job.wt)
        for c in result.checks:
            ph.log(check=c.name, passed=c.passed, command=c.command, output=c.output[-2000:])
        (job.dir / f"{name}.txt").write_text(result.feedback())
        if result.passed:
            ph.succeed(result.summary())
        else:
            ph.detail = result.summary()
    return result


def _review_diff(job: Job, name: str) -> str:
    """Stat + the diff, cut at project.max_diff_lines (as sssf does). The full diff is saved for the agent."""
    full = worktree.diff(job.wt, job.base_sha)
    stat = worktree.diff_stat(job.wt, job.base_sha)
    path = job.dir / f"diff-{name}.patch"
    path.write_text(full)
    lines = full.splitlines()
    limit = job.cfg.project.max_diff_lines
    head = f"```text\n{stat}\n```\n\n"
    if len(lines) <= limit:
        return head + f"```diff\n{full}\n```"
    return (head + f"```diff\n" + "\n".join(lines[:limit]) + "\n```\n\n"
            f"Diff cut at {limit} of {len(lines)} lines. Full diff: {path} — read it with your tools.")


def _plan_specs(job: Job, plan: PlanOutput, have: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Extra spec sections the plan names. Refs that do not resolve are logged, not fatal."""
    known = {r for r, _ in have}
    out = []
    for ref in plan.spec_refs:
        if ref in known:
            continue
        try:
            out += context.spec_sections(job.wt, [ref])
        except context.ContextError as e:
            job.trace.event(job.id, None, {"type": "warning", "text": str(e)})
    return out


def _stop_on_conflict(job: Job, build: BuildOutput) -> None:
    if build.spec_conflict:
        (job.dir / "spec_conflict.md").write_text(build.spec_conflict)
        raise StopForHuman(f"spec conflict: {build.spec_conflict}  → decide, then: factory rework {job.id} \"<note>\"")


def _plan_non_empty(p: PlanOutput) -> gates.GateResult:
    ok = bool(p.plan.strip())
    return gates.GateResult("plan_non_empty", ok, "" if ok else "the `plan` field is empty")


def _verdict_consistent(r: ReviewOutput) -> gates.GateResult:
    blocking = [f for f in r.findings if f.blocking]
    ok = (r.verdict == "PASS") == (not blocking)
    return gates.GateResult("verdict_consistent", ok,
                            "" if ok else "verdict must be CHANGES if and only if there is a blocking finding")
