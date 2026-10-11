import datetime as dt
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.channels.identity import ChannelUser
from app.channels.webapp_auth import InitDataError, verify_init_data
from app.config import Settings, get_settings
from app.models import Channel


def _bot_token(channel: Channel, settings: Settings) -> str:
    return {
        Channel.MAX: settings.max_bot_token,
        Channel.TELEGRAM: settings.telegram_bot_token,
    }.get(channel, "")


async def get_channel_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_channel: Annotated[str | None, Header()] = None,
) -> ChannelUser:
    """Клиент из Mini App.

    Заголовки: `X-Channel: max|telegram` и `Authorization: tma <initData>`.
    """
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход через мессенджер")
    if not authorization or not x_channel:
        raise unauthorized
    scheme, _, init_data = authorization.partition(" ")
    if scheme.lower() != "tma" or not init_data:
        raise unauthorized
    try:
        channel = Channel(x_channel.lower())
    except ValueError:
        raise unauthorized from None

    token = _bot_token(channel, settings)
    try:
        hours = settings.initdata_max_age_hours
        max_age = dt.timedelta(hours=hours) if hours > 0 else None
        data = verify_init_data(init_data, token, max_age=max_age)
    except InitDataError:
        raise unauthorized from None
    if data.user is None:
        raise unauthorized

    name = " ".join(p for p in (data.user.first_name, data.user.last_name) if p).strip()
    return ChannelUser(
        channel=channel,
        external_user_id=str(data.user.id),
        display_name=name or (data.user.username or ""),
        start_param=data.start_param,
    )


CurrentUser = Annotated[ChannelUser, Depends(get_channel_user)]
