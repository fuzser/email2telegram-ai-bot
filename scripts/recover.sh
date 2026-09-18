#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/mail-agent"
STATE_DIR="/var/lib/mail-agent"
SERVICE_NAME="mail-agent"
SERVICE_USER="mail-agent"
REPO_URL="https://github.com/fuzser/email2telegram-ai-bot.git"
ENV_BACKUP_PATH="${ENV_BACKUP_PATH:-}"
STATE_DB_BACKUP_PATH="${STATE_DB_BACKUP_PATH:-}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: Run this script as root, for example: sudo ./scripts/recover.sh"
    exit 1
fi

echo "[1/7] Installing system dependencies..."
apt-get update
apt-get install -y git python3 python3-venv python3-pip

echo "[2/7] Creating service account..."
if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_USER"
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd \
        --system \
        --gid "$SERVICE_USER" \
        --home-dir "$APP_DIR" \
        --shell /usr/sbin/nologin \
        "$SERVICE_USER"
fi

echo "[3/7] Restoring application..."
if [[ ! -d "$APP_DIR/.git" ]]; then
    git clone "$REPO_URL" "$APP_DIR"
else
    git -c safe.directory="$APP_DIR" -C "$APP_DIR" pull --ff-only
fi

echo "[4/7] Creating Python environment..."
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[5/7] Checking credentials and state..."
if [[ -n "$ENV_BACKUP_PATH" ]]; then
    if [[ ! -f "$ENV_BACKUP_PATH" ]]; then
        echo "ERROR: ENV_BACKUP_PATH does not point to a readable file."
        exit 1
    fi
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 \
        "$ENV_BACKUP_PATH" "$APP_DIR/.env"
fi
if [[ ! -f "$APP_DIR/.env" ]]; then
    echo
    echo "ERROR: $APP_DIR/.env is missing."
    echo "Restore the production .env, then run this script again."
    echo "Gmail EMAIL_PASSWORD must be an App Password, not the normal password."
    exit 1
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$STATE_DIR"
if [[ -n "$STATE_DB_BACKUP_PATH" ]]; then
    if [[ ! -f "$STATE_DB_BACKUP_PATH" ]]; then
        echo "ERROR: STATE_DB_BACKUP_PATH does not point to a readable file."
        exit 1
    fi
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 \
        "$STATE_DB_BACKUP_PATH" "$STATE_DIR/state.db"
else
    echo "No state database backup supplied; a fresh mailbox baseline will be created."
fi

# 应用代码由 root 管理，服务账号只拥有凭据和运行状态。
chown -R root:root "$APP_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "[6/7] Installing systemd service..."
install -o root -g root -m 644 \
    "$APP_DIR/mail-agent.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "[7/7] Verifying service..."
sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl status "$SERVICE_NAME" --no-pager || true
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    exit 1
fi
systemctl status "$SERVICE_NAME" --no-pager
echo
journalctl -u "$SERVICE_NAME" -n 30 --no-pager
