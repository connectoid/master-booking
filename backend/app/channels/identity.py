from dataclasses import dataclass

from app.models import Channel


@dataclass(frozen=True)
class ChannelUser:
    """Пользователь мессенджера, подтверждённый подписью initData."""

    channel: Channel
    external_user_id: str
    display_name: str
    start_param: str | None = None
