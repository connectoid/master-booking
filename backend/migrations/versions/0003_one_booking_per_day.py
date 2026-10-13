"""Настройка «одна запись в день на клиента»

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""
from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0003_one_booking_per_day.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE masters DROP COLUMN IF EXISTS one_booking_per_day")
