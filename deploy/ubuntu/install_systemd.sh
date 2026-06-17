#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-sales-demo}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$USER}}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn "${SERVICE_USER}")}"
APP_PORT="${APP_PORT:-8766}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  echo "Missing ${APP_DIR}/.venv/bin/python; run bootstrap.sh first." >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Missing ${APP_DIR}/.env; create it before installing the service." >&2
  exit 1
fi

sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=LangChain Sales Recommendation Demo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python web/sales_demo/server.py --host 127.0.0.1 --port ${APP_PORT}
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
