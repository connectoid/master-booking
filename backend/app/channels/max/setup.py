"""Проверка токена и подписка бота на вебхук.

Запуск из папки backend:
    python -m app.channels.max.setup            # показать бота и подписаться
    python -m app.channels.max.setup --status   # текущие подписки
    python -m app.channels.max.setup --remove   # отписаться
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.channels.max.adapter import UPDATE_TYPES
from app.channels.max.client import MaxClient
from app.config import get_settings


async def main(argv: list[str]) -> int:
    s = get_settings()
    if not s.max_bot_token:
        print("MAX_BOT_TOKEN не задан в .env")
        return 1
    client = MaxClient(s.max_bot_token, base_url=s.max_api_base, ca_file=s.max_ca_file or None)
    url = s.public_base_url.rstrip("/") + "/max/webhook"
    try:
        me = await client.get_me()
        print("Бот:", json.dumps(me, ensure_ascii=False))
        if "--status" in argv:
            print(json.dumps(await client.subscriptions(), ensure_ascii=False, indent=2))
        elif "--remove" in argv:
            print(await client.unsubscribe(url))
        else:
            if not url.startswith("https://"):
                print("PUBLIC_BASE_URL должен начинаться с https://")
                return 1
            if len(s.max_webhook_secret) < 5:
                print("MAX_WEBHOOK_SECRET не задан (5–256 символов: латиница, цифры, _ и -)")
                return 1
            print(await client.subscribe(url, UPDATE_TYPES, s.max_webhook_secret))
            print("Подписка оформлена:", url)
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
