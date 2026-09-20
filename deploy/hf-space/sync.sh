#!/usr/bin/env bash
# Assemble the Space contents (backend app + Dockerfile + README) in a folder.
# The folder can be a git clone of the Space or a plain directory for `hf upload`.
# usage: deploy/hf-space/sync.sh [folder]        (default: .runtime/hf-space, gitignored)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
dest=${1:-$root/.runtime/hf-space}
mkdir -p "$dest"
dest=$(cd "$dest" && pwd)

rm -rf "$dest/app"
mkdir -p "$dest/app"
(cd "$root/backend" && tar --exclude='__pycache__' --exclude='*.pyc' -cf - app) | tar -xf - -C "$dest"
cp "$root/backend/pyproject.toml" "$here/Dockerfile" "$here/README.md" "$dest/"
printf '__pycache__/\n*.pyc\n.env\n' > "$dest/.gitignore"

# Refuse to continue if anything that looks like a credential was copied.
if grep -rIlE '(sk-[A-Za-z0-9_-]{20,}|ApiKey [A-Za-z0-9=]{20,}|hf_[A-Za-z0-9]{30,})' "$dest" --exclude-dir=.git; then
  echo "A file above looks like it contains a secret; not safe to push." >&2
  exit 1
fi
echo "Synced to $dest"
echo "Upload with: hf upload <hf-username>/nullmap-api $dest . --repo-type space --exclude '.git/*'"
