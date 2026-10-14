"""Админка: блокировка мастера и заметка

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0004_admin.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE masters DROP COLUMN IF EXISTS admin_note")
    op.execute("ALTER TABLE masters DROP COLUMN IF EXISTS blocked_at")
