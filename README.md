# Mail Agent / 邮件摘要代理

A small Python 3.12 service that polls Gmail over IMAP, summarizes new email with an OpenAI-compatible API, and posts the result to Telegram. It is designed to run continuously under systemd on Ubuntu 24.04.

一个面向 Ubuntu 24.04 的轻量 Python 3.12 常驻服务：通过 IMAP 轮询 Gmail，使用 OpenAI-compatible API 总结新邮件，并将摘要发送到 Telegram。

## How it works / 工作方式

The service polls every 5 seconds by default. Before each UID query it sends IMAP `NOOP` so a long-lived Gmail connection refreshes its selected mailbox state. On its first successful connection it stores the current maximum IMAP UID as a baseline, so existing messages are not posted. Later messages are identified by `UIDVALIDITY + UID`. A message is marked as processed only after Telegram confirms delivery.

服务默认每 5 秒轮询一次。每次查询 UID 前先发送 IMAP `NOOP`，让 Gmail 长连接刷新已选择邮箱的状态。首次成功连接时会保存当前最大 IMAP UID 作为基线，因此不会发送历史邮件。之后使用 `UIDVALIDITY + UID` 唯一标识邮件，并且仅在 Telegram 确认发送成功后记录为已处理。

## Installation / 安装

Run the following as `root` on Ubuntu 24.04. Replace the repository URL if you use a fork.

在 Ubuntu 24.04 上以 `root` 执行以下命令；如果使用 fork，请替换仓库地址。

```bash
apt update
apt install -y git python3 python3-venv
useradd --system --home /opt/mail-agent --shell /usr/sbin/nologin mail-agent
git clone https://github.com/fuzser/glenn-ai-tel-bot.git /opt/mail-agent
python3 -m venv /opt/mail-agent/.venv
/opt/mail-agent/.venv/bin/pip install -r /opt/mail-agent/requirements.txt
cp /opt/mail-agent/.env.example /opt/mail-agent/.env
chown -R mail-agent:mail-agent /opt/mail-agent
chmod 600 /opt/mail-agent/.env
```

## Configuration / 配置

Edit `/opt/mail-agent/.env` and replace every placeholder. For Gmail, use an App Password rather than the normal account password. Never commit this file.

编辑 `/opt/mail-agent/.env` 并替换全部占位值。Gmail 应使用 App Password，而不是账号登录密码。严禁提交该文件。

```dotenv
EMAIL_ADDRESS=your-email@gmail.com
EMAIL_PASSWORD=your-gmail-app-password
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://grsaiapi.com/v1
OPENAI_MODEL=gpt-5.6-terra
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
POLL_INTERVAL=5
```

## Running manually / 手动运行

```bash
cd /opt/mail-agent
sudo -u mail-agent /opt/mail-agent/.venv/bin/python app.py
```

Stop the process with `Ctrl+C`. The IMAP and HTTP connections are closed during graceful shutdown.

使用 `Ctrl+C` 停止进程；服务会在优雅退出时关闭 IMAP 和 HTTP 连接。

## Installing the systemd service / 安装 systemd 服务

```bash
cp /opt/mail-agent/mail-agent.service /etc/systemd/system/mail-agent.service
systemctl daemon-reload
systemctl enable --now mail-agent
systemctl status mail-agent --no-pager
```

The unit starts after `network-online.target`, runs the virtual-environment Python executable, and restarts five seconds after a failure.

服务会在网络就绪后启动，使用虚拟环境中的 Python，并在失败五秒后自动重启。

## Logs / 查看日志

```bash
journalctl -u mail-agent -f
journalctl -u mail-agent --since "30 minutes ago" --no-pager
```

Logs include UID numbers and error categories, but not passwords, API keys, Telegram tokens, or email bodies.

日志会记录 UID 和错误类别，但不会记录密码、API Key、Telegram Token 或邮件正文。

## Reboot test / 重启测试

```bash
systemctl is-enabled mail-agent
reboot
# Reconnect after the host returns, then run:
systemctl is-active mail-agent
journalctl -u mail-agent -b --no-pager
```

Send a new test email only after the service has logged its initial baseline. Confirm that Telegram receives exactly one summary and that the reported latency is below 60 seconds under normal network conditions.

请在日志显示首次 UID 基线已经建立后再发送测试邮件。确认 Telegram 只收到一条摘要，并且正常网络条件下显示的延迟低于 60 秒。

## Five-line recovery runbook / 五行恢复手册

```bash
git clone https://github.com/fuzser/glenn-ai-tel-bot.git /opt/mail-agent
python3 -m venv /opt/mail-agent/.venv && /opt/mail-agent/.venv/bin/pip install -r /opt/mail-agent/requirements.txt
install -o mail-agent -g mail-agent -m 600 /secure-backup/mail-agent.env /opt/mail-agent/.env
cp /opt/mail-agent/mail-agent.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now mail-agent && journalctl -u mail-agent -f
```

The `.env` backup must be stored outside Git with restricted access. The local state file is intentionally not included in source control; restoring it is optional when reconnecting to a mailbox whose current messages should become the new baseline.

`.env` 备份必须保存在 Git 之外并限制访问权限。状态文件不会进入版本控制；如果希望恢复后以邮箱当前消息为新基线，可以不恢复旧状态。

## Troubleshooting / 基础排障

- `Missing required environment variable`: check the spelling and value in `.env`.
- `Cannot connect to IMAP server`: verify IMAP access, Gmail App Password, host, port, DNS, and outbound TCP 993.
- `LLM summarization failed after retries`: verify the API key, base URL, model access, and outbound HTTPS.
- `Telegram rejected the message`: verify the bot token, chat ID, and that the bot can post to the target chat.
- A corrupt `data/state.json` stops safe startup instead of silently replaying old mail. Restore a known-good copy or move it aside only after deciding whether replay is acceptable.

- `Missing required environment variable`：检查 `.env` 中的变量名和值。
- `Cannot connect to IMAP server`：检查 IMAP 权限、Gmail App Password、地址、端口、DNS 和出站 TCP 993。
- `LLM summarization failed after retries`：检查 API Key、Base URL、模型权限和出站 HTTPS。
- Telegram 拒绝消息：检查 Bot Token、Chat ID，以及机器人是否有目标会话的发言权限。
- `data/state.json` 损坏时，服务会停止安全启动，而不是静默重放旧邮件；只有在明确接受重放风险后才应移走损坏状态。

## Security / 安全

`.env`, runtime state, logs, virtual environments, caches, and local agent configuration are excluded by `.gitignore`. Keep the server patched, restrict SSH access, and rotate any credential that may have been exposed.

`.gitignore` 已排除 `.env`、运行状态、日志、虚拟环境、缓存和本地代理配置。请持续安装系统安全更新、限制 SSH 访问，并轮换任何可能泄露的凭据。
