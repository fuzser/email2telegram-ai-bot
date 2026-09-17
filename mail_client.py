"""Gmail IMAP 客户端占位模块。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """表示从 IMAP 获取并规范化后的邮件。"""

    uid: str
    sender: str
    subject: str
    received_at: datetime
    body: str


class MailClient:
    """负责 IMAP 连接、查询和邮件解析。"""

    def fetch_new_messages(self, processed_uids: set[str]) -> list[EmailMessage]:
        """获取尚未成功处理的邮件。"""
        raise NotImplementedError("IMAP polling will be implemented next.")

    def close(self) -> None:
        """关闭当前 IMAP 连接。"""
