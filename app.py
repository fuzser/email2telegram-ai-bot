"""邮件摘要到 Telegram 的常驻服务入口。"""

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from config import ConfigError, load_config
from mail_client import MailClient, MailClientError
from state import StateError, StateStore
from summarizer import Summarizer, SummarizerError
from telegram_client import TelegramClient, TelegramError

LOGGER = logging.getLogger(__name__)
STOP_EVENT = threading.Event()
MAX_SENDER_CHARS = 320
MAX_SUBJECT_CHARS = 500
MAX_BULLET_CHARS = 800


def _request_shutdown(signum: int, _frame: object) -> None:
    """把 SIGTERM/SIGINT 转换为可中断的主循环停止事件。"""
    LOGGER.info("Received signal %s; shutting down", signum)
    STOP_EVENT.set()


def _format_notification(
    sender: str,
    subject: str,
    bullets: list[str],
    received_at: datetime,
    processed_at: datetime,
) -> str:
    """构造测试要求的 Telegram 消息格式。"""
    latency = max(0.0, (processed_at - received_at).total_seconds())
    safe_sender = sender[:MAX_SENDER_CHARS]
    safe_subject = subject[:MAX_SUBJECT_CHARS]
    summary = "\n".join(f"• {bullet[:MAX_BULLET_CHARS]}" for bullet in bullets[:3])
    return (
        "New Email Summary\n\n"
        f"From: {safe_sender}\n"
        f"Subject: {safe_subject}\n\n"
        "Summary:\n"
        f"{summary}\n\n"
        f"Received: {received_at.astimezone(timezone.utc).isoformat()}\n"
        f"Processed: {processed_at.isoformat()}\n"
        f"Latency: {latency:.1f} seconds"
    )


def run() -> None:
    """持续轮询邮箱并处理每一封未成功投递的新邮件。"""
    config = load_config()
    state = StateStore()
    mail = MailClient(
        config.imap_host,
        config.imap_port,
        config.email_address,
        config.email_password,
    )
    summarizer = Summarizer(
        config.openai_api_key,
        config.openai_base_url,
        config.openai_model,
    )
    telegram = TelegramClient(
        config.telegram_bot_token,
        config.telegram_chat_id,
    )

    mailbox = None
    baseline = None
    try:
        while not STOP_EVENT.is_set():
            try:
                current_mailbox = mail.mailbox_status()
                if mailbox is None or current_mailbox.key != mailbox.key:
                    mailbox = current_mailbox
                    baseline = state.get_baseline(mailbox.key)
                    if baseline is None:
                        baseline = mailbox.latest_uid
                        state.set_baseline(mailbox.key, baseline)
                        LOGGER.info(
                            "Initialized mailbox baseline at UID %s; existing mail "
                            "is skipped",
                            baseline,
                        )

                if baseline is None:
                    raise StateError("Mailbox baseline was not initialized")
                processed_uids = state.load_processed_uids()
                messages = mail.fetch_new_messages(baseline + 1, processed_uids)
                if messages:
                    LOGGER.info("Found %s new email(s)", len(messages))
                for message in messages:
                    if STOP_EVENT.is_set():
                        break
                    processing_started = time.monotonic()
                    try:
                        LOGGER.info("Summarizing email UID %s", message.uid)
                        bullets = summarizer.summarize(
                            message.sender, message.subject, message.body
                        )
                        processed_at = datetime.now(timezone.utc)
                        notification = _format_notification(
                            message.sender,
                            message.subject,
                            bullets,
                            message.received_at,
                            processed_at,
                        )
                        LOGGER.info("Sending email UID %s to Telegram", message.uid)
                        telegram.send_message(notification)
                        LOGGER.info("Telegram delivered email UID %s", message.uid)
                        state.mark_processed(message.uid_key)
                        LOGGER.info("Persisted email UID %s", message.uid)
                        LOGGER.info(
                            "Completed email UID %s in %.1f seconds",
                            message.uid,
                            time.monotonic() - processing_started,
                        )
                    except (SummarizerError, TelegramError, StateError):
                        LOGGER.exception(
                            "Could not complete email UID %s; it will be retried",
                            message.uid,
                        )
            except (MailClientError, StateError):
                LOGGER.exception("Polling cycle failed; service will retry")
            except Exception:
                LOGGER.exception("Unexpected polling error; service will continue")

            STOP_EVENT.wait(config.poll_interval)
    finally:
        mail.close()
        telegram.close()


def main() -> None:
    """配置日志、信号处理并启动服务。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    try:
        run()
    except (ConfigError, StateError):
        LOGGER.exception("Service startup failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
