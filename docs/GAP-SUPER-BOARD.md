# Gap analysis: kh-factory vs super-board (v3.1.2)

Source: [EricTechPro/super-board](https://github.com/EricTechPro/super-board), read on 2026-10-18.
**Verified (read in full or in the quoted parts):** `README.md`, `docs/super-board/README.md`,
`skills/super-review/SKILL.md`, `skills/super-build/SKILL.md`, `skills/super-board/references/lint.md`,
the test-gap part of `skills/super-qa/SKILL.md`, `scripts/super-board-merge-gate.sh` (header and contract),
`hooks/guard-secrets.py`, `hooks/guard-key-literals.py`, `evals/README.md` + the `review-remembers` case,
`skills/super-build/references/decision-policy.md` (decision ladder), `references/calibration.md` (intro),
`references/block-template.md` (structure).
**Not read:** `run.md` / `run-workflow.md` (the wave engine), `super-collect` internals, `ui-refine-loop`, `visual`.

---

## 1. What super-board is

An autonomous loop for Claude Code over a **GitHub Project board**. "Add tasks, walk away, get
merged PRs with proof."

```text
/super-collect (Sentry, PostHog, issues, PRs, architecture) ──> Backlog
/super-board lint (19 criteria, interactive) + you ──> Ready
wave (sized by the dependency graph, parallel cards):
   classify (Haiku picks the model tier) -> super-build (worktree, smallest safe change, draft PR)
   -> super-qa (AC ledger, test gaps, evidence) -> super-review (prior findings first, own hypotheses)
merge gate: lock -> head == reviewed sha -> base merged in scratch worktree -> verify_commands
            -> merge_policy (money/auth/schema/>400 lines → human) -> squash --match-head-commit
state = the board; every lane re-reads it, so a run survives Ctrl-C and restarts
```

Key design rules taken from its skills:
- Lanes never ask the user anything ("a skill that waits is a skill that hangs"). Interactive work
  (grilling, shaping) happens only in `lint`, before the loop.
- "Missing, unreadable or stale evidence is a hold for a person, never a pass."
- "Nothing merges on GitHub's word": `mergeable: CLEAN` only means the text does not conflict.

## 2. Side by side

| Area | super-board | kh-factory |
|---|---|---|
| Who runs the loop | skills run by the model (wave workflow); merge gate is a script | **Python code**; agents do bounded steps |
| Models | Claude tiers by difficulty router, or Codex models | **Pi (any opencode-go model) + Claude**, per agent |
| State | GitHub Project board + PR comments | SQLite trace + task files (local) |
| Work intake | `/super-collect` from Sentry, PostHog, issues, PRs | `factory add`, `factory import` |
| Task quality | `lint`: 19 criteria, interactive fixes, pre-flight (creds/tools) | none |
| Isolation | worktree per card | worktree per job |
| Agent answers | free text + PR comments with markers | **typed JSON envelopes + gates** |
| Write limits | guard hooks (worktree path, secrets, deletes) | **writes boundary per agent** (revert after step) |
| Tests | QA lane writes missing tests (AC ledger, edge cases, "test the tests") | code runs test/lint; builder writes tests |
| Review | prior findings first, R-ids, own hypotheses, finding classes, adversarial truth check | fresh reviewer each round, PASS/CHANGES + findings |
| Follow-ups | review files refactors, QA files off-task bugs as cards | lost |
| Merge | gate: lock + head pin + verify on current base; auto-merge by policy | human `approve`, merge without re-verify |
| Docs drift | not a lane | **doc updates** (propose/apply, ADRs never applied) |
| Context | lanes read issue + PR + AGENTS.md | **context pack** with spec sections + budget + measurement |
| Secrets | hooks block reading `.env`/keys/SSH and writing key literals | none |
| Cost control | usage guard (pause at 95%), model routing, worker caps | context budget, timeouts, cost per job |
| Learning | optional calibration of skill files after verified runs | manual `.factory/learnings.md` |
| Evals | behavioural evals with LLM graders + offline `gh` stubs | unit/flow tests with a fake runner |

**kh-factory is ahead on:** code-controlled flow, multi-provider runners, typed envelopes and gates,
spec-section context with budget, doc updates, context measurement, no GitHub dependency.

## 3. Deep dive: what their skills do that we should learn from

### 3.1 super-review

1. **Review remembers.** Every report carries a marker and stable ids (R1, R2 …). On a re-review,
   **round 1 checks each prior finding before anything else**: `fixed` (cite file:line),
   `not fixed` (still true in the code — "a resolved thread is not evidence, and neither is the
   builder's reply"), or `no longer applies`. Any `not fixed` → bounce again and **skip the fresh
   pass** ("it would review code that is about to change"). New findings take the next ids.
   Their eval `review-remembers` grades exactly this. **Our gap:** round 2 gets a new session and
   never sees round 1's findings.
2. **Own hypotheses first.** Before reading the builder's summary, read the ACs and the raw diff
   and write 2–4 hypotheses: what must be true, where it would break. Then treat the builder's
   claims as leads to check, not as the frame. **We** hide the builder's reasoning entirely (good),
   but we do not ask for hypotheses, and we do not let the reviewer check the builder's claims.
3. **Finding classes.** Gap · Bug · Verification miss · Scope drift · Over-engineering · No issue.
   Severity: Blocker · Should fix · Nit · Deepening opportunity · Human gate.
   - **Over-engineering is always "should fix" and never blocks a merge on its own.**
   - **Deepening opportunities never block**; they become a follow-up ticket with real ACs.
   - **"✅ Done" line:** hypotheses that held, with proof, so the next round does not re-litigate.
   **Our gap:** only `blocking | minor`; minor findings vanish.
4. **Adversarial truth check** (diff ≥ 10 lines or security/money/auth labels): two sub-agents
   (code-grounder, historian), confidence 0–100, take the **minimum**, below 70 → block. Their
   brief is used verbatim, because a reworded brief once made over-engineering blocking.
5. **Merge protocol:** record the reviewed head sha; merge only through the gate pinned to it;
   then **confirm the merge landed** (merge commit is an ancestor of the base) — never trust the
   merge command's exit code. "A card in Done has its code on the base branch."

### 3.2 super-build

1. **Decision ladder:** acceptance criteria → repo precedent → smallest blast radius → human gate.
   Stop at the first rung that answers. Rung-3 choices go in the commit as a `--- decision ---`
   trailer, so reviewers can audit them.
2. **Docs before outside-tool code:** for a third-party API, an upgrade, or auth/billing, read the
   current official docs for the installed version first, and cite them as `Docs:` bullets
   ("not Stack Overflow, not old blog posts, not memory").
3. **Small PRs by design:** cap `auto_max_lines` = 400 changed lines (lockfiles, generated files,
   snapshots excluded). If the change grows past it: stop, push what exists, propose vertical
   slices, block with ❓ "too big".
4. **WIP-PARTIAL** is a valid outcome (open partial PR, keep the issue open, human-gated) and does
   not stop the loop. **HUMAN GATE** (prod cutover, destructive ops, secrets) stops it.
5. **`root-cause-hash:`** on every failure handoff: sha256 of lane | error class | first 3
   normalized file:line frames. The same hash twice means the loop is not converging.
6. Routing by type label: `bug` → diagnose + TDD; `feature` → implement + TDD + design;
   `docs` → no TDD. A `Skills:` line in the ticket overrides.

### 3.3 super-qa (test-gap check, issue-scoped)

"The Builder's tests answer 'does it do what I built?'; this answers 'what breaks silently?'"
1. **AC ledger:** every AC → the asserting test `file:line` (unit / component / e2e), or `none`.
   "A test that runs the code but asserts nothing about the AC is `none`."
2. **Edge-case classes** per changed input: boundaries, money/dates, input (unicode, long),
   volume (0/1/many), auth, error paths, idempotency/races, UI states, accessibility.
3. **Test the tests:** flag tests that assert on their own mock, recompute the expected value, or
   cannot fail; name a surviving mutant for high-risk logic.
4. **Rank and act:** High (an AC with no test; any gap in auth/money/security/data loss) → write
   the test red-first now; a High gap that needs app code → bounce to the builder. Medium/Low →
   listed, never block. On auth/money changes the ledger is built by a read-only sub-agent that
   sees only ACs + changed files (not anchored on the builder).
5. **Test placement ladder:** call the module directly → wire real collaborators → real browser →
   needs a live third party = not a test, file a gap. Every test must be "red for the right reason".

### 3.4 lint (19 criteria) and pre-flight

Deterministic-friendly criteria: no or empty AC section (1, 2); unmeasurable adjectives —
"snappier, polished, modern" (3); vague quantifiers with no unit — "quickly, many" (5);
`etc.`/`TODO`/`TBD` (8); more than ~3 unrelated ACs (9); `## Blocked by` missing or unparseable —
especially "None — but #26 must merge first" (13); an AC proven against a fake/stub/mock with no
card that builds the real one (14); likely too big (15); ACs not a checklist (17).
Judgment criteria (LLM): no trigger+outcome pair (6), missing test surface (7), title/body
mismatch (10), out of scope vs PROJECT.md (11), two interpretations (12), unsettled design (19).
**Pre-flight:** credentials, tools and environment the tasks need; each unchecked item blocks the run.

### 3.5 Merge gate (`super-board-merge-gate.sh`)

Why it exists, from the script header: PR #89 read MERGEABLE/CLEAN and still failed `tsc` against
the current base, because a PR merged after #89 was tested had changed a shared interface.
"Nothing textual conflicted. … The two facts were simply established at different times."
- **The check runs inside the lock** (`mkdir` lock: atomic on POSIX, visible to any process), so
  nothing can move the base between the proof and the merge.
- Pin to `--expect-head` (the reviewed sha): a push after review voids the evidence.
- Merge the current base into a scratch worktree, run `verify_commands`, then merge with
  `--match-head-commit`. No `verify_commands` → exit 9, the gate refuses to pretend.
- Separate `--lock-timeout` (how long to wait) and `--stale-after` (when a lock is abandoned).

### 3.6 Guards

- `guard-secrets`: blocks tool calls that read `.env`, `.env.local|production|…`, `id_rsa|ed25519|ecdsa`,
  `.npmrc`, `credentials(.json)`, `service-account*.json`, `*.pem`. `.env.example` is allowed (it is
  how an agent learns key names). A helper prints present/empty/missing per key, never a value.
- `guard-key-literals`: blocks writing key literals (`sk-ant-…`, `sk-proj-…`, `AKIA…`, `ghp_…`,
  `github_pat_…`, `xox?-…`, `AIza…`, private key blocks, `token = "<24+ chars>"`), with a SAFE
  filter (`process.env`, `os.environ`, `getenv`, `${VAR}`) and a FAKE filter (`your-`, `xxxx`,
  `example`, `placeholder`, `changeme`, `dummy`, `REDACTED`).
- `guard-delete-outside`, `guard-worktree-path`, opt-in `guard-protected-push`.
- All guards **fail open** on their own errors (never block every call by a bug).

### 3.7 Other

- **Block template:** every hand-off has a reason tag (❓ ambiguity · 🛡 safety · 🧑 human review ·
  🙋 needs you · 🔐 credentials …), `Why blocked` (one concrete line) and `To unblock` (a checklist
  the human can act on).
- **Evals:** each case seeds a throwaway repo, runs the real agent with stubbed `gh`, and grades
  with scripted checks plus an LLM judge (about $0.25–0.45 per run).
- **Calibration (opt-in):** after a *verified* completed run (merges confirmed), an external skill
  may edit allowed skill files. Missing evidence → skipped, not calibrated.

## 4. Gaps, ranked

| # | Gap | Value | Cost | When |
|---|---|---|---|---|
| G1 | **Merge gate on approve** (lock, head pin, verify on current base, merge the tested commit, confirm landed) | High | ½ day | **Now** |
| G2 | **Review remembers** (stable R-ids, round 1 checks prior findings, finding classes, over-engineering never blocks, hypotheses first) | High | ½ day | **Now** |
| G3 | **`factory lint`** (deterministic criteria + pre-flight) | High | ½ day | **Now** |
| G4 | **Follow-ups** (should-fix, deepening, off-task bugs → `followups.md` → task files with ACs) | Medium | ½ day | **Now** |
| G5 | **Secret guards** (deny secret reads in the Claude runner; scan added lines for key literals) | High | ½ day | **Now** |
| G6 | Same-failure detection (root-cause hash) stops a non-converging fix loop | Medium | 2 h | **Now** (cheap) |
| G7 | Size cap (`max_changed_lines`, default 400, lockfiles excluded) → stop with ❓ "too big" | Medium | 2 h | **Now** (cheap) |
| G8 | Decision ladder + `--- decision ---` trailer; docs-first citations for third-party APIs | Medium | 2 h | **Now** (prompt + envelope field) |
| G9 | Structured stop reasons (reason tag, why, to unblock) | Medium | ½ day | Next |
| G10 | AC ledger + test-gap check (reviewer or a QA step); "test the tests" | High | 1–1½ days | Next |
| G11 | Unattended drain loop: dependency waves, parallel jobs, merge lock shared | High | 1½–2 days | Next |
| G12 | Model routing by difficulty; task `type:` → prompt variants | Medium | 1 day | Next |
| G13 | Adversarial truth check (two skeptics, minimum confidence) | Medium | 1 day | Later |
| G14 | Behavioural evals for prompts (`factory eval`) | Medium | 1–2 days | Later |
| G15 | GitHub adapter (issues/board as state, draft PRs, comments with evidence) | High for team | 2–3 days | Later (planned) |
| G16 | Intake from Sentry/PostHog/PRs | Low now | 2+ days | Later |
| G17 | Auto-merge policy | — | — | **Skip** (decision Q4: a human approves every merge). Show risk flags in reports instead |

## 5. Status

- **Done (2026-10-18):** G2 review memory, G3 task lint (with context checks), G4 follow-ups.
  See HANDBOOK §5.1 and §9.0.
- **Open:** G1, G5, G6, G7, G8 below.

## 6. Plan for "Now" (G1–G8)

### G1 — merge gate inside `factory approve`

```text
approve <job> [--code-only]
  1 lock: mkdir .factory/merge.lock (wait ≤ 30 min; a lock older than 90 min is stale)
  2 head pin: branch head (or code_sha) == the commit the flow recorded as reviewed → else REVIEWED_HEAD_MOVED
  3 scratch worktree at the CURRENT base; merge the job commit (--no-ff) there
  4 run project.commands (lint, test) in the scratch worktree → red = BASE_VERIFY_FAILED
  5 main checkout: git merge --ff-only <scratch merge commit>   (merges exactly the tested tree)
  6 confirm landed: merge commit is an ancestor of the base branch
  7 remove scratch + job worktree; release the lock (always)
```
- `trace.jobs` gets `reviewed_sha` (set at the commit phase; the docs commit updates it).
- `BASE_VERIFY_FAILED` keeps the job `awaiting-human`; the reason says to `rework` ("update for the
  new base") or reject. No merge happens.
- `--no-verify` exists for emergencies and is printed loudly in the reason.

### G2 — review remembers

- `Finding` gets `id` (R1…), `klass` (`gap|bug|verification_miss|scope_drift|over_engineering`)
  and `severity` (`blocker|should_fix|nit|deepening`). `ReviewOutput` gets `prior: [{id, status:
  fixed|not_fixed|no_longer_applies, proof}]` and `held: [str]` (hypotheses that held, with proof).
- Round N+1 context gets the previous round's findings with their ids and the rule: "check each
  first; cite file:line; any not_fixed → verdict CHANGES, skip the fresh pass".
- Code gates: every prior id has a status with non-empty `proof`; new ids continue the numbering;
  `verdict_consistent` counts only `blocker` (over-engineering and deepening never block).
- Reviewer prompt: form 2–4 hypotheses from task + diff before anything else; the builder's
  `summary` and `changed_files` are given as **claims to check**.

### G3 — `factory lint [task…]`

Deterministic checks, each `error` (blocks `factory run`) or `warn`:

| Check | Level |
|---|---|
| no `## Acceptance` section, or empty / placeholder text | error |
| acceptance not a bullet list | warn |
| vague words in Objective/Acceptance (`fast, quickly, snappy, polished, modern, nice, better, improve, many, some, etc., TBD, TODO, …`) | warn |
| more than 6 acceptance bullets, or 3+ areas named | warn (too big) |
| `depends_on` names an unknown task, or a cycle | error |
| `spec:` ref does not resolve | error |
| AC proven with fake/stub/mock/"no database" and no task that builds the real one | warn |
| no `## Tests` text | warn |
| pre-flight: the test command's first word is not on PATH | error |
| pre-flight: env vars named in the task (`[A-Z][A-Z0-9_]+_KEY|TOKEN|SECRET`) missing (presence only, never value) | warn |

`factory run`/`next` run lint first (`--no-lint` to skip). The skill runs it after writing a task
and fixes errors before committing. LLM criteria (two interpretations, title/body mismatch) stay
with the skill at the keyboard, like super-board.

### G4 — follow-ups

- `ReviewOutput.followups: [{title, why, acceptance: [..], kind: refactor|bug|test|docs}]` for
  should-fix items not fixed, deepening opportunities, and off-task problems.
- Code writes `followups.md`; reason suffix `(+N follow-ups)`.
- `factory followups <job> --create` writes task files (with acceptance, so they pass lint),
  `depends_on: [<task>]`. Never automatic.

### G5 — secret guards

- **Claude runner:** add deny rules for secret files (`--disallowedTools "Read(**/.env)" …` with the
  super-board pattern list; `.env.example` allowed). **Unverified:** the exact rule syntax that
  `claude -p` accepts; checked first in the implementation.
- **Pi runner:** no pre-call hook is available to us; the protection is after the step.
- **Diff gate for every agent step** (`secrets_in_diff`): scan added lines for the key-literal
  patterns with the SAFE and FAKE filters; also flag newly added secret files (`.env`, `*.pem`,
  `id_rsa`, `credentials.json`). First hit → soft re-prompt ("remove the literal, read it from the
  environment"); still present → revert and fail `SECRET_IN_DIFF`.
- The context pack never includes secret files (the repo map lists names only; checked).

### G6 — same failure twice

`root_cause_hash` = sha256 of (phase kind | first error line | first 3 `file:line` frames) from the
test output. Two fix rounds with the same hash → stop early with `TESTS_FAILED (same failure twice)`.

### G7 — size cap

`project.max_changed_lines: 400` (additions + deletions vs base, excluding lockfiles, `*.lock`,
`*.snap`, generated paths in `project.size_exclude`). Over the cap after review → job stops
`awaiting-human` with `STOPPED: too big (N lines)`; the builder's envelope may carry a split
proposal. You can still approve.

### G8 — decision ladder and docs citations

- Builder prompt: the decision ladder; third-party API / upgrade / auth → read current official docs.
- `BuildOutput` gets `decisions: [{question, choice, rung}]` and `docs: [{url, fact}]`.
- Rung-3 decisions are appended to the commit message as a `--- decision ---` trailer;
  `docs` go to the job report.

### Order and estimate

G2 → G6 → G3 → G1 → G5 → G4 → G7 → G8. About **3 days** with tests and docs.

### Verification

- Fake-runner tests for each item: reviewer round 2 receives R-ids and must mark them (missing
  status → re-prompt; `not_fixed` → CHANGES); lint errors block `run`; approve refuses a moved head
  and a red merge with the current base, and merges exactly the tested commit; a key literal in a
  builder diff is re-prompted, then reverted; same failure twice stops after 2 rounds; a 500-line
  change stops as too big; decisions appear in the commit trailer.
- One real toy job per item group, as in earlier steps.
