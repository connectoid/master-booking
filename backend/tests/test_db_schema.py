"""Интеграционные тесты схемы на реальном PostgreSQL.

Запуск: TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/booking_test pytest
ВНИМАНИЕ: тест удаляет и пересоздаёт все таблицы в этой БД.
"""
import pytest


@pytest.fixture(scope="module")
def engine(migrated_db):
    from sqlalchemy import create_engine

    eng = create_engine(migrated_db)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    # другие тесты (test_api) коммитят данные — начинаем с пустой базы
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE masters RESTART IDENTITY CASCADE"))

    with engine.connect() as conn:
        tx = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
            yield s
        tx.rollback()


def _seed(session):
    import datetime as dt

    from app.models import Client, Master, Service

    master = Master(slug="anna_nails", name="Анна", timezone="Asia/Anadyr")
    session.add(master)
    session.flush()
    service = Service(master_id=master.id, name="Маникюр", duration_min=90, price=1500)
    client = Client(master_id=master.id, name="Ольга")
    session.add_all([service, client])
    session.flush()
    start = dt.datetime(2026, 10, 10, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=12)))
    return master, service, client, start


def _appt(master, client, start, minutes, status="confirmed"):
    import datetime as dt

    from app.models import Appointment

    return Appointment(
        master_id=master.id, client_id=client.id, service_name="Маникюр",
        starts_at=start, ends_at=start + dt.timedelta(minutes=minutes),
        status=status, source="max",
    )


def test_orm_roundtrip_and_defaults(session):
    from app.models import Appointment

    master, _, client, start = _seed(session)
    session.add(_appt(master, client, start, 90))
    session.flush()
    appt = session.query(Appointment).one()
    assert appt.status == "confirmed"
    session.refresh(master)
    assert master.slot_step_min == 15


def test_overlapping_active_appointments_rejected(session):
    import datetime as dt

    from sqlalchemy.exc import IntegrityError

    master, _, client, start = _seed(session)
    session.add(_appt(master, client, start, 90))
    session.flush()
    session.add(_appt(master, client, start + dt.timedelta(minutes=30), 60))
    with pytest.raises(IntegrityError, match="ex_appointments_no_overlap"):
        session.flush()


def test_touching_and_cancelled_appointments_allowed(session):
    import datetime as dt

    master, _, client, start = _seed(session)
    session.add(_appt(master, client, start, 90))
    session.add(_appt(master, client, start + dt.timedelta(minutes=90), 60))
    session.add(_appt(master, client, start + dt.timedelta(minutes=30), 60, status="cancelled"))
    session.flush()
