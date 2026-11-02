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


async def resolve_peer(client, chat_id=None, username=None):
    """Отдаёт input-peer для произвольного клиента.

    Нужно для команд, которые выполняет не тот аккаунт, что принял
    исходное событие (рассылка .vote/.like/.spam и т.п. ассистентам, а
    также .spam/.up в личке): у ассистента в его собственной сессии
    может не быть кэша этого чата/пользователя, поэтому raw numeric id
    ему не отдать напрямую — Telethon не резолвит id без access_hash.

    1. сначала пробуем локальный кэш клиента по chat_id (если уже
       состоит в чате, писал в него раньше, или это просто текущий
       чат исполнителя — отработает мгновенно, без сети);
    2. если есть юзернейм (паблик-чат или человек с открытым
       @username) — резолвим по нему, это работает для ЛЮБОГО
       аккаунта независимо от членства/переписки;
    3. иначе — нет доступа, сообщаем явно вместо тихого падения или
       неожиданного traceback.

    chat_id можно не передавать (None), если резолвить нужно только
    по ссылке/юзернейму — например, когда чат с сообщением не совпадает
    с чатом, откуда пришла команда.
    """
    if chat_id is not None:
        try:
            return await client.get_input_entity(chat_id)
        except ValueError:
            pass

    if username:
        return await client.get_entity(username)

    raise ValueError("нет доступа к чату — аккаунт не состоит в нём и нет публичной ссылки")


def username_of(entity):
    """Основной активный юзернейм сущности (чат/пользователь).

    У аккаунтов с несколькими юзернеймами (Telegram это разрешает) старое
    поле `.username` часто пустое, а реальный юзернейм лежит в `.usernames` —
    списке объектов с полями `username`/`active`. Без этого фолбэка команды
    вроде .spam/.up в личке с такими людьми всегда думали, что юзернейма нет.
    """
    username = getattr(entity, "username", None)
    if username:
        return username
    for u in getattr(entity, "usernames", None) or []:
        if getattr(u, "active", True):
            return getattr(u, "username", None)
    return None


def mention_markdown(user):
    """Markdown-упоминание пользователя: @username, если он есть, иначе
    упоминание по id — работает для ЛЮБОГО пользователя независимо от
    наличия юзернейма, пока у отправляющего аккаунта есть его сущность
    (access_hash), например он виден в участниках этого же чата."""
    username = username_of(user)
    if username:
        return f"@{username}"
    name = (getattr(user, "first_name", None) or "участник").strip() or "участник"
    return f"[{name}](tg://user?id={user.id})"


def fmt_uptime(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}с"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}м {s}с"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}ч {m}м"
    d, h = divmod(h, 24)
    return f"{d}д {h}ч"


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
        "description": "Клонирует пост из канала в избранное по ссылке.",
        "syntax": "`.clone` [url]",
        "example": "`.clone https://t.me/channel/123`",
    },
    "start": {
        "description": "Запускает аккаунт по номеру.",
        "syntax": "`.start` [id]",
        "example": "`.start 2`",
    },
    "stop": {
        "description": "Останавливает аккаунт по номеру.",
        "syntax": "`.stop` [id]",
        "example": "`.stop 2`",
    },
    "autostart": {
        "description": "Показывает или меняет автозапуск аккаунта при старте бота.",
        "syntax": "`.autostart` [id] [on/off]",
        "example": "`.autostart 2 on`",
    },
    "addacc": {
        "description": "Добавляет аккаунт: шлёт код на номер. Дальше `.code`, при 2FA — `.pass`.",
        "syntax": "`.addacc` [номер]",
        "example": "`.addacc +79123456789`",
    },
    "code": {
        "description": "Код из Telegram для добавляемого аккаунта. Цифры пиши через дефис.",
        "syntax": "`.code` [1-2-3-4-5]",
        "example": "`.code 1-2-3-4-5`",
    },
    "pass": {
        "description": "Пароль 2FA для добавляемого аккаунта.",
        "syntax": "`.pass` [пароль]",
        "example": "`.pass мойпароль`",
    },
    "delacc": {
        "description": "Удаляет аккаунт и его сессию после подтверждения.",
        "syntax": "`.delacc` [id] [код]",
        "example": "`.delacc 2 1234`",
    },
    "logs": {
        "description": "Присылает последние строки лога в избранное.",
        "syntax": "`.logs` [число строк]",
        "example": "`.logs 30`",
    },
    "like": {
        "description": "Ставит реакцию на сообщение со всех запущенных аккаунтов. Ответом на соо или по ссылке t.me.",
        "syntax": "`.like` [эмодзи] / `.like` [ссылка] [эмодзи]",
        "example": "`.like 🔥`",
    },
    "joinall": {
        "description": "Заводит все запущенные аккаунты в чат/канал: без аргумента — в текущий, или по ссылке.",
        "syntax": "`.joinall` [ссылка/юзернейм]",
        "example": "`.joinall https://t.me/channel`",
    },
    "vote": {
        "description": "Голосует в опросе со всех запущенных аккаунтов. Без номера — покажет варианты.",
        "syntax": "`.vote` [номер] / `.vote` [1,3] — для multiple-choice",
        "example": "`.vote 2`",
    },
    "qr": {
        "description": "Генерирует QR-код из текста/ссылки и присылает картинку в избранное.",
        "syntax": "`.qr` [текст]",
        "example": "`.qr https://example.com`",
    },
    "delme": {
        "description": "Удаляет всю переписку в текущем чате после подтверждения.",
        "syntax": "`.delme` [код]",
        "example": "`.delme 1234`",
    },
    "ani": {
        "description": "Включает/выключает анимацию набора текста — только в этом чате, в остальных не действует.",
        "syntax": "`.ani` [on/off]",
        "example": "`.ani on`",
    },
    "sti": {
        "description": "Отправляет указанное количество стикеров в ответ на стикер.",
        "syntax": "`.sti` [число]",
        "example": "`.sti 10`",
    },
    "tagall": {
        "description": "Упоминает всех участников чата через @username.",
        "syntax": "`.tagall`",
        "example": "`.tagall`",
    },
    "iter": {
        "description": "Экспортирует участников чата в файл и отправляет в избранное.",
        "syntax": "`.iter` [-n]",
        "example": "`.iter -n`",
    },
    "up": {
        "description": "Многократные упоминания пользователя с удалением.",
        "syntax": "`.up` [число]",
        "example": "`.up 5`",
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
    "role": {
        "description": "Показывает или меняет роль аккаунта: главный / ассистент / самостоятельный.",
        "syntax": "`.role` [id] [master/assistant/solo]",
        "example": "`.role 2 assistant`",
    },
    "accounts": {
        "description": "Список аккаунтов, их роли и состояние.",
        "syntax": "`.accounts`",
        "example": "`.accounts`",
    },
    "all": {
        "description": "Выполняет следующую команду на всех ассистентах.",
        "syntax": "`.all` [команда]",
        "example": "`.all spam 3 привет`",
    },
    "me": {
        "description": "Выполняет следующую команду на главном аккаунте.",
        "syntax": "`.me` [команда]",
        "example": "`.me delme`",
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


@command(r'^\.ping$', name="ping", scope="local")
async def ping_handler(event, client, state):
    start = time.time()
    await safe_edit(event, "**✦ pong**")
    delta = round((time.time() - start) * 1000)
    await safe_edit(event, fmt("pong", [f"{delta}ms"]))


@command(r'^\.ani(?:\s+(on|off))?$', name="ani", scope="local")
async def ani_handler(event, client, state):
    """Анимация печати запоминается ПО ЧАТУ — включил здесь, в остальных
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
