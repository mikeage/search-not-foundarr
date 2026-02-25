#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "Usage: run_server.sh <server_index>" >&2
  exit 2
fi

idx="$1"
runtime_dir="/app/runtime"

type_file="${runtime_dir}/server_${idx}.type"
host_file="${runtime_dir}/server_${idx}.hostname"
key_file="${runtime_dir}/server_${idx}.api_key"
missing_file="${runtime_dir}/server_${idx}.missing_weight"
cutoff_file="${runtime_dir}/server_${idx}.cutoff_unmet_weight"

if [[ ! -f "${type_file}" || ! -f "${host_file}" || ! -f "${key_file}" ]]; then
  echo "ERROR: missing config file(s) for SERVER_${idx}" >&2
  exit 1
fi

arr_type="$(cat "${type_file}")"
arr_host="$(cat "${host_file}")"
arr_api_key="$(cat "${key_file}")"

args=(--type "${arr_type}" --hostname "${arr_host}")
if [[ -f "${missing_file}" ]]; then
  args+=(--missing-weight "$(cat "${missing_file}")")
fi
if [[ -f "${cutoff_file}" ]]; then
  args+=(--cutoff-unmet-weight "$(cat "${cutoff_file}")")
fi

export ARR_API_KEY="${arr_api_key}"

echo "INFO: starting SERVER_${idx} (${arr_type} @ ${arr_host})"
exec uv run /app/search_not_foundarr.py "${args[@]}"
