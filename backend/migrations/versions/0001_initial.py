"""Начальная схема

Revision ID: 0001
Revises:
Create Date: 2026-10-06
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "0001_initial.sql"


def upgrade() -> None:
    # exec_driver_sql: SQL уходит в драйвер как есть, без разбора bind-параметров SQLAlchemy
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS reminders, appointments, client_contacts, clients,
            schedule_overrides, working_intervals, services, master_accounts, masters CASCADE;
        """
    )
