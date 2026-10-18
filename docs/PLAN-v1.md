# Plan: kh-factory v1 — a small, generic software factory

## Context

`kh-sf` holds the Nizk design (docs, ADRs, specs, `docs/planning/TASKS.md`), with no code yet. The user
wants a simple software factory that automates their own work on any repo. Inputs studied:

- **disler/super-simple-software-factory (sssf):** Python controls the flow, agents do bounded steps
  ("agent proposes, code disposes"). Typed JSON envelopes, gates, `writes` boundary, tests run as code,
  SQLite trace. Pi only (`agent_cc.py` is a stub). No branch or worktree isolation.
- **zazencodes software-factory-claude-code:** `/factory` skill, subagents, one git worktree + branch per
  job, `job.json`, markdown backlog, polling dashboard, human approve/rework commands.

Local tools (verified): `pi` 1.3.0 with `opencode-go` auth (kimi-k3, glm-5.3, deepseek-v4-pro, gpt-6-luna,
qwen3.8-max …), `claude` 2.1.302, `uv`. Not installed: `gh`, `just`.

## Decisions

| Topic | Decision |
|---|---|
| Scope | Generic factory for any repo. Not tied to Nizk. |
| Control | Hybrid: a Python script owns flow, state, and gates. A thin Claude Code skill is an optional entry point. |
| Code | Own small version (~1,200–1,600 lines incl. UI). Copy sssf patterns, not its code. |
| Location | New repo `~/Desktop/setup/kh-factory` (path can change). `factory init` installs into a target repo. |
| Users | Local, one user now. State in files + SQLite so a team move (GitHub issues/PRs) is a later adapter. |
| Work input | One markdown task file per task in the target repo: `tasks/<id>.md`. |
| Runners | Two runners behind one interface: `pi` (opencode-go models) and `claude` (`claude -p`, subscription). Chosen per agent in config. |
| Isolation | One git worktree + branch `factory/<job-id>` per job (zazencodes). |
| Merge | Human approves every merge. Factory never merges or pushes. |
| Visibility | SQLite event trace + a small read-only web UI. |
| Script lang | Python 3.12 + `uv` (PEP 723 inline deps). |
| First proof | A toy repo (small Python app with pytest), shipped as `examples/toy/`. |

## Pipeline v1 (adapted from sssf `adw_simple_sdlc`)

```text
task.md -> plan (agent) -> build (agent) -> test (code) <-> fix (agent, max 3)
        -> review (agent, other model family) <-> revise (agent, max 2) -> retest (code)
        -> commit on factory/<job-id> -> status: awaiting-human
human:  factory approve <job>  (merge to base branch locally)
        factory rework <job> "note"  (re-enter build with the note)
        factory reject <job>   (remove worktree, keep trace)
```

- Dropped from sssf for v1: scout, documenter, Pi subagent extensions, 11 other workflows.
- Tests and lint are **code**, from commands in `factory.yaml`. No tester agent.
- Every phase defaults to failed; code marks success.

## Two project modes

Same pipeline for both. Only the planner's input changes.

**Fresh project (no docs):**
1. `factory init` writes `factory.yaml` with empty `context:` and empty test commands.
2. User runs `factory add "<idea>"`; it writes `tasks/<id>.md` from the template (user fills acceptance).
3. Planner reads task + repo tree only. Task 1 should be "bootstrap + test command".
4. If no test command exists, the test gate fails with `NO_TEST_COMMAND` and the job stops for the human.

**Project with docs (Nizk style):**
1. `factory.yaml` lists `context:` paths (e.g. `docs/specs/`, `docs/decisions/`, `docs/architecture/`).
2. `factory import <file>` converts structured task cards (e.g. `docs/planning/TASKS.md`) into `tasks/*.md`,
   keeping `Depends on`, `Spec`, `Acceptance`, `Tests`.
3. Planner reads the task + only the spec sections the task names (not all docs).
4. Reviewer checks the diff against the task's acceptance and the named spec sections.
5. Docs paths are protected. If the build must contradict a spec, the builder sets
   `spec_conflict` in its envelope; the job stops as `awaiting-human`. Agents never silently edit ADRs/specs.
6. `factory next` picks the first task whose `depends_on` are all `done`.

## Why `claude -p` and not Claude Code subagents as a runner

Subagents are spawned by a parent model, so the flow, loop limits, and gates depend on that model obeying a
prompt. They run only Claude models, cannot be traced per event into SQLite from code, and cannot be resumed
per phase from a script. `claude -p` is a process the Python code starts, times out, parses, and resumes.
Subagents stay useful in two places: a thin `/factory` skill as the human front door (it calls `factory.py`),
and helpers *inside* a builder session (allowed later).

## Agent roster (default `factory.yaml`, editable)

| Agent | Runner / model (default) | writes | Output envelope |
|---|---|---|---|
| planner | `claude` / opus | `.factory/` job folder only | `PlanOutput{plan_path, summary, files_to_touch, acceptance[]}` |
| builder | `pi` / `opencode-go/kimi-k3` | repo (except protected files) | `BuildOutput{summary, changed_files[], commit_message}` |
| reviewer | `pi` / `opencode-go/gpt-6-luna` (different family from builder) | none | `ReviewOutput{verdict: PASS\|CHANGES, findings[]}` |

Protected files (no agent may write): `factory.yaml`, `.factory/prompts/`, test commands config.
The user can swap any runner/model per agent with one config line.

## Config file (`factory.yaml`) — generic, like sssf

Rule (from sssf): **pipeline steps name agents, never models.** Changing a model, runner, tool, or prompt
is a config edit, not a code change.

```yaml
defaults:
  runner: pi                      # pi | claude
  model: opencode-go/kimi-k3      # provider/id
  thinking: medium                # off | low | medium | high | max
  timeout_min: 20
  tools: [read, grep, find, ls, bash]
  protected_files: [factory.yaml, .factory/prompts/]

runners:                          # how each CLI is started
  pi:     { bin: pi, extra_args: [] }
  claude: { bin: claude, extra_args: [] }

agents:
  planner:
    runner: claude
    model: opus
    thinking: high
    tools: [read, grep, find, ls, write]
    writes: [.factory/jobs/]
    prompts: { system: .factory/prompts/planner/system.md, user: .factory/prompts/planner/user.md }
  builder:                        # uses defaults: pi + kimi-k3
    tools: [read, grep, find, ls, bash, edit, write]
    writes: ["**"]                # whole repo except protected_files
    prompts: { system: .factory/prompts/builder/system.md, user: .factory/prompts/builder/user.md }
  reviewer:
    model: opencode-go/gpt-6-luna # different family from the builder
    thinking: high
    writes: []                    # read-only
    prompts: { system: .factory/prompts/reviewer/system.md, user: .factory/prompts/reviewer/user.md }

pipeline:
  flow: sdlc                      # selects factory/flows/sdlc.py
  plan: planner
  build: builder
  fix: builder
  review: reviewer
  max_fix_loops: 3
  max_review_loops: 2

project:                          # see "Context pack"
  stack: "Python 3.12, pytest"
  commands: { test: "uv run pytest -q", lint: "uv run ruff check ." }
  context: []
  context_budget_tokens: 40000
```

What changes without code:

| Change | Where |
|---|---|
| Model / thinking for one agent | `agents.<name>.model` / `thinking` |
| Pi ↔ Claude Code for one agent | `agents.<name>.runner` |
| One model for all agents | `defaults.model` |
| Tools / write paths | `agents.<name>.tools` / `writes` |
| Prompt text | `.factory/prompts/<agent>/*.md` |
| Which agent does a step | `pipeline.<step>` |
| Loop limits, timeouts | `pipeline.max_*`, `defaults.timeout_min` |
| New agent (e.g. `reviewer-security`) | new entry under `agents:` |

What stays in code: **the order of steps.** Each flow is one Python file in `factory/flows/` (v1 ships
`sdlc.py`). A new flow (e.g. `quick.py`: build → test) is a new file selected by `pipeline.flow`.
Reason: a YAML-defined flow becomes a small language that is hard to debug (same choice as sssf ADWs).

Validation at load (fail before any job starts, with a clear message):
- unknown runner name;
- Pi model not in `pi --list-models`;
- an agent's `writes` overlaps `protected_files`;
- a prompt file is missing;
- a `pipeline.<step>` names an agent that does not exist, or `pipeline.flow` has no file.
- **Verified (2026-10-18):** both CLIs exit with code `0` on a bad model. `claude -p --output-format json`
  returns `"is_error": true`, `"api_error_status": 404`, `"terminal_reason": "api_error"` (and `subtype` still says
  `"success"`). `pi -p` prints `Warning: Model ... not found` and a `400` error text. So runners **must not** trust
  the exit code: `claude.py` checks `is_error`; `pi.py` checks the model with `pi --list-models` before the run and
  parses error events. Any runner error ends the phase as `RUNNER_ERROR` with the CLI's message.

## Context pack (how agents get good context)

Rule: **code pushes a small, exact context; the agent pulls more with its tools** (`read`, `grep`, `ls`).
Before every agent step, `factory/context.py` builds `context.md` and saves it in
`.factory/jobs/<id>/<step>/context.md`, so you can always see exactly what the agent saw.

```text
AGENTS.md/CLAUDE.md ─┐
       factory.yaml ─┤
            task.md ─┼──> context.py ──> context.md ──> agent ──> envelope.json ──> next step
      spec sections ─┤
           repo map ─┤
  previous envelope ─┘
```

| # | Layer | Source |
|---|---|---|
| 1 | Repo rules | `AGENTS.md` / `CLAUDE.md` in the target repo (always injected by code, so both runners see the same rules) |
| 2 | Project facts | `factory.yaml` `project:` block: stack, `test` / `lint` / `build` / `run` commands, rules |
| 3 | Task | `tasks/<id>.md`: objective, acceptance, `Spec:` refs |
| 4 | Spec sections | Only the sections the task names, cut by heading (e.g. `docs/specs/SCHEDULING.md#6`) |
| 5 | Repo map | File tree (depth-limited, gitignore-aware) + recently changed files |
| 6 | Previous step | `plan.md`, test output, or review findings — only the step just before |
| + | Learnings | `.factory/learnings.md`, written by the human, included in every prompt |

Per agent:

| Agent | Gets | Session |
|---|---|---|
| planner | layers 1–5 + learnings | new |
| builder | 1, 2, 3, full `plan.md`, spec sections the plan names | new |
| fixer / reviser | exact test output or review findings | **resumes the builder session** |
| reviewer | 1, 3, plan, `git diff`, spec sections, test result (not the builder's reasoning) | **always new** |

`factory.yaml` `project:` block example:

```yaml
project:
  stack: "Python 3.12, pytest"
  commands:
    test: "uv run pytest -q"
    lint: "uv run ruff check ."
  rules:
    - "Do not add new dependencies without saying so in the envelope."
  context: []            # doc folders, e.g. [docs/specs, docs/decisions]
  context_budget_tokens: 40000
```

Context failures:
- Planner lacks information → returns `questions[]` → job goes `awaiting-human`. The builder never guesses.
- Task names a spec section that does not exist → fail before any agent runs with `SPEC_REF_NOT_FOUND`.
- Pack exceeds `context_budget_tokens` → fail with `CONTEXT_TOO_LARGE`; nothing is cut silently.
- No test command → test gate fails with `NO_TEST_COMMAND`.

Risk: context quality depends on `AGENTS.md`, the `project:` block, and task acceptance. A weak task gives a weak build.

## Repo layout (kh-factory)

```text
kh-factory/
  factory.py              CLI entry (uv script): init, add, run, next, status, approve, rework, reject, ui
  factory/
    config.py             load + validate factory.yaml (pydantic): defaults merge, runners, agents, pipeline, project
    flows/sdlc.py         the v1 step order; more flows are more files
    tasks.py              parse tasks/*.md (frontmatter: id, title, status, depends_on)
    jobs.py               job lifecycle + state machine, job folder .factory/jobs/<id>/
    worktree.py           git worktree add/remove, branch, diff, commit
    runners/base.py       Runner protocol: run(agent, prompt, cwd, session_id) -> RunResult(text, events, session_id)
    runners/pi.py         `pi -p --mode json --model ... --tools ... --session-id ...`
    runners/claude.py     `claude -p --output-format stream-json --model ... --resume ... --allowedTools ...`
    context.py            build context.md per step: repo rules, project facts, task, spec sections, repo map, previous envelope, learnings; token budget
    envelope.py           extract final JSON, validate against pydantic type, re-prompt same session on failure (max 2)
    gates.py              artifacts_exist, json_valid, diff_matches_claims, writes_boundary (rollback on violation)
    quality.py            run test/lint commands from config, capture output as an envelope for fix loop
    pipeline.py           shared step runner used by every flow: phase, envelope, gates, trace
    trace.py              SQLite (WAL): jobs, phases, events, envelopes, gate_results
    ui/server.py          stdlib http.server + JSON endpoints over SQLite
    ui/index.html         single page, vanilla JS, polls every 1s: job board + phase timeline + event log
  templates/              factory.yaml, prompts/{planner,builder,reviewer}/{system,user}.md, task.md
  examples/toy/           tiny Python app + pytest + 3 sample tasks
  tests/                  pytest for config, tasks, envelope, gates, state machine (runners faked)
```

Target repo after `factory init`:

```text
factory.yaml   tasks/   .factory/  (prompts/ committed; jobs/ and trace.db gitignored)
```

## Failure behavior

- Agent returns bad JSON: re-prompt the same session with the parse error, max 2, then phase fails.
- Agent writes outside `writes`: changes are reverted in the worktree, the phase fails.
- Runner crash or timeout (default 20 min/phase): phase fails, job goes to `failed`, worktree kept for inspection.
- Factory process killed mid-job: job row stays `running` with a stale heartbeat; `factory status` shows it
  as `stalled`; `factory run <job> --resume` restarts from the last successful phase.
- Two jobs at once: each has its own worktree and branch; SQLite WAL handles concurrent writers.
- Source of truth: SQLite for job state; job folder for artifacts (plan.md, envelopes, logs); git for code.

## Build order (each step is runnable and tested before the next)

1. Repo skeleton, `factory.yaml` schema (defaults merge + all load checks except the Pi model check), task
   parser, `factory init` / `add` / `status` / `config check`. (~0.5–1 day)
2. Runner interface + `pi` runner + `claude` runner; a `factory try <agent> "<prompt>"` smoke command. (~1 day)
3. Context pack (`context.py`, `project:` block, spec-section cutter, budget) + envelope parsing + re-prompt;
   gates; writes-boundary check with rollback. (~1.5 days)
4. Worktree module + quality runner + full pipeline + approve/rework/reject. (~1–1.5 days)
5. SQLite trace wired into every phase and event. (~0.5 day)
6. Web UI (`factory ui`, port 4600). (~1 day)
7. Toy repo example + docs (README with quick start). (~0.5 day)

Estimate: 5.5–6.5 working days with an agent. Depends mostly on the JSON event formats of `pi --mode json` and
`claude --output-format stream-json` (step 2 confirms them).

## Verification

- `uv run pytest` in kh-factory: unit tests with fake runners (no model calls).
- `factory try builder "say hi"` and `factory try planner "say hi"`: both runners return text and a session id.
- End to end on `examples/toy`: `factory init`, `factory next` → job reaches `awaiting-human` with a green
  test run, a PASS review, and one commit on `factory/<job-id>`.
- Negative test: a task with impossible acceptance → fix loop runs 3 times, job ends `failed`, base branch unchanged.
- Config test: change `agents.reviewer.model` and `runner` → next run uses them with no code change;
  a bad model, missing prompt, or `writes` overlap fails `factory config check` with a clear message.
- Context test: each step folder has a `context.md`; the reviewer's pack has the diff and no builder reasoning;
  a task with a bad `Spec:` ref fails with `SPEC_REF_NOT_FOUND` before any model call.
- Boundary test: prompt reviewer to edit a file → change reverted, gate fails.
- `factory ui`: open `http://localhost:4600`, see the job board and live events during a run.
- `factory approve <job>` merges locally; `git log` on base branch shows the commit; worktree removed.

## Later (not v1)

- GitHub adapter: tasks from issues, PR instead of local merge (needs `gh`).
- Import Nizk `TASKS.md` cards as task files; run on Nizk FND-01.
- Parallel job scheduler with `depends_on`; scout and documenter agents; cost tracking per job.

## Implementation status (2026-10-18)

All 7 build steps are implemented in `src/kh_factory/` (about 2,200 lines of Python, 150 lines of HTML for the UI, and 480 lines of tests).
- `uv run pytest -q`: 29 tests pass. They use a fake runner and temporary git repos.
- Real end-to-end run on `examples/toy` task T-001 (planner `claude`/opus, builder `pi`/kimi-k3, reviewer `pi`/gpt-6-luna):
  9 phases passed, including one claims re-prompt and one review → revise → review → retest loop. Cost about $0.34.
  `factory approve` merged the branch and removed the worktree.
- `factory import` on Nizk `docs/planning/TASKS.md` (dry run): 98 cards, 188 spec refs, 0 unresolved.

Changes from the plan, made during the build:
- The planner returns the plan text in `PlanOutput.plan`, and code writes `plan.md`. The planner is read-only (`writes: []`).
- Default `protected_files` is `[factory.yaml, .factory/, tasks/]`, plus the folders in `project.context`.
- Task status is derived from the job history in SQLite. It is not stored in the task file, so the factory never makes the repo dirty.
- Fix/revise steps resume the builder session. The reviewer gets a new session each round.
- Cache paths (`__pycache__/`, `.venv/`, `node_modules/` …) go to `.git/info/exclude`, so they never count as agent changes.
  Quality commands run with `PYTHONDONTWRITEBYTECODE=1`.
- Prompts go on stdin. Reason: Claude's `--allowedTools` flag takes every following argument as a tool name.
- Added after v1: the `/factory` Claude Code skill (`templates/skill/SKILL.md`), `factory install-skill`
  (user symlink or project copy), `factory init --with-skill`, and `.claude/` in the default protected paths.
  Tests: 32 pass.
- Added from sssf: context use per agent call (tokens in window vs model limit, shown in `factory show`
  and the UI, warning over 80%) and diff cutting for the reviewer (`project.max_diff_lines`, default 2000,
  full diff saved as a file). Kept (sssf has none): agent timeout and the pre-call context budget. Tests: 38 pass.
- Added: doc updates inside `sdlc` (`doc_updates` / `doc_drift` / `doc_rejects`, `doc-updates.md`,
  `pipeline.doc_updates: propose|apply|off`, docs step limited to the proposed files, separate docs
  commit, `approve --code-only`, ADRs never applied). Tests: 46 pass.
