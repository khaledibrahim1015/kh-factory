"""Git helpers: one worktree + branch per job. The factory never pushes."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Commits made by the factory need an identity even on a fresh machine.
IDENTITY = ["-c", "user.name=kh-factory", "-c", "user.email=factory@localhost"]


class GitError(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}:\n{p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def repo_root(start: Path) -> Path:
    try:
        return Path(git(start, "rev-parse", "--show-toplevel").strip())
    except GitError as e:
        raise GitError(f"{start} is not inside a git repository. Run `git init` first.") from e


def has_commits(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root,
                          capture_output=True).returncode == 0


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def rev(root: Path, ref: str = "HEAD") -> str:
    return git(root, "rev-parse", ref).strip()


def worktrees_dir(root: Path) -> Path:
    return root.parent / f"{root.name}-factory"


def create(root: Path, job_id: str, base: str) -> tuple[Path, str]:
    branch = f"factory/{job_id}"
    path = worktrees_dir(root) / job_id
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-b", branch, str(path), base)
    return path, branch


def remove(root: Path, path: Path, branch: str | None = None) -> None:
    if path.exists():
        git(root, "worktree", "remove", "--force", str(path), check=False)
    git(root, "worktree", "prune", check=False)
    if branch:
        git(root, "branch", "-D", branch, check=False)


def changed_files(wt: Path) -> list[str]:
    """Tracked changes vs HEAD plus untracked files (not ignored)."""
    out = git(wt, "status", "--porcelain", "-uall", "-z")
    files = []
    items = out.split("\0")
    i = 0
    while i < len(items):
        item = items[i]
        if not item:
            i += 1
            continue
        status, path = item[:2], item[3:]
        files.append(path)
        if status[0] in "RC":  # rename/copy: next item is the source path
            i += 1
        i += 1
    return sorted(set(files))


def commit_all(wt: Path, message: str) -> str | None:
    git(wt, "add", "-A")
    if not git(wt, "diff", "--cached", "--name-only").strip():
        return None
    git(wt, *IDENTITY, "commit", "-q", "-m", message)
    return rev(wt)


def diff(wt: Path, base: str) -> str:
    """Committed + uncommitted + untracked changes since base."""
    git(wt, "add", "-A", "--intent-to-add")
    return git(wt, "diff", base)


def reset_hard(wt: Path) -> None:
    """Drop uncommitted changes and untracked files in a job worktree (never the main checkout)."""
    git(wt, "reset", "-q", "--hard", "HEAD")
    git(wt, "clean", "-q", "-fd")


def diff_stat(wt: Path, base: str) -> str:
    """`git diff --stat` since base, including untracked files (call after diff())."""
    return git(wt, "diff", "--stat", base).rstrip()


def merge(root: Path, branch: str, message: str) -> str:
    p = subprocess.run(["git", *IDENTITY, "merge", "--no-ff", "-m", message, branch], cwd=root,
                       capture_output=True, text=True)
    if p.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=root, capture_output=True)
        raise GitError(f"merge of {branch} failed and was aborted:\n{p.stdout}{p.stderr}")
    return rev(root)


# Build and test caches must never look like agent changes. They go to the repo's
# local exclude file (.git/info/exclude), which is not committed and is shared by worktrees.
CACHE_EXCLUDES = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
                  ".venv/", "node_modules/", ".DS_Store", ".factory/jobs/", ".factory/trace.db*"]
MARK = "# kh-factory caches"


def ensure_excludes(root: Path) -> None:
    common = Path(git(root, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = root / common
    path = common / "info" / "exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    if MARK in text:
        return
    path.write_text(text.rstrip("\n") + f"\n{MARK}\n" + "\n".join(CACHE_EXCLUDES) + "\n")


def commits_ahead(root: Path, base: str, branch: str) -> int:
    return int(git(root, "rev-list", "--count", f"{base}..{branch}").strip() or 0)
