from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4


class Database:
    """Small SQLite wrapper for connections, transactions, and migrations."""

    def __init__(
        self,
        path: str | Path,
        *,
        migrations_dir: str | Path | None = None,
    ) -> None:
        self.path = Path(path)

        if migrations_dir is None:
            migrations_dir = Path(__file__).parent / "migrations"

        self.migrations_dir = Path(migrations_dir)
        self._active_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"reminder_agent_connection_{id(self)}",
            default=None,
        )

    def _open_connection(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.path)

        # Return rows that can be accessed as row["column_name"].
        conn.row_factory = sqlite3.Row

        # Apply these settings to every new SQLite connection.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")

        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a configured connection and always close it afterwards."""
        active = self._active_connection.get()
        if active is not None:
            yield active
            return

        conn = self._open_connection()

        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(
        self,
        *,
        immediate: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        """
        Open a transaction.

        Commit if the block succeeds.
        Roll back if an exception is raised.
        """
        active = self._active_connection.get()
        if active is not None:
            savepoint = f"nested_{uuid4().hex}"
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                active.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            return

        conn = self._open_connection()
        token = self._active_connection.set(conn)

        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._active_connection.reset(token)
            conn.close()

    def migrate(self) -> None:
        """Apply all unapplied numbered SQL migrations in filename order."""
        migration_files = sorted(self.migrations_dir.glob("*.sql"))

        with self.connect() as conn:
            self._ensure_schema_migrations_table(conn)

            applied = {
                row["migration_id"]: row["checksum"]
                for row in conn.execute(
                    """
                    SELECT migration_id, checksum
                    FROM schema_migrations
                    """
                )
            }

            for migration_path in migration_files:
                migration_id = migration_path.stem
                sql = migration_path.read_text(encoding="utf-8")
                checksum = sha256(sql.encode("utf-8")).hexdigest()

                if migration_id in applied:
                    if applied[migration_id] != checksum:
                        raise RuntimeError(
                            f"Migration {migration_id} has changed after being applied."
                        )
                    continue

                conn.executescript(sql)
                conn.execute(
                    """
                    INSERT INTO schema_migrations (
                        migration_id,
                        checksum,
                        applied_at_utc
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (migration_id, checksum),
                )
                conn.commit()

    @staticmethod
    def _ensure_schema_migrations_table(
        conn: sqlite3.Connection,
    ) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        conn.commit()
