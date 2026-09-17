"""Telegram 消息客户端占位模块。"""


class TelegramClient:
    """负责格式化并发送 Telegram 通知。"""

    def send_message(self, text: str) -> None:
        """发送消息，失败时抛出异常。"""
        raise NotImplementedError("Telegram delivery will be implemented next.")

