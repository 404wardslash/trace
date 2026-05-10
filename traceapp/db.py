from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from .config import db_path, ensure_home

engine = None
engine_path = None


def get_engine():
    global engine, engine_path
    current_path = db_path()
    if engine is None or engine_path != current_path:
        ensure_home()
        engine_path = current_path
        engine = create_engine(f"sqlite:///{current_path}", connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())
    # Migrate existing databases that predate the status column.
    with get_engine().connect() as conn:
        try:
            conn.execute(text("ALTER TABLE engagement ADD COLUMN status VARCHAR NOT NULL DEFAULT 'in_progress'"))
            conn.commit()
        except Exception:
            pass  # Column already exists
        # Fix rows written with the hyphenated value before this correction.
        conn.execute(text("UPDATE engagement SET status = 'in_progress' WHERE status = 'in-progress'"))
        conn.execute(text("UPDATE engagement SET status = 'completed' WHERE status = 'Completed'"))
        conn.commit()


@contextmanager
def session_scope():
    init_db()
    with Session(get_engine()) as session:
        yield session


def session_dependency():
    with session_scope() as session:
        yield session
