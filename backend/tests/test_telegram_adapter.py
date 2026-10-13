from app.bot.ui import Button
from app.channels.telegram.adapter import keyboard_to_markup, parse_update

USER = {"id": 42, "is_bot": False, "first_name": "Ольга", "last_name": "Иванова", "username": "olga"}
PRIVATE = {"id": 42, "type": "private"}


def msg(text, chat=PRIVATE, user=USER):
    return {"update_id": 1, "message": {"message_id": 5, "from": user, "chat": chat, "date": 1, "text": text}}


def test_text_and_start():
    e = parse_update(msg("  Маникюр, 90 "))
    assert (e.kind, e.user_id, e.name, e.text) == ("text", "42", "Ольга Иванова", "Маникюр, 90")
    e = parse_update(msg("/start anna_123"))
    assert (e.kind, e.payload) == ("start", "anna_123")
    e = parse_update(msg("/start"))
    assert (e.kind, e.payload) == ("start", "")
    e = parse_update(msg("/start@okoshko_bot anna_123"))
    assert (e.kind, e.payload) == ("start", "anna_123")
    assert parse_update(msg("/startup")).kind == "text"


def test_non_text_message_is_empty_text():
    u = msg("x")
    del u["message"]["text"]
    u["message"]["photo"] = [{"file_id": "f"}]
    e = parse_update(u)
    assert (e.kind, e.text) == ("text", "")


def test_groups_and_bots_ignored():
    assert parse_update(msg("привет", chat={"id": -100, "type": "supergroup"})) is None
    assert parse_update(msg("привет", user={**USER, "is_bot": True})) is None
    assert parse_update({"update_id": 1, "edited_message": {}}) is None


def test_callback():
    e = parse_update({"update_id": 2, "callback_query": {
        "id": "777", "from": USER, "data": "m:today", "chat_instance": "x",
        "message": {"message_id": 5, "chat": PRIVATE}}})
    assert (e.kind, e.payload, e.callback_id, e.user_id) == ("callback", "m:today", "777", "42")


def test_garbage():
    assert parse_update({"message": {"chat": PRIVATE}}) is None  # нет from
    assert parse_update({"callback_query": {"id": "1"}}) is None
    assert parse_update({}) is None


def test_keyboard_to_markup():
    kb = keyboard_to_markup([
        [Button("Сегодня", payload="m:today"), Button("Сайт", url="https://okoshko.website")],
        [Button("Кабинет", web_app="https://okoshko.website/app/?cabinet")],
    ])
    assert kb == {"inline_keyboard": [
        [{"text": "Сегодня", "callback_data": "m:today"},
         {"text": "Сайт", "url": "https://okoshko.website"}],
        [{"text": "Кабинет", "web_app": {"url": "https://okoshko.website/app/?cabinet"}}],
    ]}
    assert keyboard_to_markup(None) is None
