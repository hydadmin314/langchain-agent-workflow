#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-sales-demo}"
SERVER_NAME="${1:-_}"
APP_PORT="${APP_PORT:-8766}"
SITE_FILE="/etc/nginx/sites-available/${SERVICE_NAME}"

if ! command -v nginx >/dev/null 2>&1; then
  echo "Nginx was not found. Install it with apt first." >&2
  exit 1
fi

sudo tee "${SITE_FILE}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }
}
EOF

sudo ln -sfn "${SITE_FILE}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  sudo rm /etc/nginx/sites-enabled/default
fi
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

echo "Nginx is proxying port 80 to 127.0.0.1:${APP_PORT}."
