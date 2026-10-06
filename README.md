# master-booking

Онлайн-запись для частных мастеров с напоминаниями клиентам в MAX (затем Telegram и браузер).
Архитектура и решения — в [CLAUDE.md](CLAUDE.md).

## Быстрый старт (на VPS)

```bash
docker compose up -d db               # или свой PostgreSQL 16
cp .env.example .env                  # заполнить токены
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
pytest                                # тесты логики; для тестов БД задайте TEST_DATABASE_URL
uvicorn app.main:app --reload
```

Тесты БД (удаляют и пересоздают таблицы — только на отдельной тестовой базе):

```bash
TEST_DATABASE_URL=postgresql+psycopg://booking:booking@localhost:5432/booking_test pytest
```
