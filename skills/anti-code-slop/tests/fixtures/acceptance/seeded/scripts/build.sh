#!/usr/bin/env bash
# ############################################
set -euo pipefail

out_dir="${1:-out}"
files=("$@")
echo "building ${#files[@]} inputs into $out_dir"
# shellcheck disable=SC2086
mkdir -p $out_dir
rm -r "$out_dir/stale" 2>/dev/null || true
sleep 0.1
echo "#done" > "$out_dir/status"
