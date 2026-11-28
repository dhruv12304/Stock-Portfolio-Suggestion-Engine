#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

cd "${ROOT_DIR}"

require_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n%s\n' "${cmd}" "${hint}" >&2
    exit 1
  fi
}

require_cmd "python3" "Install Python 3.11+ and rerun this script."
require_cmd "npm" "Install Node.js 20+ (includes npm) and rerun this script."

printf 'Creating virtual environment in %s\n' "${VENV_DIR}"
python3 -m venv "${VENV_DIR}"

printf 'Upgrading pip\n'
"${VENV_DIR}/bin/pip" install --upgrade pip

printf 'Installing backend dependencies\n'
"${VENV_DIR}/bin/pip" install -r api/requirements.txt

printf 'Installing frontend dependencies\n'
(cd web && npm install)

printf '\nInstall complete.\n'
printf 'Next steps:\n'
printf '  make dev\n'
printf '  open http://localhost:5173\n'
