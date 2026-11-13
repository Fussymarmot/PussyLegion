import asyncio
import io
import os

import pytz
from telethon import events
from telethon.tl.types import DocumentAttributeFilename
from commands import background, command
from commands.utils import fmt, safe_edit, get_moscow_time, get_usage_instructions
from config import CACHE_DIR, TEMP_DIR
from logger import exception

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import qrcode
except ImportError:
    qrcode = None


MESSAGE_CACHE = {}
MOSCOW = pytz.timezone("Europe/Moscow")
CACHE_PER_CHAT = 100

# Медиа из личек скачивается заранее: после удаления сообщения Telegram уже
# не отдаёт файл, поэтому спасти его можно только из локального кэша.
MEDIA_CACHE_DIR = CACHE_DIR / "deleted_media"
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_CACHE_LIMIT = 1024 * 1024 * 1024  # общий лимит на диске, 1 ГиБ
MAX_CACHED_FILE = 200 * 1024 * 1024     # один файл больше — не кэшируем


def _remove_media_file(entry):
    paths = {
        entry.get("media_path"),
        entry.get("media_path_hint"),
    }
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
    entry["media_path"] = None
    entry["media_size"] = 0


def _drop_entry(bucket, msg_id):
    entry = bucket.pop(msg_id, None)
    if not entry:
        return

    task = entry.get("media_task")
    if task and not task.done():
        hint = entry.get("media_path_hint")
        task.cancel()
        if hint:
            # загрузка могла успеть создать файл уже после очистки
            task.add_done_callback(lambda _t, path=hint: _remove_path(path))

    _remove_media_file(entry)


def _remove_path(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _cached_media_bytes():
    total = 0
    for messages in MESSAGE_CACHE.values():
        for entry in messages.values():
            total += entry.get("media_size") or 0
    return total


def _evict_for_space(needed):
    """Освобождает место, удаляя самые старые закэшированные файлы."""
    while _cached_media_bytes() + needed > MEDIA_CACHE_LIMIT:
        candidates = []
        for chat_id, messages in MESSAGE_CACHE.items():
            for msg_id, entry in messages.items():
                if entry.get("media_path"):
                    date = entry.get("date")
                    candidates.append((date.timestamp() if date else 0, chat_id, msg_id))
        if not candidates:
            return
        _, chat_id, msg_id = min(candidates)
        _drop_entry(MESSAGE_CACHE.get(chat_id, {}), msg_id)


def _purge_media_cache():
    """Чистит файлы, оставшиеся от прошлого запуска: кэш живёт в памяти."""
    try:
        for path in MEDIA_CACHE_DIR.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _media_cache_path(chat_id, msg):
    file_info = getattr(msg, "file", None)
    ext = ""
    if file_info is not None:
        ext = getattr(file_info, "ext", "") or ""
    if not ext.startswith(".") or len(ext) > 12:
        ext = ""
    return MEDIA_CACHE_DIR / f"{chat_id}_{msg.id}{ext}"


async def _cache_media(client, media, path):
    try:
        downloaded = await client.download_media(media, file=str(path))
    except Exception:
        return None
    if not downloaded:
        return None

    try:
        actual_size = os.path.getsize(downloaded)
    except OSError:
        return None

    if actual_size > MAX_CACHED_FILE:
        _remove_path(downloaded)
        return None

    # размер в метаданных мог быть неизвестен — проверяем бюджет ещё раз
    _evict_for_space(actual_size)
    if _cached_media_bytes() + actual_size > MEDIA_CACHE_LIMIT:
        _remove_path(downloaded)
        return None

    return {"media_path": str(downloaded), "media_size": actual_size}


async def _cache_media_into(client, media, path, entry):
    media_info = await _cache_media(client, media, path)
    if media_info:
        entry.update(media_info)


def _cached_entries(msg_id):
    """Все записи кэша с указанным id.

    UpdateDeleteMessages для личек и обычных групп не содержит peer:
    Telegram рассчитывает, что клиент сам найдёт чат по id сообщения
    (иды сообщений там уникальны). Поэтому при удалении ищем по всему
    кэшу, а не по event.chat_id — в личке он вообще None.
    """
    found = []
    for chat_id, messages in list(MESSAGE_CACHE.items()):
        entry = messages.get(msg_id)
        if entry is not None:
            found.append((chat_id, entry))
    return found


def compress_image(image_bytes):
    if not Image:
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        img.thumbnail((500, 500), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return image_bytes


def _get_ttl(obj):
    return getattr(obj, "ttl_seconds", None)


async def save_self_destruct_photo(client, event):
    try:
        if not event.is_private:
            return False
        ttl = _get_ttl(event) or _get_ttl(getattr(event, "media", None))
        if not ttl or ttl <= 0:
            return False
        if not hasattr(event.media, "photo"):
            return False

        data = await client.download_media(event.media, file=bytes)
        if not data:
            return False

        data = compress_image(data)

        sender = await event.get_sender()
        username = getattr(sender, "username", None) or getattr(sender, "first_name", "unknown")

        caption = (
            f"**✦ сохранено**\n\n"
            f"**✧** от — `{username}`\n"
            f"**✧** время — `{get_moscow_time()}`\n"
            f"**✧** id — `{event.id}`\n\n"
            f"**PussyLegion**"
        )

        await client.send_file(
            "me",
            data,
            caption=caption,
            force_document=False,
            attributes=[DocumentAttributeFilename(file_name=f"self_destruct_{event.id}.jpg")],
            parse_mode="md",
        )
        return True
    except Exception:
        exception("save_self_destruct_photo")
        return False


async def _report_deleted(client, chat_id, msg_id, cached):
    """Присылает в избранное отчёт об одном удалённом сообщении."""
    msg = None
    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
    except Exception:
        pass

    sender_name = cached.get("sender_name") or "unknown"
    sender_username = cached.get("sender_username") or ""
    date_str = get_moscow_time()

    if msg is not None:
        sender = None
        try:
            sender = await msg.get_sender()
        except Exception:
            pass
        if sender is not None:
            sender_name = getattr(sender, "first_name", None) or getattr(sender, "username", None) or sender_name
            sender_username = getattr(sender, "username", None) or sender_username
        if msg.date:
            date_str = msg.date.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M:%S")
    elif cached.get("date"):
        date_str = cached["date"].astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M:%S")

    caption = (
        f"**✦ удалённое сообщение**\n\n"
        f"**✧** от — `{sender_name}`"
        + (f" (@{sender_username})" if sender_username else "") + "\n"
        f"**✧** отправлено — `{date_str}`\n"
        f"**✧** удалено — `{get_moscow_time()}`\n"
    )

    text_content = ""
    if msg is not None and msg.text:
        text_content = f"\n**✧** текст — `{msg.text}`\n"
    elif cached.get("text"):
        text_content = f"\n**✧** текст — `{cached['text']}`\n"

    full_caption = caption + text_content + "\n**PussyLegion**"

    media_task = cached.get("media_task")
    if media_task is not None:
        try:
            await media_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    cached_path = cached.get("media_path")
    if cached_path and os.path.exists(cached_path):
        try:
            await client.send_file(
                "me",
                cached_path,
                caption=full_caption,
                force_document=False,
                parse_mode="md",
            )
            return
        except Exception:
            exception("_report_deleted.cached_media")

    if msg is not None and msg.media:
        media = await client.download_media(msg.media, file=str(TEMP_DIR))
        if media:
            try:
                await client.send_file("me", media, caption=full_caption, force_document=True, parse_mode="md")
            finally:
                try:
                    os.remove(media)
                except Exception:
                    pass
            return

    await client.send_message("me", full_caption, parse_mode="md")


async def save_deleted_message(client, event):
    try:
        chat_id = event.chat_id

        for msg_id in event.deleted_ids:
            try:
                if chat_id is not None:
                    # канал/супергруппа: peer известен, но фича — для личек
                    if not event.is_private:
                        continue
                    candidates = [(cid, entry) for cid, entry in _cached_entries(msg_id)
                                  if cid == chat_id]
                else:
                    # личка: chat_id в апдейте отсутствует, ищем по всему кэшу;
                    # в личном чате id чата совпадает с id собеседника
                    candidates = [(cid, entry) for cid, entry in _cached_entries(msg_id)
                                  if entry.get("sender_id") == cid]

                for cid, cached in candidates:
                    try:
                        await _report_deleted(client, cid, msg_id, cached)
                    finally:
                        # повторный апдейт по тому же id не должен дублировать
                        # отчёт; заодно убираем скачанный файл с диска
                        _drop_entry(MESSAGE_CACHE.get(cid, {}), msg_id)
            except Exception:
                continue
    except Exception:
        exception("save_deleted_message")


async def cache_message_handler(client, event):
    try:
        if not event.is_private or event.chat_id is None:
            return
        chat_id = event.chat_id

        sender = None
        try:
            sender = await event.get_sender()
        except Exception:
            pass

        bucket = MESSAGE_CACHE.setdefault(chat_id, {})
        if len(bucket) >= CACHE_PER_CHAT:
            _drop_entry(bucket, min(bucket))

        msg = event.message
        ttl = _get_ttl(event) or _get_ttl(msg) or _get_ttl(getattr(msg, "media", None))
        entry = {
            "text": msg.text,
            "date": msg.date,
            "has_media": bool(msg.media),
            "sender_id": msg.sender_id,
            "sender_name": getattr(sender, "first_name", None) or getattr(sender, "username", None) or "",
            "sender_username": getattr(sender, "username", None) or "",
            # самоудаляющееся фото сохраняется сразу при получении
            "ttl": bool(ttl and ttl > 0),
        }
        bucket[msg.id] = entry

        if msg.media and not entry["ttl"]:
            file_info = getattr(msg, "file", None)
            size = (getattr(file_info, "size", 0) or 0) if file_info else 0
            if file_info is not None and size <= MAX_CACHED_FILE:
                _evict_for_space(size)
                path = _media_cache_path(chat_id, msg)
                # файл качается в фоне, чтобы не тормозить обработку апдейтов;
                # удаление дождётся задачи в _report_deleted
                entry["media_path_hint"] = str(path)
                entry["media_task"] = asyncio.create_task(
                    _cache_media_into(client, msg.media, path, entry)
                )
    except Exception:
        pass


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
        username = getattr(sender, "username", None) or getattr(sender, "first_name", "unknown")

        caption = (
            f"**✦ сохранено**\n\n"
            f"**✧** от — `{username}`\n"
            f"**✧** время — `{get_moscow_time()}`\n"
            f"**✧** id — `{reply.id}`\n\n"
            f"**PussyLegion**"
        )

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


@background
def register_media(client, state):
    _purge_media_cache()

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def auto_save_media(event):
        await cache_message_handler(client, event)
        await save_self_destruct_photo(client, event)

    @client.on(events.MessageDeleted())
    async def deleted_handler(event):
        await save_deleted_message(client, event)
