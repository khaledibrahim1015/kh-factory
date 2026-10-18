# kh-factory handbook

Everything about kh-factory in one file: what it is, how to install it, how to use it from the
terminal and from Claude Code, how it works inside, how to configure it, and what to do when
something fails.

Other docs: [PLAN-v1.md](PLAN-v1.md) (design), [DECISIONS-QA.md](DECISIONS-QA.md) (every decision
and why), [TESTING.md](TESTING.md) (manual test steps), [NEW-PROJECT-GUIDE.md](NEW-PROJECT-GUIDE.md)
(a project from zero).

---

## 1. What it is

kh-factory turns a task file into a tested commit on its own branch. Then it waits for you.

**Rule: agent proposes, code disposes.** Python code runs the flow, checks every claim, and runs
the tests. AI agents do one bounded step each. You approve every merge.

```text
tasks/T-001.md
   │
   ▼
plan (agent) ─> build (agent) ─> test (code) <─> fix (agent, max 3)
   ─> review (agent, other model) <─> revise (agent, max 2) ─> retest (code)
   ─> commit on factory/<job-id> ─> awaiting-human
                                        │
you:  factory approve <job>  |  factory rework <job> "note"  |  factory reject <job>
```

It came from two projects:
- [disler/super-simple-software-factory](https://github.com/disler/super-simple-software-factory):
  code controls the flow, typed JSON answers, gates, tests run as code, SQLite trace.
- [zazencodes software-factory-claude-code](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code):
  one git worktree per job, human approve/rework, the `/factory` skill.

## 2. Words used in this handbook

| Word | Meaning |
|---|---|
| **task** | One markdown file in `tasks/`: what to build and how to check it |
| **job** | One run of the pipeline for one task. Has an id like `t-001-14b738` |
| **agent** | A role with its own prompt, model, tools and write rights: `planner`, `builder`, `reviewer` |
| **runner** | The CLI that runs an agent: `pi` or `claude` (`claude -p`) |
| **envelope** | The JSON block an agent must end its answer with (`PlanOutput`, `BuildOutput`, `ReviewOutput`) |
| **gate** | A code check after an agent step (for example: did it change only allowed files?) |
| **context pack** | The `context.md` file the code builds for each agent step |
| **worktree** | A second checkout of the repo, one per job, on branch `factory/<job-id>` |
| **flow** | The step order, one Python file in `src/kh_factory/flows/` (v1: `sdlc`) |
| **trace** | `.factory/trace.db` (SQLite): the source of truth for job state, phases, events |

## 3. Install

Needs: `git`, `uv`, and at least one agent CLI:
- `pi` with a provider login (here: `opencode-go`), and/or
- `claude` (Claude Code, your subscription).

```bash
cd ~/Desktop/setup/kh-factory
uv sync
uv run pytest -q                 # expect: 62 passed
uv tool install -e .             # `factory` on your PATH (editable: code changes apply at once)
factory --help
factory install-skill            # optional: /factory in Claude Code (symlink, all repos)
```

`factory: command not found` after install → open a new terminal or run `rehash`.

## 4. Set up a project

The factory works in any git repo with at least one commit. You run `factory` commands in the
repo root. Jobs get their own folders next to the repo:

```text
~/code/my-app                   your repo (you run `factory` here)
~/code/my-app-factory/<job-id>  one worktree per job (created and removed by the factory)
```

### 4.1 Existing code project

```bash
cd ~/code/my-app
factory init                    # or: factory init --with-skill (also copies the skill into the repo)
```

`init` writes these files (it keeps existing files unless you pass `--force`):

| File | Purpose | Commit it? |
|---|---|---|
| `factory.yaml` | agents, models, pipeline, project facts | yes |
| `.factory/prompts/{planner,builder,reviewer}/{system,user}.md` | agent prompts | yes |
| `.factory/learnings.md` | rules every agent sees | yes |
| `AGENTS.md` (only if neither `AGENTS.md` nor `CLAUDE.md` exists) | starter repo rules: stack, commands, a Conventions section to fill in | yes |
| `tasks/` | task files | yes |
| `.gitignore` entries `.factory/jobs/`, `.factory/trace.db*` | runtime state stays out of git | yes |
| `.git/info/exclude` entries (caches like `__pycache__/`, `.venv/`) | caches never count as agent changes | (local only) |

`init` detects the test command:

| Marker file | Stack | `test` | `lint` |
|---|---|---|---|
| `pyproject.toml` | Python (uv) | `uv run pytest -q` | — |
| `package.json` | Node.js | `npm test --silent` | — |
| `go.mod` | Go | `go test ./...` | `go vet ./...` |
| `Cargo.toml` | Rust | `cargo test` | `cargo clippy -q` |

Check `project.commands` in `factory.yaml`, then:

```bash
factory config check            # roster + all checks, including `pi --list-models`
git add -A && git commit -m "factory setup"
```

### 4.2 Fresh project (no code, no docs)

Same as 4.1. `init` finds no stack and writes `commands: {}`. **Set `project.commands.test`
before any job**, or every job fails with `NO_TEST_COMMAND`. Your first task should be
"bootstrap the project and its test command". The factory does not invent a product vision: you
write the tasks. Full walk-through: [NEW-PROJECT-GUIDE.md](NEW-PROJECT-GUIDE.md).

### 4.3 Project with docs, specs and task cards (Nizk style)

```bash
factory init
factory import docs/planning/TASKS.md     # '### ID — Title' cards with '- **Field:** value' bullets
```

Then in `factory.yaml`:

```yaml
project:
  context: [docs]                # doc folders: protected, never edited by agents
  rules: ["Go 1.23; follow docs/specs/CODEBASE.md"]
```

- Each imported task keeps `depends_on` and `spec` refs, for example `docs/specs/CODEBASE.md#3`.
- The code cuts only those sections into the context pack. It does not send all the docs.
- A ref that does not resolve fails the job with `SPEC_REF_NOT_FOUND` before any model is called.
- If the build must break a spec, the builder sets `spec_conflict` and the job stops for you.
- Tested on Nizk `TASKS.md`: 98 cards, 188 spec refs, 0 unresolved.

## 5. Task files

`factory add "<title>"` creates `tasks/T-00N-<slug>.md`:

```markdown
---
id: T-001
title: Add a goodbye function
depends_on: []              # task ids that must be `done` (approved) first
spec: []                    # e.g. [docs/specs/API.md#3.2, docs/specs/API.md#errors]
---

## Objective

Add `goodbye(name)` to `greet.py`.

## Acceptance

- `goodbye("Sara")` returns `"Goodbye, Sara!"`

## Tests

Add a test in `tests/test_greet.py`.

## Notes
```

Rules for a good task:
- Each acceptance bullet is checkable by a test: inputs → exact output or exact error.
- One task = one small change (one job, a few files).
- A weak task gives a weak build. A good model does not fix a vague task.

A `spec` ref is `path` (whole file) or `path#anchor`. The anchor matches a heading by number
(`#3`, `#5.2`, `#§6`) or by text (`#errors`). The section runs to the next heading of the same
or higher level.

**Task status is not stored in the file.** It comes from the job history:

| Status | Meaning |
|---|---|
| `todo` | no job yet |
| `running` / `stalled` | a job is running / its process died |
| `awaiting-human` | a job waits for you |
| `failed` / `rejected` | last job failed / you rejected it (can run again) |
| `done` | a job for it was approved and merged |

**Jobs start from the last commit.** Commit a task (or a task edit) before you run it.

### 5.1 Task lint (`factory lint`)

`factory lint [ID…] [--strict]` checks that a task is clear **and** that the agents will get a good
context pack for it. `factory run` and `factory next` run it first and refuse a task with errors
(`--no-lint` skips it). The `/factory` skill runs it after writing each task.

| Code | Level | Check |
|---|---|---|
| `NO_ACCEPTANCE` | error | `## Acceptance` missing, empty, or still the template text |
| `UNKNOWN_DEPENDENCY` · `DEPENDENCY_CYCLE` | error | `depends_on` names no task, or loops |
| `SPEC_REF_NOT_FOUND` | error | a `spec:` ref has no file or heading |
| `CONTEXT_TOO_LARGE` | error | the planner's pack (rules, facts, learnings, task, specs, repo map) is over the budget |
| `NO_TEST_COMMAND` · `TEST_COMMAND_NOT_FOUND` | error (repo) | no `project.commands.test`, or its program is not on PATH |
| `THIN_OBJECTIVE` | warn | Objective only repeats the title |
| `ACCEPTANCE_NOT_LIST` · `NOT_CHECKABLE` | warn | not bullets / no item names an exact value, output or error |
| `TOO_BIG` | warn | more than 6 acceptance items |
| `VAGUE_WORDS` | warn | `fast, snappy, polished, better, improve, many, etc, TBD …` |
| `FAKE_WITHOUT_OWNER` | warn | proven with a fake/stub/mock, and Notes name no task that builds the real one |
| `NO_TESTS` | warn | `## Tests` empty |
| `NO_SPEC_REFS` | warn | the repo has doc folders (`project.context`) but the task names no spec section |
| `CONTEXT_NEAR_BUDGET` | warn | the planner's pack uses over 60% of the budget |
| `ENV_MISSING` | warn | an env var named in the task (`*_KEY`, `*_TOKEN` …) is not set (value never read) |
| `NO_REPO_RULES` | warn (repo) | no `AGENTS.md` / `CLAUDE.md` |
| `RULES_NOT_FILLED` | warn (repo) | `AGENTS.md` still has the starter Conventions text |

Judgment checks (two meanings, title vs body) stay with you or the skill, as in super-board.

## 6. Run work from the terminal

```bash
factory status                  # tasks and jobs
factory next                    # first runnable task (todo/rejected, dependencies done)
factory run T-003 [--force]     # one task; --force ignores dependency and duplicate checks
factory show <job-id>           # phases, gates, paths, cost
factory approve <job-id>        # merge into the branch the job started from; remove worktree + branch
factory approve <job-id> --code-only   # merge the code commit only; drop the docs commit
factory rework <job-id> "note"  # back to the builder with your note (or answer planner questions)
factory reject <job-id>         # remove worktree + branch; trace kept
factory resume <job-id>         # continue a failed or stalled job (reuses plan + builder session)
factory try <agent> "prompt"    # smoke test one agent (no worktree, no gates)
factory lint [ID…]              # task clarity + context checks (§5.1)
factory followups <job-id> [--create]   # list a job's follow-ups / write them as task files
factory init --update-prompts   # refresh .factory/prompts/ from this kh-factory version (keeps factory.yaml)
factory ui                      # http://localhost:4600
```

A job runs in the foreground of the command that started it (1–5 minutes). `Ctrl+C` stops it;
the job then shows as `stalled` → `factory resume <job-id>`.

### Job states

```text
queued ─> running ─> awaiting-human ─> approved
             │             │        └─> rejected
             │             └─ (rework) ─> running
             └─> failed ─ (resume | rework) ─> running
running + dead process = stalled ─ (resume) ─> running
```

`awaiting-human` has two kinds:
- **`ready: <sha> on factory/<job-id>`**: a tested, reviewed commit. Approve, rework or reject.
- **`STOPPED: ...`**: planner questions or a spec conflict. No commit. Answer with `factory rework`.
  `approve` refuses a job with no commit.

### Approve rules

- The job must be `awaiting-human` with a commit.
- The main checkout must be on the branch the job started from (usually `main`).
- If the merge conflicts, the CLI aborts it and tells you. Nothing is half-merged.
- The factory never pushes.

### Parallel jobs

Each job has its own worktree and branch, so you can run jobs in several terminals at once.
SQLite (WAL mode) handles the concurrent writes. Two jobs that change the same files can conflict
at `approve`.

## 7. Use it from Claude Code (`/factory` skill)

The skill makes your Claude Code session the **front door**. The `factory` CLI still runs the loop.

```text
you: /factory add a shout(name) function
  Claude: reads AGENTS.md and the relevant files
          factory add "..." → fills Objective / Acceptance / Tests
          shows you the task, commits ONLY that file
          factory run T-00N (in the background)
          factory show + plan.md + review + git diff --stat
  Claude: "t-002-ab12cd — awaiting-human — $0.31 ... Next: approve | rework | reject"
you: /factory approve t-002-ab12cd
```

### Install

| Command | Result | Use when |
|---|---|---|
| `factory install-skill` | `~/.claude/skills/factory/SKILL.md` → symlink to kh-factory's template | you, one machine (default) |
| `factory install-skill --copy` | user-level copy instead of a link | you want to edit your own copy |
| `factory install-skill --project` | `<repo>/.claude/skills/factory/SKILL.md` copy | a team: commit it |
| `factory init --with-skill` | `init` + the project copy | new team repo |

- Keep **one copy per machine** (user link *or* project copies). Two skills with the same name is
  untested territory.
- The user-level link follows kh-factory: when the skill improves, you have the new version at once.
  If you move kh-factory, run `factory install-skill --force` again.
- `.claude/` is a protected path: factory agents cannot edit the skill during a job.
- Start a new `claude` session after installing, so `/factory` is listed.

### Commands

| You type | Claude does |
|---|---|
| `/factory <feature text>` | writes a task with checkable acceptance (asks 1–3 questions if the feature is vague), commits only that file, runs it, reports |
| `/factory next` | `factory next` in the background, then reports |
| `/factory lint [ID…]` | runs `factory lint`, offers to fix the task files |
| `/factory followups <job>` | lists follow-ups; creates tasks only on your yes |
| `/factory status` | short summary of tasks and jobs |
| `/factory show <job>` | the report for that job |
| `/factory rework <job> <note>` | passes your note verbatim |
| `/factory approve <job>` / `/factory reject <job>` | **only when you ask** |
| `/factory resume <job>` | for failed or stalled jobs |
| `/factory import <file>` | imports task cards; asks before committing or editing `factory.yaml` |
| `/factory ui` | starts the UI and gives you the URL |

Plain words work too: "run the next factory task", "approve it".

### Hard rules in the skill

- Never `approve` or `reject` unless you asked in this conversation. A PASS review is not approval.
- Never push. Never merge or remove worktrees by hand.
- Never edit `factory.yaml`, `.factory/prompts/`, `.factory/learnings.md`, or doc folders unless you ask.
- Commit only the task file it wrote. Your main checkout may be dirty; it stays as it is.
- Never write the feature's code itself; the factory does that.

Two Claude sessions exist during a job: yours, and the planner's `claude -p`. They do not share
context (by design: the planner sees only its context pack). Both count against your subscription.

## 8. Watch and inspect

- **`factory status`**: tasks, jobs, last reason, cost per job.
- **`factory show <job>`**: one line per phase with status, owner, time, context use, detail; plus paths.
  Context use is `ctx <tokens>/<model limit> (<percent>)`, measured after each agent call (see §12).
  A call over 80% of the window adds a `warning` event; the UI shows that cell in yellow.
- **`factory ui`** (port 4600, `127.0.0.1` only, read-only): job board, phase table with gates,
  job files (click to read), live event stream. Light and dark theme.
- **Job folder** `.factory/jobs/<job-id>/` in the main repo (gitignored):

```text
plan.md                         the planner's plan
diff-review_N.patch             the full diff the reviewer of round N could read
questions.md | spec_conflict.md when the job stopped for you
doc-updates.md                  doc proposals: [APPLIED] / [PROPOSED], plus dropped ones and why
review.md                       the review ledger: every finding id, round, kind, severity, status, proof
followups.md                    work found during review, outside this task
test_1.txt ... retest.txt       verbatim test output per run
error.txt                       traceback if the factory itself crashed
steps/NN-<phase>/
  context.md                    exactly what the agent saw (the context pack)
  prompt-0.md, prompt-1.md      the prompt per attempt (attempt 1+ = correction)
  answer-0.md, ...              the agent's raw answer per attempt
  envelope.json                 the accepted JSON answer
  raw.jsonl                     the CLI's raw event stream
sessions/                       Pi session files
```

- **Worktree** `../<repo>-factory/<job-id>/`: the code. `git -C <worktree> log`, `git diff <base>..HEAD`.

## 9. Inside the pipeline (flow `sdlc`)

| Phase | Kind | Who | What happens | Fails with |
|---|---|---|---|---|
| `context` | code | factory | resolves the task's spec refs | `SPEC_REF_NOT_FOUND` |
| `plan` | agent | `pipeline.plan` (planner) | reads repo + context; returns `PlanOutput`; code writes `plan.md`. Skipped on resume/rework when a plan exists | `ENVELOPE_INVALID`, `RUNNER_ERROR`, stop: questions |
| `build` / `rework` | agent | `pipeline.build` (builder) | implements the plan, writes tests; returns `BuildOutput` | `WRITES_BOUNDARY`, stop: spec conflict |
| `test_N` | code | quality | runs `lint` then `test` from `project.commands` | `NO_TEST_COMMAND` |
| `fix_N` | agent | `pipeline.fix` (builder, same session) | gets the verbatim failing output | `TESTS_FAILED` after `max_fix_loops` |
| `review_N` | agent | `pipeline.review` (reviewer, **new session each round**) | gets task, plan, spec sections, full diff, test result — not the builder's reasoning; returns `ReviewOutput` | `REVIEW_CHANGES` after `max_review_loops` |
| `revise_N` | agent | builder (same session) | closes the reviewer's findings | — |
| `retest` | code | quality | only if a revision changed code | `TESTS_FAILED` |
| `commit` | code | git | commits on `factory/<job-id>` with the builder's message + `factory-job:` / `task:` trailers | `NO_CHANGES` |
| `doc_updates` | code | factory | only when doc updates were proposed: collects, checks, writes `doc-updates.md` (§9.1) | — |
| `docs` · `docs_review` · `docs_revise` · `commit_docs` | agent / code | `pipeline.docs`, reviewer | only in `apply` mode: edits only the proposed doc files, one review, a separate docs commit (§9.1) | a failure drops the doc edits; the code commit stays |

Every phase starts as failed; code marks it passed. Sessions: the builder keeps one session for
the whole job (build → fix → revise → rework), so it remembers what it did. The reviewer never
reuses a session.

### Envelopes

Each agent must end its answer with one fenced JSON block. The code appends the JSON schema to
every prompt, so prompt edits cannot break the contract.

```text
PlanOutput    summary, plan (markdown), files_to_touch[], acceptance[], spec_refs[], questions[]
BuildOutput   summary, changed_files[], commit_message, spec_conflict?, doc_updates[]
ReviewOutput  verdict: PASS|CHANGES, summary, prior[{id, status: fixed|not_fixed|no_longer_applies, proof}],
              findings[{severity: blocker|should_fix|nit|deepening, kind: gap|bug|verification_miss|
              scope_drift|over_engineering, file, message}], held[], followups[], doc_drift[], doc_rejects[]
DocUpdate     path, section, reason, proposal (the exact new text)
DocsOutput    summary, changed_files[], commit_message          (docs step)
```

Bad JSON or a failed soft gate → the code re-prompts **the same session** with the exact problem,
up to `pipeline.max_envelope_retries` (default 2), then `ENVELOPE_INVALID`.

### Gates

| Gate | Type | Check |
|---|---|---|
| `writes_boundary` | **hard** | files changed in this step must match the agent's `writes` and must not be protected. Violations are **reverted** and the phase fails (`WRITES_BOUNDARY`) |
| `envelope` | soft | the answer has valid JSON for the expected type |
| `diff_matches_claims` | soft | every file changed in this step is in `changed_files`; every claimed file really changed |
| `plan_non_empty` | soft | the plan text is not empty |
| `prior_checked` | soft | rounds after the first: every open finding id has a status and a proof |
| `verdict_consistent` | soft | `CHANGES` if and only if an open blocker exists (new, or a prior one `not_fixed`); over-engineering and deepening never block |
| `spec_refs_resolve` | soft | docs step only: every task's `spec:` refs still resolve after the doc edit |

Soft gate failures go back to the agent as a correction. Hard gate failures end the phase.

### 9.0 Review memory and follow-ups

**Review memory.** Every job has one review ledger (in the trace, shown as `review.md`).
- **Code gives each new finding an id** (R1, R2 …); the ids never change, across rounds and reworks.
- Every round after the first gets the open findings under "Previous review" and must check **each
  one first**: `fixed`, `not_fixed` or `no_longer_applies`, with a `file:line` or command as proof
  (gate `prior_checked`). A blocker that is `not_fixed` forces `CHANGES`, and the reviewer skips the
  fresh pass. The reviewer still starts a new session each round and never sees the builder's reasoning.
- The reviewer first writes 2–4 hypotheses from the acceptance and the plan, then checks them;
  the ones that hold go in `held`, with proof.
- The revise step gets the open findings by id: blockers as `MUST FIX`, `should_fix` as "fix if small".
- `REVIEW_CHANGES` names the open ids, e.g. `(open: R2; see review.md)`.

**Follow-ups.** When the review passes, code collects: the reviewer's explicit `followups`, plus
findings still open that are `should_fix`, `deepening` or `over_engineering` (nits are not filed).
They go to `followups.md`, and the reason gets `(+N follow-ups → followups.md)`.
`factory followups <job> --create` writes one task file each, with `depends_on: [<task>]`.
Follow-ups without acceptance fail lint until you write it. A `docs` follow-up for a file that the
doc-updates step already covers is dropped (no duplicates).

### 9.1 Doc updates (specs, plans, README)

A change can make a doc section wrong. Doc folders are protected, so the builder must not edit them.
The flow handles this in three parts: **agents propose, code records and limits, you decide.**

```text
... -> review PASS -> retest -> commit (code)
    -> doc_updates (only if something was proposed)
    -> apply mode: docs -> docs_review [-> docs_revise] -> commit_docs (a second, separate commit)
    -> awaiting-human: "ready: <sha> ... (+2 doc updates: 1 applied in <sha>, 1 proposed → doc-updates.md)"
```

1. **Propose.** The builder lists `doc_updates` (file, section, reason, exact new text). The reviewer
   adds `doc_drift` for sections it sees no longer match, and can reject a builder proposal with
   `doc_rejects`. The latest builder proposal per `path#section` wins.
2. **Record.** Code keeps a proposal only if its file exists and is inside a `project.context` folder
   or is a top-level `*.md`. Others (code, config, missing files, rejected) are dropped with a warning.
   All of it goes to `doc-updates.md`.
3. **Apply** (only with `pipeline.doc_updates: apply`). The `pipeline.docs` agent (default: the builder,
   which resumes its session and knows the change) gets each proposal with the current section text.
   For this step its `writes` are **exactly the proposed files**; anything else is reverted and the
   step fails. The `spec_refs_resolve` gate keeps task spec refs working. One docs review, at most
   one revise, then a separate docs commit. Paths in `docs_never_apply` (default `docs/decisions/`,
   the ADRs) and the repo rules files (`AGENTS.md`, `CLAUDE.md`) are never applied: they record
   decisions and rules, and changing them is your decision.
4. **Decide.** `factory approve <job>` merges code + docs. `factory approve <job> --code-only` merges
   the code commit only. `[PROPOSED]` entries stay in `doc-updates.md` for you.

| `pipeline.doc_updates` | Behavior |
|---|---|
| `propose` (default) | record proposals only; nothing is written to docs |
| `apply` | record, then the docs step and a separate docs commit |
| `off` | ignore proposals |

If the docs step fails for any reason, the doc edits are dropped, the code commit stays, and the
reason says `docs step failed`; the details are in `doc-updates.md`.

## 10. Context pack

Before every agent step, code builds `context.md`. The agent can still read more with its tools.

| Layer | Source | planner | builder | fixer / reviser | reviewer |
|---|---|---|---|---|---|
| Repo rules | `AGENTS.md`, `CLAUDE.md`, with `@file.md` imports resolved once (inside the repo only) | ✓ | ✓ | ✓ | ✓ |
| Project facts | `project.stack`, `commands`, `rules`, protected paths | ✓ | ✓ | ✓ | ✓ |
| Learnings | `.factory/learnings.md` (read from the main repo) | ✓ | ✓ | ✓ | ✓ |
| Task | the task file | ✓ | ✓ | ✓ | ✓ |
| Spec sections | task `spec:` refs (+ plan `spec_refs` for later steps) | ✓ | ✓ | — | ✓ |
| Repo map | `git ls-files`, depth 3, max 300 entries | ✓ | — | — | — |
| Plan | `plan.md` | — | ✓ | ✓ | ✓ |
| Previous step | rework note / review findings / planner answers | when set | when set | ✓ | — |
| Diff | `git diff --stat` + `git diff <base>` incl. new files, cut at `max_diff_lines` | — | — | — | ✓ |
| Test result | verbatim failing output (fixer) / summary (reviewer) | — | — | ✓ | ✓ |

**Big diffs (as sssf does):** the reviewer gets the `--stat` list of every changed file and at most
`project.max_diff_lines` (default 2,000) diff lines. When the diff is longer, the pack says
`Diff cut at 2000 of N lines. Full diff: <path>` and the agent reads `diff-review_N.patch` in the
job folder with its tools.

**The pack is the only copy of the repo rules.** Both CLIs would load rule files on their own
(verified): Pi reads `AGENTS.md`/`CLAUDE.md` (also from parent folders and globally), Claude Code reads
`CLAUDE.md` and its imports. The runners turn that off — Pi with `--no-context-files`, Claude with
`--safe-mode` — so every agent sees the same rules, nothing leaks in from outside the repo, and
`context.md` is exactly what the agent saw.

Budget: `project.context_budget_tokens` (default 40,000; about 4 characters per token). Over
budget → `CONTEXT_TOO_LARGE` with the size of each layer. Nothing is cut silently.

`.factory/learnings.md` is your manual memory: when a mistake repeats, add one short rule there.

## 11. Configuration reference (`factory.yaml`)

Pipeline steps name **agents**, never models. Change a model or runner with one line.

```yaml
defaults:                         # every agent inherits these
  runner: pi                      # pi | claude
  model: opencode-go/kimi-k3      # pi: provider/id; claude: opus | sonnet | haiku | full id
  thinking: medium                # off | minimal | low | medium | high | xhigh | max
  timeout_min: 20                 # per agent call; then RUNNER_ERROR: TIMEOUT
  tools: [read, grep, find, ls, bash]
  protected_files: [factory.yaml, .factory/, tasks/, .claude/, AGENTS.md, CLAUDE.md]   # + project.context folders

runners:
  pi:     { bin: pi, extra_args: [] }
  claude: { bin: claude, extra_args: [] }

agents:
  planner:
    runner: claude
    model: opus
    thinking: high
    tools: [read, grep, find, ls]
    writes: []                    # read-only; the plan comes back in JSON
    prompts: { system: .factory/prompts/planner/system.md, user: .factory/prompts/planner/user.md }
  builder:
    tools: [read, grep, find, ls, bash, edit, write]
    writes: ["**"]                # whole repo except protected paths
    prompts: { ... }
  reviewer:
    model: opencode-go/gpt-6-luna # other model family than the builder
    thinking: high
    writes: []
    prompts: { ... }

pipeline:
  flow: sdlc                      # file in src/kh_factory/flows/
  plan: planner                   # which agent does each step
  build: builder
  fix: builder
  review: reviewer
  max_fix_loops: 3
  max_review_loops: 2
  max_envelope_retries: 2
  doc_updates: propose            # propose | apply | off   (§9.1)
  docs: builder                   # agent for the docs step
  docs_never_apply: [docs/decisions/, AGENTS.md, CLAUDE.md]   # never applied, proposal only

project:
  stack: "Python (uv)"
  commands: { test: "uv run pytest -q", lint: "uv run ruff check ." }   # test is required
  rules: []                       # short rules every agent sees
  context: []                     # doc folders: protected from agent edits
  context_budget_tokens: 40000
  command_timeout_min: 10
  max_diff_lines: 2000            # reviewer diff lines; the full diff is a file
```

| Key | Notes |
|---|---|
| `writes` | `[]` = read-only. `"**"` = everything except protected. `dir/` = a folder. Globs work (`docs/*.md`). The job folder is always writable (it is outside the repo) |
| `tools` | factory names: `read`, `grep`, `find`, `ls`, `bash`, `edit`, `write`. For `claude` they map to `Read`, `Grep`, `Glob`, `Bash`, `Edit`, `Write` |
| `thinking` | `pi`: `--thinking`. `claude`: `MAX_THINKING_TOKENS` (assumed, not verified) |
| new agent | add an entry under `agents:` and point a `pipeline` step at it |
| new flow | add `src/kh_factory/flows/<name>.py` with `run(job, rework_note=None) -> str`, register it in `flows/__init__.py`, set `pipeline.flow` |

`factory config check` fails before any job on: unknown runner, Pi model not in `pi --list-models`,
missing prompt file, a `writes` entry that overlaps a protected path, a step that names an unknown
agent, an unknown flow, or an invalid value.

Common changes:

```yaml
agents:
  builder:  { runner: claude, model: sonnet }          # build with Claude Code instead of Pi
  reviewer: { model: opencode-go/deepseek-v4-pro }     # another reviewer model
defaults:
  model: opencode-go/glm-5.3                           # one model for every Pi agent
pipeline:
  max_fix_loops: 5
```

## 12. Runners

| | `pi` | `claude` |
|---|---|---|
| Command | `pi -p --mode json --session-dir … --session-id … --model … --thinking … --tools … --append-system-prompt …` | `claude -p --output-format stream-json --verbose --model … --permission-mode acceptEdits (--session-id \| --resume) … --append-system-prompt … --allowedTools …` |
| Prompt | stdin | stdin (`--allowedTools` takes all following args, so it is last) |
| Own rule files | off: `--no-context-files` | off: `--safe-mode` (also no user skills, plugins, hooks, output styles; auth, models, tools and permissions work normally) |
| Final answer | last assistant `message_end` | the `result` event |
| Error signal | `stopReason: "error"` + `errorMessage` | `is_error: true` (`subtype` can still say `success`) |
| Resume | same `--session-id` | `--resume <id>` |
| Cost | sum of `usage.cost.total` | `total_cost_usd` |
| Context used | last valid turn's `usage.totalTokens` (an errored turn does not count) | last `assistant` event: input + cache writes + cache reads + output |
| Context limit | `context` column of `pi --list-models` (cached) | `result.modelUsage.<model>.contextWindow` |

**Both CLIs exit `0` on model errors** (verified). The runners never trust the exit code.
Timeout per call: `timeout_min`. Raw streams go to `steps/*/raw.jsonl`.

Smoke test: `factory try planner "Reply with one word: ready"` (prints cost and `ctx …/…`).

**Timeouts.** sssf has no agent timeout (it records the process id so you can kill it). kh-factory
keeps `timeout_min` per agent call and `command_timeout_min` per test/lint command. Limit: on a
timeout only the agent CLI process is killed; a command it started with `bash` can keep running.

## 13. Safety model

| Risk | Protection |
|---|---|
| Agent edits its own grader or rules (config, prompts, tasks, skill, `AGENTS.md`, `CLAUDE.md`) | protected paths; changes reverted, phase fails |
| Agent edits specs/ADRs to fit its code | `project.context` folders protected; `spec_conflict` stops the job; doc changes only as proposals or, in `apply` mode, a separate docs commit limited to the proposed files; ADRs never applied |
| Reviewer "fixes" instead of reviewing | `writes: []`; any change reverted |
| Agent claims work it did not do | `diff_matches_claims`; tests run by code |
| Bad code reaches main | commit only on the job branch; merge only by `factory approve`; never push |
| Your dirty main checkout | jobs use worktrees from the last commit; the skill commits only its task file |

**Limit:** Pi has no sandbox. A write outside `writes` happens and is then reverted; it is not
blocked while it happens. `bash` can run any command. Run the factory on repos and machines where
that is acceptable.

## 14. Failure codes and what to do

| Code | Meaning | What to do |
|---|---|---|
| `RUNNER_ERROR` | CLI failed, timed out, or the model is unknown/unavailable | `factory try <agent> "hi"` shows the raw error; check login and model name |
| `WRITES_BOUNDARY` | agent changed a path it may not change; reverted | read the path in the reason; fix the task or prompt; `resume` |
| `ENVELOPE_INVALID` | no valid JSON after retries | `resume`, or switch that agent's model |
| `TESTS_FAILED` | tests still fail after fix rounds | read `test_*.txt`; `rework` with a hint, or improve acceptance |
| `REVIEW_CHANGES` | reviewer still blocks after review rounds | read the last review `envelope.json`; `rework` or `reject` |
| `NO_TEST_COMMAND` | `project.commands.test` is not set | set it, commit, `resume` |
| `SPEC_REF_NOT_FOUND` | a `spec:` ref has no file or heading | fix the ref, commit, `factory run <task>` (a new job starts from the new commit) |
| `CONTEXT_TOO_LARGE` | pack over budget | narrow spec refs or raise `context_budget_tokens` |
| `NO_CHANGES` | nothing to commit | the work already exists or the task is empty; `reject` |
| `INTERNAL_ERROR` | a factory bug or environment problem | read `<job folder>/error.txt` |
| `STOPPED: planner has questions` | planner needs a decision | `factory rework <job> "<answers>"` |
| `STOPPED: spec conflict` | build would break a spec | decide; `factory rework <job> "<decision>"` |
| `task … fails lint` (on `run`/`next`) | the task has lint errors | fix the task file, commit, run again (`factory lint <ID>`) |

### Troubleshooting

| Symptom | Fix |
|---|---|
| `factory: command not found` | `uv tool install -e ~/Desktop/setup/kh-factory`; new terminal |
| `not inside a git repository` | `git init -b main` |
| `the repo has no commits yet` | commit once (`factory.yaml`, `tasks/`) |
| `task ... is already awaiting-human` | approve/reject that job, or `--force` |
| `depends on unfinished task(s)` | approve the dependency first, or `--force` |
| job shows `stalled` | the process died or `Ctrl+C`; `factory resume <job>` |
| `check out 'main' ... first` | `git checkout main`, then approve |
| merge conflict on approve | resolve on the branch in the worktree, commit there, approve again; or reject and rerun |
| `/factory` missing in Claude Code | `factory install-skill`; start a new session |
| UI empty | run `factory ui` inside the repo |
| job uses an old task text | jobs start from the last commit; commit the task edit |

## 15. Cost

Measured on the toy task T-001 (Opus planner, kimi-k3 builder, gpt-6-luna reviewer): about
**$0.34** per job, 2–3 minutes, including one review round with a revision. `factory status` and
the UI show cost per job (the sum of what each runner reports). Planner and reviewer run once per
round; the builder's cost grows with fix and revise rounds.

To spend less: use `sonnet` or a Pi model for the planner; lower `max_fix_loops`; write tighter tasks.

## 16. Testing the factory

- **Unit and flow tests** (no model calls; fake runner, temporary git repos): `uv run pytest -q`
  → 62 pass. They cover the happy path to approve, fix loops, write-boundary reverts, claim
  re-prompts, bad JSON, planner questions and rework, review → revise → retest, bad spec refs,
  missing test command, runner errors, dependencies, reject, the importer, the CLI parsers, and
  `install-skill` / `init --with-skill`, context measurement, diff cutting, the trace migration, and doc updates (propose, drop, apply,
  `--code-only`, ADRs never applied, reviewer rejects, spec refs kept), reviewer memory (ids, prior
  checks, not-fixed blockers, memory across rework), follow-ups, and every lint check.
- **Manual end-to-end:** [TESTING.md](TESTING.md) (toy repo, Parts 0–9) or
  [NEW-PROJECT-GUIDE.md](NEW-PROJECT-GUIDE.md) (your own folder).

## 17. Developing kh-factory

```text
src/kh_factory/
  cli.py          commands; init; install-skill
  config.py       factory.yaml schema, defaults merge, checks
  tasks.py        task files; card import
  jobs.py         job lifecycle: start, execute, resume, rework, approve, reject, next_task
  pipeline.py     Job, phase(), call_agent(): context.md, prompts, retries, gates, trace
  flows/sdlc.py   the v1 step order
  context.py      context pack layers, spec cutter, budget
  envelope.py     PlanOutput / BuildOutput / ReviewOutput, JSON extraction
  gates.py        writes boundary (snapshot + revert), claims
  quality.py      test/lint commands
  worktree.py     git helpers, worktrees, excludes, merge
  trace.py        SQLite: jobs, phases, events, envelopes, gate_results, sessions
  runners/        base (process, stream, timeout), pi, claude, fake
  ui/             server.py (stdlib HTTP), index.html (vanilla JS)
  templates/      factory.yaml, prompts, learnings.md, skill/SKILL.md
tests/            conftest.py (temp repo + fake runner), test_units.py, test_flow.py
examples/         toy/ + setup-toy.sh
```

- **Add a runner:** subclass `runners/base.Runner` (`command`, `parse`, optional `env`), register
  it in `runners/__init__.build`, add the name to `config.RUNNER_NAMES` and the `Agent.runner` literal.
- **Add a gate:** a function returning `GateResult`; pass it in `soft_gates=` of `call_agent`.
- **Change a prompt for one repo:** edit `<repo>/.factory/prompts/...`. For new repos: edit
  `templates/prompts/...`.
- **Change the skill:** edit `templates/skill/SKILL.md`. The user-level symlink picks it up at once;
  project copies need `factory install-skill --project --force`.

## 18. Limits and what comes later

Limits in v1:
- One job per command, in the foreground. Parallel = several terminals.
- Pi has no sandbox; the writes rule is enforced after each step by reverting.
- Claude `thinking` → `MAX_THINKING_TOKENS` is assumed, not verified.
- Task status lives in the trace; deleting `.factory/trace.db` forgets which tasks are done.

Later:
- GitHub adapter: issues → tasks, pull requests instead of local merges (needs `gh`).
- Run on Nizk: `factory import docs/planning/TASKS.md`, start with FND-01.
- A scheduler for parallel jobs with `depends_on`; scout and documenter agents; cost limits per job.

## 19. Decisions in short

- Generic factory for any repo; Python + uv; our own small code (sssf patterns, not its code).
- Markdown task files; sssf `adw_simple_sdlc` subset; tests run as code; a human approves every merge.
- Runners: Pi (main, opencode-go models) + `claude -p` (Opus planner). One worktree + branch per job.
- Code builds the context pack; agents look up more with tools.
- Generic `factory.yaml`: steps name agents, never models; step order in `flows/*.py`.
- `/factory` skill (zazencodes structure) as the front door; the CLI runs the loop.
- Skill: user-level symlink by default; project copy on request; `.claude/` protected.

Full reasoning for each: [DECISIONS-QA.md](DECISIONS-QA.md).
