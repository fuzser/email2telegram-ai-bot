"""本地持久化状态占位模块。"""

from pathlib import Path


class StateStore:
    """持久化已成功发送 Telegram 通知的 IMAP UID。"""

    def __init__(self, path: Path = Path("data/state.json")) -> None:
        self.path = path

    def load_processed_uids(self) -> set[str]:
        """读取已处理 UID 集合。"""
        raise NotImplementedError("Persistent state loading will be implemented next.")

    def mark_processed(self, uid: str) -> None:
        """在 Telegram 发送成功后原子化保存 UID。"""
        raise NotImplementedError("Persistent state writing will be implemented next.")

