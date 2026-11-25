#!/bin/sh
# Did anything that goes into the Docker image change between two commits?
#
#   scripts/image-changed.sh <before> <after>
#
# Exits 0 (yes, build) or 1 (no, only docs, the blueprint, examples etc.).
# Used by both workflows so a docs- or blueprint-only push doesn't rebuild.
# When <before> is unknown (first push, force push, manual run) it says yes.
#
# Keep the list in step with .dockerignore: these are the files the image
# is built from. VERSION is left out on purpose: a version bump on its own
# releases by tagging the existing image instead of rebuilding it.
set -eu

before="${1:-}"
after="${2:-HEAD}"

if [ -z "$before" ] || ! git cat-file -e "$before^{commit}" 2>/dev/null; then
  echo "No earlier commit to compare with: treating the image as changed." >&2
  exit 0
fi

if git diff --name-only "$before" "$after" \
   | grep -Eq '^(app/|tests/|Dockerfile$|requirements\.txt$|\.dockerignore$)'; then
  echo "Image files changed since $(git rev-parse --short "$before")." >&2
  exit 0
fi

echo "No image files changed since $(git rev-parse --short "$before")." >&2
exit 1
