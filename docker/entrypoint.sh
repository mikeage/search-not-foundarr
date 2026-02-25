#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="/app/runtime"
CRONTAB_FILE="/tmp/search-not-foundarr.cron"
DEFAULT_SCHEDULE="${ARR_DEFAULT_SCHEDULE:-*/5 * * * *}"

mkdir -p "${RUNTIME_DIR}"
rm -f "${RUNTIME_DIR}"/server_*.*

cat >"${CRONTAB_FILE}" <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EOF

mapfile -t server_indices < <(env | sed -nE 's/^SERVER_([0-9]+)_.*/\1/p' | sort -n | uniq)

if [[ "${#server_indices[@]}" -eq 0 ]]; then
  echo "ERROR: no SERVER_<n>_* variables found; nothing to schedule" >&2
  exit 1
fi

job_count=0
for idx in "${server_indices[@]}"; do
  type_var="SERVER_${idx}_TYPE"
  host_var="SERVER_${idx}_HOSTNAME"
  key_var="SERVER_${idx}_API_KEY"
  schedule_var="SERVER_${idx}_SCHEDULE"
  missing_var="SERVER_${idx}_MISSING_WEIGHT"
  cutoff_var="SERVER_${idx}_CUTOFF_UNMET_WEIGHT"

  server_type="${!type_var:-}"
  server_host="${!host_var:-}"
  server_key="${!key_var:-}"
  server_schedule="${!schedule_var:-${DEFAULT_SCHEDULE}}"
  missing_weight="${!missing_var:-}"
  cutoff_weight="${!cutoff_var:-}"

  if [[ -z "${server_type}" || -z "${server_host}" || -z "${server_key}" ]]; then
    echo "ERROR: server ${idx} requires ${type_var}, ${host_var}, and ${key_var}" >&2
    exit 1
  fi

  printf '%s' "${server_type}" >"${RUNTIME_DIR}/server_${idx}.type"
  printf '%s' "${server_host}" >"${RUNTIME_DIR}/server_${idx}.hostname"
  printf '%s' "${server_key}" >"${RUNTIME_DIR}/server_${idx}.api_key"

  if [[ -n "${missing_weight}" ]]; then
    printf '%s' "${missing_weight}" >"${RUNTIME_DIR}/server_${idx}.missing_weight"
  fi
  if [[ -n "${cutoff_weight}" ]]; then
    printf '%s' "${cutoff_weight}" >"${RUNTIME_DIR}/server_${idx}.cutoff_unmet_weight"
  fi

  chmod 600 "${RUNTIME_DIR}/server_${idx}.type" "${RUNTIME_DIR}/server_${idx}.hostname" "${RUNTIME_DIR}/server_${idx}.api_key"
  [[ -f "${RUNTIME_DIR}/server_${idx}.missing_weight" ]] && chmod 600 "${RUNTIME_DIR}/server_${idx}.missing_weight"
  [[ -f "${RUNTIME_DIR}/server_${idx}.cutoff_unmet_weight" ]] && chmod 600 "${RUNTIME_DIR}/server_${idx}.cutoff_unmet_weight"

  echo "${server_schedule} /app/docker/run_server.sh ${idx} >> /proc/1/fd/1 2>> /proc/1/fd/2" >>"${CRONTAB_FILE}"
  echo "INFO: scheduled SERVER_${idx} (${server_type} @ ${server_host}) on '${server_schedule}'"
  job_count=$((job_count + 1))
done

if [[ "${job_count}" -eq 0 ]]; then
  echo "ERROR: no valid server jobs configured" >&2
  exit 1
fi

crontab "${CRONTAB_FILE}"
echo "INFO: installed ${job_count} cron job(s)"
echo "INFO: ARR_SEARCH_COOLDOWN_HOURS=${ARR_SEARCH_COOLDOWN_HOURS:-24}"
echo "INFO: ARR_PAGE_SIZE=${ARR_PAGE_SIZE:-250}"
echo "INFO: ARR_STATE_FILE=${ARR_STATE_FILE:-/root/.local/state/search-not-foundarr/state.json}"
echo "INFO: ARR_DEFAULT_SCHEDULE=${DEFAULT_SCHEDULE}"

exec cron -f
