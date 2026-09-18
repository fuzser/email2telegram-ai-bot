# Email2Telegram AI Bot / 邮件转 Telegram AI 机器人

A small Python 3.12 service that polls Gmail over IMAP, summarizes new email with the OpenAI API, and posts the result to Telegram. It is designed to run continuously under systemd on Ubuntu 24.04.

一个面向 Ubuntu 24.04 的轻量 Python 3.12 常驻服务：通过 IMAP 轮询 Gmail，使用 OpenAI API 总结新邮件，并将摘要发送到 Telegram。

## How it works / 工作方式

The service polls every 5 seconds by default. Before each UID query it sends IMAP `NOOP` so a long-lived Gmail connection refreshes its selected mailbox state. On its first successful connection it stores the current maximum IMAP UID as a baseline, so existing messages are not posted. After every later process restart, the bot posts `BOT reconnected` and recovers at most the newest 30 emails by default. Later messages are identified by `UIDVALIDITY + UID` and registered in SQLite before external API calls. Up to three LLM summaries run concurrently; Telegram delivery remains sequential in UID order. Summaries are cached for delivery retries, and a message is marked as sent only after Telegram confirms delivery.

服务默认每 5 秒轮询一次。每次查询 UID 前先发送 IMAP `NOOP`，让 Gmail 长连接刷新已选择邮箱的状态。首次成功连接时会保存当前最大 IMAP UID 作为基线，因此不会发送历史邮件。之后每次进程重启，Bot 都会发送 `BOT reconnected`，并默认只从最新邮件开始向旧邮件回溯最多 30 封。之后使用 `UIDVALIDITY + UID` 唯一标识邮件，并在调用外部 API 前登记到 SQLite。最多三个 LLM 摘要任务并发执行，Telegram 仍按 UID 顺序逐条发送。摘要会缓存用于投递重试，并且仅在 Telegram 确认发送成功后标记为已发送。

## Installation / 安装

Run the following as `root` on Ubuntu 24.04. Replace the repository URL if you use a fork.

在 Ubuntu 24.04 上以 `root` 执行以下命令；如果使用 fork，请替换仓库地址。

```bash
apt update
apt install -y git python3 python3-venv
useradd --system --home /opt/mail-agent --shell /usr/sbin/nologin mail-agent
git clone https://github.com/fuzser/email2telegram-ai-bot.git /opt/mail-agent
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
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.6-terra
LLM_CONCURRENCY=3
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
POLL_INTERVAL=5
RESTART_EMAIL_LOOKBACK_LIMIT=30
STATE_DB_PATH=data/state.db
LEGACY_STATE_PATH=data/state.json
APP_TIMEZONE=Pacific/Auckland
```

`LLM_CONCURRENCY=3` is the conservative default for burst processing. `RESTART_EMAIL_LOOKBACK_LIMIT=30` limits only the first mailbox recovery scan after each process start: it counts backward from the newest email, permanently skips older backlog beyond the limit, and accepts values from 1 to 10000. Normal polling after that scan is not capped. Notifications display `Received` and `Processed` in `Pacific/Auckland`. The systemd unit overrides the database path to `/var/lib/mail-agent/state.db`, creates that persistent directory with mode `0700`, and imports the old `/opt/mail-agent/data/state.json` through the configured relative legacy path.

`LLM_CONCURRENCY=3` 是突发邮件处理的保守默认值。`RESTART_EMAIL_LOOKBACK_LIMIT=30` 仅限制每次进程启动后的第一次邮箱恢复扫描：从最新邮件向旧邮件计数，超过上限的更早积压邮件会被永久跳过，可配置范围为 1 到 10000；之后的正常轮询不受此上限影响。通知中的 `Received` 和 `Processed` 使用 `Pacific/Auckland`。systemd 服务会把数据库路径覆盖为 `/var/lib/mail-agent/state.db`，以 `0700` 权限自动创建持久目录，并通过配置的旧状态相对路径导入 `/opt/mail-agent/data/state.json`。

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

The unit starts after `network-online.target`, runs the virtual-environment Python executable, uses Auckland time for logs, creates `/var/lib/mail-agent`, and restarts five seconds after a failure.

服务会在网络就绪后启动，使用虚拟环境中的 Python，以奥克兰时区记录日志，创建 `/var/lib/mail-agent`，并在失败五秒后自动重启。

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

Confirm that Telegram receives `BOT reconnected` after the service reconnects. Send a new test email only after the service has logged its initial baseline. Confirm that Telegram receives exactly one summary and that the reported latency is below 60 seconds under normal network conditions. To test recovery limiting, stop the service, deliver more messages than `RESTART_EMAIL_LOOKBACK_LIMIT`, and confirm that only the newest configured number are recovered after startup.

确认服务重连后 Telegram 收到 `BOT reconnected`。请在日志显示首次 UID 基线已经建立后再发送测试邮件，确认 Telegram 只收到一条摘要，并且正常网络条件下显示的延迟低于 60 秒。如需验证恢复上限，可先停止服务，再投递超过 `RESTART_EMAIL_LOOKBACK_LIMIT` 数量的邮件，启动后应只恢复最新的配置数量。

## Automated recovery / 自动恢复

`scripts/recover.sh` rebuilds the Ubuntu host-side installation. It installs system packages, creates both the `mail-agent` user and group to prevent systemd `217/USER`, clones or fast-forwards the repository without persistent Git `safe.directory` changes, creates the virtual environment, installs dependencies, installs the systemd unit, starts the service, and prints status and recent logs. Application code remains owned by `root`; only `.env` and `/var/lib/mail-agent` are writable by the service account.

`scripts/recover.sh` 用于重建 Ubuntu 主机上的安装。它会安装系统依赖、同时创建 `mail-agent` 用户和组以避免 systemd `217/USER`、在不永久修改 Git `safe.directory` 的情况下克隆或快进仓库、创建虚拟环境、安装依赖与 systemd 单元、启动服务，并输出状态和最近日志。应用代码归 `root` 所有，只有 `.env` 与 `/var/lib/mail-agent` 可由服务账号写入。

The production `.env` is intentionally excluded from Git. Restore it separately, and remember that Gmail `EMAIL_PASSWORD` must be an App Password. You may pass a protected copy explicitly; a state database backup is optional:

生产 `.env` 被明确排除在 Git 之外，必须单独恢复；Gmail 的 `EMAIL_PASSWORD` 必须使用 App Password。可以显式传入受保护的副本；状态数据库备份不是必需项：

```bash
git clone https://github.com/fuzser/email2telegram-ai-bot.git
cd email2telegram-ai-bot
sudo ENV_BACKUP_PATH=/path/to/protected/mail-agent.env ./scripts/recover.sh
# Optional: also add STATE_DB_BACKUP_PATH=/path/to/protected/state.db
```

If `ENV_BACKUP_PATH` is omitted, the script requires `/opt/mail-agent/.env` to already exist and stops safely when it is missing. If `STATE_DB_BACKUP_PATH` is omitted, the service creates a fresh ledger and establishes a new mailbox baseline; previously pending deliveries cannot be recovered. An existing initialization marker is never removed to bypass the application's total-loss guard.

如果未设置 `ENV_BACKUP_PATH`，脚本要求 `/opt/mail-agent/.env` 已存在；缺失时会安全停止。如果未设置 `STATE_DB_BACKUP_PATH`，服务会创建新的状态账本并建立新的邮箱基线，之前未完成的投递将无法恢复。脚本不会删除既有初始化标记来绕过应用的全丢失保护。

## Five-line recovery runbook / 五行恢复手册

1. Provision a fresh Ubuntu 24.04 host and clone `https://github.com/fuzser/email2telegram-ai-bot.git`.
2. Restore the protected `.env` credentials; Gmail must use an App Password rather than the normal account password.
3. Run `sudo ENV_BACKUP_PATH=/path/to/protected/mail-agent.env ./scripts/recover.sh`; it recreates the service account, virtual environment, dependencies, and systemd service.
4. Verify recovery with `systemctl status mail-agent` and `journalctl -u mail-agent -n 100 --no-pager`.
5. Send a test email and confirm the summary reaches Telegram; the service is enabled to start automatically after reboot.

1. 准备全新的 Ubuntu 24.04 主机，并克隆 `https://github.com/fuzser/email2telegram-ai-bot.git`。
2. 恢复受保护的 `.env` 凭据；Gmail 必须使用 App Password，而不能使用普通账号密码。
3. 运行 `sudo ENV_BACKUP_PATH=/path/to/protected/mail-agent.env ./scripts/recover.sh`；脚本会重建服务账号、虚拟环境、依赖和 systemd 服务。
4. 使用 `systemctl status mail-agent` 和 `journalctl -u mail-agent -n 100 --no-pager` 验证恢复结果。
5. 发送测试邮件并确认摘要到达 Telegram；服务已配置为重启后自动启动。

Back up `.env` outside Git with restricted access. Backing up `/var/lib/mail-agent/state.db` is recommended but optional: it preserves mailbox watermarks, pending tasks, cached summaries, and delivery history. The adjacent `state.db.backup` only protects against loss of the main database on the same disk; whole-disk loss requires an off-host copy.

请将 `.env` 备份到 Git 之外并限制访问权限。建议但不强制备份 `/var/lib/mail-agent/state.db`：它可以保留邮箱水位、待处理任务、缓存摘要和投递历史。同目录的 `state.db.backup` 只能应对同一磁盘上的主数据库丢失；整盘故障仍需要异机副本。

## Troubleshooting / 基础排障

- `Missing required environment variable`: check the spelling and value in `.env`.
- Gmail `AUTHENTICATIONFAILED`: replace the normal account password with a valid Gmail App Password in `EMAIL_PASSWORD`.
- systemd `status=217/USER`: the `mail-agent` user or group is missing; rerun `scripts/recover.sh` as root.
- Git `detected dubious ownership`: use `scripts/recover.sh`, which applies `safe.directory` only to its pull command and restores root ownership of the application checkout.
- `Cannot connect to IMAP server`: verify IMAP access, Gmail App Password, host, port, DNS, and outbound TCP 993.
- `LLM summarization failed after retries`: verify the API key, base URL, model access, and outbound HTTPS.
- `Telegram rejected the message`: verify the bot token, chat ID, and that the bot can post to the target chat.
- `Cannot initialize state database`: restore `/var/lib/mail-agent/state.db` from a known-good off-host backup. Do not delete the initialization marker merely to force startup, because that can skip queued mail.
- The first SQLite startup automatically imports a valid legacy `data/state.json`. Keep the old JSON until the migration has been verified.

- `Missing required environment variable`：检查 `.env` 中的变量名和值。
- Gmail `AUTHENTICATIONFAILED`：将 `EMAIL_PASSWORD` 中的普通账号密码替换为有效的 Gmail App Password。
- systemd `status=217/USER`：缺少 `mail-agent` 用户或组；请以 root 身份重新运行 `scripts/recover.sh`。
- Git `detected dubious ownership`：使用 `scripts/recover.sh`；它仅对本次拉取设置 `safe.directory`，并恢复应用目录的 root ownership。
- `Cannot connect to IMAP server`：检查 IMAP 权限、Gmail App Password、地址、端口、DNS 和出站 TCP 993。
- `LLM summarization failed after retries`：检查 API Key、Base URL、模型权限和出站 HTTPS。
- Telegram 拒绝消息：检查 Bot Token、Chat ID，以及机器人是否有目标会话的发言权限。
- `Cannot initialize state database`：从可靠的异机备份恢复 `/var/lib/mail-agent/state.db`。不要为了强制启动而删除初始化标记，否则可能跳过积压邮件。
- 第一次使用 SQLite 启动时会自动导入有效的旧版 `data/state.json`；确认迁移成功前请保留旧 JSON。

## Security / 安全

`.env`, runtime state, logs, virtual environments, caches, and local agent configuration are excluded by `.gitignore`. Keep the server patched, restrict SSH access, and rotate any credential that may have been exposed.

`.gitignore` 已排除 `.env`、运行状态、日志、虚拟环境、缓存和本地代理配置。请持续安装系统安全更新、限制 SSH 访问，并轮换任何可能泄露的凭据。
