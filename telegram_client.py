"""Telegram Bot API 消息投递客户端。"""

import logging
import time

import requests

LOGGER = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """表示 Telegram 消息多次尝试后仍未成功发送。"""


class TelegramClient:
    """通过 Telegram Bot API 发送纯文本通知。"""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._session = requests.Session()

    def send_message(self, text: str) -> None:
        """发送消息，网络错误、限流和服务端错误会自动重试。"""
        for attempt in range(3):
            try:
                response = self._session.post(
                    self._url,
                    json={"chat_id": self.chat_id, "text": text},
                    timeout=(5, 15),
                )
                if response.status_code == 429:
                    delay = min(_retry_after(response), 10)
                    raise _RetryableTelegramError(delay)
                if 500 <= response.status_code < 600:
                    raise _RetryableTelegramError(2**attempt)
                if not response.ok:
                    raise TelegramError(
                        f"Telegram rejected the message with HTTP {response.status_code}"
                    )
                payload = response.json()
                if not payload.get("ok"):
                    raise TelegramError("Telegram returned an unsuccessful response")
                return
            except _RetryableTelegramError as exc:
                delay = exc.delay
            except (requests.Timeout, requests.ConnectionError) as exc:
                delay = 2**attempt
                if attempt == 2:
                    raise TelegramError(
                        "Telegram request failed after retries"
                    ) from exc
            except requests.RequestException as exc:
                raise TelegramError("Telegram request failed") from exc
            except ValueError as exc:
                raise TelegramError("Telegram returned invalid JSON") from exc

            if attempt == 2:
                raise TelegramError("Telegram request failed after retries")
            LOGGER.warning("Telegram request failed; retrying in %s second(s)", delay)
            time.sleep(delay)

    def close(self) -> None:
        """释放 HTTP 连接池。"""
        self._session.close()


class _RetryableTelegramError(Exception):
    """携带服务端建议等待时间的内部重试信号。"""

    def __init__(self, delay: int) -> None:
        self.delay = delay


def _retry_after(response: requests.Response) -> int:
    """安全读取 Telegram 限流等待时间。"""
    try:
        payload = response.json()
        return max(1, int(payload.get("parameters", {}).get("retry_after", 1)))
    except (ValueError, TypeError):
        return 1
