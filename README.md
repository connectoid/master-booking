# master-booking

Онлайн-запись для частных мастеров с напоминаниями клиентам в MAX и Telegram (затем браузер).
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

## Подключение мессенджеров

MAX: `MAX_BOT_TOKEN`, `MAX_WEBHOOK_SECRET` в `.env`, затем `python -m app.channels.max.setup`.

Telegram:

1. Создать бота в @BotFather (`/newbot`), токен — в `TELEGRAM_BOT_TOKEN`.
2. Придумать `TELEGRAM_WEBHOOK_SECRET` (латиница, цифры, `_` и `-`).
3. `systemctl restart okoshko-api`, затем `python -m app.channels.telegram.setup`.
4. В @BotFather: Bot Settings → Configure Mini App → Enable, адрес `https://<домен>/app/`.
   Без этого не откроются ссылки для соцсетей вида `t.me/<бот>?startapp=<мастер>`
   (кнопки внутри бота работают и так).
