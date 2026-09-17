"""SQLite 队列驱动的并发邮件摘要与 Telegram 投递服务。"""

import logging
import signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import ConfigError, load_config
from mail_client import EmailMessage, MailClient, MailClientError
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
    display_timezone: ZoneInfo,
) -> str:
    """使用配置时区构造 Telegram 消息。"""
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
        f"Received: {received_at.astimezone(display_timezone).isoformat()}\n"
        f"Processed: {processed_at.astimezone(display_timezone).isoformat()}\n"
        f"Timezone: {display_timezone.key}\n"
        f"Latency: {latency:.1f} seconds"
    )


def _summarize_batch(
    messages: list[EmailMessage],
    state: StateStore,
    summarizer: Summarizer,
    executor: ThreadPoolExecutor,
) -> dict[str, list[str]]:
    """最多按配置并发生成摘要，并在主线程持久化结果。"""
    summaries: dict[str, list[str]] = {}
    futures: dict[Future[list[str]], EmailMessage] = {}

    state.register_emails(
        [
            (
                message.uid_key,
                message.uid_validity,
                message.uid,
                message.received_at,
            )
            for message in messages
        ]
    )
    for message in messages:
        email_state = state.get_email_state(message.uid_key)
        if email_state.status == "sent":
            continue
        if email_state.status == "summarized":
            if email_state.summary is None:
                raise StateError("Summarized email has no stored summary")
            summaries[message.uid_key] = email_state.summary
            continue
        if email_state.status != "pending":
            raise StateError(
                f"Email UID {message.uid} has unexpected state {email_state.status}"
            )
        if STOP_EVENT.is_set():
            break
        state.mark_processing(message.uid_key)
        LOGGER.info("Queueing email UID %s for summarization", message.uid)
        future = executor.submit(
            summarizer.summarize,
            message.sender,
            message.subject,
            message.body,
        )
        futures[future] = message

    for future in as_completed(futures):
        message = futures[future]
        try:
            bullets = future.result()
            state.mark_summarized(message.uid_key, bullets)
            summaries[message.uid_key] = bullets
            LOGGER.info("Summarized email UID %s", message.uid)
        except Exception as exc:
            state.mark_pending(message.uid_key, type(exc).__name__)
            if isinstance(exc, SummarizerError):
                LOGGER.exception(
                    "Could not summarize email UID %s; it will be retried",
                    message.uid,
                    exc_info=exc,
                )
            else:
                LOGGER.exception(
                    "Unexpected summarization error for email UID %s",
                    message.uid,
                    exc_info=exc,
                )
    return summaries


def _deliver_batch(
    messages: list[EmailMessage],
    summaries: dict[str, list[str]],
    state: StateStore,
    telegram: TelegramClient,
    display_timezone: ZoneInfo,
    mailbox_key: str,
) -> None:
    """按 UID 顺序向 Telegram 投递已生成的摘要。"""
    for message in messages:
        if STOP_EVENT.is_set():
            break
        bullets = summaries.get(message.uid_key)
        if bullets is None:
            continue
        processed_at = datetime.now(timezone.utc)
        notification = _format_notification(
            message.sender,
            message.subject,
            bullets,
            message.received_at,
            processed_at,
            display_timezone,
        )
        try:
            LOGGER.info("Sending email UID %s to Telegram", message.uid)
            telegram.send_message(notification)
            LOGGER.info("Telegram delivered email UID %s", message.uid)
            state.mark_processed(message.uid_key)
            state.advance_completed_through(mailbox_key, message.uid)
            LOGGER.info("Persisted email UID %s as sent", message.uid)
        except TelegramError as exc:
            state.mark_delivery_failed(message.uid_key, type(exc).__name__)
            LOGGER.exception(
                "Could not deliver email UID %s; cached summary will be retried",
                message.uid,
            )


def run() -> None:
    """轮询邮箱、并发摘要，并串行投递 Telegram。"""
    config = load_config()
    state = StateStore(config.state_db_path, config.legacy_state_path)
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
    executor = ThreadPoolExecutor(
        max_workers=config.llm_concurrency,
        thread_name_prefix="llm-summary",
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
                reconciled_uid = state.reconcile_mailbox(
                    mailbox.key, mailbox.uid_validity
                )
                if reconciled_uid is not None:
                    baseline = max(baseline, reconciled_uid)
                sent_uids = state.load_processed_uids()
                messages = mail.fetch_new_messages(baseline + 1, sent_uids)
                if messages:
                    LOGGER.info("Found %s queued email(s)", len(messages))
                    summaries = _summarize_batch(
                        messages,
                        state,
                        summarizer,
                        executor,
                    )
                    _deliver_batch(
                        messages,
                        summaries,
                        state,
                        telegram,
                        config.app_timezone,
                        mailbox.key,
                    )
            except (MailClientError, StateError):
                LOGGER.exception("Polling cycle failed; service will retry")
            except Exception:
                LOGGER.exception("Unexpected polling error; service will continue")

            STOP_EVENT.wait(config.poll_interval)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        mail.close()
        telegram.close()
        state.close()


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
