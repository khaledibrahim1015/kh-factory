# kh-factory — questions and answers (planning session, 2026-10-18)

This file records every question asked while planning kh-factory v1, the answer, and why.
The plan itself is in [PLAN-v1.md](PLAN-v1.md).

## Research inputs

| Source | What it is | What we take |
|---|---|---|
| [disler/super-simple-software-factory](https://github.com/disler/super-simple-software-factory) (sssf) | Python scripts ("ADWs") run agents through the Pi CLI. "Agent proposes, code disposes." | Typed JSON envelopes, gates, `writes` boundary, tests run as code, bounded fix/review loops, SQLite trace |
| [zazencodes software-factory-claude-code](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code) | `/factory` Claude Code skill + 7 subagents, markdown files | One git worktree + branch per job, human approve/rework commands, job board |

Facts checked on this machine:
- `pi` 1.3.0 with `opencode-go` login. Models include `kimi-k3`, `glm-5.3`, `deepseek-v4-pro`, `gpt-6-luna`, `qwen3.8-max`, `claude-haiku-5-5`.
- `claude` 2.1.302 (Claude Code), `uv` installed. `gh` and `just` are not installed.
- sssf `agent_cc.py` (Claude Code runner) is a stub. sssf has no branch or worktree isolation.
- sssf's most complete workflow is `adw_simple_sdlc.py`: plan → build → test ↔ fix (max 3) → review ↔ revise (max 2) → retest → commit → document.

---

## Round 1 — the shape of the factory

**Q1. What must the factory produce first?**
- Options: build Nizk from `TASKS.md` / generic for any repo / maintain the docs.
- **Answer: Generic, any repo.**
- Note: Nizk is a good later target because its task cards are already well formed.

**Q2. Who controls the flow between steps?**
- Options: code runs agents (sssf) / a skill runs agents (zazencodes) / hybrid.
- **Answer: Hybrid, but more reusable and simple.**
- Result: a Python script owns flow, state, and gates. A thin `/factory` skill can call it later.

**Q3. Who uses the factory?**
- Options: only me / small team / me now, team later.
- **Answer: Me now, team later.**
- Result: local state (files + SQLite). A GitHub issues/PR adapter comes later.

**Q4. How much autonomy on merge?**
- Options: human approves every merge / auto-merge when gates pass / auto for low-risk only.
- **Answer: Human approves every merge.**

## Round 2 — inputs, pipeline, runtime

**Q5. What is the unit of work?**
- Options: markdown task file / GitHub issue / free text prompt.
- **Answer: Markdown task file** — `tasks/<id>.md` with fixed fields (Objective, Acceptance, Tests, Depends on).

**Q6. Which pipeline for v1?**
- **Answer: follow sssf; Claude recommends the subset.**
- Recommendation taken: sssf `adw_simple_sdlc` without scout and documenter, stopping before merge:
  `plan → build → test (code) ↔ fix (max 3) → review ↔ revise (max 2) → retest → commit on branch → wait for human`.

**Q7. Language for the control script?**
- **Answer: Python + uv.**

**Q8. How do agents run, and how is each job isolated?**
- **Answer: mix Pi (opencode subscription) and Claude Code.**
- Result: two runners behind one interface (`pi` and `claude -p`), chosen per agent in `factory.yaml`.
  One git worktree + branch `factory/<job-id>` per job.

## Round 3 — build approach

**Q9. Fork sssf or write our own small version?**
- **Answer: Own small version** (~1,200–1,600 lines). Copy the patterns, not the code.

**Q10. Where does the factory code live?**
- **Answer: New repo that installs into targets** — `~/Desktop/setup/kh-factory`. `factory init` sets up a target repo.

**Q11. How do you watch jobs in v1?**
- **Answer: SQLite trace + UI** — a small stdlib web server and one HTML page on port 4600.

**Q12. Which real repo proves v1 works?**
- **Answer: A toy repo** — `examples/toy/` (small Python app with pytest).

## Round 4 — your follow-up questions

**Q13. What happens with a fresh project that has no docs?**
1. `factory init` creates `factory.yaml` with no doc context and no test command.
2. You add a task: `factory add "<idea>"` creates `tasks/<id>.md`; you write the acceptance criteria.
3. The planner reads only the task and the repo files.
4. Task 1 must set up the project and a test command. Without one, the test gate fails with
   `NO_TEST_COMMAND` and the job stops for you.
- The factory does not invent a product vision. In a fresh project, you write the tasks.

**Q14. What happens when the project has docs, plans, and specs (like Nizk)?**
1. `factory.yaml` lists doc folders in `context:` (for example `docs/specs/`, `docs/decisions/`).
2. `factory import docs/planning/TASKS.md` converts each task card into `tasks/<id>.md`.
3. The planner reads the task and only the spec sections the task names, not all docs.
4. The reviewer checks the code against the task's acceptance and those spec sections.
5. Doc folders are protected. If the code must break a spec, the builder sets `spec_conflict`
   and the job stops for you. No agent edits an ADR or spec on its own.
6. `factory next` runs the next task whose dependencies are all `done`.

**Q15. `claude -p` or Pi — which is best? Can subagents replace `claude -p`?**
- **Pi is the main runner.** One interface for all opencode-go models.
- **`claude -p` is needed only for Claude Opus/Sonnet.** Pi has no Anthropic login here, and the
  opencode-go plan has only `claude-haiku-5-5`. If you add an Anthropic key to Pi, `claude -p` becomes optional.
- **Subagents must not run the pipeline steps.**

| | `claude -p` (process started by code) | Claude Code subagents |
|---|---|---|
| Who controls the flow | Python code | The parent model, by following a prompt |
| Loop limits and gates | Always enforced | The model can skip them |
| Models | Claude only (Pi covers the rest) | Claude only |
| Timeout, retry, resume per step | Yes | No |
| Per-step trace in SQLite | Yes, from the JSON stream | Hard |
| Cost | Subscription | Subscription |

- Subagents stay useful as a `/factory` skill front door that calls `factory.py`, and as helpers inside a builder session (later).

**Q16. Which runner setup?**
- **Answer: Pi main + `claude -p` for Opus.**
- Defaults: planner = `claude` / Opus; builder = `pi` / `opencode-go/kimi-k3`; reviewer = `pi` / `opencode-go/gpt-6-luna` (different model family from the builder).

**Q17. How will agents get good context to plan and build?**
- **Answer: a context pack built by code, plus tool lookups by the agent.** Added to the plan as "Context pack".
- Before each step, `factory/context.py` writes `context.md` into the step folder. It has 6 layers:
  repo rules (`AGENTS.md`/`CLAUDE.md`), project facts (`factory.yaml` `project:`), the task, only the named
  spec sections, a repo map, and the previous step's output. `.factory/learnings.md` is added to every prompt.
- The agent can still `read` / `grep` / `ls` for more.
- Fix and revise steps resume the builder session. The reviewer always starts a new session and does not
  see the builder's reasoning.
- Failures: missing info → planner returns `questions[]` and the job waits for you; bad spec ref →
  `SPEC_REF_NOT_FOUND`; too big → `CONTEXT_TOO_LARGE`.
- Why code builds the context: same input every run, you can inspect it, lower token cost.
- **Q17b. Add it to the plan?** **Answer: yes.** Cost: `context.py` (~150 lines) in build step 3 (+0.5 day).

**Q18. Will the config file be generic like sssf, so I can change models and more?**
- **Answer: yes.** Added to the plan as "Config file (`factory.yaml`)".
- Same rule as sssf: pipeline steps name agents, never models.
- Each agent sets `runner`, `model`, `thinking`, `tools`, `writes`, `prompts`. `defaults:` fills what an agent leaves out.
- `runners:` sets how `pi` and `claude` are started. `pipeline:` maps steps to agents and sets loop limits.
- The step order stays in code: one Python file per flow in `factory/flows/`, chosen by `pipeline.flow`.
- The config is checked at load: unknown runner, unknown Pi model, `writes` overlapping protected files,
  missing prompt file, or a step naming an agent that does not exist.
- **Q18b. Add it to the plan?** **Answer: yes.** New command `factory config check`; build step 1 grows to ~0.5–1 day.

**Q19. What does "how `claude -p` reports an unknown model" mean?**
- It means: if the config has a wrong model name, how does the CLI tell the factory? Tested on 2026-10-18.
- Both `claude -p` and `pi -p` exit with code `0` even when the model does not exist.
- `claude -p` JSON has `is_error: true` and `api_error_status: 404`. `pi -p` prints a warning and a `400` error.
- Result: runners check the JSON / output, not the exit code. Pi models are also checked against `pi --list-models` at config load.

**Q20. How do I use the factory inside Claude Code?**
- **Answer: a combination.** The `/factory` skill (structure taken from zazencodes) is the front door;
  the `factory` CLI still runs the loop in code.
- The skill writes the task from your feature text (the zazencodes spec-writer role), commits only that
  task file, runs the job in the background, then reports plan, review, and diff.
- Hard rule in the skill: Claude never runs `factory approve`/`reject` unless you ask. Never pushes.
- Install: `factory install-skill` → `~/.claude/skills/factory/SKILL.md`.

**Q21. Can the factory add the skill at project level? What is the recommendation?**
- Yes: `factory install-skill --project` copies it into `<repo>/.claude/skills/factory/SKILL.md`.
- **Recommendation taken:**
  1. `.claude/` is always protected (default `protected_files`), so agents cannot edit the skill in a job.
  2. For one user: the user-level skill is a **symlink** to kh-factory's template, so updates arrive at once
     and there is only one copy. Risk: moving kh-factory breaks the link → run `factory install-skill --force`.
  3. For a team: `factory init --with-skill` (opt-in) copies it into the repo to commit and share.
- Not chosen: auto-install on every `init` (duplicate copies, name conflicts, stale copies).
- Found while building it: `factory init` in a repo with no detected stack wrote an invalid
  `commands:` value. Fixed (`commands: {}`; the loader also accepts a null value).

**Q22. sssf has no timeout and no context budget. What do we do?**
- Checked in sssf: no timeout on agent calls (it records the pid for `just kill`); test commands do
  have a timeout. No budget before a call, but it **measures** context use after each call
  (tokens in window vs the model's `contextWindow`) and **cuts big diffs** (`max_diff_lines: 2000`).
- **Answer: "do what sssf does".** Added both sssf behaviors: context measurement per agent call
  (`factory show`, UI, warning over 80%) and diff cutting for the reviewer with the full diff as a file.
- Kept ours, because sssf has nothing to replace them: agent `timeout_min` and `context_budget_tokens`.
- Not done (sssf does not do it either): killing the whole process group on timeout.

**Q23. After a build or fix, specs/ADRs/plans may need updates. How does the current flow handle it?**
- The main Claude session would notice only by chance, so the flow handles it in code (§9.1 of the handbook).
- Builder proposes `doc_updates`; reviewer adds `doc_drift` and can `doc_rejects`; code keeps only existing
  doc paths and writes `doc-updates.md`.
- `pipeline.doc_updates`: `propose` (default) | `apply` | `off`. `apply` runs a docs step whose writes are
  exactly the proposed files, one docs review, and a separate docs commit.
- ADRs (`docs_never_apply: [docs/decisions/]`) are never applied. `factory approve --code-only` drops the docs commit.
- A failed docs step never loses the code commit.
- New flows (`docs`, `design`, `quick`, …) stay for later.

**Q24. From the super-board gaps, what do we take?**
- **Answer:** our flow is more reliable (code runs it), so we take three ideas: reviewer memory,
  follow-up findings, and task lint that makes sure every task has a good context.
- Reviewer memory: code-assigned stable ids, prior findings checked first with proof, a not-fixed
  blocker forces CHANGES, finding kinds, over-engineering and deepening never block, memory in the
  trace (survives rework). We keep: new reviewer session each round, no builder reasoning.
- Follow-ups: explicit + leftover should-fix/deepening/over-engineering → `followups.md` →
  `factory followups --create` on request.
- Lint: clarity checks plus context checks (spec refs when docs exist, budget, repo rules, test
  command). `run`/`next` refuse tasks with errors.
- Not taken now: merge gate, secret guards, size cap, decision trailer.

**Q25. Why do we ignore AGENTS.md? Do we not need it?**
- We do not ignore it: it is the first layer of every context pack. But three problems were found:
  both CLIs also loaded rule files on their own (twice, and from outside the repo); `AGENTS.md` was
  not protected; `factory init` did not create one.
- **Answer (verified with a planted rule):** the pack is the only copy. Pi runs with `--no-context-files`,
  Claude with `--safe-mode` (`--bare` would need an API key). The pack resolves `@file.md` imports itself.
- `AGENTS.md` and `CLAUDE.md` are protected and never auto-applied by doc updates (proposal only).
- `factory init` writes a starter `AGENTS.md` when no rules file exists; lint warns until its
  Conventions section is filled in.

---

## Decision summary

**Decided**
- Generic factory, Python + uv, own small code, new repo `kh-factory`.
- Markdown task files; sssf pipeline subset; tests run as code; human approves every merge.
- Runners: Pi (main) + `claude -p` (Opus). One worktree + branch per job.
- SQLite trace + small web UI. First proof on a toy repo.
- Context pack built by code per step (`context.md`), with a token budget; agents pull more with tools.
- `/factory` skill in Claude Code as the front door; the CLI runs the loop.
- Repo rules: the context pack is the only copy (CLIs' own loading off); rules files protected; starter AGENTS.md.
- Reviewer memory (ids + prior checks), follow-ups, and task lint (clarity + context) from super-board.
- Doc updates inside `sdlc`: propose by default, apply as a separate commit on request, ADRs never applied.
- Context use measured per agent call; reviewer diffs cut at `max_diff_lines` (both from sssf).
- Skill: user-level symlink by default; project copy with `--project` / `init --with-skill`; `.claude/` protected.
- Generic `factory.yaml`: per-agent runner/model/tools/writes/prompts, steps name agents; step order in `factory/flows/*.py`.

**Open**
- Exact JSON event formats of `pi --mode json` and `claude --output-format stream-json` (checked in build step 2).

**Later**
- GitHub adapter (issues → tasks, PR instead of local merge).
- Run on Nizk: import `TASKS.md`, start with FND-01.
- Parallel scheduler, scout and documenter agents, cost per job.
