from app.bot.ui import Button
from app.channels.max.adapter import keyboard_to_attachments, mini_app_link, parse_update

USER = {"user_id": 42, "first_name": "Ольга", "last_name": "Иванова", "is_bot": False}


def test_bot_started_with_payload():
    e = parse_update({"update_type": "bot_started", "timestamp": 1, "chat_id": 7,
                      "user": USER, "payload": "anna_123"})
    assert (e.kind, e.user_id, e.name, e.payload) == ("start", "42", "Ольга Иванова", "anna_123")


def test_message_text_and_start_command():
    base = {"update_type": "message_created", "timestamp": 1, "message": {
        "sender": USER, "recipient": {"chat_id": 7, "chat_type": "dialog"},
        "body": {"mid": "m1", "text": "  Маникюр, 90 "}}}
    e = parse_update(base)
    assert (e.kind, e.text) == ("text", "Маникюр, 90")

    base["message"]["body"]["text"] = "/start anna_123"
    e = parse_update(base)
    assert (e.kind, e.payload) == ("start", "anna_123")


def test_group_chat_and_bots_ignored():
    msg = {"sender": USER, "recipient": {"chat_id": 7, "chat_type": "chat"}, "body": {"text": "x"}}
    assert parse_update({"update_type": "message_created", "message": msg}) is None
    msg = {"sender": {**USER, "is_bot": True}, "recipient": {}, "body": {"text": "x"}}
    assert parse_update({"update_type": "message_created", "message": msg}) is None


def test_callback():
    e = parse_update({"update_type": "message_callback", "timestamp": 1, "callback": {
        "timestamp": 1, "callback_id": "cb1", "payload": "m:today", "user": USER}})
    assert (e.kind, e.payload, e.callback_id, e.user_id) == ("callback", "m:today", "cb1", "42")


def test_garbage_and_unknown_updates():
    assert parse_update({"update_type": "bot_started"}) is None  # нет user
    assert parse_update({"update_type": "dialog_muted", "user": USER}) is None
    assert parse_update({}) is None


def test_keyboard_to_attachments():
    att = keyboard_to_attachments([
        [Button("Сегодня", payload="m:today"), Button("Сайт", url="https://okoshko.website")],
    ])
    assert att == [{"type": "inline_keyboard", "payload": {"buttons": [[
        {"type": "callback", "text": "Сегодня", "payload": "m:today"},
        {"type": "link", "text": "Сайт", "url": "https://okoshko.website"},
    ]]}}]
    assert keyboard_to_attachments(None) is None


def test_mini_app_link():
    assert mini_app_link("id123_bot", "anna_123") == "https://max.ru/id123_bot?startapp=anna_123"
