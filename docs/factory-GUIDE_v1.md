# kh-factory — your test runbook

Read this before you start. It tells you what is ready, and gives two paths to test the tool:
**A) the `/factory` skill inside Claude Code**, and **B) the `factory` CLI** (from any terminal or from Pi).

Everything below is already set up on this machine. Path B is a real run I did from Pi, with real output,
so you know exactly what to expect.

---

## 0. What kh-factory is (30 seconds)

kh-factory turns one task file into a **tested commit on its own git branch**, then waits for you.

The rule: **agents propose, code disposes.** Python runs the flow, checks every claim, and runs the
tests. AI agents do one bounded step each. You approve every merge.

```text
tasks/G-01.md
   plan (agent) -> build (agent) -> test (code) <-> fix (agent, max 3)
   -> review (agent, different model) <-> revise (agent, max 2) -> retest (code)
   -> commit on branch factory/<job-id> -> awaiting-human
you:  factory approve <job>  |  factory rework <job> "note"  |  factory reject <job>
```

Read the full docs when you want depth:

- `~/Desktop/setup/kh-factory/README.md`
- `~/Desktop/setup/kh-factory/docs/HANDBOOK.md` (everything in one file)
- `~/Desktop/setup/kh-factory/docs/PLAN-v1.md` (design)
- `~/Desktop/setup/kh-factory/docs/DECISIONS-QA.md` (25 planning decisions)
- `~/Desktop/setup/kh-factory/docs/TESTING.md` (manual test steps)
- `~/Desktop/setup/kh-factory/docs/NEW-PROJECT-GUIDE.md` (a project from zero)

---

## 1. What is already installed

| Thing | State |
|---|---|
| `factory` CLI | installed (editable from `~/Desktop/setup/kh-factory`) |
| `/factory` skill | installed in Claude Code: `~/.claude/skills/factory/SKILL.md` (symlink to the template) |
| unit + flow tests | `63 passed` (`cd ~/Desktop/setup/kh-factory && uv run pytest -q`) |
| agent CLIs | `pi` 1.3.0 and `claude` 2.1.302 both present |

The skill is **Claude Code only**. Pi (and your terminal) use the CLI. There is no `/factory` skill
for Pi.

---

## 2. The two test projects

Both are ready, committed, and pass `factory config check` (prints `OK`) and `factory lint`
(0 errors, 0 warnings).

### Project 1 — no docs: `~/tmp/factory-nodocs`

- `greet.py` with `hello(name)`, one passing test, no docs folder.
- `factory.yaml` has `project.context: []` (empty).
- No tasks yet. You create the first one with `/factory add` (skill) or `factory add` (CLI).

**What it proves:** the planner reads only the task + the repo tree. Nothing else.

### Project 2 — with docs: `~/tmp/factory-docs`

- `greet.py` with `hello(name)`, one passing test.
- Docs: `docs/specs/GREETING.md`, `docs/architecture/ARCHITECTURE.md`,
  `docs/decisions/0001-python.md`, `docs/planning/TASKS.md`.
- `factory.yaml` has `project.context: [docs]` (docs are protected, agents never edit them).
- Three imported tasks with dependencies and spec refs:

| Task | Title | Depends on | Spec refs |
|---|---|---|---|
| `G-01` | Add goodbye | — | `docs/specs/GREETING.md#2` |
| `G-02` | Reject empty names | G-01 | `docs/specs/GREETING.md#3` |
| `G-03` | Describe the architecture | G-02 | `docs/architecture/ARCHITECTURE.md#1`, `#2` |

All three are `todo`. `G-02` waits for `G-01`; `G-03` waits for `G-02`.

**What it proves:** the code cuts only the named spec sections into the agent's context; docs are
protected; dependencies gate tasks in order.

> Note: I ran the CLI demo on a throwaway copy, so `~/tmp/factory-docs` is still untouched at
> `G-01 todo`. You test it fresh.

---

## 3. Path A — the `/factory` skill in Claude Code

This is the "front door". You type `/factory ...`; Claude writes tasks, starts jobs, and reports.
Claude **never approves on its own**.

### Start (each project)

```bash
cd ~/tmp/factory-nodocs && claude     # project 1
cd ~/tmp/factory-docs   && claude     # project 2
```

Start a **new** Claude session so the skill is listed.

### Project 1 — no docs (the skill writes the task)

```text
/factory add a goodbye(name) function that returns "Goodbye, <name>!"
```

Claude does this: reads `AGENTS.md`, writes the task with checkable acceptance, runs
`factory lint`, commits only that task file, runs the job in the background, and reports.

Then, when it reports `awaiting-human`:

```text
/factory show <job-id>     # phases + review + changed files
/factory approve <job-id>  # ONLY when you decide to merge
```

### Project 2 — with docs (imported tasks)

```text
/factory status     # G-01 todo, G-02 waits for G-01, G-03 waits for G-02
/factory next       # runs G-01 (builder must follow GREETING.md §2)
/factory show <job-id>
/factory approve <job-id>
/factory next       # now G-02 can run
```

Open a second terminal for the live board during any job:

```bash
cd ~/tmp/factory-docs && factory ui     # http://localhost:4600
```

### Hard rules the skill must follow (watch for these)

- Never runs `factory approve` / `reject` unless you asked in the conversation.
- Never pushes, never merges by hand, never edits `factory.yaml`, prompts, `learnings.md`, or doc folders.
- Commits only the task file it wrote. Never writes the feature code itself.

---

## 4. Path B — the `factory` CLI (real run from Pi)

The skill is just a wrapper over these exact commands. Here is a real run I did from Pi on a throwaway
copy of the docs project, so you see the real output and timing.

### Step 1 — look before running

```bash
cd ~/tmp/factory-docs-pidemo
factory status
```

```text
TASKS
  G-01       todo            Add goodbye
  G-02       todo            Reject empty names  (waits for G-01)
  G-03       todo            Describe the architecture  (waits for G-02)
JOBS
```

### Step 2 — run the next task

```bash
factory next
```

```text
next task: G-01 — Add goodbye

job g-01-484f25: awaiting-human
  ready: 0e4f5648 on factory/g-01-484f25 — factory approve g-01-484f25
  details: factory show g-01-484f25   |   UI: factory ui
```

### Step 3 — inspect the job

```bash
factory show g-01-484f25
```

```text
g-01-484f25  awaiting-human  task G-01: Add goodbye
  branch factory/g-01-484f25  worktree .../factory-docs-pidemo-factory/g-01-484f25  base main@2763a0ad
  cost $0.145  reason: ready: 0e4f5648 on factory/g-01-484f25 — factory approve g-01-484f25
  [passed] context   code   factory    0s                   1 spec section(s)
  [passed] plan      agent  planner   18s  ctx 18k/1M (2%)  Add goodbye(name: str) -> str ...
  [passed] build     agent  builder   15s  ctx 5k/1M (1%)   Added goodbye(name) to greet.py ...
  [passed] test_1    code   quality    0s                   test: pass
  [passed] review_1  agent  reviewer  21s  ctx 7k/1.1M (1%) PASS: The change implements ...
  [passed] commit    code   git        0s                   0e4f5648 on factory/g-01-484f25
```

The planner wrote `plan.md`. It cut only `GREETING.md §2` into its context, planned the function and
the test, and correctly noted: *"§3 says goodbye must raise on empty names, but that is G-02 — don't
add it here."* That is the spec-section gating working.

Changed files (the reviewer checked only these):

```text
 greet.py            | 4 ++++
 tests/test_greet.py | 6 +++++-
 2 files changed, 9 insertions(+), 1 deletion(-)
```

The review ledger (`review.md`) showed `no findings`, verdict `PASS`.

### Step 4 — approve (you decide)

```bash
factory approve g-01-484f25
```

```text
merged job g-01-484f25 as d4cd7649; worktree and branch removed
```

Then:

```bash
git log --oneline -3      # shows the merge commit
uv run pytest -q          # 2 passed
factory status            # G-01 done, G-02 todo (no longer waiting)
```

### CLI cheat sheet

```text
factory status                  tasks + jobs
factory next                    run the next runnable task
factory run <ID> [--force]      run one task
factory show <job>              phases, gates, cost, paths
factory approve <job> [--code-only]   merge locally
factory rework <job> "note"     send back with a note (or answer planner questions)
factory reject <job>            drop worktree + branch
factory resume <job>            continue a failed/stalled job
factory lint [ID] [--strict]    task clarity + context checks
factory followups <job> [--create]    list/create follow-up tasks
factory ui [--port 4600]        read-only web UI
factory try <agent> "hi"        smoke test one agent (no worktree, no gates)
```

---

## 5. Skill vs CLI

| | `/factory` skill (Claude Code) | `factory` CLI |
|---|---|---|
| Who writes the task | Claude, from your feature text | you, in a text editor (or `factory add`) |
| Who starts the job | Claude, in the background | you |
| Who reports | Claude, in a few lines | you read `factory show` |
| Who approves | Claude **only when you ask** | you |
| Good for | hands-off driving, feature text in | scripts, exact control, understanding the flow |
| Cost | same per job | same per job |

Both paths run the **same** loop in code. The skill is not the orchestrator; the CLI is.

---

## 6. What to watch for while you test

- **No-docs project:** the planner sees only the task + repo tree. A good task gives a good build;
  a vague task fails lint (`NO_ACCEPTANCE`, `VAGUE_WORDS`) or stops with "planner has questions".
- **Docs project:** only the named spec sections reach the agent (`§2`, `§3`), not all docs. A bad
  ref fails with `SPEC_REF_NOT_FOUND` **before any model call** (cost $0.000).
- **Dependencies:** `G-02` must not start until `G-01` is `done`.
- **Protection:** agents may not write `factory.yaml`, `.factory/`, `tasks/`, `.claude/`, `AGENTS.md`,
  `CLAUDE.md`, or any folder in `project.context` (here: `docs/`). A violation is reverted and the
  step fails with `WRITES_BOUNDARY`.
- **Human gate:** the factory never pushes and never merges without `approve`.

---

## 7. Cost and time (real numbers from my run)

- One simple job (`G-01`, no fix/revise rounds): **$0.145**, about **1 minute** wall time.
  - plan: 18s (Claude Opus), build: 15s (Pi kimi-k3), review: 21s (Pi gpt-6-luna).
- The handbook estimates ~$0.30 per job when a fix or revise round is needed.
- `factory show` prints the exact cost. `factory status` also shows `$` per job.

---

## 8. Optional: more things to test later

### Safety checks (free, no model calls)

In `~/tmp/factory-nodocs`, break things on purpose, check, then `git checkout factory.yaml`:

```bash
# wrong Pi model
sed -i '' 's|opencode-go/gpt-6-luna|opencode-go/nope|' factory.yaml
factory config check      # -> "... not in `pi --list-models`"

# missing prompt
mv .factory/prompts/reviewer/user.md /tmp/
factory config check      # -> "file not found"
mv /tmp/user.md .factory/prompts/reviewer/user.md

# writes overlaps protected
# set reviewer writes: [tasks/]  ->  "overlaps protected path 'tasks/'"
```

### Doc updates (docs project, ~$0.40)

This tests the "a change makes a doc section wrong" path. Follow `TESTING.md` Part 10:

1. In `factory.yaml`: `project.context: [docs]` (already set) and `pipeline.doc_updates: apply`.
2. Give a todo task a `spec:` ref whose section the build will contradict.
3. Run it. Expect the reason to end with `(+N doc updates: ... applied in <sha> ...)`.
4. `factory approve <job> --code-only` merges the code but drops the docs commit.

### Planner questions

`factory add "Make the calculator better"` (leave it vague), commit, run. Often the planner returns
questions and the job stops with `STOPPED: planner has questions`. Answer with
`factory rework <job> "<answer>"`.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `factory: command not found` | `uv tool install -e ~/Desktop/setup/kh-factory`, new terminal |
| `/factory` not listed in Claude | `factory install-skill`, start a new `claude` session |
| `RUNNER_ERROR` | `factory try <agent> "hi"` shows the raw CLI error (login/model/timeout) |
| job `failed` | `factory show <job>` shows the phase and reason code |
| job `stalled` | process died or Ctrl+C → `factory resume <job>` |
| `check out 'main' ... first` on approve | `git checkout main`, then approve |
| merge conflict on approve | the CLI aborts; resolve on the branch in the worktree, commit, approve again |
| UI empty | run `factory ui` inside the repo |

---

## 10. Start over / cleanup

```bash
rm -rf ~/tmp/factory-nodocs ~/tmp/factory-nodocs-factory
rm -rf ~/tmp/factory-docs   ~/tmp/factory-docs-factory
rm -rf ~/tmp/factory-docs-pidemo ~/tmp/factory-docs-pidemo-factory   # my demo copy
```

Then re-create them with the steps in section 2 (see `docs/NEW-PROJECT-GUIDE.md`).
