"""Gmail IMAP 连接、查询和 MIME 邮件解析。"""

import imaplib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from typing import TypeVar

from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)
INTERNAL_DATE_PATTERN = re.compile(rb'INTERNALDATE "([^"]+)"')
ResultType = TypeVar("ResultType")


class MailClientError(RuntimeError):
    """表示 IMAP 操作失败。"""


@dataclass(frozen=True, slots=True)
class MailboxStatus:
    """用于区分邮箱 UID 空间并建立首次启动基线。"""

    uid_validity: str
    latest_uid: int

    @property
    def key(self) -> str:
        """返回持久化状态中的邮箱实例键。"""
        return f"INBOX:{self.uid_validity}"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """表示从 IMAP 获取并规范化后的邮件。"""

    uid: int
    uid_validity: str
    sender: str
    subject: str
    received_at: datetime
    body: str

    @property
    def uid_key(self) -> str:
        """返回跨 UIDVALIDITY 安全的持久化标识。"""
        return f"{self.uid_validity}:{self.uid}"


@dataclass(frozen=True, slots=True)
class MailFetchResult:
    """保存本次读取结果，以及因重启上限而跳过的最高 UID。"""

    messages: list[EmailMessage]
    skipped_through_uid: int | None


class MailClient:
    """负责 IMAP 连接、自动重连、查询和邮件解析。"""

    def __init__(self, host: str, port: int, address: str, password: str) -> None:
        self.host = host
        self.port = port
        self.address = address
        self.password = password
        self._connection: imaplib.IMAP4_SSL | None = None
        self._uid_validity = ""

    def mailbox_status(self) -> MailboxStatus:
        """返回当前 UIDVALIDITY 和最大 UID，失败时自动重连一次。"""
        return self._with_reconnect(self._read_mailbox_status)

    def fetch_new_messages(
        self,
        minimum_uid: int,
        processed_uids: set[str],
        maximum_messages: int | None = None,
    ) -> MailFetchResult:
        """获取基线后的邮件，并可只保留最新的指定数量。"""
        return self._with_reconnect(
            lambda: self._fetch_new_messages(
                minimum_uid, processed_uids, maximum_messages
            )
        )

    def _connect(self) -> imaplib.IMAP4_SSL:
        """建立 TLS IMAP 连接并以只读方式选择 INBOX。"""
        if self._connection is not None:
            return self._connection
        try:
            connection = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
            connection.login(self.address, self.password)
            status, _ = connection.select("INBOX", readonly=True)
            if status != "OK":
                raise MailClientError("Cannot select INBOX")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailClientError("Cannot connect to IMAP server") from exc
        self._connection = connection
        try:
            self._uid_validity = self._get_uid_validity(connection)
        except MailClientError:
            self._disconnect()
            raise
        LOGGER.info("Connected to IMAP mailbox")
        return connection

    @staticmethod
    def _get_uid_validity(connection: imaplib.IMAP4_SSL) -> str:
        """读取当前 INBOX 的 UIDVALIDITY。"""
        status, values = connection.response("UIDVALIDITY")
        if status != "UIDVALIDITY" or not values or values[0] is None:
            raise MailClientError("IMAP server did not provide UIDVALIDITY")
        value = values[0]
        return value.decode("ascii") if isinstance(value, bytes) else str(value)

    def _search_uids(self, connection: imaplib.IMAP4_SSL) -> list[int]:
        """按 UID 查询邮箱，避免依赖 UNSEEN 标记。"""
        status, data = connection.uid("SEARCH", None, "ALL")
        if status != "OK" or not data:
            raise MailClientError("IMAP UID search failed")
        return [int(item) for item in data[0].split()]

    def _read_mailbox_status(self) -> MailboxStatus:
        connection = self._connect()
        self._refresh_mailbox(connection)
        uids = self._search_uids(connection)
        return MailboxStatus(self._uid_validity, max(uids, default=0))

    @staticmethod
    def _refresh_mailbox(connection: imaplib.IMAP4_SSL) -> None:
        """用 NOOP 刷新所选邮箱状态，让长连接看到新邮件。"""
        status, _ = connection.noop()
        if status != "OK":
            raise MailClientError("IMAP NOOP refresh failed")
        LOGGER.debug("Mailbox refreshed via IMAP NOOP")

    def _fetch_new_messages(
        self,
        minimum_uid: int,
        processed_uids: set[str],
        maximum_messages: int | None,
    ) -> MailFetchResult:
        connection = self._connect()
        uids = self._search_uids(connection)
        candidates = [uid for uid in uids if uid >= minimum_uid]
        skipped_through_uid = None
        if maximum_messages is not None and len(candidates) > maximum_messages:
            skipped_through_uid = candidates[-maximum_messages - 1]
            candidates = candidates[-maximum_messages:]
        messages: list[EmailMessage] = []
        for uid in candidates:
            uid_key = f"{self._uid_validity}:{uid}"
            if uid_key in processed_uids:
                continue
            status, data = connection.uid(
                "FETCH", str(uid), "(UID INTERNALDATE BODY.PEEK[])"
            )
            if status != "OK" or not data or not isinstance(data[0], tuple):
                raise MailClientError(f"IMAP fetch failed for UID {uid}")
            metadata, raw_message = data[0]
            if not isinstance(metadata, bytes) or not isinstance(raw_message, bytes):
                raise MailClientError(f"Invalid IMAP response for UID {uid}")
            messages.append(
                self._parse_message(uid, self._uid_validity, metadata, raw_message)
            )
        return MailFetchResult(messages, skipped_through_uid)

    def _with_reconnect(self, operation: Callable[[], ResultType]) -> ResultType:
        """连接失效时清理并重试一次当前 IMAP 操作。"""
        for attempt in range(2):
            try:
                return operation()
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as exc:
                self._disconnect()
                if attempt == 0:
                    LOGGER.warning("IMAP connection lost; reconnecting")
                    continue
                raise MailClientError("IMAP operation failed after reconnect") from exc
            except MailClientError:
                self._disconnect()
                if attempt == 0:
                    LOGGER.warning("IMAP operation failed; reconnecting")
                    continue
                raise
        raise MailClientError("IMAP operation failed")

    @staticmethod
    def _parse_message(
        uid: int, uid_validity: str, metadata: bytes, raw_message: bytes
    ) -> EmailMessage:
        """解析头部、到达时间和正文，优先使用纯文本正文。"""
        parsed = message_from_bytes(raw_message, policy=policy.default)
        sender = _decode_header(parsed.get("From", "Unknown sender"))
        subject = _decode_header(parsed.get("Subject", "(no subject)"))
        received_at = _parse_internal_date(metadata)
        body = _extract_body(parsed)
        return EmailMessage(
            uid=uid,
            uid_validity=uid_validity,
            sender=sender,
            subject=subject,
            received_at=received_at,
            body=body,
        )

    def _disconnect(self) -> None:
        """尽力关闭当前连接，不让清理异常影响主流程。"""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def close(self) -> None:
        """关闭当前 IMAP 连接。"""
        self._disconnect()


def _decode_header(value: str) -> str:
    """解码 MIME 编码邮件头。"""
    try:
        decoded = str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        decoded = value
    return " ".join(decoded.split()).strip()


def _parse_internal_date(metadata: bytes) -> datetime:
    """解析服务器记录的邮件到达时间。"""
    match = INTERNAL_DATE_PATTERN.search(metadata)
    if not match:
        return datetime.now(timezone.utc)
    try:
        value = match.group(1).decode("ascii")
        return datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    except (UnicodeDecodeError, ValueError):
        return datetime.now(timezone.utc)


def _decode_part(part: Message) -> str:
    """按 MIME charset 解码正文片段。"""
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_body(message: Message) -> str:
    """提取纯文本正文；缺失时将 HTML 转换为可读文本。"""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            html_parts.append(_decode_part(part))

    if plain_parts:
        body = "\n\n".join(plain_parts)
    elif html_parts:
        soup = BeautifulSoup("\n".join(html_parts), "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
        body = soup.get_text("\n")
    else:
        body = "(No readable message body)"

    lines = [line.strip() for line in body.splitlines()]
    return "\n".join(line for line in lines if line).strip()
