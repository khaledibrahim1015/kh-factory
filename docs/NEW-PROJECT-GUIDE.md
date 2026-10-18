# Create a new test project and run kh-factory inside it

## How it fits together

Mental model:
- **kh-factory** = the tool, installed once (like `git`). It lives in `~/Desktop/setup/kh-factory`.
- **test project** = any git repo. You run `factory` commands *inside* it.
- The factory creates a second folder next to it, `~/tmp/my-toy-factory/`, for job worktrees.

```text
~/Desktop/setup/kh-factory   <- the tool (install once)
~/tmp/my-toy                 <- your test project (you run `factory` here)
~/tmp/my-toy-factory/<job>   <- created by the factory, one folder per job
```

## Step 1 — install the tool once

```bash
cd ~/Desktop/setup/kh-factory
uv sync
uv tool install -e .
factory --help            # expect: usage: factory ... {init,config,add,...}
```

## Step 2 — create the test project folder

```bash
mkdir -p ~/tmp/my-toy
cd ~/tmp/my-toy
git init -b main
```

## Step 3 — add a tiny program and one test

Create `pyproject.toml`:
```toml
[project]
name = "mytoy"
version = "0.1.0"
requires-python = ">=3.12"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
pythonpath = ["."]
```

Create `greet.py`:
```python
def hello(name: str) -> str:
    return f"Hello, {name}!"
```

Create `tests/test_greet.py`:
```python
from greet import hello


def test_hello():
    assert hello("Sara") == "Hello, Sara!"
```

Check it works:
```bash
uv lock
uv run pytest -q          # expect: 1 passed
```

## Step 4 — install the factory into the project

```bash
factory init
```
Expect: it writes `factory.yaml`, `.factory/prompts/...`, `tasks/`, updates `.gitignore`, and prints
`Detected stack: Python (uv); test command: uv run pytest -q`.

## Step 5 — write one task

```bash
factory add "Add a goodbye function"
```
Open `tasks/T-001-add-a-goodbye-function.md` and replace the text under the headings:
```markdown
## Objective

Add `goodbye(name)` to `greet.py`.

## Acceptance

- `goodbye("Sara")` returns `"Goodbye, Sara!"`

## Tests

Add a test in `tests/test_greet.py`.
```

## Step 6 — commit (jobs start from the last commit)

```bash
git add -A
git commit -m "start"
factory config check      # expect: 3 agents, then OK
factory status            # expect: T-001  todo  Add a goodbye function
```

## Step 7 — run the job

Optional, second terminal: `cd ~/tmp/my-toy && factory ui`, then open http://localhost:4600.

```bash
factory next
```
Takes 1–4 minutes. Expect at the end:
```text
job t-001-xxxxxx: awaiting-human
  ready: <sha> on factory/t-001-xxxxxx — factory approve t-001-xxxxxx
```

## Step 8 — check the result, then approve

```bash
factory show t-001-xxxxxx                       # all phases "passed"
cat .factory/jobs/t-001-xxxxxx/plan.md          # what the planner decided
cat greet.py                                    # NOT changed yet (main is safe)
factory approve t-001-xxxxxx
cat greet.py                                    # now has goodbye()
uv run pytest -q                                # expect: 2 passed
factory status                                  # expect: T-001 done
```

## Step 9 — use it from Claude Code (optional)

```bash
factory install-skill          # once per machine: links ~/.claude/skills/factory/SKILL.md
cd ~/tmp/my-toy && claude      # start Claude Code in the project
```
In Claude Code, type:
```text
/factory status
/factory add a shout(name) function that returns the name in upper case with "!"
```
Claude writes the task, commits only that file, runs the job in the background, and reports.
It runs `factory approve` only when you ask (`/factory approve <job-id>`).

## Start over

```bash
rm -rf ~/tmp/my-toy ~/tmp/my-toy-factory
```

## If a step fails

| Symptom | Fix |
|---|---|
| `factory: command not found` | Step 1 not done, or open a new terminal |
| `not inside a git repository` | run `git init -b main` in the project folder |
| `the repo has no commits yet` | do Step 6 |
| `RUNNER_ERROR` | `factory try builder "hi"` and `factory try planner "hi"` show the raw error (login or model) |
| job `failed` | `factory show <job>` shows which phase and why |
