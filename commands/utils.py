import asyncio
import time
from datetime import datetime

import pytz
from telethon import events
from telethon.errors import MessageNotModifiedError

from commands import background, command
from logger import exception


def fmt(title, rows=None):
    lines = [f"**✦ {title}**"]
    if rows:
        lines.append("")
        for row in rows:
            lines.append(f"**✧** {row}")
    lines.append("")
    lines.append("**PussyLegion**")
    return "\n".join(lines)


def username_of(entity):
    """Основной активный юзернейм сущности (чат/пользователь)."""
    username = getattr(entity, "username", None)
    if username:
        return username
    for u in getattr(entity, "usernames", None) or []:
        if getattr(u, "active", True):
            return getattr(u, "username", None)
    return None


def mention_markdown(user):
    """Markdown-упоминание: @username, если он есть, иначе по id."""
    username = username_of(user)
    if username:
        return f"@{username}"
    name = (getattr(user, "first_name", None) or "участник").strip() or "участник"
    return f"[{name}](tg://user?id={user.id})"


def get_moscow_time():
    return datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d %H:%M:%S")


COMMAND_INFO = {
    "help": {
        "description": "Показывает список всех команд или справку по конкретной.",
        "syntax": "`.help` [команда]",
        "example": "`.help save`",
    },
    "save": {
        "description": "Сохраняет самоудаляющееся фото из личного чата в избранное.",
        "syntax": "`.save` (ответом на фото)",
        "example": "`.save`",
    },
    "clone": {
        "description": "Клонирует пост из канала в текущий чат по ссылке.",
        "syntax": "`.clone` [url]",
        "example": "`.clone https://t.me/channel/123`",
    },
    "logs": {
        "description": "Присылает последние строки лога в избранное.",
        "syntax": "`.logs` [число строк]",
        "example": "`.logs 30`",
    },
    "qr": {
        "description": "Генерирует QR-код из текста/ссылки и присылает картинку.",
        "syntax": "`.qr` [текст]",
        "example": "`.qr https://example.com`",
    },
    "ani": {
        "description": "Включает/выключает анимацию набора текста в этом чате.",
        "syntax": "`.ani` [on/off]",
        "example": "`.ani on`",
    },
    "tagall": {
        "description": "Упоминает всех участников чата.",
        "syntax": "`.tagall`",
        "example": "`.tagall`",
    },
    "up": {
        "description": "Многократные упоминания пользователя с удалением.",
        "syntax": "`.up` [число] (ответом на сообщение)",
        "example": "`.up 5`",
    },
    "iter": {
        "description": "Экспортирует участников чата в файл и отправляет в избранное.",
        "syntax": "`.iter` [-n]",
        "example": "`.iter -n`",
    },
    "data": {
        "description": "Информация о пользователе.",
        "syntax": "`.data` (ответом на сообщение)",
        "example": "`.data`",
    },
    "osint": {
        "description": "Проверка данных по IP / номеру / почте.",
        "syntax": "`.osint` [значение]",
        "example": "`.osint 8.8.8.8`",
    },
    "whois": {
        "description": "Информация о домене.",
        "syntax": "`.whois` [домен]",
        "example": "`.whois google.com`",
    },
    "spam": {
        "description": "Отправляет указанное количество одинаковых сообщений.",
        "syntax": "`.spam` [количество] [текст]",
        "example": "`.spam 10 Привет`",
    },
    "sti": {
        "description": "Отправляет указанное количество стикеров в ответ на стикер.",
        "syntax": "`.sti` [число]",
        "example": "`.sti 10`",
    },
    "crash": {
        "description": "Отправляет стикеры, нагружающие телефон.",
        "syntax": "`.crash`",
        "example": "`.crash`",
    },
    "bomb": {
        "description": "Спам-атака на номер телефона (5 минут).",
        "syntax": "`.bomb` [номер]",
        "example": "`.bomb +79123456789`",
    },
    "bot": {
        "description": "Информация о боте.",
        "syntax": "`.bot`",
        "example": "`.bot`",
    },
    "ping": {
        "description": "Проверка отклика.",
        "syntax": "`.ping`",
        "example": "`.ping`",
    },
    "mute": {
        "description": "Замутить пользователя в личных сообщениях.",
        "syntax": "`.mute` [срок] (ответом или в лс)",
        "example": "`.mute 1h`",
    },
    "unmute": {
        "description": "Снимает мут с текущего собеседника.",
        "syntax": "`.unmute`",
        "example": "`.unmute`",
    },
    "mutes": {
        "description": "Список замученных.",
        "syntax": "`.mutes`",
        "example": "`.mutes`",
    },
    "muteword": {
        "description": "Удаляет сообщения с указанным словом в личных чатах.",
        "syntax": "`.muteword` [слово]",
        "example": "`.muteword реклама`",
    },
    "unmuteworlds": {
        "description": "Снимает мут со всех стоп-слов (.muteword) сразу.",
        "syntax": "`.unmuteworlds`",
        "example": "`.unmuteworlds`",
    },
    "random": {
        "description": "Случайное число в диапазоне.",
        "syntax": "`.random` [от]-[до]",
        "example": "`.random 1-100`",
    },
    "dice": {
        "description": "Бросает кубик.",
        "syntax": "`.dice`",
        "example": "`.dice`",
    },
}


def get_usage_instructions(name, status=None):
    info = COMMAND_INFO.get(name)
    if not info:
        return fmt("ошибка", [f"нет справки для `{name}`"])
    rows = []
    if status:
        rows.append(f"статус — `{status}`")
    rows.append(f"описание — {info['description']}")
    rows.append(f"синтаксис — {info['syntax']}")
    rows.append(f"пример — {info['example']}")
    return fmt(name, rows)


async def safe_edit(event, text, parse_mode="md", **kwargs):
    try:
        return await event.edit(text, parse_mode=parse_mode, **kwargs)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        exception(f"safe_edit: {e}")


@command(r'^\.ping$', name="ping")
async def ping_handler(event, client, state):
    start = time.time()
    await safe_edit(event, "**✦ pong**")
    delta = round((time.time() - start) * 1000)
    await safe_edit(event, fmt("pong", [f"{delta}ms"]))


@command(r'^\.ani(?:\s+(on|off))?$', name="ani")
async def ani_handler(event, client, state):
    """Анимация печати запоминается по чату — включил здесь, в остальных
    чатах этого же аккаунта сообщения уходят как обычно, мгновенно."""
    state.last_user_activity = time.time()
    action = event.pattern_match.group(1)
    chat_id = event.chat_id
    currently_on = chat_id in state.animated_chats

    if not action:
        status = "включена" if currently_on else "выключена"
        await safe_edit(event, get_usage_instructions("ani", status=status))
        return

    new_state = action.lower() == "on"
    if new_state == currently_on:
        status = "включена" if currently_on else "выключена"
        await safe_edit(event, fmt("анимация уже " + status + " в этом чате"))
        return

    if new_state:
        state.animated_chats.add(chat_id)
    else:
        state.animated_chats.discard(chat_id)
        await state.stop_animation()

    status = "включена" if new_state else "выключена"
    await safe_edit(event, fmt(f"анимация {status} в этом чате"))


@background
def register_animation(client, state):
    @client.on(events.NewMessage(outgoing=True))
    async def animate_message(event):
        state.last_user_activity = time.time()
        if event.chat_id not in state.animated_chats or not event.text or event.text.startswith("."):
            return
        try:
            await state.stop_animation()
            current = ""
            await asyncio.sleep(0.1)
            for char in event.text:
                if event.chat_id not in state.animated_chats:
                    break
                current += char
                try:
                    await event.edit(current)
                    await asyncio.sleep(0.12)
                except MessageNotModifiedError:
                    continue
                except Exception:
                    break
        except Exception:
            exception("animate_message")
