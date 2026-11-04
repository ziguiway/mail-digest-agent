from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, delete, event, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from ..domain.models import Setting


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, record):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")

        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        with self.path.with_suffix(".migrate.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cfg = Config()
            cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.path}")
            command.upgrade(cfg, "head")

    @contextmanager
    def session(self):
        with self.factory() as session, session.begin():
            yield session

    def get(self, key, default=None):
        with self.session() as session:
            row = session.get(Setting, key)
            return row.value if row else default

    def set(self, key, value):
        with self.session() as session:
            row = session.get(Setting, key)
            if row:
                row.value = value
            else:
                session.add(Setting(key=key, value=value))

    def compare_set(self, key, expected_revision, value):
        """Atomic revision check across API and Worker processes."""
        with self.session() as session:
            result = session.execute(
                update(Setting)
                .where(
                    Setting.key == key,
                    func.coalesce(Setting.value["revision"].as_integer(), 0) == expected_revision,
                )
                .values(value=value)
            )
            if result.rowcount:
                return True
        if expected_revision != 0:
            return False
        try:
            with self.session() as session:
                session.add(Setting(key=key, value=value))
            return True
        except IntegrityError:
            return False

    def delete(self, key):
        with self.session() as session:
            session.execute(delete(Setting).where(Setting.key == key))

    def close(self):
        self.engine.dispose()


def record(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
