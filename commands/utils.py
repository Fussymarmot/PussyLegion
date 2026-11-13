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
        "description": "Сохраняет самоудаляющееся фото из личного чата в тему этого человека в архиве.",
        "syntax": "`.save` (ответом на фото)",
        "example": "`.save`",
    },
    "clone": {
        "description": "Клонирует пост из канала в текущий чат по ссылке.",
        "syntax": "`.clone` [url]",
        "example": "`.clone https://t.me/channel/123`",
    },
    "video": {
        "description": "Скачивает видео с YouTube или TikTok, отправляет в этот чат и удаляет файл с устройства. Ссылку можно дать ответом на сообщение.",
        "syntax": "`.video` [ссылка]",
        "example": "`.video https://youtu.be/dQw4w9WgXcQ`",
    },
    "logs": {
        "description": "Присылает последние строки лога в закреплённую тему General архивной группы.",
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
        "description": "Без ответа — инфо о текущем чате/канале (в лс — о собеседнике): id, тип, название/имя, юзернейм, участники, описание. Ответом на сообщение — инфо об авторе: пользователь (id, имя, телефон, статус, метки, био) или канал, если пост отправлен от его имени.",
        "syntax": "`.data` [ответом на сообщение]",
        "example": "`.data`",
    },
    "bday": {
        "description": "Показывает дату рождения, сколько дней осталось до следующего дня рождения и сколько прошло с прошлого. Без ответа — твоя дата, ответом на сообщение — дата собеседника (если она открыта в его профиле).",
        "syntax": "`.bday` (можно ответом на сообщение)",
        "example": "`.bday`",
    },
    "osint": {
        "description": "Проверка данных по IP / номеру / почте (holehe). Флаг `-r` для почты — повторный прогон, подтягивает домены, упавшие с ошибкой в прошлый раз.",
        "syntax": "`.osint` [значение] [-r]",
        "example": "`.osint person@mail.com -r`",
    },
    "sherlock": {
        "description": "Поиск аккаунта по юзернейму на множестве площадок (sherlock-project).",
        "syntax": "`.sherlock` [юзернейм]",
        "example": "`.sherlock johndoe`",
    },
    "ignorant": {
        "description": "Проверка, на каких сервисах привязан номер телефона.",
        "syntax": "`.ignorant` [код_страны] [номер]",
        "example": "`.ignorant 7 9123456789`",
    },
    "mosint": {
        "description": "Отчёт по почте: утечки, соцсети и т.п. (mosint).",
        "syntax": "`.mosint` [почта]",
        "example": "`.mosint person@mail.com`",
    },
    "h8mail": {
        "description": "Поиск почты в брич-базах (h8mail).",
        "syntax": "`.h8mail` [почта]",
        "example": "`.h8mail person@mail.com`",
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
    "noarchive": {
        "description": "Управляет архивацией обычных сообщений для текущего чата. `on` — отключить (исчезашки всегда сохраняются), `off` — включить обратно, `list` — список чатов без архивации. Результат отправляется в General архива.",
        "syntax": "`.noarchive` [on/off/list]",
        "example": "`.noarchive on`",
    },
    "archive": {
        "description": "Глобальный переключатель архивации для всех чатов. `on` — полная архивация, `ttl` — только исчезающие фото, `off` — ничего не архивировать, `status` — текущий режим.",
        "syntax": "`.archive` [on/ttl/off/status]",
        "example": "`.archive ttl`",
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


# Telegram: до 4096 символов и ~100 entities (жирный, код, ссылки) на сообщение.
# Берём запас — разметка в тексте длиннее, чем то, что считает Telegram.
MAX_MSG_LEN = 4000
MAX_MSG_ENTITIES = 90


def _count_entities(text):
    """Грубая оценка числа entities в markdown-тексте."""
    return text.count("**") // 2 + text.count("`") // 2 + text.count("](")


def split_message(text, limit=MAX_MSG_LEN, max_entities=MAX_MSG_ENTITIES):
    """Режет длинный markdown-текст на части по границам строк.

    Не разрывает блоки ``` посередине: если часть закончилась внутри блока
    кода, он закрывается, а в следующей части открывается заново.
    """
    if len(text) <= limit and _count_entities(text) <= max_entities:
        return [text]

    piece_len = limit - 20  # запас под закрывающие ``` и перенос строки
    chunks, cur, cur_len, cur_ent, in_fence = [], [], 0, 0, False

    def flush():
        nonlocal cur, cur_len, cur_ent
        if in_fence:
            cur.append("```")
        chunks.append("\n".join(cur))
        cur = ["```"] if in_fence else []
        cur_len = sum(len(x) + 1 for x in cur)
        cur_ent = sum(_count_entities(x) for x in cur)

    for line in text.split("\n"):
        # одну слишком длинную строку режем жёстко
        pieces = [line[i:i + piece_len] for i in range(0, len(line), piece_len)] or [""]
        for piece in pieces:
            ent = _count_entities(piece)
            if cur and (cur_len + len(piece) + 1 > piece_len or cur_ent + ent > max_entities):
                flush()
            cur.append(piece)
            cur_len += len(piece) + 1
            cur_ent += ent
            if piece.count("```") % 2:
                in_fence = not in_fence
    if cur:
        chunks.append("\n".join(cur))
    return [c.strip("\n") for c in chunks if c.strip()]


async def safe_edit(event, text, parse_mode="md", **kwargs):
    """Правит сообщение с командой. Если текст не влезает в одно сообщение —
    первая часть идёт в правку, остальные отправляются следом новыми."""
    try:
        parts = split_message(text) if parse_mode == "md" else [text]
        result = await event.edit(parts[0], parse_mode=parse_mode, **kwargs)
        if len(parts) > 1:
            client = getattr(event, "client", None)
            for part in parts[1:]:
                await asyncio.sleep(0.5)  # не ловить флуд-лимит
                await client.send_message(event.chat_id, part, parse_mode=parse_mode)
        return result
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
