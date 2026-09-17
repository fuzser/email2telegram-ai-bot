# Mail Agent / 邮件摘要代理

## English

This repository contains the initial scaffold for a Python 3.12 service that polls Gmail through IMAP, summarizes new messages with an OpenAI-compatible API, and sends the result to Telegram.

The current phase provides the required module layout, dependency manifest, safe environment template, Git exclusions, and systemd unit placeholder. Runtime behavior will be implemented in the next planned phase.

### Planned setup

1. Copy `.env.example` to `.env` and replace every placeholder locally.
2. Create a Python 3.12 virtual environment at `.venv`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run manually with `.venv/bin/python app.py` after implementation is complete.

Never commit `.env`, API keys, mailbox passwords, Telegram tokens, chat identifiers, runtime state, or logs.

## 中文

本仓库是 Python 3.12 邮件摘要服务的初始框架。完整服务将通过 IMAP 轮询 Gmail，使用 OpenAI-compatible API 总结新邮件，并把结果发送到 Telegram。

当前阶段只建立计划要求的模块结构、依赖清单、安全环境变量模板、Git 忽略规则和 systemd 单元占位文件。实际运行逻辑将在下一计划阶段实现。

### 计划中的安装方式

1. 将 `.env.example` 复制为 `.env`，并仅在本机替换所有占位值。
2. 使用 Python 3.12 在 `.venv` 创建虚拟环境。
3. 执行 `pip install -r requirements.txt` 安装依赖。
4. 完成实现后，使用 `.venv/bin/python app.py` 手动运行。

严禁提交 `.env`、API Key、邮箱密码、Telegram Token、Chat ID、运行状态或日志。

