"""Диспетчер команд для одного аккаунта.

Один слушатель на исходящие сообщения: если текст начинается с точки и
совпадает с зарегистрированной командой — выполняем её на этом же клиенте.
"""

import asyncio

from telethon import events
from telethon.errors import FloodWaitError

from commands import find_command
from commands.utils import fmt
from core.views import EventView
from logger import exception

REPORT_TTL = 20


async def _delete_later(message, seconds):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


def _short(exc):
    text = str(exc) or exc.__class__.__name__
    return text if len(text) <= 140 else text[:137] + "…"


async def notify(client, event, title, rows):
    """Показывает отчёт об ошибке: сначала правит сообщение с командой."""
    text = fmt(title, rows)
    try:
        await event.edit(text, parse_mode="md")
        return
    except Exception:
        pass
    try:
        msg = await client.send_message(event.chat_id, text, parse_mode="md")
        asyncio.create_task(_delete_later(msg, REPORT_TTL))
    except Exception:
        exception("dispatcher.notify")


def attach(client, state):
    """Вешает диспетчер команд на клиент."""

    @client.on(events.NewMessage(
        outgoing=True,
        func=lambda e: bool(e.raw_text) and e.raw_text.startswith("."),
    ))
    async def _dispatcher(event):
        text = (event.raw_text or "").strip()
        cmd, match = find_command(text)
        if not cmd:
            return

        view = EventView(event, text, match)
        try:
            await cmd.handler(view, client, state)
        except FloodWaitError as e:
            seconds = int(getattr(e, "seconds", 60) or 60)
            await notify(client, event, "флуд-вейт", [f"подожди `{seconds}с`"])
        except Exception as e:
            exception(f"команда .{cmd.name}")
            await notify(client, event, "команда не выполнена", [f"`{_short(e)}`"])

    return _dispatcher
