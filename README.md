# kh-factory

A small software factory for any git repo. **Agent proposes, code disposes:** Python code runs the
flow, checks every claim, and runs the tests. Agents (Pi or Claude Code) do one bounded step each.
A human approves every merge.

```text
tasks/T-001.md -> plan (agent) -> build (agent) -> test (code) <-> fix (agent, max 3)
               -> review (agent, other model) <-> revise (agent, max 2) -> retest (code)
               -> commit on branch factory/<job-id> -> awaiting-human
you:           factory approve <job> | factory rework <job> "note" | factory reject <job>
```

Design and decisions: [docs/PLAN-v1.md](docs/PLAN-v1.md), [docs/DECISIONS-QA.md](docs/DECISIONS-QA.md).
**Everything in one place: [docs/HANDBOOK.md](docs/HANDBOOK.md).** Step-by-step tests: [docs/TESTING.md](docs/TESTING.md). New empty project: [docs/NEW-PROJECT-GUIDE.md](docs/NEW-PROJECT-GUIDE.md).

## Install

Needs `git`, `uv`, and at least one agent CLI: `pi` (with a provider login) and/or `claude` (Claude Code).

```bash
uv tool install -e ~/Desktop/setup/kh-factory    # puts `factory` on your PATH
```

## Quick start (toy repo)

```bash
~/Desktop/setup/kh-factory/examples/setup-toy.sh ~/tmp/toy
cd ~/tmp/toy
factory config check            # roster + checks every Pi model with `pi --list-models`
factory try builder "say hi"    # smoke test one agent (no worktree, no gates)
factory next                    # runs T-001 end to end
factory status                  # tasks and jobs
factory ui                      # http://localhost:4600 — live job board, phases, gates, events
factory approve <job-id>        # merge the job branch into the branch it started from
```

## Use it in your own repo

```bash
cd my-repo                      # must be a git repo with at least one commit
factory init                    # writes factory.yaml, .factory/prompts/, tasks/, .gitignore entries
# edit factory.yaml: project.commands.test is required
factory add "Add login rate limit"     # creates tasks/T-001-add-login-rate-limit.md; fill in Acceptance
git add -A && git commit -m "factory setup"
factory next
```

A repo with docs and task cards (for example Nizk):

```bash
factory import docs/planning/TASKS.md  # '### ID — Title' cards with '- **Field:** value' bullets
# in factory.yaml: project.context: [docs]   (doc folders are protected; agents never edit them)
```

Each imported task keeps `depends_on` and `spec` refs (`docs/specs/X.md#6`). The code cuts only those
sections into the agent's context. A ref that does not resolve fails the job before any model call.

## Use it inside Claude Code (`/factory` skill)

```bash
factory install-skill            # you: symlink ~/.claude/skills/factory/SKILL.md -> kh-factory's template (all repos, always current)
factory install-skill --project  # team: copy into <repo>/.claude/skills/factory/SKILL.md, then commit it
factory init --with-skill        # init + the project copy in one step
```

Keep one copy per machine: the user-level symlink, or project copies. `.claude/` is a protected path,
so factory agents cannot edit the skill during a job.

Then, in a Claude Code session inside a repo that has `factory.yaml`:

| You type | Claude does |
|---|---|
| `/factory add a shout(name) function` | writes `tasks/T-00N-*.md` with checkable acceptance, commits only that file, runs the job in the background, reports |
| `/factory next` · `/factory status` · `/factory show <job>` | runs the CLI and summarizes |
| `/factory rework <job> <note>` | passes your note (also answers planner questions) |
| `/factory approve <job>` · `/factory reject <job>` | only when you ask — Claude never approves on its own |

The CLI still runs the loop (code decides). The skill is the front door: it writes tasks, starts
jobs, reads `plan.md`, the review and the diff, and reports. Structure adapted from the zazencodes
`/factory` skill; its subagent loop is replaced by the `factory` CLI.

## Commands

| Command | What it does |
|---|---|
| `factory init [--force] [--with-skill]` | Install into the current repo; detects Python/Node/Go/Rust test commands |
| `factory config check [--no-models]` | Validate `factory.yaml` |
| `factory add "<title>"` | New task file from the template |
| `factory import <file>` | Convert task cards into task files |
| `factory status` | Tasks (status from job history) and recent jobs with cost |
| `factory run <task> [--force]` / `factory next` | Start a job for one task / the next runnable task |
| `factory show <job>` | Phases, gates, files of one job |
| `factory resume <job>` | Continue a failed or stalled job (reuses the plan and the builder session) |
| `factory rework <job> "<note>"` | Send back to the builder with a note (or answer the planner's questions) |
| `factory approve <job> [--code-only]` / `factory reject <job>` | Merge locally (optionally without the docs commit) / drop worktree and branch |
| `factory try <agent> "<prompt>"` | Smoke test one agent |
| `factory ui [--port 4600]` | Read-only web UI over the trace |
| `factory install-skill [--project] [--copy] [--force]` | Install the `/factory` Claude Code skill (user: symlink; project: copy) |

## How it works

- **Config** (`factory.yaml`): agents set `runner`, `model`, `thinking`, `tools`, `writes`, `prompts`;
  `defaults:` fills the rest. Pipeline steps name agents, never models. The step order lives in
  `src/kh_factory/flows/<flow>.py` (v1: `sdlc`).
- **Runners**: `pi -p --mode json` and `claude -p --output-format stream-json`, prompt on stdin, session id
  pinned so fix/revise steps resume the builder's session. Both CLIs exit `0` on model errors, so the
  runners read `stopReason` / `is_error` instead of the exit code.
- **Context pack**: before each agent step, code writes `context.md`: repo rules (`AGENTS.md`/`CLAUDE.md`),
  project facts, learnings (`.factory/learnings.md`), the task, the named spec sections, plan, diff or test
  output. Budget: `project.context_budget_tokens`.
- **Envelopes**: each agent ends with one JSON block (`PlanOutput`, `BuildOutput`, `ReviewOutput`). Bad JSON
  or a failed claim check re-prompts the same session (max `pipeline.max_envelope_retries`).
- **Gates**: `writes_boundary` (hard: changes outside `writes` or in protected paths are reverted and the step
  fails), `diff_matches_claims`, `verdict_consistent`, `plan_non_empty`. Tests and lint are code (`quality.py`).
- **Isolation**: one git worktree per job at `../<repo>-factory/<job-id>` on branch `factory/<job-id>`.
  The factory never pushes and never merges without `factory approve`.
- **Context use**: after each agent call, code records tokens in the model's window vs its limit
  (`factory show`: `ctx 38k/1M (4%)`; warning over 80%). The reviewer's diff is cut at
  `project.max_diff_lines` (2000); the full diff is a file it can read. Both ideas come from sssf.
- **Doc updates**: the builder proposes `doc_updates` and the reviewer `doc_drift`; code keeps only doc
  paths and writes `doc-updates.md`. `pipeline.doc_updates: apply` adds a docs step limited to the
  proposed files and a separate docs commit (`approve --code-only` drops it). ADRs are never applied.
- **State**: `.factory/trace.db` (SQLite, WAL) holds jobs, phases, events, envelopes, gates, sessions.
  `.factory/jobs/<id>/` holds `plan.md`, each step's `context.md`, prompts, answers, raw streams.

## Failure codes

| Code | Meaning |
|---|---|
| `RUNNER_ERROR` | CLI failed, timed out, or the model is unknown/unavailable |
| `WRITES_BOUNDARY` | Agent changed a path it may not write; changes reverted |
| `ENVELOPE_INVALID` | No valid JSON answer after retries |
| `TESTS_FAILED` | Tests still fail after the fix rounds |
| `REVIEW_CHANGES` | Reviewer still blocks after the review rounds |
| `NO_TEST_COMMAND` | `project.commands.test` is not set |
| `SPEC_REF_NOT_FOUND` / `CONTEXT_TOO_LARGE` | Context pack problems, found before any model call |
| `STOPPED: planner has questions` / `spec conflict` | Waiting for you: answer with `factory rework` |

## Development

```bash
uv sync && uv run pytest -q     # fake runner, temporary git repos, no model calls
```

## Known limits (v1)

- One job runs in the foreground per command. Parallel jobs work from separate terminals (separate worktrees).
- Pi has no sandbox: `writes` is enforced after the step by reverting, not by blocking the write.
- Claude `thinking` is passed as `MAX_THINKING_TOKENS` (assumed; not verified).
- No GitHub adapter yet (issues as tasks, PRs instead of local merge).
