#!/usr/bin/env bash
# Bootstrap K1 for Alpha Part I — fixes ownership, applies patches, installs deps, runs tests.
set -euo pipefail

K1_ROOT="${K1_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
SIM_ROOT="${K1_ROOT}/SIM"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)/patches"
USER_NAME="${SUDO_USER:-$(whoami)}"

echo "K1 Alpha bootstrap"
echo "  K1_ROOT=${K1_ROOT}"
echo "  patches=${PATCH_DIR}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo so K1 source files can be updated:" >&2
  echo "  sudo K1_ROOT=${K1_ROOT} $0" >&2
  exit 1
fi

chown "${USER_NAME}:${USER_NAME}" "${K1_ROOT}"
chown -R "${USER_NAME}:${USER_NAME}" "${K1_ROOT}/src" "${K1_ROOT}/tests" "${K1_ROOT}/k1_os" \
  "${K1_ROOT}/modules" "${K1_ROOT}/config" "${K1_ROOT}/deploy" "${K1_ROOT}/scripts" \
  "${K1_ROOT}/pyproject.toml" "${K1_ROOT}/README.md" "${K1_ROOT}/logs" \
  "${K1_ROOT}/.venv" "${K1_ROOT}/k1.egg-info" 2>/dev/null || true
rm -rf "${K1_ROOT}/k1.egg-info" "${K1_ROOT}/build" "${K1_ROOT}/dist" 2>/dev/null || true

if [[ -d "${PATCH_DIR}" ]]; then
  for patch in "${PATCH_DIR}"/*.patch; do
    [[ -f "$patch" ]] || continue
    echo "Applying $(basename "$patch")..."
    patch -p1 --forward -d "${K1_ROOT}" < "$patch" || true
  done
fi

python3 "${SIM_ROOT}/examples/k1/apply_alpha_fixes.py"

if [[ ! -x "${K1_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${K1_ROOT}/.venv"
  chown -R "${USER_NAME}:${USER_NAME}" "${K1_ROOT}/.venv"
fi

sudo -u "${USER_NAME}" "${K1_ROOT}/.venv/bin/pip" install -U pip -q
sudo -u "${USER_NAME}" "${K1_ROOT}/.venv/bin/pip" install -e "${K1_ROOT}[dev]" -q

sudo -u "${USER_NAME}" env K1_ROOT="${K1_ROOT}" \
  "${K1_ROOT}/.venv/bin/pytest" tests -q --basetemp=/tmp/k1-pytest

echo ""
echo "Bootstrap complete. Next:"
echo "  sim k1 preflight --k1-root ${K1_ROOT}"
echo "  sudo cp deploy/systemd/*.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now k1-runtime k1-openwebui-bridge"
