"""应用配置占位模块。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    """集中保存从环境变量读取的应用配置。"""

    email_address: str
    email_password: str
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    openai_api_key: str = ""
    openai_base_url: str = "https://grsaiapi.com/v1"
    openai_model: str = "gpt-5.6-terra"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    poll_interval: int = 10


def load_config() -> Config:
    """从 .env 和进程环境读取配置。"""
    raise NotImplementedError("Configuration loading will be implemented next.")

