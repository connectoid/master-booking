"""Telegram: клиент Bot API, ссылки и вебхук (без базы данных)."""
import json

import httpx
import pytest

from app.models import Channel

TOKEN = "123:ABC"


async def test_client_sends_message_and_raises_on_error():
    from app.channels.telegram.client import TelegramApiError, TelegramClient

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(403, json={"ok": False, "error_code": 403,
                                         "description": "Forbidden: bot was blocked by the user"})

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(handler))
    try:
        await client.send_message(42, "Привет", {"inline_keyboard": []})
        assert seen[0][0] == f"/bot{TOKEN}/sendMessage"
        assert seen[0][1]["chat_id"] == 42 and seen[0][1]["text"] == "Привет"
        with pytest.raises(TelegramApiError) as err:
            await client.answer_callback("1", "")
        assert err.value.error_code == 403 and TOKEN not in str(err.value)
        assert "text" not in seen[1][1]  # пустой текст не отправляем
    finally:
        await client.close()


@pytest.fixture
def tg_runtime(monkeypatch):
    from app.config import Settings, get_settings
    from app.runtime import runtime

    get_settings.cache_clear()
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://okoshko.website")
    runtime.bot_usernames[Channel.MAX] = "id123_bot"
    runtime.bot_usernames[Channel.TELEGRAM] = "okoshko_bot"
    yield runtime
    runtime.reset()
    get_settings.cache_clear()


def test_links(tg_runtime):
    from app.channels import links

    assert [(x.title, x.url) for x in links.share_links("anna")] == [
        ("MAX", "https://max.ru/id123_bot?startapp=anna"),
        ("Сайт", "https://okoshko.website/anna"),
        ("Telegram", "https://t.me/okoshko_bot?startapp=anna"),
    ]
    b = links.client_app_button(Channel.TELEGRAM, "Выбрать время", "anna")
    assert b.web_app == "https://okoshko.website/app/?m=anna" and b.url is None
    b = links.client_app_button(Channel.MAX, "Выбрать время", "anna")
    assert b.url == "https://max.ru/id123_bot?startapp=anna"
    assert links.cabinet_button(Channel.TELEGRAM, "Кабинет").web_app == "https://okoshko.website/app/?cabinet"
    assert links.cabinet_button(Channel.MAX, "Кабинет").url == "https://max.ru/id123_bot?startapp=cabinet"


async def test_webhook_secret_and_dispatch(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.channels.telegram import webhook
    from app.config import Settings, get_settings
    from app.main import app

    got = []

    async def fake_process(channel, event):
        got.append((channel, event))

    monkeypatch.setattr(webhook, "process_event", fake_process)
    app.dependency_overrides[get_settings] = lambda: Settings(telegram_webhook_secret="s3cret")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            update = {"update_id": 1, "message": {"from": {"id": 5, "first_name": "Ира"},
                                                  "chat": {"id": 5, "type": "private"}, "text": "/start anna"}}
            r = await c.post("/telegram/webhook", json=update)
            assert r.status_code == 403
            r = await c.post("/telegram/webhook", json=update,
                             headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
            assert r.status_code == 403
            r = await c.post("/telegram/webhook", json=update,
                             headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
            assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert [(ch, e.kind, e.payload, e.user_id) for ch, e in got] == [(Channel.TELEGRAM, "start", "anna", "5")]
