"""Проверка токена и подключение вебхука Telegram.

Запуск из папки backend:
    python -m app.channels.telegram.setup            # показать бота и подключить вебхук
    python -m app.channels.telegram.setup --status   # состояние вебхука
    python -m app.channels.telegram.setup --remove   # отключить вебхук
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

from app.channels.telegram.adapter import ALLOWED_UPDATES
from app.channels.telegram.client import TelegramApiError, TelegramClient
from app.config import get_settings


async def main(argv: list[str]) -> int:
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN не задан в .env")
        return 1
    client = TelegramClient(s.telegram_bot_token, base_url=s.telegram_api_base)
    url = s.public_base_url.rstrip("/") + "/telegram/webhook"
    try:
        me = await client.get_me()
        print("Бот:", json.dumps(me, ensure_ascii=False))
        if "--status" in argv:
            print(json.dumps(await client.webhook_info(), ensure_ascii=False, indent=2))
        elif "--remove" in argv:
            print(await client.delete_webhook())
        else:
            if not url.startswith("https://"):
                print("PUBLIC_BASE_URL должен начинаться с https://")
                return 1
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", s.telegram_webhook_secret):
                print("TELEGRAM_WEBHOOK_SECRET не задан (латиница, цифры, _ и -, до 256 символов)")
                return 1
            await client.set_webhook(url, s.telegram_webhook_secret, ALLOWED_UPDATES)
            await client.set_my_commands([{"command": "start", "description": "Меню"}])
            print("Вебхук подключён:", url)
            print(f"Mini App: {s.public_base_url.rstrip('/')}/app/. Укажите этот адрес в @BotFather "
                  "(Bot Settings → Configure Mini App), чтобы работали ссылки t.me/<бот>?startapp=…")
    except TelegramApiError as e:
        print("Ошибка Telegram API:", e)
        return 1
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
