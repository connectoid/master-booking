# master-booking

Онлайн-запись для частных мастеров с напоминаниями клиентам: MAX, браузер, Telegram.
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

## Веб-версия

- `https://<домен>/<slug>` — запись к мастеру в браузере (имя и телефон обязательны).
  После записи — кнопка «Получать напоминания в MAX» и ссылка на свои записи для другого устройства.
- `https://<домен>/cabinet` — кабинет мастера в браузере. Вход по ссылке из бота
  («Кабинет на компьютере» в меню или в настройках кабинета), ссылка живёт 10 минут, сессия — 30 дней.
- Задайте `WEB_SECRET` в `.env` (например, `openssl rand -hex 32`).
- nginx должен проксировать на бэкенд все адреса (`location /`), а не только `/api` и `/app`.

## Главная и политика

`/` — лендинг (`webapp/site/index.html`), `/start` — «Создать кабинет» (шаги и QR-код на бота MAX,
код рисует `app/domain/qr.py` без внешних библиотек), `/privacy` — политика обработки персональных данных.
Реквизиты ИП и почта подставляются из `.env`: `SITE_OWNER_NAME`, `SITE_INN`, `SITE_OGRNIP`, `SITE_EMAIL`.
Политика — черновик: перед запуском покажите юристу.

## Админка

`https://<домен>/admin` — сводка (мастера, записи по дням, источники), список мастеров с фильтрами и поиском,
карточка мастера: услуги, график, «оплачено до» (+30/+90/+365 дней или дата), блокировка страницы записи,
заметка, сообщение мастеру в бот. Данные клиентов в админку не выводятся.

1. Узнайте свой ID: команда `/myid` боту в MAX.
2. Впишите его в `.env`: `ADMIN_MAX_IDS=…` и перезапустите сервис.
3. Команда `/admin` боту — придёт ссылка для входа (10 минут), сессия — 7 дней.
