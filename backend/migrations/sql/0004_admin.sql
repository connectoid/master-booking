-- Админка: блокировка мастера и заметка администратора.
ALTER TABLE masters ADD COLUMN blocked_at timestamptz;
ALTER TABLE masters ADD COLUMN admin_note text;
-- На время запуска сервис бесплатный: пробный срок не ведём.
UPDATE masters SET trial_ends_at = NULL;
