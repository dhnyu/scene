#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
export TZ="Asia/Seoul"
TIMESTAMP="${1:-${AUDIT_TIMESTAMP:-$(date '+%Y%m%d_%H%M%S_KST')}}"
OUTPUT="${PROJECT_ROOT}/metadata/${TIMESTAMP}_project_structure.txt"

{
  printf 'generated_at_kst=%s\n' "$(date '+%Y-%m-%d %H:%M:%S KST')"
  printf 'project_root=%s\n' "${PROJECT_ROOT}"
  find "${PROJECT_ROOT}" \
    \( -path "${PROJECT_ROOT}/.git" \
       -o -path "${PROJECT_ROOT}/outputs" \
       -o -path "${PROJECT_ROOT}/artifacts" \
       -o -path "${PROJECT_ROOT}/tmp" \
       -o -path "${PROJECT_ROOT}/metadata/raw" \
       -o -path '*/__pycache__' \) -prune \
    -o -printf '%y\t%P\n' | LC_ALL=C sort
} > "${OUTPUT}"

printf 'Project structure inventory: %s\n' "${OUTPUT}"
