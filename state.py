"""已处理邮件的本地持久化状态。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    """表示状态文件无法安全读取或写入。"""


class StateStore:
    """持久化邮箱基线及已成功投递的 IMAP UID。"""

    def __init__(self, path: Path = Path("data/state.json")) -> None:
        self.path = path
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        """读取状态；损坏时拒绝静默重置，防止重复发送旧邮件。"""
        if not self.path.exists():
            return {"version": 1, "baselines": {}, "processed": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Cannot read state file: {self.path}") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("baselines"), dict)
            or not isinstance(data.get("processed"), list)
        ):
            raise StateError("State file has an unsupported format")
        return data

    def load_processed_uids(self) -> set[str]:
        """读取已成功发送通知的 UID 标识集合。"""
        return {str(item) for item in self._state["processed"]}

    def get_baseline(self, mailbox_key: str) -> int | None:
        """读取指定邮箱实例的首次启动 UID 基线。"""
        value = self._state["baselines"].get(mailbox_key)
        return int(value) if value is not None else None

    def set_baseline(self, mailbox_key: str, uid: int) -> None:
        """记录首次启动时的最大 UID。"""
        self._state["baselines"][mailbox_key] = uid
        self._save()

    def mark_processed(self, uid_key: str) -> None:
        """在 Telegram 发送成功后原子化保存 UID 标识。"""
        processed = self.load_processed_uids()
        if uid_key in processed:
            return
        processed.add(uid_key)
        self._state["processed"] = sorted(processed)
        self._save()

    def _save(self) -> None:
        """通过同目录临时文件和原子替换避免半写状态。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                json.dump(self._state, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            os.replace(temp_path, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except OSError as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise StateError(f"Cannot write state file: {self.path}") from exc
