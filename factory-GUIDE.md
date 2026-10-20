# kh-factory — test from scratch (copy-paste runbook)

You run every command. Copy each block, paste it, press Enter.
Under each step there is one line: **expect** = what you should see.
Replace `JOB_ID` with the real id from the report (looks like `t-001-xxxxxx` or `g-01-xxxxxx`).

Order matters: Part 0 → Part 1 → Part 2 → Part 3 → Part 4.

---

## Part 0 — install kh-factory (one time)

```bash
cd ~/Desktop/setup/kh-factory
```

```bash
uv sync
```

```bash
uv tool install -e .
```

**expect:** `Installed 1 executable: factory`

```bash
factory --help
```

**expect:** `usage: factory` followed by a list of commands

```bash
factory install-skill
```

**expect:** `linked: ~/.claude/skills/factory/SKILL.md -> ...`

```bash
uv run pytest -q
```

**expect:** `63 passed`

---

## Part 1 — create Project A (no docs) from scratch

### 1.1 Make the folder and git repo

```bash
mkdir -p ~/tmp/factory-a && cd ~/tmp/factory-a
```

```bash
git init -b main
```

**expect:** `Initialized empty Git repository`

### 1.2 Create the project files (paste each block)

```bash
cat > pyproject.toml <<'EOF'
[project]
name = "greeter"
version = "0.1.0"
requires-python = ">=3.12"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
EOF
```

```bash
mkdir -p tests
```

```bash
cat > greet.py <<'EOF'
def hello(name: str) -> str:
    return f"Hello, {name}!"
EOF
```

```bash
cat > tests/test_greet.py <<'EOF'
from greet import hello


def test_hello():
    assert hello("Sara") == "Hello, Sara!"
EOF
```

```bash
cat > AGENTS.md <<'EOF'
# AGENTS.md

Rules for every agent that works in this repo.

## Project

- Stack: Python (uv)
- Test: `uv run pytest -q`

## Conventions

- Code lives at the repo root (`greet.py`); tests in `tests/`.
- Functions are small and typed. Raise `ValueError` on bad input.

## Do not

- Add dependencies without saying so in the result.
- Change files outside the task's scope.
EOF
```

```bash
printf '__pycache__/\n*.pyc\n.venv/\n' > .gitignore
```

### 1.3 Lock and run the tests

```bash
uv lock
```

```bash
uv run pytest -q
```

**expect:** `1 passed`

### 1.4 Install the factory into the project

```bash
factory init
```

**expect:** `Detected stack: Python (uv); test command: uv run pytest -q`

### 1.5 Commit

```bash
git add -A
```

```bash
git -c user.name=kh-factory -c user.email=factory@localhost commit -m "start"
```

### 1.6 Check

```bash
factory config check
```

**expect:** 3 agents, then `OK`

```bash
factory lint
```

**expect:** `0 task(s): 0 error(s), 0 warning(s)`

```bash
factory status
```

**expect:** `(none)` and a hint to run: `factory add "<title>"`

---

## Part 2 — create Project B (with docs) from scratch

### 2.1 Make the folder and git repo

```bash
mkdir -p ~/tmp/factory-b && cd ~/tmp/factory-b
```

```bash
git init -b main
```

### 2.2 Create code + tests (same as Project A)

```bash
cat > pyproject.toml <<'EOF'
[project]
name = "greeter"
version = "0.1.0"
requires-python = ">=3.12"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
EOF
```

```bash
mkdir -p tests
```

```bash
cat > greet.py <<'EOF'
def hello(name: str) -> str:
    return f"Hello, {name}!"
EOF
```

```bash
cat > tests/test_greet.py <<'EOF'
from greet import hello


def test_hello():
    assert hello("Sara") == "Hello, Sara!"
EOF
```

```bash
printf '__pycache__/\n*.pyc\n.venv/\n' > .gitignore
```

### 2.3 Create the docs

```bash
mkdir -p docs/specs docs/architecture docs/decisions docs/planning
```

```bash
cat > docs/specs/GREETING.md <<'EOF'
# Greeting spec

## 1. hello

`hello(name)` returns `"Hello, {name}!"` for any non-empty name.

## 2. goodbye

`goodbye(name)` returns `"Goodbye, {name}!"` for any non-empty name.

## 3. Errors

An empty name (`""`) raises `ValueError` with the message `name must not be empty`.
EOF
```

```bash
cat > docs/architecture/ARCHITECTURE.md <<'EOF'
# Architecture

## 1. Modules

`greet.py` is the only module. No packages, no I/O.

## 2. Types

Every public function is typed and returns `str`.

## 3. Tests

Tests live in `tests/` and use pytest.
EOF
```

```bash
cat > docs/decisions/0001-python.md <<'EOF'
# ADR 0001: Use Python

## Status

Accepted.

## Decision

Use Python 3.12 with pytest.
EOF
```

```bash
cat > docs/planning/TASKS.md <<'EOF'
# Tasks

### G-01 — Add goodbye
- **Objective:** Add `goodbye(name)` to `greet.py`.
- **Acceptance:** `goodbye("Sara")` returns `"Goodbye, Sara!"`.
- **Tests:** Add a test in `tests/test_greet.py`.
- **Spec:** [Greeting spec](../specs/GREETING.md) §2

---

### G-02 — Reject empty names
- **Objective:** Make `hello` and `goodbye` raise on an empty name.
- **Depends on:** G-01
- **Acceptance:** `hello("")` raises `ValueError` with `name must not be empty`.
- **Tests:** Add `pytest.raises` tests in `tests/test_greet.py`.
- **Spec:** [Greeting spec](../specs/GREETING.md) §3

---

### G-03 — Describe the architecture
- **Objective:** Write a README that states the module layout and typing rule.
- **Depends on:** G-02
- **Acceptance:** `README.md` exists and names `greet.py` and the `str` rule.
- **Tests:** No code tests; the reviewer checks the file.
- **Spec:** [Architecture](../architecture/ARCHITECTURE.md) §1, §2
EOF
```

```bash
cat > AGENTS.md <<'EOF'
# AGENTS.md

Rules for every agent that works in this repo.

## Project

- Stack: Python (uv)
- Test: `uv run pytest -q`

## Conventions

- Code lives at the repo root (`greet.py`); tests in `tests/`.
- Functions are typed (`str` in, `str` out). Raise `ValueError` on empty names.
- `docs/` is protected: follow it, never edit it.

## Do not

- Add dependencies without saying so in the result.
- Change files outside the task's scope.
EOF
```

### 2.4 Lock and run the tests

```bash
uv lock
```

```bash
uv run pytest -q
```

**expect:** `1 passed`

### 2.5 Install the factory

```bash
factory init
```

**expect:** `Detected stack: Python (uv); test command: uv run pytest -q`

### 2.6 Turn on the doc context (edit factory.yaml)

```bash
sed -i '' 's|  context: \[\]|  context: [docs]|' factory.yaml
```

```bash
sed -i '' 's|  rules: \[\]|  rules: ["Follow docs/specs/GREETING.md"]|' factory.yaml
```

### 2.7 Import the task cards

```bash
factory import docs/planning/TASKS.md
```

**expect:** `imported 3 of 3 task card(s) into tasks/`

### 2.8 Commit

```bash
git add -A
```

```bash
git -c user.name=kh-factory -c user.email=factory@localhost commit -m "start with docs"
```

### 2.9 Check

```bash
factory config check
```

**expect:** `OK`

```bash
factory lint
```

**expect:** `G-01 ok`, `G-02 ok`, `G-03 ok`, then `3 task(s): 0 error(s), 0 warning(s)`

```bash
factory status
```

**expect:** `G-01 todo`, `G-02 todo (waits for G-01)`, `G-03 todo (waits for G-02)`

---

## Part 3 — test with the `/factory` skill in Claude Code

### 3.1 Project A (no docs)

```bash
cd ~/tmp/factory-a
```

```bash
claude
```

**expect:** Claude Code starts. If `/factory` is not listed, exit and run
`factory install-skill`, then open a new `claude` session.

Inside Claude, type one line at a time. Wait for each report before the next:

```
/factory status
```

```
/factory add a goodbye(name) function that returns "Goodbye, <name>!"
```

**expect:** Claude writes a task, lints it, commits only that file, runs the job in the
background, and reports `awaiting-human` with the job id.

```
/factory show JOB_ID
```

```
/factory approve JOB_ID
```

**expect:** Claude merges the branch.

Check the result in a second terminal:

```bash
cd ~/tmp/factory-a && cat greet.py
```

**expect:** `goodbye` is now in the file.

```bash
cd ~/tmp/factory-a && uv run pytest -q
```

**expect:** `2 passed`

### 3.2 Project B (with docs)

```bash
cd ~/tmp/factory-b
```

```bash
claude
```

Inside Claude, one line at a time:

```
/factory status
```

**expect:** G-01 todo, G-02 waits for G-01, G-03 waits for G-02.

```
/factory next
```

**expect:** Claude runs G-01 and reports `awaiting-human`. The plan follows `GREETING.md §2` only.

```
/factory show JOB_ID
```

**expect:** phases `context → plan → build → test → review → commit`, all `passed`.

```
/factory approve JOB_ID
```

```
/factory next
```

**expect:** now G-02 runs (it follows `GREETING.md §3`, the empty-name error).

```
/factory show JOB_ID
```

```
/factory approve JOB_ID
```

Check the result in a second terminal:

```bash
cd ~/tmp/factory-b && factory status
```

**expect:** `G-01 done`, `G-02 done`, `G-03 todo`.

```bash
cd ~/tmp/factory-b && uv run pytest -q
```

**expect:** `3 passed` (hello, goodbye, and the empty-name error tests)

---

## Part 4 — compare with the raw CLI (no Claude)

This runs G-03 with plain commands.

```bash
cd ~/tmp/factory-b
```

```bash
factory next
```

**expect:** `job g-03-xxxxxx: awaiting-human` with a `ready: <sha>` line. Takes 1–4 minutes.

```bash
factory show JOB_ID
```

```bash
factory approve JOB_ID
```

```bash
factory status
```

**expect:** G-01 done, G-02 done, G-03 done.

```bash
git log --oneline -5
```

**expect:** merge commits for G-01, G-02, G-03 on `main`.

---

## Part 5 — watch a job live (optional)

Open a second terminal while any job runs:

```bash
cd ~/tmp/factory-b
```

```bash
factory ui
```

**expect:** `kh-factory UI: http://localhost:4600`. Open that URL in a browser.

---

## Start over (wipe everything and re-run)

```bash
rm -rf ~/tmp/factory-a ~/tmp/factory-a-factory
```

```bash
rm -rf ~/tmp/factory-b ~/tmp/factory-b-factory
```

Then run Part 1 or Part 2 again.
