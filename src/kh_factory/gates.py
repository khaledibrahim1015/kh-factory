"""Gates: code checks the agent's claims after each agent step.

Soft gates (artifacts, claims) send the agent a correction in the same session.
The writes boundary is hard: changes outside `writes` are reverted and the step fails.
"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import worktree
from .config import Agent


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""


def artifacts_exist(paths: list[Path]) -> GateResult:
    missing = [str(p) for p in paths if not p.is_file()]
    return GateResult("artifacts_exist", not missing, f"missing: {', '.join(missing)}" if missing else "")


def files_non_empty(paths: list[Path]) -> GateResult:
    empty = [str(p) for p in paths if p.is_file() and not p.read_text().strip()]
    return GateResult("files_non_empty", not empty, f"empty: {', '.join(empty)}" if empty else "")


def diff_matches_claims(touched: list[str], all_changed: list[str], claimed: list[str]) -> GateResult:
    """Every file touched in this step must be claimed; every claimed file must really differ from HEAD."""
    claimed_s = {c.removeprefix("./") for c in claimed}
    unclaimed = sorted(set(touched) - claimed_s)
    phantom = sorted(claimed_s - set(all_changed) - set(touched))
    problems = []
    if unclaimed:
        problems.append(f"changed but not listed in changed_files: {', '.join(unclaimed)}")
    if phantom:
        problems.append(f"listed in changed_files but not changed: {', '.join(phantom)}")
    return GateResult("diff_matches_claims", not problems, "; ".join(problems))


def claims(wt: Path, snap: "Snapshot", claimed: list[str]) -> GateResult:
    return diff_matches_claims(touched_since(wt, snap), worktree.changed_files(wt), claimed)


# ---- writes boundary ----

def matches(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p in ("**", "*"):
            return True
        if p.endswith("/") and (path.startswith(p) or path == p.rstrip("/")):
            return True
        if fnmatch.fnmatch(path, p) or path == p:
            return True
    return False


@dataclass
class Snapshot:
    """State of the dirty files before an agent step, so we can restore them."""
    hashes: dict[str, str]
    contents: dict[str, bytes | None]  # None = file did not exist


def _hash(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else "-"


def snapshot(wt: Path) -> Snapshot:
    files = worktree.changed_files(wt)
    hashes, contents = {}, {}
    for f in files:
        p = wt / f
        hashes[f] = _hash(p)
        contents[f] = p.read_bytes() if p.is_file() else None
    return Snapshot(hashes, contents)


def touched_since(wt: Path, snap: Snapshot) -> list[str]:
    now = worktree.changed_files(wt)
    touched = [f for f in now if snap.hashes.get(f) != _hash(wt / f)]
    # files that were dirty before and are now back to HEAD also count as touched
    touched += [f for f in snap.hashes if f not in now]
    return sorted(set(touched))


def writes_boundary(wt: Path, snap: Snapshot, agent: Agent, protected: list[str]) -> GateResult:
    touched = touched_since(wt, snap)
    bad = [f for f in touched if matches(f, protected) or not matches(f, agent.writes)]
    if not bad:
        return GateResult("writes_boundary", True)
    for f in bad:
        _restore(wt, f, snap)
    return GateResult("writes_boundary", False,
                      f"agent '{agent.name}' changed paths outside its `writes`/protected rules; "
                      f"reverted: {', '.join(bad)}")


def _restore(wt: Path, f: str, snap: Snapshot) -> None:
    p = wt / f
    if f in snap.contents:
        before = snap.contents[f]
        if before is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(before)
        return
    tracked = worktree.git(wt, "ls-files", "--", f).strip()
    if tracked:
        worktree.git(wt, "checkout", "HEAD", "--", f)
    else:
        p.unlink(missing_ok=True)
