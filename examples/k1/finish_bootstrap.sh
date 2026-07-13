#!/usr/bin/env bash
# Resume K1 bootstrap after alpha fixes were applied (pip install + pytest only).
set -euo pipefail

K1_ROOT="${K1_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
USER_NAME="${SUDO_USER:-$(whoami)}"

if [[ "$(id -u)" -eq 0 ]]; then
  chown "${USER_NAME}:${USER_NAME}" "${K1_ROOT}"
  chown -R "${USER_NAME}:${USER_NAME}" "${K1_ROOT}/.pytest_cache" 2>/dev/null || true
  rm -rf "${K1_ROOT}/k1.egg-info" "${K1_ROOT}/build" "${K1_ROOT}/dist" 2>/dev/null || true
  exec sudo -u "${USER_NAME}" env K1_ROOT="${K1_ROOT}" "$0"
fi

cd "${K1_ROOT}"

if [[ ! -x "${K1_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${K1_ROOT}/.venv"
fi

"${K1_ROOT}/.venv/bin/pip" install -U pip -q
"${K1_ROOT}/.venv/bin/pip" install -e "${K1_ROOT}[dev]" -q
"${K1_ROOT}/.venv/bin/pytest" tests -q --basetemp=/tmp/k1-pytest

echo ""
echo "Bootstrap complete. Next:"
echo "  sim k1 preflight --k1-root ${K1_ROOT}"
echo "  sudo cp deploy/systemd/*.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now k1-runtime k1-openwebui-bridge"
