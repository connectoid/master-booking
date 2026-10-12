"""Состояние диалога с ботом

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0002_bot_states.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bot_states")
