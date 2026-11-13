import io
from datetime import datetime, timezone

import pytz
from telethon import events
from telethon.tl.types import DocumentAttributeFilename
from commands import background, command
from commands.utils import fmt, safe_edit, get_moscow_time, get_usage_instructions
from config import CACHE_DIR, TEMP_DIR
from core.archive import archive
from database import db
from logger import exception

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import qrcode
except ImportError:
    qrcode = None


# лёгкий кэш метаданных: нужен, чтобы при удалении сообщения знать, из
# какого оно чата и что в нём было. Сами файлы живут в Telegram-архиве.
MESSAGE_CACHE = {}
MOSCOW = pytz.timezone("Europe/Moscow")
CACHE_PER_CHAT = 100
CACHE_TOTAL = 5000
MAX_MEMORY_FILE = 200 * 1024 * 1024

# файловый кэш старой версии — вычищаем, чтобы не занимал диск сервера
LEGACY_MEDIA_CACHE = CACHE_DIR / "deleted_media"


def _purge_legacy_media_cache():
    if not LEGACY_MEDIA_CACHE.exists():
        return
    for path in LEGACY_MEDIA_CACHE.iterdir():
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            pass
    try:
        LEGACY_MEDIA_CACHE.rmdir()
    except OSError:
        pass


# Лимиты Telegram для отправки фото (не документом)
_TG_MAX_SIDE = 2560
_TG_MAX_BYTES = 10 * 1024 * 1024  # 10 МБ


def compress_image(image_bytes, max_side=_TG_MAX_SIDE, quality=95):
    """Готовит фото к пересылке/архивации.

    Если фото уже вписывается в лимиты Telegram (≤2560 px по длинной
    стороне и ≤10 МБ), возвращаем оригинальные байты без изменений —
    никакого повторного кодирования, качество 1-в-1.
    Только если фото превышает хотя бы один лимит — ресайзим/жмём.
    """
    if not Image:
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        needs_resize = max(img.size) > max_side
        needs_repack = len(image_bytes) > _TG_MAX_BYTES

        if not needs_resize and not needs_repack:
            # оригинал в пределах лимитов — отдаём без изменений
            return image_bytes

        img = img.convert("RGB")
        if needs_resize:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return image_bytes


# ── глобальный режим архивации ────────────────────────────────────────────────
# Хранится в archive_meta под ключом "archive_mode".
# Значения: "on" (по умолчанию), "ttl" (только исчезашки), "off" (ничего).

_ARCHIVE_MODE_KEY = "archive_mode"
_ARCHIVE_MODE_DEFAULT = "on"
_ARCHIVE_MODE_LABELS = {
    "on":  "полная архивация",
    "ttl": "только исчезающие фото",
    "off": "архивация полностью отключена",
}


def _get_archive_mode() -> str:
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT value FROM archive_meta WHERE key = ?", (_ARCHIVE_MODE_KEY,)
            ).fetchone()
        return row["value"] if row else _ARCHIVE_MODE_DEFAULT
    except Exception:
        return _ARCHIVE_MODE_DEFAULT


def _set_archive_mode(mode: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO archive_meta (key, value) VALUES (?, ?)",
            (_ARCHIVE_MODE_KEY, mode),
        )


# ── управление списком чатов без архивации обычных сообщений ─────────────────

def _is_archive_skipped(chat_id: int) -> bool:
    """True, если для этого чата архив обычных сообщений отключён."""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT 1 FROM archive_skip WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _set_archive_skip(chat_id: int, skip: bool) -> None:
    with db() as conn:
        if skip:
            conn.execute(
                "INSERT OR IGNORE INTO archive_skip (chat_id, created_at) VALUES (?, ?)",
                (chat_id, datetime.now(timezone.utc).isoformat()),
            )
        else:
            conn.execute("DELETE FROM archive_skip WHERE chat_id = ?", (chat_id,))


def _list_archive_skipped() -> list:
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT chat_id, created_at FROM archive_skip ORDER BY created_at"
            ).fetchall()
        return [(r["chat_id"], r["created_at"]) for r in rows]
    except Exception:
        return []


def _get_ttl(obj):
    return getattr(obj, "ttl_seconds", None)


def _ttl_value(event, msg):
    """TTL исчезающего медиа; 0 — тоже исчезающее (view-once)."""
    for obj in (event, msg, getattr(msg, "media", None)):
        value = _get_ttl(obj)
        if value is not None:
            return value
    return None


def _is_disappearing(event):
    msg = getattr(event, "message", None)
    return _ttl_value(event, msg) is not None


def _sender_meta(entity):
    if entity is None:
        return "", ""
    name = (
        getattr(entity, "first_name", None)
        or getattr(entity, "title", None)
        or ""
    )
    username = getattr(entity, "username", None) or ""
    if not name:
        name = username
    return name, username


def _clean(value, limit=None):
    text = (value or "").replace("`", "'")
    if limit and len(text) > limit:
        text = text[:limit - 1] + "…"
    return text


def _remember(chat_id, msg_id, entry):
    bucket = MESSAGE_CACHE.setdefault(chat_id, {})
    while len(bucket) >= CACHE_PER_CHAT:
        bucket.pop(min(bucket), None)

    total = sum(len(messages) for messages in MESSAGE_CACHE.values())
    while total >= CACHE_TOTAL and MESSAGE_CACHE:
        oldest_chat = next(iter(MESSAGE_CACHE))
        oldest = MESSAGE_CACHE[oldest_chat]
        oldest.pop(min(oldest), None)
        total -= 1
        if not oldest:
            MESSAGE_CACHE.pop(oldest_chat, None)

    bucket = MESSAGE_CACHE.setdefault(chat_id, {})
    bucket[msg_id] = entry


def _drop_entry(chat_id, msg_id):
    bucket = MESSAGE_CACHE.get(chat_id)
    if not bucket:
        return
    bucket.pop(msg_id, None)
    if not bucket:
        MESSAGE_CACHE.pop(chat_id, None)


def _cached_entries(msg_id):
    found = []
    for chat_id, messages in list(MESSAGE_CACHE.items()):
        entry = messages.get(msg_id)
        if entry is not None:
            found.append((chat_id, entry))
    return found


def _entry_for(msg, ttl, name, username, archived_id):
    return {
        "text": msg.text,
        "date": msg.date,
        "has_media": bool(msg.media),
        "ttl": ttl is not None,
        "sender_id": msg.sender_id,
        "sender_name": name,
        "sender_username": username,
        "archived_id": archived_id,
    }


async def _topic_for(chat_id, entity=None):
    name, username = _sender_meta(entity)
    return await archive.user_topic(chat_id, name=name, username=username)


async def _download_data(client, msg):
    """Скачивает медиа в память — на диск сервера ничего не пишется."""
    file_info = getattr(msg, "file", None)
    size = (getattr(file_info, "size", 0) or 0) if file_info else 0
    if size > MAX_MEMORY_FILE:
        return None

    try:
        data = await client.download_media(msg.media, file=bytes)
    except Exception:
        exception("archive.download_media")
        return None
    if not data:
        return None

    if hasattr(msg.media, "photo"):
        data = compress_image(data)
    return data


def _upload_stream(msg, data):
    """Байты с именем файла: безымянный поток Telegram показывает как «unnamed».

    Имя с расширением .jpg заодно заставляет Telethon отправить фото именно
    фотографией, а не документом.
    """
    media = getattr(msg, "media", None)
    is_photo = hasattr(media, "photo")
    file_info = getattr(msg, "file", None)

    name = getattr(file_info, "name", None) if file_info else None
    if not name:
        ext = getattr(file_info, "ext", None) if file_info else None
        if not ext or not ext.startswith(".") or len(ext) > 8:
            ext = ".jpg" if is_photo else ".bin"
        name = f"archive_{msg.id}{ext}"

    stream = io.BytesIO(data)
    stream.name = name
    return stream


async def _send_media(client, topic_id, msg, data, caption, fallback=None):
    """Отправляет готовые байты в тему; при сбое — в запасное место."""
    upload = _upload_stream(msg, data)
    if topic_id is not None:
        try:
            sent = await archive.send_file(
                topic_id, upload, caption=caption, parse_mode="md",
                force_document=False,
            )
            if sent is not None:
                return sent
        except Exception:
            exception("archive.send_file")

    if fallback is not None:
        try:
            return await client.send_file(
                fallback, upload, caption=caption, parse_mode="md",
                force_document=False,
            )
        except Exception:
            exception("archive.send_file.fallback")
    return None


async def _download_and_send(client, topic_id, msg, caption, fallback=None):
    data = await _download_data(client, msg)
    if data is None:
        return None
    return await _send_media(client, topic_id, msg, data, caption, fallback=fallback)


async def archive_message(client, event):
    """Пересылает сообщение из лички в тему собеседника.

    Обычные сообщения уходят пересылкой — файл не скачивается на сервер.
    Исчезающие фото пересылать нельзя, поэтому они качаются в память.
    """
    try:
        msg = event.message
        if msg is None or getattr(msg, "action", None) is not None:
            return

        text = msg.raw_text or ""
        if event.out and text.startswith("."):
            return
        if not msg.media and not text:
            return

        chat_id = event.chat_id
        if chat_id is None:
            return

        ttl = _ttl_value(event, msg)
        disappearing = ttl is not None and bool(msg.media)

        # Глобальный режим архивации.
        _mode = _get_archive_mode()
        if _mode == "off":
            return                          # вообще ничего не архивируем
        if _mode == "ttl" and not disappearing:
            return                          # только исчезашки — обычное пропускаем

        # Если архивация обычных сообщений отключена для этого чата —
        # пропускаем всё, кроме исчезающих фото (они сохраняются всегда).
        if not disappearing and _is_archive_skipped(chat_id):
            return

        # Исчезающее медиа качаем до любых сетевых запросов: file reference
        # живёт недолго, а get_chat() и создание темы тратят это время.
        data = await _download_data(client, msg) if disappearing else None

        try:
            chat = await event.get_chat()
        except Exception:
            chat = None
        name, username = _sender_meta(chat)

        topic_id = await _topic_for(chat_id, chat)
        if topic_id is None and not disappearing:
            _remember(chat_id, msg.id, _entry_for(msg, ttl, name, username, None))
            return

        author = f"@{username}" if username else _clean(name) or str(chat_id)
        date_str = get_moscow_time()

        archived_id = None
        if disappearing:
            caption = (
                f"**✦ исчезающее фото**\n\n"
                f"**✧** от — `{_clean(author)}`\n"
                f"**✧** время — `{date_str}`\n"
                f"**✧** id — `{msg.id}`\n\n"
                f"**PussyLegion**"
            )
            if data is not None:
                sent = await _send_media(
                    client, topic_id, msg, data, caption,
                    fallback="me" if topic_id is None else None,
                )
                archived_id = getattr(sent, "id", None)
            else:
                # скачать не вышло — пробуем хотя бы переслать
                try:
                    input_chat = await event.get_input_chat()
                    if input_chat is not None and topic_id is not None:
                        archived_id = await archive.forward(
                            topic_id, input_chat, msg.id,
                        )
                except Exception:
                    archived_id = None
        else:
            try:
                input_chat = await event.get_input_chat()
                if input_chat is not None:
                    archived_id = await archive.forward(topic_id, input_chat, msg.id)
            except Exception:
                archived_id = None

            if archived_id is None and msg.media:
                caption = (
                    f"**✦ медиа**\n\n"
                    f"**✧** от — `{_clean(author)}`\n"
                    f"**✧** время — `{date_str}`\n"
                    f"**✧** id — `{msg.id}`\n\n"
                    f"**PussyLegion**"
                )
                sent = await _download_and_send(
                    client, topic_id, msg, caption,
                    fallback="me" if topic_id is None else None,
                )
                archived_id = getattr(sent, "id", None)

        _remember(chat_id, msg.id, _entry_for(msg, ttl, name, username, archived_id))
    except Exception:
        exception("archive_message")


async def _report_deleted(client, chat_id, msg_id, cached):
    """Пишет отчёт об удалении в тему человека, к которому относился чат."""
    sender_name = cached.get("sender_name") or "unknown"
    sender_username = cached.get("sender_username") or ""
    date = cached.get("date")
    date_str = (
        date.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M:%S")
        if date else "unknown"
    )

    rows = [
        f"**✧** от — `{_clean(sender_name)}`"
        + (f" (@{sender_username})" if sender_username else ""),
        f"**✧** отправлено — `{date_str}`",
        f"**✧** удалено — `{get_moscow_time()}`",
        f"**✧** id — `{msg_id}`",
    ]

    if cached.get("ttl"):
        rows.append("**✧** тип — `исчезающее фото в архиве`")
    elif cached.get("has_media"):
        rows.append("**✧** тип — `медиа в архиве`")

    if cached.get("text"):
        rows.append(f"**✧** текст — `{_clean(cached['text'], 3000)}`")
    elif not cached.get("has_media"):
        rows.append("**✧** текст — `удалён без содержимого`")

    caption = (
        "**✦ удалённое сообщение**\n\n"
        + "\n".join(rows)
        + "\n\n**PussyLegion**"
    )

    topic_id = await archive.user_topic(
        chat_id, name=sender_name, username=sender_username,
    )
    if topic_id is None:
        await client.send_message("me", caption, parse_mode="md")
        return

    archived_id = cached.get("archived_id")
    if archived_id:
        # ответ на архивную копию: если там было фото/видео, оно будет видно
        if await archive.reply(topic_id, archived_id, caption) is not None:
            return
    await archive.send_text(topic_id, caption, parse_mode="md")


async def save_deleted_message(client, event):
    try:
        chat_id = event.chat_id

        for msg_id in event.deleted_ids:
            try:
                if chat_id is not None:
                    if not event.is_private:
                        continue
                    candidates = [
                        (cid, entry) for cid, entry in _cached_entries(msg_id)
                        if cid == chat_id
                    ]
                else:
                    # личка: в апдейте нет peer, ищем по кэшу; в личном чате
                    # id чата совпадает с id собеседника
                    hits = _cached_entries(msg_id)
                    candidates = [
                        (cid, entry) for cid, entry in hits
                        if entry.get("sender_id") == cid
                    ]
                    if not candidates and len(hits) == 1:
                        candidates = hits

                for cid, cached in candidates:
                    try:
                        await _report_deleted(client, cid, msg_id, cached)
                    finally:
                        _drop_entry(cid, msg_id)
            except Exception:
                continue
    except Exception:
        exception("save_deleted_message")


@command(r'^\.save$', name="save")
async def manual_save(event, client, state):
    try:
        await event.delete()
    except Exception:
        pass

    if not event.is_reply:
        return

    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return

    try:
        data = await client.download_media(reply.media, file=bytes)
        if not data:
            return

        if hasattr(reply.media, "photo"):
            data = compress_image(data)

        sender = await reply.get_sender()
        name, username = _sender_meta(sender)
        username = username or name or "unknown"

        caption = (
            f"**✦ сохранено**\n\n"
            f"**✧** от — `{_clean(username)}`\n"
            f"**✧** время — `{get_moscow_time()}`\n"
            f"**✧** id — `{reply.id}`\n\n"
            f"**PussyLegion**"
        )

        topic_id = None
        if event.is_private and event.chat_id is not None:
            topic_id = await _topic_for(event.chat_id, sender)

        if topic_id is not None:
            await archive.send_file(
                topic_id, data,
                caption=caption,
                attributes=[DocumentAttributeFilename(file_name=f"saved_{reply.id}.jpg")],
                parse_mode="md",
            )
        else:
            await client.send_file(
                "me",
                data,
                caption=caption,
                force_document=False,
                attributes=[DocumentAttributeFilename(file_name=f"saved_{reply.id}.jpg")],
                parse_mode="md",
            )
    except Exception:
        exception("manual_save")


@command(r'^\.clone(?:\s+(.+))?$', name="clone")
async def clone_handler(event, client, state):
    post_link = event.pattern_match.group(1)
    if not post_link:
        await safe_edit(event, get_usage_instructions("clone"))
        return
    if not post_link.startswith("https://t.me/"):
        await safe_edit(event, fmt("ошибка", ["формат: `.clone https://t.me/канал/123`"]))
        return
    try:
        parts = post_link.split("/")
        channel = parts[-2]
        post_id = int(parts[-1])
        entity = await client.get_entity(channel)
        message = await client.get_messages(entity, ids=post_id)
        if message is None:
            await safe_edit(event, fmt("ошибка", ["пост не найден"]))
            return
        if message.media:
            await client.send_file(event.chat_id, message.media, caption=message.text or "")
        else:
            await client.send_message(event.chat_id, message.text or "пустой пост")
        await safe_edit(event, fmt("клонирование выполнено", ["отправлено в чат"]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


@command(r'^\.qr(?:\s+(.+))?$', name="qr")
async def qr_handler(event, client, state):
    text = event.pattern_match.group(1)
    if not text:
        await safe_edit(event, get_usage_instructions("qr"))
        return

    if not qrcode:
        await safe_edit(event, fmt("ошибка", [
            "библиотека `qrcode` не установлена в этом окружении",
            "поставь: `pip install qrcode[pil]` и перезапусти бота",
        ]))
        return

    # удаляем команду только после того, как QR реально готов и отправлен —
    # иначе при любой ошибке сообщение пропадает молча, без следа
    try:
        img = qrcode.make(text)
        path = TEMP_DIR / f"qr_{event.id}.png"
        img.save(str(path))
        await client.send_file(
            event.chat_id,
            str(path),
            caption=(
                f"**✦ qr готов**\n\n"
                f"**✧** текст — `{text[:60]}`\n\n"
                f"**PussyLegion**"
            ),
            parse_mode="md",
        )
        path.unlink(missing_ok=True)
    except Exception as e:
        exception("qr_handler")
        await safe_edit(event, fmt("ошибка", [f"не удалось создать QR — `{str(e)[:120]}`"]))
        return

    try:
        await event.delete()
    except Exception:
        pass


@command(r'^\.archive(?:\s+(on|ttl|off|status))?$', name="archive")
async def archive_mode_handler(event, client, state):
    """Глобальный переключатель архивации.

    .archive on     — полная архивация (по умолчанию)
    .archive ttl    — только исчезающие фото во всех чатах
    .archive off    — не архивировать вообще ничего
    .archive status — текущий режим
    """
    try:
        await event.delete()
    except Exception:
        pass

    action = (event.pattern_match.group(1) or "status").lower()
    current = _get_archive_mode()

    if action == "status":
        await _send_to_general_or_me(
            client,
            fmt("archive · статус", [
                f"режим — `{_ARCHIVE_MODE_LABELS.get(current, current)}`",
                "`.archive on` — полная архивация",
                "`.archive ttl` — только исчезающие фото",
                "`.archive off` — ничего не архивировать",
                f"время — `{get_moscow_time()}`",
            ]),
        )
        return

    if action not in _ARCHIVE_MODE_LABELS:
        await _send_to_general_or_me(
            client,
            fmt("ошибка", [f"неизвестный режим `{action}` — используй on / ttl / off"]),
        )
        return

    if action == current:
        await _send_to_general_or_me(
            client,
            fmt("archive", [
                f"уже установлен режим `{_ARCHIVE_MODE_LABELS[current]}`",
            ]),
        )
        return

    _set_archive_mode(action)
    await _send_to_general_or_me(
        client,
        fmt(f"archive · {action}", [
            f"режим изменён → `{_ARCHIVE_MODE_LABELS[action]}`",
            f"время — `{get_moscow_time()}`",
        ]),
    )


async def _send_to_general_or_me(client, text):
    """Отправляет текст в General архива (как .logs), либо в избранное."""
    if archive.ready and archive.main_topic_id:
        await archive.send_logs(text)
    else:
        await client.send_message("me", text, parse_mode="md")


@command(r'^\.noarchive(?:\s+(on|off|list))?$', name="noarchive")
async def noarchive_handler(event, client, state):
    """Управляет архивацией обычных сообщений для текущего чата.

    .noarchive on   — отключить архивацию обычных сообщений (исчезашки сохраняются)
    .noarchive off  — включить обратно
    .noarchive list — список чатов с отключённым архивом (в General)
    """
    try:
        await event.delete()
    except Exception:
        pass

    action = (event.pattern_match.group(1) or "").lower()

    if not action:
        await _send_to_general_or_me(
            client,
            fmt("noarchive", [
                "использование:",
                "`.noarchive on` — отключить архив обычных сообщений в этом чате",
                "`.noarchive off` — включить обратно",
                "`.noarchive list` — список чатов без архивации",
                "исчезающие фото сохраняются всегда",
            ]),
        )
        return

    # ── list ────────────────────────────────────────────────────────────────
    if action == "list":
        rows = _list_archive_skipped()
        if not rows:
            await _send_to_general_or_me(
                client,
                fmt("noarchive · список", ["список пуст — все чаты архивируются полностью"]),
            )
        else:
            items = [f"`{chat_id}` — с {ts[:10]}" for chat_id, ts in rows]
            await _send_to_general_or_me(
                client,
                fmt(f"noarchive · список ({len(rows)})", items),
            )
        return

    # ── on / off ─────────────────────────────────────────────────────────────
    if not event.is_private:
        await _send_to_general_or_me(
            client,
            fmt("ошибка", ["`.noarchive on/off` работает только в личных чатах"]),
        )
        return

    chat_id = event.chat_id
    currently_skipped = _is_archive_skipped(chat_id)

    try:
        chat = await event.get_chat()
        name = getattr(chat, "first_name", None) or getattr(chat, "title", None) or str(chat_id)
    except Exception:
        name = str(chat_id)

    if action == "on":
        if currently_skipped:
            await _send_to_general_or_me(
                client,
                fmt("noarchive", [
                    f"уже отключён для чата **{name}**",
                    f"id — `{chat_id}`",
                ]),
            )
            return
        _set_archive_skip(chat_id, True)
        await _send_to_general_or_me(
            client,
            fmt("noarchive · on", [
                f"обычные сообщения из чата **{name}** больше не архивируются",
                "исчезающие фото — по-прежнему сохраняются",
                f"id — `{chat_id}`",
                f"время — `{get_moscow_time()}`",
            ]),
        )

    else:  # off
        if not currently_skipped:
            await _send_to_general_or_me(
                client,
                fmt("noarchive", [
                    f"архив уже включён для чата **{name}**",
                    f"id — `{chat_id}`",
                ]),
            )
            return
        _set_archive_skip(chat_id, False)
        await _send_to_general_or_me(
            client,
            fmt("noarchive · off", [
                f"архивация чата **{name}** восстановлена",
                f"id — `{chat_id}`",
                f"время — `{get_moscow_time()}`",
            ]),
        )


@background
def register_media(client, state):
    _purge_legacy_media_cache()

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def archive_incoming(event):
        await archive_message(client, event)

    @client.on(events.NewMessage(
        outgoing=True,
        func=lambda e: e.is_private and _is_disappearing(e),
    ))
    async def archive_outgoing(event):
        await archive_message(client, event)

    @client.on(events.MessageDeleted())
    async def deleted_handler(event):
        await save_deleted_message(client, event)
