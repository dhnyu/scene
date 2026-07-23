#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
export SCENE_PROJECT_ROOT="${PROJECT_ROOT}"
export TZ="Asia/Seoul"
export AUDIT_TIMESTAMP="${AUDIT_TIMESTAMP:-$(date '+%Y%m%d_%H%M%S_KST')}"
export PYTHONPATH="${PROJECT_ROOT}/python/audit${PYTHONPATH:+:${PYTHONPATH}}"

LOG_DIR="${PROJECT_ROOT}/logs"
RAW_DIR="${PROJECT_ROOT}/metadata/raw/${AUDIT_TIMESTAMP}"
mkdir -p "${LOG_DIR}" "${RAW_DIR}" "${PROJECT_ROOT}/reports" "${PROJECT_ROOT}/metadata"
LOG_PATH="${LOG_DIR}/${AUDIT_TIMESTAMP}_project_audit.log"
touch "${LOG_PATH}"
exec > >(tee -a "${LOG_PATH}") 2>&1

declare -a FAILED_STEPS=()

run_step() {
  local name="$1"
  shift
  printf '%s\n' '---'
  printf '[%s] START %s\n' "$(date '+%Y-%m-%d %H:%M:%S KST')" "${name}"
  local started
  started="$(date +%s)"
  if "$@"; then
    printf '[%s] OK %s elapsed=%ss\n' \
      "$(date '+%Y-%m-%d %H:%M:%S KST')" "${name}" "$(( $(date +%s) - started ))"
  else
    local status=$?
    FAILED_STEPS+=("${name}:${status}")
    printf '[%s] FAILED %s status=%s elapsed=%ss; continuing\n' \
      "$(date '+%Y-%m-%d %H:%M:%S KST')" "${name}" "${status}" \
      "$(( $(date +%s) - started ))"
  fi
}

printf 'Scene project read-only audit\n'
printf 'timestamp=%s\nproject_root=%s\ninput_root=%s\nexternal_root=%s\n' \
  "${AUDIT_TIMESTAMP}" "${PROJECT_ROOT}" "/members/dhnyu/fusedata/seoul" \
  "/members/dhnyu/fuse_external"
printf 'conda_env=%s\npython=%s\nRscript=%s\n' \
  "${CONDA_DEFAULT_ENV:-미확정}" "$(command -v python || true)" \
  "$(command -v Rscript || true)"

run_step "R environment" \
  Rscript --vanilla "${PROJECT_ROOT}/R/audit/00_check_r_environment.R"
run_step "R vector access" \
  Rscript --vanilla "${PROJECT_ROOT}/R/audit/01_audit_vector_data.R"
run_step "R Parquet access" \
  Rscript --vanilla "${PROJECT_ROOT}/R/audit/02_audit_tabular_data.R"
run_step "R raster access" \
  Rscript --vanilla "${PROJECT_ROOT}/R/audit/03_audit_raster_data.R"
run_step "R join key cross-check" \
  Rscript --vanilla "${PROJECT_ROOT}/R/audit/04_audit_cross_source_keys.R"
run_step "Python/system environment" \
  python "${PROJECT_ROOT}/python/audit/check_python_environment.py"
run_step "Input data audit" \
  python "${PROJECT_ROOT}/python/audit/audit_data.py"
run_step "External repository audit" \
  python "${PROJECT_ROOT}/python/audit/audit_external_repositories.py"
run_step "Report generation" \
  python "${PROJECT_ROOT}/python/audit/generate_reports.py" \
  --timestamp "${AUDIT_TIMESTAMP}"
run_step "Project structure inventory" \
  "${PROJECT_ROOT}/scripts/print_project_tree.sh" "${AUDIT_TIMESTAMP}"
run_step "Output validation" \
  python "${PROJECT_ROOT}/python/audit/validate_audit_outputs.py" \
  --timestamp "${AUDIT_TIMESTAMP}"

printf '%s\n' '---'
printf 'timestamp=%s\n' "${AUDIT_TIMESTAMP}"
printf 'failed_steps=%s\n' "${FAILED_STEPS[*]:-none}"
printf 'project_design=%s\n' \
  "${PROJECT_ROOT}/reports/${AUDIT_TIMESTAMP}_project_design.md"
printf 'data_audit=%s\n' \
  "${PROJECT_ROOT}/reports/${AUDIT_TIMESTAMP}_data_audit.md"
printf 'external_code_audit=%s\n' \
  "${PROJECT_ROOT}/reports/${AUDIT_TIMESTAMP}_external_code_audit.md"
printf 'summary=%s\n' \
  "${PROJECT_ROOT}/reports/${AUDIT_TIMESTAMP}_audit_summary.json"
printf 'log=%s\n' "${LOG_PATH}"

if [[ ${#FAILED_STEPS[@]} -gt 0 ]]; then
  exit 1
fi
