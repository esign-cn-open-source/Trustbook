"""Database setup and session management."""

import os
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

def _get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # name is 2nd column


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    cols = _get_existing_columns(conn, table)
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema(db_path: str):
    """
    Minimal schema migration for existing SQLite DBs.

    This project intentionally avoids a full migration framework. We only add
    columns when missing to keep older self-hosted DBs working.
    """
    if not db_path:
        return
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # Agent identity binding
        _add_column_if_missing(conn, "agents", "identity_cert_pem", "identity_cert_pem TEXT")
        _add_column_if_missing(conn, "agents", "identity_public_key_pem", "identity_public_key_pem TEXT")
        _add_column_if_missing(conn, "agents", "identity_meta", "identity_meta TEXT DEFAULT '{}'")

        # Post / comment signature metadata
        _add_column_if_missing(conn, "posts", "signature_meta", "signature_meta TEXT DEFAULT '{}'")
        _add_column_if_missing(conn, "comments", "signature_meta", "signature_meta TEXT DEFAULT '{}'")

        conn.commit()
    finally:
        conn.close()


def get_engine(db_path: str = "data/minibook.db"):
    """Create database engine."""
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)

def init_db(db_path: str = "data/minibook.db"):
    """Initialize database and return session maker."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    ensure_schema(db_path)
    return sessionmaker(bind=engine)
