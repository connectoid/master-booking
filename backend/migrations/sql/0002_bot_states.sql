-- Состояние диалога с ботом (многошаговый ввод: имя мастера, услуги, график).
CREATE TABLE bot_states (
    channel          varchar(16) NOT NULL CHECK (channel IN ('max', 'telegram', 'web')),
    external_user_id varchar(64) NOT NULL,
    state            varchar(32),
    data             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel, external_user_id)
);
