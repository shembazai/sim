#!/usr/bin/env bash
# Verify K1 Alpha fixes without modifying root-owned source (overlay + pytest).
set -euo pipefail

K1_ROOT="${K1_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
SIM_ROOT="${K1_ROOT}/SIM"
OVERLAY="${OVERLAY:-/tmp/k1-alpha-overlay-$$}"
PYTHON="${PYTHON:-${SIM_ROOT}/.venv/bin/python}"

echo "K1 overlay test run"
echo "  K1_ROOT=${K1_ROOT}"
echo "  OVERLAY=${OVERLAY}"

"${PYTHON}" "${SIM_ROOT}/examples/k1/apply_alpha_fixes.py" --overlay "${OVERLAY}" --k1-root "${K1_ROOT}"

"${PYTHON}" -m pip install -q -e "${OVERLAY}[dev]"

export PYTHONPATH="${OVERLAY}/src:${OVERLAY}/modules:${OVERLAY}/k1_os:${OVERLAY}"
export K1_ROOT="${OVERLAY}"
cd "${OVERLAY}"

"${PYTHON}" -m pytest tests -q --basetemp=/tmp/k1-pytest-overlay

echo ""
echo "Overlay tests passed. Apply to live tree with:"
echo "  sudo ${SIM_ROOT}/examples/k1/bootstrap_alpha.sh"
