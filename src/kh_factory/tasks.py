"""Task files: tasks/<id>.md with YAML frontmatter and markdown sections.

Task status is not stored in the file. It comes from the job history in the
trace database, so the factory never leaves the repo dirty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TASKS_DIR = "tasks"
FRONT = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)


@dataclass
class Task:
    id: str
    title: str
    path: Path
    body: str
    depends_on: list[str] = field(default_factory=list)
    spec: list[str] = field(default_factory=list)

    def notes_mention_owner(self) -> bool:
        """Notes name another task id (the one that builds the real thing)."""
        return bool(re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", self.section("Notes")))

    def section(self, name: str) -> str:
        """Text under `## <name>`, or ''."""
        m = re.search(rf"^##\s+{re.escape(name)}\s*\n(.*?)(?=^##\s|\Z)", self.body, re.S | re.M | re.I)
        return m.group(1).strip() if m else ""


class TaskError(Exception):
    pass


def parse(path: Path) -> Task:
    m = FRONT.match(path.read_text())
    if not m:
        raise TaskError(f"{path}: missing '---' YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    if "id" not in meta or "title" not in meta:
        raise TaskError(f"{path}: frontmatter needs 'id' and 'title'")
    return Task(id=str(meta["id"]), title=str(meta["title"]), path=path, body=m.group(2),
                depends_on=[str(x) for x in meta.get("depends_on") or []],
                spec=[str(x) for x in meta.get("spec") or []])


def load_all(root: Path) -> list[Task]:
    d = root / TASKS_DIR
    if not d.exists():
        return []
    tasks = [parse(p) for p in sorted(d.glob("*.md"))]
    seen: dict[str, Path] = {}
    for t in tasks:
        if t.id in seen:
            raise TaskError(f"duplicate task id {t.id}: {seen[t.id]} and {t.path}")
        seen[t.id] = t.path
    return tasks


def find(root: Path, task_id: str) -> Task:
    for t in load_all(root):
        if t.id == task_id:
            return t
    raise TaskError(f"task '{task_id}' not found in {root / TASKS_DIR}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "task"


def render(task_id: str, title: str, *, objective: str = "", acceptance: list[str] | None = None,
           tests: str = "", notes: str = "", depends_on: list[str] | None = None,
           spec: list[str] | None = None) -> str:
    meta = {"id": task_id, "title": title, "depends_on": depends_on or [], "spec": spec or []}
    acc = "\n".join(f"- {a}" for a in (acceptance or [])) or "- (write what must be true when this task is done)"
    return (f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()}\n---\n\n"
            f"## Objective\n\n{objective or title}\n\n"
            f"## Acceptance\n\n{acc}\n\n"
            f"## Tests\n\n{tests or '(which tests prove it)'}\n\n"
            f"## Notes\n\n{notes}\n")


def next_id(root: Path, prefix: str = "T") -> str:
    nums = [int(m.group(1)) for t in load_all(root) if (m := re.fullmatch(rf"{prefix}-(\d+)", t.id))]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


def add(root: Path, title: str) -> Path:
    tid = next_id(root)
    path = root / TASKS_DIR / f"{tid}-{slug(title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(tid, title))
    return path


# ---- import of structured task cards (e.g. Nizk docs/planning/TASKS.md) ----

CARD = re.compile(r"^###\s+([A-Z][A-Z0-9]*-\d+)\s+[—–-]\s+(.+?)\s*$", re.M)
FIELD = re.compile(r"^- \*\*([^*]+?):\*\*\s?(.*)$")
LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)\s*((?:§[\w.]+(?:\s*[–-]\s*§?[\w.]+)?(?:,\s*)?)*)")
IDS = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


def parse_cards(source: Path, repo_root: Path) -> list[str]:
    """Convert '### ID — Title' cards with '- **Field:** value' bullets into task files.

    Returns the markdown text of each task file (caller writes them).
    """
    text = source.read_text()
    starts = list(CARD.finditer(text))
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[m.end():end].split("\n---", 1)[0]
        fields = _card_fields(block)
        deps = IDS.findall(fields.get("depends on", ""))
        spec = _spec_refs(fields.get("spec", ""), source, repo_root)
        acceptance = [fields["acceptance"]] if fields.get("acceptance") else []
        notes = "\n\n".join(f"**{k.title()}:** {v}" for k, v in fields.items()
                            if k not in ("objective", "acceptance", "tests", "depends on", "spec"))
        out.append(render(m.group(1), m.group(2), objective=fields.get("objective", ""), acceptance=acceptance,
                          tests=fields.get("tests", ""), notes=notes, depends_on=deps, spec=spec))
    return out


def _card_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current = None
    for line in block.splitlines():
        fm = FIELD.match(line)
        if fm:
            current = fm.group(1).strip().lower()
            fields[current] = fm.group(2).strip()
        elif current and line.startswith("  "):
            fields[current] += " " + line.strip()
        elif line.strip() == "":
            continue
        else:
            current = None
    return fields


def _spec_refs(spec_field: str, source: Path, repo_root: Path) -> list[str]:
    refs = []
    for link, sections in LINK.findall(spec_field):
        target = (source.parent / link).resolve()
        try:
            rel = target.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = link
        secs = re.findall(r"§?([\w.]+)", sections.replace("–", " ").replace(",", " "))
        if secs:
            refs += [f"{rel}#{s.rstrip('.')}" for s in secs if s.rstrip('.')]
        else:
            refs.append(rel)
    return refs
