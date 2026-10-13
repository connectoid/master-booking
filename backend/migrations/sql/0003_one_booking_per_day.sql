-- Настройка мастера: клиент может записаться не больше одного раза в день.
ALTER TABLE masters ADD COLUMN one_booking_per_day boolean NOT NULL DEFAULT false;
