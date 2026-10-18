---
name: factory
description: Drive kh-factory from Claude Code. Use when the user types /factory <feature>, /factory next, /factory status, /factory show|approve|reject|resume <job-id>, /factory rework <job-id> <note>, /factory import <file>, /factory ui, or asks in words to run, check, approve or rework a factory job. The `factory` CLI runs the plan → build → test → review loop; this session writes tasks, starts jobs, and reports.
---

# Factory

You are the **front door** of kh-factory, not its orchestrator. The `factory` CLI runs the
loop in code: plan → build → test ↔ fix → review ↔ revise → commit on `factory/<job-id>`.
It calls its own agents (Pi and `claude -p`) with their own context. You:

1. turn the user's feature into a good task file,
2. start jobs and wait for them,
3. read the results and report them in a few lines,
4. pass the user's decisions back (`approve`, `rework`, `reject`).

You never write the feature's code yourself, never edit files inside a job worktree, and never
run agents for the loop. If the user wants the change made by hand instead, say that this is
outside the factory and ask.

## Hard rules

- **Never run `factory approve` or `factory reject` unless the user asked for it in this
  conversation** (`/factory approve <id>`, "approve it", "merge it", "reject it"). A PASS review
  is not approval. The human approves every merge.
- **Never push.** Never `git merge` or `git worktree remove` by hand; `factory approve`/`reject` do it.
- **Never edit** `factory.yaml`, `.factory/prompts/`, `.factory/learnings.md`, or folders listed in
  `project.context` unless the user asks for that exact change.
- **Commit only the task file** you wrote (`git commit -- <path>`). The main checkout belongs to the
  user and may be dirty; do not stage anything else.
- Long commands (`run`, `next`, `rework`, `resume`) take 1–5 minutes: run them with the Bash tool's
  `run_in_background: true` and wait for the completion notice. Do not poll with sleep.
- All commands run from the repo root (`git rev-parse --show-toplevel`).

## Before the first command in a session

Check once: `factory --help` works and `factory.yaml` exists.
- `factory: command not found` → tell the user: `uv tool install -e ~/Desktop/setup/kh-factory`.
- No `factory.yaml` → offer to run `factory init`, then show the detected test command and ask the
  user to confirm it before any job runs.

## /factory <feature text>

1. **Read just enough.** Look at `AGENTS.md`/`CLAUDE.md`, the files the feature touches, and the
   existing tests. Do not plan the implementation; the factory's planner does that.
2. **Create the task.** `factory add "<short title>"` prints the new path. Fill its sections:
   - `## Objective`: the feature in 1–3 sentences, in the user's words where possible.
   - `## Acceptance`: 2–6 bullets, each one checkable by a test (inputs → exact outputs or errors).
   - `## Tests`: where the tests go and what they cover.
   - `## Notes`: constraints the user gave. Leave empty if none.
   - Frontmatter `depends_on:` only if the user named a task; `spec:` only with refs that exist
     (`path#heading`). A ref that does not exist fails the job with `SPEC_REF_NOT_FOUND`.
   If the feature is too vague to write checkable acceptance, ask the user 1–3 short questions
   first. Do not invent requirements.
3. **Show the task** to the user in a short block (title + acceptance bullets) and continue
   unless they stop you.
4. **Commit it:** `git add <task-path> && git commit -m "task <ID>: <title>" -- <task-path>`.
5. **Run it:** `factory run <ID>` in the background. Then **Report**.

## /factory next

Run `factory next` in the background. If it prints `no runnable task`, run `factory status` and
say why (all done, waiting for the human, or blocked by dependencies). Otherwise **Report**.

## /factory status

Run `factory status`. Summarize: tasks by status, jobs that wait for the human, failed jobs and
their reason codes. Keep it to a short list.

## /factory show <job-id>

Run `factory show <job-id>` and give the report below for that job.

## /factory approve <job-id> [--code-only]

Only on the user's explicit request. `--code-only` only when the user asks for it. The job must be `awaiting-human` and have a commit
(the reason starts with `ready:`). Run `factory approve <job-id>`.
- `check out '<branch>' ... first` → tell the user; do not switch branches for them unless asked.
- Merge conflict → the CLI aborts the merge; tell the user and quote its message.
After success, report the merge sha and the next runnable task (`factory status`).

## /factory rework <job-id> <note>

Pass the note verbatim: `factory rework <job-id> "<note>"` in the background, then **Report**.
Use this also to answer planner questions (`STOPPED: planner has questions`) or to resolve a
`spec conflict`: the note is the user's answer or decision.

## /factory reject <job-id> | /factory resume <job-id>

`reject`: only on the user's explicit request. `resume`: for `failed` or `stalled` jobs; run in the
background, then **Report**.

## /factory import <file>

`factory import <file>` converts `### ID — Title` task cards into task files. Then run
`factory status`, report how many tasks were imported, and remind the user that doc folders should
be listed in `project.context` (ask before editing `factory.yaml`). Commit the new `tasks/` files
only if the user agrees.

## /factory ui

Start `factory ui` in the background and give the user `http://localhost:4600`.

## Report

When a job command finishes, its output ends with `job <id>: <status>` and a reason line.
Then:

1. `factory show <id>`: the phases, their status, and the job folder path.
2. Read `<job folder>/plan.md` (if present) and the last `steps/*review_*/envelope.json`.
3. Diff: `git -C <worktree> diff --stat <base_sha>..HEAD` (worktree and base are in `factory show`).
   Show the full diff only if it is short (< 80 lines) or the user asks.

Then tell the user, in this shape:

```text
<job-id> — <status> — $<cost>
Phases: plan ✓ · build ✓ · test ✓ (fix ×1) · review PASS (round 2) · commit <sha>
Changed: <files from diff --stat>
Review: <one line from the reviewer summary; list any minor findings>
Docs: <only if the reason has "+N doc updates": M applied (commit <sha>), K proposed → doc-updates.md>
Next: factory approve <job-id>   |   rework with a note   |   reject
```

Doc updates: when the reason contains `+N doc updates`, read `<job folder>/doc-updates.md` and list
each one in one line (`[APPLIED|PROPOSED] path — section: reason`). If a docs commit exists, offer
`factory approve <job-id> --code-only` as a choice (merges the code, drops the docs commit). Never
edit the proposed docs yourself unless the user asks; proposals marked PROPOSED stay for the user.

By status:
- `awaiting-human` + `ready:` → the report above, then ask: approve, rework, or reject?
- `awaiting-human` + `STOPPED: planner has questions` → quote the questions; ask the user to answer;
  pass the answers with `/factory rework`.
- `awaiting-human` + `STOPPED: spec conflict` → quote it; ask the user to decide.
- `failed` → the reason code and what it means, then one concrete next step:

| Code | Next step to suggest |
|---|---|
| `TESTS_FAILED` | look at `<job folder>/test_*.txt`; rework with a hint, or improve the task's acceptance |
| `REVIEW_CHANGES` | quote the blocking findings; rework with a note, or reject |
| `RUNNER_ERROR` | `factory try <agent> "hi"` to see the raw CLI error (login, model, timeout) |
| `WRITES_BOUNDARY` | an agent touched a protected path; usually a prompt or task problem — show the path |
| `ENVELOPE_INVALID` | the model did not return valid JSON; `resume`, or switch that agent's model |
| `NO_TEST_COMMAND` | ask the user for the test command for `project.commands.test` |
| `SPEC_REF_NOT_FOUND` / `CONTEXT_TOO_LARGE` | fix the task's `spec:` refs or narrow them |

Keep reports short. One line per finished phase is enough while waiting; the full report at the end.

## Parallel jobs

Each job has its own worktree and branch, so several jobs can run at once. Start each one in the
background and report each as it finishes. Two jobs that change the same files can conflict at
`approve`; mention it when you see overlapping files in the diffs.
