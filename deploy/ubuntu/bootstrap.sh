#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${APP_DIR}"

if command -v git >/dev/null 2>&1 && git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "WARNING: .env is tracked by Git. Rotate exposed keys and remove it from Git history." >&2
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python was not found. Install python3 and python3-venv first." >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10 or newer is required; found {sys.version.split()[0]}"
    )
print(f"Using Python {sys.version.split()[0]}")
PY

if [[ ! -d .venv ]]; then
  "${PYTHON_BIN}" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example. Fill in the real model credentials."
else
  chmod 600 .env
fi

.venv/bin/python - <<'PY'
from pathlib import Path

from web.sales_demo.server import SalesDemoService

service = SalesDemoService(
    published_root=Path("data/product_doc_agent/published"),
    use_llm=False,
)
if service.product_count < 1:
    raise SystemExit("No published product JSON files were loaded.")
print(f"Offline startup check passed: {service.product_count} products loaded.")
PY

echo "Bootstrap complete: ${APP_DIR}"
