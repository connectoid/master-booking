# master-booking

Онлайн-запись для частных мастеров с напоминаниями клиентам в MAX, Telegram и браузере.

## Стек
- Python 3.12, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Alembic, PostgreSQL 16.
- MAX: неофициальная `maxapi` или прямые HTTP-вызовы — держать адаптер тонким.
- Telegram: aiogram 3.
- Фронтенд: React + MAX UI (решим на этапе Mini App).

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

## API клиента (Mini App)

| Метод | Путь | Вход |
|---|---|---|
| GET | `/api/masters/{slug}` — мастер и активные услуги | нет |
| GET | `/api/masters/{slug}/slots?service_id=&date_from=&days=` — свободные слоты | нет |
| POST | `/api/masters/{slug}/appointments` — записаться | да |
| GET | `/api/masters/{slug}/appointments/my` — мои предстоящие записи | да |
| POST | `/api/appointments/{id}/cancel` — отменить свою запись | да |

Вход: заголовки `X-Channel: max|telegram` и `Authorization: tma <initData>`.
Документация с примерами — `/docs` после запуска `uvicorn app.main:app`.

## Бот MAX

Мастер пишет боту: регистрация (имя, часовой пояс) → услуги → график → ссылка для клиентов.
Дальше в боте: записи на сегодня/неделю, услуги, график, уведомления о новых записях и отменах.
Клиент получает подтверждение записи и напоминания (за сутки и за 2 часа) с кнопками
«Приду» / «Отменить запись».

### Настройка на сервере

1. Сертификат Минцифры для `platform-api2.max.ru` — только для нашего процесса, без установки в систему:
   ```bash
   mkdir -p /etc/okoshko && cd /etc/okoshko
   wget https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer
   wget https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer
   openssl x509 -inform DER -in russian_trusted_root_ca.cer -out root.pem
   openssl x509 -inform DER -in russian_trusted_sub_ca.cer -out sub.pem
   cat root.pem sub.pem > russian_trusted_ca.pem
   ```
2. В `.env`: `MAX_BOT_TOKEN`, `MAX_WEBHOOK_SECRET` (придумать), `MAX_CA_FILE=/etc/okoshko/russian_trusted_ca.pem`,
   `PUBLIC_BASE_URL=https://<домен>`.
3. `alembic upgrade head`, перезапуск сервиса.
4. Подписка на вебхук: `python -m app.channels.max.setup` (статус: `--status`, отписка: `--remove`).

## Mini App

Страница записи для клиента — `webapp/` (без сборки), раздаётся бэкендом по `https://<домен>/app/`.
В платформе MAX: **Чат-боты → ⋮ → Настройки** → ссылка на мини-приложение `https://<домен>/app/`.
Ссылка для клиентов: `https://max.ru/<бот>?startapp=<slug>` (бот выдаёт её мастеру сам).
Просмотр в браузере без записи: `https://<домен>/app/?m=<slug>`.
