# Test kh-factory yourself

Follow the parts in order. Each step shows the command and the output you should see.
Use two terminal windows: **A** runs commands, **B** shows the UI.

## Part 0 — one-time install (2 min)

```bash
cd ~/Desktop/setup/kh-factory
uv sync
uv run pytest -q                 # expect: 46 passed
uv tool install -e .             # puts `factory` on your PATH
factory --help                   # expect: list of commands
```

## Part 1 — make a toy repo (1 min)

```bash
~/Desktop/setup/kh-factory/examples/setup-toy.sh ~/tmp/toy
cd ~/tmp/toy
factory config check             # expect: 3 agents, then "OK"
factory status                   # expect: T-001 todo, T-002/T-003 "waits for ..."
```

## Part 2 — smoke test each agent (1 min, a few cents)

```bash
factory try builder  "Reply with one word: ready"   # Pi + kimi-k3
factory try reviewer "Reply with one word: ready"   # Pi + gpt-6-luna
factory try planner  "Reply with one word: ready"   # Claude Code + opus
```

Expect `ready` and a `-- runner=... session=... cost=...` line for each.
If one fails, the error text says why (login, model name). Fix that before Part 3.

## Part 3 — run one real job (2–4 min, about $0.30)

- Window B: `cd ~/tmp/toy && factory ui`, then open http://localhost:4600
- Window A: `factory next`
- Watch in B: the job card goes `running` → `awaiting-human`. Phases fill in one by one.
- Expect in A: `job t-001-xxxxxx: awaiting-human` and `ready: <sha> on factory/t-001-xxxxxx`.

## Part 4 — inspect what happened

```bash
factory show <job-id>                                 # each phase, pass/fail, time
ls .factory/jobs/<job-id>/                            # plan.md, steps/, test_1.txt
cat .factory/jobs/<job-id>/plan.md                    # the planner's plan
cat .factory/jobs/<job-id>/steps/*review_1/context.md # exactly what the reviewer saw
cd ../toy-factory/<job-id> && git log --oneline -2 && git show --stat HEAD && cd -
git log --oneline -1                                  # main is NOT changed yet
```

## Part 5 — approve and check

```bash
factory approve <job-id>       # expect: merged ... worktree and branch removed
git log --oneline -3           # expect: a merge commit on main
uv run pytest -q               # expect: all pass on main
factory status                 # expect: T-001 done, T-002 todo (no longer waiting)
```

## Part 6 — the human controls (use T-002)

```bash
factory next                                              # runs T-002 -> awaiting-human
factory rework <job-id> "Also add a docstring to divide." # builder resumes with your note
factory show <job-id>                                     # expect: a 'rework' phase, then tests + review
factory reject <job-id>                                   # OR approve; reject drops worktree + branch
factory status                                            # a rejected task can run again with `factory next`
```

## Part 7 — see the safety checks fail on purpose (no model cost)

| Test | Do | Expect | Undo |
|---|---|---|---|
| Wrong model name | set `reviewer.model: opencode-go/nope` in `factory.yaml`, run `factory config check` | `'opencode-go/nope' not in pi --list-models` | `git checkout factory.yaml` |
| Missing prompt | `mv .factory/prompts/reviewer/user.md /tmp/`, then `factory config check` | `prompts.user: file not found` | move it back |
| No test command | remove the `test:` line, commit, `factory run T-00x` | job fails with `NO_TEST_COMMAND` (after plan and build) | `git revert --no-edit HEAD` |
| Bad spec ref | set `spec: [docs/NOPE.md]` in a task, commit, `factory run` it | `SPEC_REF_NOT_FOUND`, no model called ($0.000) | `git revert --no-edit HEAD` |
| Planner question | `factory add "Make the calculator better"` (leave it vague), commit, run it | often `STOPPED: planner has questions`; answer with `factory rework <job> "<answer>"` | — |

Jobs run from the last commit, so commit a change before `factory run` if the job must see it.

## Part 8 — use it on a real repo

```bash
cd <your-repo>                      # git repo with at least one commit
factory init                        # check the detected test command in factory.yaml
factory add "<small, clear task>"   # fill in Acceptance in tasks/T-001-*.md
git add -A && git commit -m "factory setup"
factory next
```

Start with a small task. Read `plan.md` and the diff before you approve.

## Part 9 — the `/factory` skill in Claude Code

```bash
factory install-skill                          # expect: linked: ~/.claude/skills/factory/SKILL.md -> ...
ls -la ~/.claude/skills/factory/               # expect: SKILL.md -> .../kh-factory/src/kh_factory/templates/skill/SKILL.md
cd ~/tmp/toy && claude
```
In Claude Code: `/factory status` (expect a short summary), then
`/factory add a power(a, b) function`. Expect: a new task file shown, one commit with only that file,
the job running in the background, then a report with `Next: factory approve <job-id> | rework | reject`.
Claude must not approve on its own.

## Part 10 — doc updates (about $0.40)

```bash
cd ~/tmp/toy
mkdir -p docs && printf '# Calculator spec\n\n## 1. Operations\n\nOnly add(a, b) exists.\n' > docs/SPEC.md
# factory.yaml: project.context: [docs]   and   pipeline.doc_updates: apply
# a todo task: add `spec: [docs/SPEC.md#1]` to its frontmatter
git add -A && git commit -m "spec + doc updates"
factory next
```
Expect: the reason ends with `(+1 doc updates: 1 applied in <sha> ...)`, `factory show` lists
`doc_updates`, `docs`, `docs_review`, `commit_docs`, and `doc-updates.md` shows `[APPLIED]`.
Then `factory approve <job> --code-only` and check that `docs/SPEC.md` on main is unchanged.

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `factory: command not found` | Part 0 install not done | `uv tool install -e ~/Desktop/setup/kh-factory` |
| `RUNNER_ERROR` | CLI login, model name, or timeout | `factory try <agent> "hi"` shows the raw error |
| job shows `stalled` | the terminal was closed during a job | `factory resume <job-id>` |
| `check out 'main' ... first` on approve | main repo is on another branch | `git checkout main`, then approve |
| UI is empty | started in another folder | run `factory ui` inside the repo |
| `/factory` not listed in Claude Code | skill not installed, or session started before install | `factory install-skill`, then start a new `claude` session |
