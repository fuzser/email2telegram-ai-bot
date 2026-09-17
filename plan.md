Build a small production-style Python service for Ubuntu 24.04.

Goal:
Monitor a Gmail mailbox using IMAP. When a new email arrives, automatically summarise it using an LLM API and send the summary to a Telegram chat. The complete workflow should normally finish within 60 seconds of the email arriving.

Requirements:

1. Python 3.12 compatible.

2. Poll Gmail IMAP every 5 seconds.

3. Read:
- sender
- subject
- received timestamp
- plain text email body
- HTML fallback converted to readable text if no plain text body exists.

4. Track processing and delivery using IMAP UID plus a local SQLite task ledger.
Do not rely solely on UNSEEN.
Store state locally so rebooting the server resumes pending work without replaying sent mail.

5. Send the email content to an LLM and produce:
- maximum 3 bullet points
- concise factual summary
- no hallucinated information

6. Send this format to Telegram:

New Email Summary

From: ...
Subject: ...

Summary:
• ...
• ...
• ...

Received: ...
Processed: ...
Latency: ... seconds

7. Environment configuration must come from .env:
EMAIL_ADDRESS
EMAIL_PASSWORD
IMAP_HOST
IMAP_PORT
OPENAI_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
POLL_INTERVAL

8. Implement robust error handling:
- IMAP reconnect
- LLM API timeout/retry
- Telegram API timeout/retry
- unexpected exceptions should not terminate the service
- only mark/store a message as processed after Telegram delivery succeeds

9. Use Python logging.

Do not print credentials or API keys in logs.

10. Include:
requirements.txt
.env.example
.gitignore
README.md
mail-agent.service

11. mail-agent.service must:
- start after network-online.target
- automatically restart on failure
- restart after 5 seconds
- run the Python virtual environment executable
- support systemctl enable mail-agent

12. Keep the project simple.
Use only the Python standard-library SQLite database for the durable task ledger. Do not use Docker, Redis, Celery, web frameworks, or external queue infrastructure.

16. Process at most three LLM summaries concurrently by default, then send Telegram messages sequentially in UID order. Cache completed summaries so Telegram retries do not repeat LLM calls.

17. Display notification timestamps and systemd logs in the `Pacific/Auckland` timezone.

13. Code should be modular:
app.py
config.py
mail_client.py
summarizer.py
telegram_client.py
state.py

14. Add graceful shutdown for SIGTERM/SIGINT.

15. README should include:
installation
configuration
running manually
installing systemd service
viewing logs
reboot test
basic troubleshooting

The service will run on a fresh Ubuntu 24.04 VPS.
