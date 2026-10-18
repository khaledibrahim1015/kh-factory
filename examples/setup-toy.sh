#!/usr/bin/env bash
# Copy the toy repo to a new folder, make it a git repo, and install the factory into it.
# Usage: examples/setup-toy.sh <dest-dir>
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${1:?usage: setup-toy.sh <dest-dir>}"
[ -e "$dest" ] && { echo "error: $dest already exists" >&2; exit 1; }
cp -R "$here/toy" "$dest"
cd "$dest"
git init -q -b main
uv lock -q   # commit the lock file so `uv run` does not create it inside a job
uv run --project "$here/.." factory init
git add -A
git -c user.name=kh-factory -c user.email=factory@localhost commit -q -m "toy: initial commit with factory"
echo "toy repo ready at $dest — try: cd $dest && factory status"
