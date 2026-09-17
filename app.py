"""邮件摘要服务入口占位模块。"""

import logging

from config import load_config


LOGGER = logging.getLogger(__name__)


def main() -> None:
    """加载配置并启动轮询服务。"""
    load_config()
    raise NotImplementedError("Service orchestration will be implemented next.")


if __name__ == "__main__":
    main()

