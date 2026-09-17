"""SQLite 持久任务状态和本地恢复备份。"""

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    """表示状态数据库无法安全读取或写入。"""


@dataclass(frozen=True, slots=True)
class EmailState:
    """一封邮件当前的持久处理状态。"""

    status: str
    summary: list[str] | None


class StateStore:
    """使用 SQLite 持久化邮箱基线和投递任务。"""

    def __init__(
        self,
        path: Path = Path("data/state.db"),
        legacy_path: Path | None = None,
    ) -> None:
        self.path = path
        self.backup_path = path.with_suffix(f"{path.suffix}.backup")
        self.marker_path = path.with_suffix(f"{path.suffix}.initialized")
        self.legacy_path = legacy_path or path.with_name("state.json")
        self._connection: sqlite3.Connection | None = None
        self._prepare_storage()
        try:
            self._connection = sqlite3.connect(self.path, timeout=10)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._create_schema()
            self.path.chmod(0o600)
            self._migrate_legacy_state()
            self._recover_inflight()
            self._write_marker()
            self._backup()
        except (OSError, sqlite3.Error, ValueError, StateError) as exc:
            self.close()
            raise StateError(f"Cannot initialize state database: {self.path}") from exc

    def _prepare_storage(self) -> None:
        """创建目录，并在主库丢失时优先恢复本地备份。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        if self.backup_path.exists():
            shutil.copy2(self.backup_path, self.path)
            return
        if self.marker_path.exists():
            raise StateError(
                "State database and backup are missing; refusing a silent reset"
            )

    def _create_schema(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS emails (
                uid_key TEXT PRIMARY KEY,
                uid_validity TEXT NOT NULL,
                uid INTEGER NOT NULL,
                record_type TEXT NOT NULL DEFAULT 'email' CHECK (
                    record_type IN ('email', 'reconciled_checkpoint')
                ),
                received_at TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'processing', 'summarized', 'sent')
                ),
                summary_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_emails_status_uid
            ON emails(status, uid);

            CREATE INDEX IF NOT EXISTS idx_emails_uid_validity_uid
            ON emails(uid_validity, uid);
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(emails)")
        }
        if "record_type" not in columns:
            connection.execute(
                """
                ALTER TABLE emails ADD COLUMN record_type TEXT NOT NULL DEFAULT 'email'
                CHECK (record_type IN ('email', 'reconciled_checkpoint'))
                """
            )
        connection.commit()

    def reconcile_mailbox(self, mailbox_key: str, uid_validity: str) -> int | None:
        """启动时以前进方式对账水位与最新 sent 记录。"""
        connection = self._require_connection()
        watermark_row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (f"completed_through:{mailbox_key}",),
        ).fetchone()
        sent_row = connection.execute(
            """
            SELECT uid_key, uid FROM emails
            WHERE uid_validity = ? AND status = 'sent'
            ORDER BY uid DESC LIMIT 1
            """,
            (uid_validity,),
        ).fetchone()
        watermark = int(watermark_row["value"]) if watermark_row else None
        latest_sent = int(sent_row["uid"]) if sent_row else None
        values = [value for value in (watermark, latest_sent) if value is not None]
        if not values:
            return None
        effective = max(values)
        now = _utc_now()
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (f"completed_through:{mailbox_key}", str(effective)),
            )
            if latest_sent != effective:
                uid_key = f"{uid_validity}:{effective}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO emails(
                        uid_key, uid_validity, uid, record_type, status,
                        created_at, updated_at, sent_at
                    ) VALUES (?, ?, ?, 'reconciled_checkpoint', 'sent', ?, ?, ?)
                    """,
                    (uid_key, uid_validity, effective, now, now, now),
                )
        return effective

    def advance_completed_through(self, mailbox_key: str, uid: int) -> None:
        """只允许向前推进已完成水位。"""
        connection = self._require_connection()
        key = f"completed_through:{mailbox_key}"
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        current = int(row["value"]) if row else None
        if current is not None and uid <= current:
            return
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (key, str(uid)),
            )
        self._backup()

    def _migrate_legacy_state(self) -> None:
        """首次升级时导入旧 JSON，保留既有基线与去重记录。"""
        connection = self._require_connection()
        migrated = connection.execute(
            "SELECT value FROM metadata WHERE key = 'legacy_json_migrated'"
        ).fetchone()
        if migrated is not None or not self.legacy_path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(
                self.legacy_path.read_text(encoding="utf-8")
            )
            baselines = data["baselines"]
            processed = data["processed"]
            if not isinstance(baselines, dict) or not isinstance(processed, list):
                raise TypeError("Unsupported legacy state")
            now = _utc_now()
            with connection:
                for mailbox_key, uid in baselines.items():
                    connection.execute(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                        (f"baseline:{mailbox_key}", str(int(uid))),
                    )
                for uid_key_value in processed:
                    uid_key = str(uid_key_value)
                    uid_validity, uid_text = uid_key.rsplit(":", 1)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO emails(
                            uid_key, uid_validity, uid, status,
                            created_at, updated_at, sent_at
                        ) VALUES (?, ?, ?, 'sent', ?, ?, ?)
                        """,
                        (uid_key, uid_validity, int(uid_text), now, now, now),
                    )
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('legacy_json_migrated', ?)",
                    (now,),
                )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StateError("Cannot migrate legacy state.json") from exc

    def _recover_inflight(self) -> None:
        """把进程中断时遗留的处理中任务放回待处理队列。"""
        connection = self._require_connection()
        with connection:
            connection.execute(
                """
                UPDATE emails
                SET status = 'pending', updated_at = ?, last_error = 'process_restarted'
                WHERE status = 'processing'
                """,
                (_utc_now(),),
            )

    def load_processed_uids(self) -> set[str]:
        """读取已成功发送 Telegram 的 UID 集合。"""
        rows = self._require_connection().execute(
            "SELECT uid_key FROM emails WHERE status = 'sent'"
        )
        return {str(row["uid_key"]) for row in rows}

    def get_baseline(self, mailbox_key: str) -> int | None:
        """读取指定邮箱实例的首次启动 UID 基线。"""
        row = (
            self._require_connection()
            .execute(
                "SELECT value FROM metadata WHERE key = ?", (f"baseline:{mailbox_key}",)
            )
            .fetchone()
        )
        return int(row["value"]) if row is not None else None

    def set_baseline(self, mailbox_key: str, uid: int) -> None:
        """记录首次启动时的最大 UID。"""
        connection = self._require_connection()
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (f"baseline:{mailbox_key}", str(uid)),
            )
        self._backup()

    def register_email(
        self,
        uid_key: str,
        uid_validity: str,
        uid: int,
        received_at: datetime,
    ) -> None:
        """在调用外部服务前原子登记新邮件。"""
        self.register_emails([(uid_key, uid_validity, uid, received_at)])

    def register_emails(
        self,
        records: list[tuple[str, str, int, datetime]],
    ) -> None:
        """在一次事务中登记一批新邮件，并生成一个恢复备份。"""
        if not records:
            return
        now = _utc_now()
        connection = self._require_connection()
        with connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO emails(
                    uid_key, uid_validity, uid, received_at, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                [
                    (
                        uid_key,
                        uid_validity,
                        uid,
                        received_at.astimezone(timezone.utc).isoformat(),
                        now,
                        now,
                    )
                    for uid_key, uid_validity, uid, received_at in records
                ],
            )
        self._backup()

    def get_email_state(self, uid_key: str) -> EmailState:
        """读取任务状态及已缓存摘要。"""
        row = (
            self._require_connection()
            .execute(
                "SELECT status, summary_json FROM emails WHERE uid_key = ?", (uid_key,)
            )
            .fetchone()
        )
        if row is None:
            raise StateError(f"Email state is missing for UID key {uid_key}")
        summary = None
        if row["summary_json"] is not None:
            try:
                decoded = json.loads(row["summary_json"])
            except json.JSONDecodeError as exc:
                raise StateError("Stored summary is invalid") from exc
            if not isinstance(decoded, list) or not all(
                isinstance(item, str) for item in decoded
            ):
                raise StateError("Stored summary has an unsupported format")
            summary = decoded
        return EmailState(str(row["status"]), summary)

    def mark_processing(self, uid_key: str) -> None:
        """记录一次 LLM 处理尝试。"""
        connection = self._require_connection()
        with connection:
            cursor = connection.execute(
                """
                UPDATE emails
                SET status = 'processing', attempts = attempts + 1,
                    last_error = NULL, updated_at = ?
                WHERE uid_key = ? AND status = 'pending'
                """,
                (_utc_now(), uid_key),
            )
            if cursor.rowcount != 1:
                raise StateError("Cannot start email from current task state")

    def mark_summarized(self, uid_key: str, bullets: list[str]) -> None:
        """缓存摘要，使 Telegram 重试不重复消耗 LLM。"""
        connection = self._require_connection()
        with connection:
            cursor = connection.execute(
                """
                UPDATE emails
                SET status = 'summarized', summary_json = ?, last_error = NULL,
                    updated_at = ?
                WHERE uid_key = ? AND status = 'processing'
                """,
                (json.dumps(bullets, ensure_ascii=False), _utc_now(), uid_key),
            )
            if cursor.rowcount != 1:
                raise StateError("Cannot persist summary from current task state")
        self._backup()

    def mark_pending(self, uid_key: str, error_category: str) -> None:
        """摘要失败后将任务放回待处理状态。"""
        connection = self._require_connection()
        with connection:
            connection.execute(
                """
                UPDATE emails
                SET status = 'pending', last_error = ?, updated_at = ?
                WHERE uid_key = ? AND status = 'processing'
                """,
                (error_category[:120], _utc_now(), uid_key),
            )
        self._backup()

    def mark_delivery_failed(self, uid_key: str, error_category: str) -> None:
        """保留已生成摘要，并记录 Telegram 投递失败。"""
        connection = self._require_connection()
        with connection:
            connection.execute(
                """
                UPDATE emails
                SET last_error = ?, updated_at = ?
                WHERE uid_key = ? AND status = 'summarized'
                """,
                (error_category[:120], _utc_now(), uid_key),
            )
        self._backup()

    def mark_processed(self, uid_key: str) -> None:
        """Telegram 成功后把任务标记为已发送。"""
        connection = self._require_connection()
        now = _utc_now()
        with connection:
            cursor = connection.execute(
                """
                UPDATE emails
                SET status = 'sent', sent_at = ?, updated_at = ?, last_error = NULL
                WHERE uid_key = ? AND status = 'summarized'
                """,
                (now, now, uid_key),
            )
            if cursor.rowcount != 1:
                raise StateError("Cannot mark email sent from current task state")
        self._backup()

    def _write_marker(self) -> None:
        """记录本机曾初始化状态库，用于识别主库和备份同时丢失。"""
        if self.marker_path.exists():
            return
        self.marker_path.write_text("initialized\n", encoding="ascii")
        self.marker_path.chmod(0o600)

    def _backup(self) -> None:
        """通过 SQLite backup API 原子刷新同目录恢复副本。"""
        connection = self._require_connection()
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent,
                prefix=f".{self.backup_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            backup_connection = sqlite3.connect(temp_path)
            try:
                connection.backup(backup_connection)
            finally:
                backup_connection.close()
            with temp_path.open("r+b") as backup_file:
                backup_file.flush()
                os.fsync(backup_file.fileno())
            os.replace(temp_path, self.backup_path)
            self.backup_path.chmod(0o600)
        except (OSError, sqlite3.Error) as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise StateError("Cannot back up state database") from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StateError("State database is closed")
        return self._connection

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
