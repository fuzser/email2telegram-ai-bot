"""应用配置加载与校验。"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(ValueError):
    """表示环境配置缺失或格式不正确。"""


@dataclass(frozen=True, slots=True)
class Config:
    """集中保存应用运行所需配置。"""

    email_address: str
    email_password: str
    imap_host: str
    imap_port: int
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval: int


def _required(name: str) -> str:
    """读取必填环境变量，但不在错误中包含变量值。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: str, maximum: int) -> int:
    """读取有合理上限的正整数配置。"""
    raw_value = os.getenv(name, default).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ConfigError(f"{name} must be between 1 and {maximum}")
    return value


def load_config() -> Config:
    """从 .env 和进程环境读取并校验配置。"""
    load_dotenv()
    return Config(
        email_address=_required("EMAIL_ADDRESS"),
        email_password=_required("EMAIL_PASSWORD"),
        imap_host=_required("IMAP_HOST"),
        imap_port=_positive_int("IMAP_PORT", "993", 65535),
        openai_api_key=_required("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://grsaiapi.com/v1").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip(),
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
        poll_interval=_positive_int("POLL_INTERVAL", "10", 300),
    )
