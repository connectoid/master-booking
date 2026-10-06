-- Начальная схема. Источник правды для таблиц; app/models.py должен ей соответствовать.

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Мастер (арендатор). У салона в будущем будет несколько мастеров — пока 1 мастер = 1 профиль.
CREATE TABLE masters (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug             varchar(32)  NOT NULL UNIQUE
                     CHECK (slug ~ '^[a-z0-9_]{3,32}$'),
    name             varchar(120) NOT NULL,
    timezone         varchar(64)  NOT NULL DEFAULT 'Europe/Moscow',
    slot_step_min    smallint     NOT NULL DEFAULT 15  CHECK (slot_step_min BETWEEN 5 AND 120),
    min_lead_min     integer      NOT NULL DEFAULT 60  CHECK (min_lead_min >= 0),
    horizon_days     smallint     NOT NULL DEFAULT 30  CHECK (horizon_days BETWEEN 1 AND 365),
    buffer_min       smallint     NOT NULL DEFAULT 0   CHECK (buffer_min BETWEEN 0 AND 120),
    trial_ends_at    timestamptz,
    paid_until       timestamptz,
    created_at       timestamptz  NOT NULL DEFAULT now()
);

-- Через какие мессенджеры мастер управляет профилем (один человек может зайти и из MAX, и из TG).
CREATE TABLE master_accounts (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint      NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    channel          varchar(16) NOT NULL CHECK (channel IN ('max', 'telegram', 'web')),
    external_user_id varchar(64) NOT NULL,
    chat_id          varchar(64),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel, external_user_id, master_id)
);
CREATE INDEX ix_master_accounts_master ON master_accounts (master_id);

CREATE TABLE services (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint       NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    name             varchar(120) NOT NULL,
    duration_min     smallint     NOT NULL CHECK (duration_min BETWEEN 5 AND 720),
    price            integer      CHECK (price >= 0),          -- рубли; NULL = «по договорённости»
    is_active        boolean      NOT NULL DEFAULT true,
    position         smallint     NOT NULL DEFAULT 0,
    created_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_services_master ON services (master_id);

-- Недельный график в локальном времени мастера. Несколько интервалов в день = перерывы.
-- weekday: 0 = понедельник ... 6 = воскресенье (как date.weekday() в Python).
CREATE TABLE working_intervals (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint   NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    weekday          smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time       time     NOT NULL,
    end_time         time     NOT NULL,
    CHECK (start_time < end_time)
);
CREATE INDEX ix_working_intervals_master ON working_intervals (master_id, weekday);

-- Исключения на конкретную дату. Если на дату есть строки — они ЗАМЕНЯЮТ недельный график.
-- Строка с start_time/end_time = NULL означает выходной.
CREATE TABLE schedule_overrides (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    day              date   NOT NULL,
    start_time       time,
    end_time         time,
    CHECK ((start_time IS NULL AND end_time IS NULL)
        OR (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time))
);
CREATE INDEX ix_schedule_overrides_master_day ON schedule_overrides (master_id, day);

-- Клиент конкретного мастера (один человек у двух мастеров = две строки).
CREATE TABLE clients (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint       NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    name             varchar(120) NOT NULL,
    phone            varchar(32),
    note             text,
    created_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_clients_master ON clients (master_id);

-- Куда слать клиенту уведомления. Клиент, добавленный мастером вручную, может не иметь контактов.
CREATE TABLE client_contacts (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id        bigint      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    channel          varchar(16) NOT NULL CHECK (channel IN ('max', 'telegram', 'web')),
    external_user_id varchar(64) NOT NULL,
    chat_id          varchar(64),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, channel)
);
CREATE INDEX ix_client_contacts_lookup ON client_contacts (channel, external_user_id);

CREATE TABLE appointments (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_id        bigint       NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
    client_id        bigint       NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    service_id       bigint       REFERENCES services(id) ON DELETE SET NULL,
    service_name     varchar(120) NOT NULL,                 -- снимок на момент записи
    price            integer      CHECK (price >= 0),        -- снимок на момент записи
    starts_at        timestamptz  NOT NULL,
    ends_at          timestamptz  NOT NULL,
    status           varchar(16)  NOT NULL DEFAULT 'confirmed'
                     CHECK (status IN ('pending', 'confirmed', 'cancelled', 'completed', 'no_show')),
    source           varchar(16)  NOT NULL
                     CHECK (source IN ('max', 'telegram', 'web', 'manual')),
    comment          text,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    CHECK (starts_at < ends_at),
    -- Главная защита от двойной записи: активные записи мастера не пересекаются.
    CONSTRAINT ex_appointments_no_overlap EXCLUDE USING gist (
        master_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&
    ) WHERE (status IN ('pending', 'confirmed'))
);
CREATE INDEX ix_appointments_master_start ON appointments (master_id, starts_at);
CREATE INDEX ix_appointments_client ON appointments (client_id);

CREATE TABLE reminders (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id   bigint      NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    kind             varchar(16) NOT NULL CHECK (kind IN ('day_before', 'two_hours')),
    send_at          timestamptz NOT NULL,
    status           varchar(16) NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    attempts         smallint    NOT NULL DEFAULT 0,
    sent_at          timestamptz,
    last_error       text,
    UNIQUE (appointment_id, kind)
);
-- Планировщик выбирает только ожидающие — частичный индекс.
CREATE INDEX ix_reminders_due ON reminders (send_at) WHERE status = 'pending';
