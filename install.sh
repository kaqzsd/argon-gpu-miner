#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == "--system-deps" ]]; then
  if [[ "$(id -u)" != 0 ]]; then
    echo 'Run sudo bash install.sh --system-deps, then bash install.sh as your normal user.' >&2
    exit 1
  fi
  command -v apt-get >/dev/null || { echo 'System dependency setup supports Debian/Ubuntu only.' >&2; exit 1; }
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv ca-certificates ocl-icd-libopencl1
  echo 'System dependencies installed. NVIDIA driver must already be provided by the host.'
  exit 0
fi
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo 'Missing python3. Run: bash install.sh --system-deps (root)' >&2; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "Python >= 3.10 required")'
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv || { echo 'Missing venv support. Run: sudo bash install.sh --system-deps' >&2; exit 1; }
fi
if [[ -d wheelhouse ]] && .venv/bin/python -c 'import sys,platform;sys.exit(0 if sys.version_info[:2]==(3,12) and platform.machine()=="x86_64" else 1)'; then
  echo 'Installing bundled Linux x86_64 / Python 3.12 wheels without contacting PyPI.'
  .venv/bin/python -m pip install --disable-pip-version-check --no-index --find-links=wheelhouse -r requirements.txt
else
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi
mkdir -p runtime
echo 'Installed. Checking the NVIDIA/OpenCL environment (no mining started).'
exec .venv/bin/python manage.py doctor
