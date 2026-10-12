# webapp — Mini App записи

Одна страница без сборки (HTML + CSS + JS), раздаётся бэкендом по адресу `/app/`.

- **MAX**: подключается MAX Bridge (`window.WebApp`), slug мастера приходит в
  `initDataUnsafe.start_param` из ссылки `https://max.ru/<бот>?startapp=<slug>`.
- **Telegram** (позже): `window.Telegram.WebApp` — код уже определяет платформу.
- **Браузер**: `https://<домен>/app/?m=<slug>` — только просмотр услуг и времени.

Запросы к API подписываются заголовками `Authorization: tma <initData>` и `X-Channel`.
Экраны: услуги → день и время → подтверждение → готово; «Мои записи» с отменой.
