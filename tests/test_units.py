from __future__ import annotations

import json
from pathlib import Path

import pytest

from kh_factory import config, context, envelope, tasks
from kh_factory.runners.claude import ClaudeRunner, parse_claude
from kh_factory.runners.pi import PiRunner, parse_pi
from kh_factory.runners.base import RunRequest


# ---- config ----

def test_defaults_merge_and_override(repo):
    cfg = config.load(repo)
    assert cfg.agents["builder"].runner == "fake"
    assert cfg.agents["builder"].writes == ["**"]
    assert "docs/" in cfg.protected  # context folders are protected
    assert config.check(cfg, check_models=False) == []


def test_check_reports_problems(repo):
    raw = {
        "defaults": {"runner": "fake", "model": "m", "tools": ["read"]},
        "agents": {"planner": {"writes": ["tasks/x.md"],
                               "prompts": {"system": "nope.md", "user": "nope.md"}}},
        "pipeline": {"build": "ghost"},
    }
    problems = config.check(config.parse(raw, repo), check_models=False)
    text = "\n".join(problems)
    assert "pipeline.build: agent 'ghost'" in text
    assert "prompts.system: file not found" in text
    assert "overlaps protected path 'tasks/'" in text


def test_invalid_field_is_reported(repo):
    with pytest.raises(config.ConfigError, match="thinking"):
        config.parse({"defaults": {"runner": "pi", "model": "m", "tools": [], "thinking": "huge"},
                      "agents": {"a": {"prompts": {"system": "s", "user": "u"}}}}, repo)


# ---- tasks ----

def test_add_and_parse(repo):
    p = tasks.add(repo, "Second thing")
    t = tasks.parse(p)
    assert t.id == "T-002" and t.title == "Second thing"
    assert "Objective" in t.body
    assert tasks.find(repo, "T-001").spec == ["docs/SPEC.md#1"]


CARDS = """# Tasks

### FND-01 — Repository bootstrap
- **Objective:** monorepo with tiers.
- **Depends on:** —
- **Tests:** fixture packages fail.
- **Acceptance:** a bad import fails CI.
- **Spec:** [ARCHITECTURE.md](../architecture/ARCHITECTURE.md) §5.2, [CODEBASE.md](../specs/CODEBASE.md) §3–§4.

---

### FND-02 — Second card
- **Objective:** something
  that continues.
- **Depends on:** FND-01
- **Acceptance:** works.
"""


def test_import_cards(tmp_path: Path):
    src = tmp_path / "docs" / "planning" / "TASKS.md"
    src.parent.mkdir(parents=True)
    src.write_text(CARDS)
    out = tasks.parse_cards(src, tmp_path)
    assert len(out) == 2
    d = tmp_path / "tasks"
    d.mkdir()
    for i, text in enumerate(out):
        (d / f"{i}.md").write_text(text)
    t1, t2 = tasks.load_all(tmp_path)
    assert t1.id == "FND-01" and t1.depends_on == []
    assert t1.spec == ["docs/architecture/ARCHITECTURE.md#5.2", "docs/specs/CODEBASE.md#3",
                       "docs/specs/CODEBASE.md#4"]
    assert t2.depends_on == ["FND-01"]
    assert "that continues" in t2.section("Objective")


# ---- context ----

def test_cut_section_numbered_and_text():
    text = "# T\n\n## 1. One\n\na\n\n### 1.1 Sub\n\nb\n\n## 2. Two\n\nc\n"
    assert context.cut_section(text, "1").startswith("## 1. One")
    assert "1.1 Sub" in context.cut_section(text, "1") and "Two" not in context.cut_section(text, "1")
    assert context.cut_section(text, "1.1").startswith("### 1.1 Sub")
    assert context.cut_section(text, "two").startswith("## 2. Two")
    assert context.cut_section(text, "9") is None


def test_spec_ref_not_found(repo):
    with pytest.raises(context.ContextError) as e:
        context.spec_sections(repo, ["docs/SPEC.md#7"])
    assert e.value.code == "SPEC_REF_NOT_FOUND"
    with pytest.raises(context.ContextError):
        context.spec_sections(repo, ["docs/NOPE.md"])


def test_budget(repo):
    cfg = config.load(repo)
    cfg.project.context_budget_tokens = 10
    with pytest.raises(context.ContextError) as e:
        context.build(cfg, layers={"Task": "x" * 400})
    assert e.value.code == "CONTEXT_TOO_LARGE"


def test_repo_rules_dedupes_claude_pointer(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use tabs.")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md")
    assert context.repo_rules(tmp_path) == "### AGENTS.md\n\nUse tabs."


# ---- envelope ----

def test_envelope_fenced_and_bare():
    e = envelope.parse('text\n```json\n{"verdict": "PASS", "summary": "s"}\n```', envelope.ReviewOutput)
    assert e.verdict == "PASS"
    e = envelope.parse('ok {"verdict": "CHANGES", "summary": "s", "findings": '
                       '[{"severity": "blocking", "message": "m"}]} end', envelope.ReviewOutput)
    assert e.findings[0].severity == "blocker" and e.findings[0].blocking  # old name still accepted


def test_envelope_errors_are_actionable():
    with pytest.raises(ValueError, match="no JSON"):
        envelope.parse("no json here", envelope.BuildOutput)
    with pytest.raises(ValueError, match="changed_files"):
        envelope.parse('{"summary": "s", "commit_message": "m"}', envelope.BuildOutput)


# ---- runner parsers (event shapes captured from pi 1.3.0 and claude 2.1.302) ----

def test_parse_pi_ok_and_error():
    ok = [{"type": "session", "id": "S1"},
          {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "q"}]}},
          {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                                              "content": [{"type": "thinking"}, {"type": "text", "text": "A"}],
                                              "usage": {"cost": {"total": 0.01}}}}]
    r = parse_pi(ok, "x")
    assert (r.text, r.session_id, r.is_error, r.cost_usd) == ("A", "S1", False, 0.01)
    bad = [{"type": "stderr", "text": "Warning: Model not found"},
           {"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "content": [],
                                               "errorMessage": "400: Model is unavailable."}}]
    r = parse_pi(bad, "x")
    assert r.is_error and "Model is unavailable" in r.error


def test_parse_claude_is_error_wins_over_subtype():
    r = parse_claude([{"type": "result", "subtype": "success", "is_error": True, "api_error_status": 404,
                       "result": "There's an issue with the selected model", "session_id": "S"}], "x")
    assert r.is_error and "404" in r.error
    r = parse_claude([{"type": "result", "subtype": "success", "is_error": False, "result": "hi",
                       "session_id": "S", "total_cost_usd": 0.002}], "x")
    assert (r.text, r.session_id, r.is_error) == ("hi", "S", False)


def test_commands(repo, tmp_path):
    cfg = config.load(repo)
    agent = cfg.agents["builder"].model_copy(update={"runner": "claude", "model": "opus"})
    req = RunRequest(agent=agent, prompt="p", system="sys", cwd=repo, session_id="S", resume=True,
                     session_dir=tmp_path, log_path=tmp_path / "l")
    cmd = ClaudeRunner(config.RunnerCfg(bin="claude")).command(req)
    assert cmd[cmd.index("--resume") + 1] == "S"
    assert cmd[-2] == "--allowedTools"  # variadic flag must be last
    assert "p" not in cmd  # prompt goes on stdin
    cmd = PiRunner(config.RunnerCfg(bin="pi")).command(req)
    assert cmd[cmd.index("--session-id") + 1] == "S" and "--mode" in cmd


# ---- install-skill ----

def test_install_skill_user_level_is_a_symlink(tmp_path, monkeypatch, repo):
    from kh_factory import cli
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)
    dest = tmp_path / "home" / ".claude" / "skills" / "factory" / "SKILL.md"
    assert cli.main(["install-skill"]) == 0
    assert dest.is_symlink() and dest.resolve() == cli._skill_source().resolve()
    assert dest.read_text().startswith("---\nname: factory\n")
    assert cli.main(["install-skill"]) == 0              # same link: up to date
    dest.unlink()
    dest.write_text("local edit")
    assert cli.main(["install-skill"]) == 2              # a different file: refuses without --force
    assert cli.main(["install-skill", "--force"]) == 0 and dest.is_symlink()
    assert cli.main(["install-skill", "--copy", "--force"]) == 0
    assert dest.is_file() and not dest.is_symlink()


def test_install_skill_project_level_is_a_copy(repo, monkeypatch):
    from kh_factory import cli
    monkeypatch.chdir(repo)
    assert cli.main(["install-skill", "--project"]) == 0
    dest = repo / ".claude" / "skills" / "factory" / "SKILL.md"
    assert dest.is_file() and not dest.is_symlink()


def test_init_with_skill_and_claude_dir_protected(tmp_path, monkeypatch):
    import subprocess
    from kh_factory import cli
    root = tmp_path / "fresh"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    monkeypatch.chdir(root)
    assert cli.main(["init", "--with-skill"]) == 0
    assert (root / ".claude" / "skills" / "factory" / "SKILL.md").is_file()
    cfg = config.load(root)
    assert ".claude/" in cfg.protected
    assert config.parse({}, root).protected_files == config.DEFAULT_PROTECTED


# ---- context measurement (as sssf does it) ----

def test_parse_pi_context_from_last_valid_turn():
    good = {"role": "assistant", "stopReason": "toolUse", "content": [],
            "usage": {"totalTokens": 1200, "cost": {"total": 0.01}}}
    final = {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "A"}],
             "usage": {"input": 1500, "output": 20, "cacheRead": 0, "cacheWrite": 0}}
    r = parse_pi([{"type": "message_end", "message": good}, {"type": "message_end", "message": final}], "s")
    assert r.context_tokens == 1520
    bad = {"role": "assistant", "stopReason": "error", "content": [], "errorMessage": "x",
           "usage": {"totalTokens": 99999}}
    r = parse_pi([{"type": "message_end", "message": good}, {"type": "message_end", "message": bad}], "s")
    assert r.is_error and r.context_tokens == 1200  # the errored turn does not overwrite it


def test_parse_claude_context_and_window():
    events = [{"type": "assistant", "message": {"usage": {"input_tokens": 2, "cache_creation_input_tokens": 13745,
                                                          "cache_read_input_tokens": 8867, "output_tokens": 8}}},
              {"type": "result", "is_error": False, "result": "ok", "session_id": "S",
               "modelUsage": {"claude-haiku-5-5": {"contextWindow": 1000000}}}]
    r = parse_claude(events, "x")
    assert (r.context_tokens, r.context_window) == (22622, 1000000)


def test_parse_size_and_pi_catalog(monkeypatch):
    assert [config.parse_size(x) for x in ("200K", "1.0M", "262.1K", "?")] == [200000, 1000000, 262100, 0]
    out = ("provider     model        context  max-out  thinking  images\n"
           "opencode-go  kimi-k3      1.0M     131.1K   yes       yes\n"
           "opencode-go  kimi-k2.7-code  262.1K  262.1K  yes   yes\n")

    class P:
        stdout = out
    config.pi_catalog.cache_clear()
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: P())
    assert config.pi_catalog("fake-pi") == {"opencode-go/kimi-k3": 1000000, "opencode-go/kimi-k2.7-code": 262100}
    config.pi_catalog.cache_clear()


def test_old_trace_db_is_migrated(tmp_path):
    import sqlite3
    from kh_factory.trace import Trace
    db = tmp_path / ".factory" / "trace.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE phases (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, name TEXT NOT NULL, "
                "kind TEXT NOT NULL, owner TEXT, status TEXT NOT NULL, detail TEXT, started_at REAL, ended_at REAL)")
    con.commit()
    con.close()
    t = Trace(tmp_path)
    cols = {r["name"] for r in t.query("PRAGMA table_info(phases)")}
    assert {"context_tokens", "context_window"} <= cols
    assert "max_context_pct" in {r["name"] for r in t.query("PRAGMA table_info(jobs)")}


def test_init_update_prompts_keeps_config(tmp_path, monkeypatch):
    import subprocess
    from kh_factory import cli
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    monkeypatch.chdir(root)
    assert cli.main(["init"]) == 0
    (root / "factory.yaml").write_text((root / "factory.yaml").read_text() + "# my edit\n")
    prompt = root / ".factory" / "prompts" / "reviewer" / "system.md"
    prompt.write_text("old prompt")
    assert cli.main(["init", "--update-prompts"]) == 0
    assert "Previous review" in prompt.read_text()
    assert (root / "factory.yaml").read_text().endswith("# my edit\n")
