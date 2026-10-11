import os
from pathlib import Path

import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Пересоздаёт схему в тестовой БД через Alembic и возвращает её URL (psycopg).

    ВНИМАНИЕ: удаляет все таблицы в базе TEST_DATABASE_URL.
    """
    if not DB_URL:
        pytest.skip("TEST_DATABASE_URL не задан")
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"db_url={DB_URL}"]})()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return DB_URL
