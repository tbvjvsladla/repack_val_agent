#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
bash "$here/build.sh" "$work/out" a b
test -f "$work/out/status"
grep -q '#done' "$work/out/status"
echo "check ok"
