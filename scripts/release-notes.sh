#!/usr/bin/env bash
# Prints the CHANGELOG.md section for one version, without its heading:
#
#   scripts/release-notes.sh 1.2.3
#
# Both build workflows use it: first as a check that VERSION has a changelog
# entry (the build fails without one), then as the release notes.
set -euo pipefail

version="${1:?usage: release-notes.sh VERSION}"
changelog="$(dirname "$0")/../CHANGELOG.md"

# Lines after "## [version]" up to the next "## [" heading.
notes="$(awk -v head="## [$version]" '
  index($0, "## [") == 1 { inside = (index($0, head) == 1); next }
  inside
' "$changelog")"

# Trim blank lines at both ends.
notes="$(printf '%s\n' "$notes" | sed -e '/./,$!d' | sed -e ':a' -e '/^\n*$/{$d;N;ba' -e '}')"

if [ -z "$notes" ]; then
  echo "CHANGELOG.md has no entry for $version. Add a '## [$version] - YYYY-MM-DD' section." >&2
  exit 1
fi

printf '%s\n' "$notes"
