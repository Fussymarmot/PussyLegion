"""Архив в Telegram: форум-группа с темами.

При первом запуске бот сам создаёт мегагруппу с включённым форумом и сразу
убирает её в папку «Архив». Логи идут в закреплённую тему General, в
отдельных темах с эмодзи микрофона — переписка с каждым человеком:
сообщения, медиа, исчезающие фото и отчёты об удалении.

Тяжёлые файлы не оседают на диске сервера: обычные сообщения пересылаются
силами Telegram, а исчезающие фото скачиваются в память и сразу уходят в
тему человека.
"""

import asyncio
import logging
import random
import re
import threading
import time
from collections import deque

from telethon.errors import (
    ChannelForumMissingError,
    FloodWaitError,
    MessageIdInvalidError,
    TopicDeletedError,
)
from telethon.extensions import markdown
from telethon.tl.functions.channels import CreateChannelRequest, ToggleForumRequest
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    ForwardMessagesRequest,
    GetForumTopicsByIDRequest,
    GetForumTopicsRequest,
    GetStickerSetRequest,
    SendMessageRequest,
    UpdatePinnedForumTopicRequest,
)
from telethon.tl.types import (
    ForumTopicDeleted,
    InputReplyToMessage,
    InputStickerSetEmojiDefaultTopicIcons,
)

from config import ARCHIVE_ABOUT, ARCHIVE_TITLE
from database import db
from logger import _is_noise, log

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

GENERAL_TOPIC_ID = 1
TOPIC_TITLE_LIMIT = 120
TOPIC_EMOJI = "🎤"
ICON_COLORS = (0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F)
LOG_BATCH_SECONDS = 5
LOG_CHUNK_LIMIT = 3500
TOPIC_CHECK_TTL = 20  # секунд, сколько доверяем проверке «тема жива»


def _chunks(text, limit):
    """Режет текст по строкам на куски не длиннее limit."""
    chunk, size = [], 0
    for line in text.split("\n"):
        while len(line) > limit:
            if chunk:
                yield "\n".join(chunk)
                chunk, size = [], 0
            yield line[:limit]
            line = line[limit:]
        if chunk and size + len(line) + 1 > limit:
            yield "\n".join(chunk)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        yield "\n".join(chunk)


class _TopicLogHandler(logging.Handler):
    """Складывает строки лога в очередь архива, не блокируя event loop."""

    def __init__(self, archive):
        super().__init__()
        self.archive = archive

    def emit(self, record):
        try:
            if _is_noise(record.getMessage()):
                return
            self.archive.enqueue_log(self.format(record))
        except Exception:
            pass


class Archive:
    def __init__(self):
        self.client = None
        self.group = None
        self.group_id = None
        self.main_topic_id = None
        self._topics = {}
        self._titles = {}
        self._lock = asyncio.Lock()
        self._log_lock = threading.Lock()
        self._log_lines = deque(maxlen=2000)
        self._log_task = None
        self._handler = None
        self._started = False
        self._mic_emoji_id = None
        self._mic_checked = False
        self._moved = {}         # старый id удалённой темы -> id новой
        self._alive_until = {}   # topic_id -> monotonic-время, до которого тема считается живой

    @property
    def ready(self):
        return bool(self.client and self.group_id and self.group)

    # --- запуск ---

    async def start(self, client):
        if self._started:
            return
        self.client = client

        try:
            self._load_state()
        except Exception:
            log.exception("архив: не удалось прочитать состояние")

        try:
            await self._ensure_group()
        except Exception:
            log.exception("архив: не удалось подготовить группу")

        self._handler = _TopicLogHandler(self)
        self._handler.setLevel(logging.INFO)
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger("pussylegion").addHandler(self._handler)

        self._log_task = asyncio.create_task(self._log_worker())
        self._started = True

    async def stop(self):
        if self._handler is not None:
            logging.getLogger("pussylegion").removeHandler(self._handler)
            self._handler = None
        if self._log_task is not None:
            self._log_task.cancel()
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._log_task = None
        self._started = False

    # --- состояние в базе ---

    def _load_state(self):
        with db() as conn:
            meta = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM archive_meta")
            }
            topics = conn.execute(
                "SELECT user_id, topic_id, title FROM archive_topics"
            ).fetchall()

        self.group_id = int(meta["group_id"]) if meta.get("group_id") else None
        self.main_topic_id = int(meta["main_topic_id"]) if meta.get("main_topic_id") else None
        self._topics = {int(row["user_id"]): int(row["topic_id"]) for row in topics}
        self._titles = {
            int(row["user_id"]): row["title"] for row in topics if row["title"]
        }

    def _set_meta(self, key, value):
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO archive_meta (key, value) VALUES (?, ?)",
                (key, str(value)),
            )

    def _save_topic(self, user_id, topic_id, title):
        with db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO archive_topics
                   (user_id, topic_id, title, created_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (user_id, topic_id, title),
            )

    def _forget_topic(self, user_id):
        self._topics.pop(user_id, None)
        self._titles.pop(user_id, None)
        with db() as conn:
            conn.execute("DELETE FROM archive_topics WHERE user_id = ?", (user_id,))

    # --- группа и темы ---

    async def _group_input(self):
        return await self.client.get_input_entity(self.group or self.group_id)

    async def _ensure_group(self):
        if self.group_id:
            try:
                entity = await self.client.get_entity(self.group_id)
                if getattr(entity, "megagroup", False) or getattr(entity, "forum", False):
                    self.group = entity
                    self.group = await self._ensure_forum(entity)
                    await self._archive_group()
                    await self._ensure_main_topic()
                    return
                log.warning("архив: сохранённая группа больше не подходит, создаю новую")
            except Exception:
                log.warning("архив: сохранённая группа недоступна, создаю новую")

        await self._create_group()

    async def _ensure_forum(self, entity):
        if getattr(entity, "forum", False):
            return entity
        try:
            await self.client(ToggleForumRequest(
                channel=await self.client.get_input_entity(entity),
                enabled=True,
                tabs=False,
            ))
        except Exception:
            log.exception("архив: не удалось включить форум")
            return entity
        try:
            return await self.client.get_entity(entity.id)
        except Exception:
            return entity

    async def _create_group(self):
        try:
            updates = await self.client(CreateChannelRequest(
                title=ARCHIVE_TITLE,
                about=ARCHIVE_ABOUT,
                megagroup=True,
                forum=True,
            ))
        except Exception:
            updates = await self.client(CreateChannelRequest(
                title=ARCHIVE_TITLE,
                about=ARCHIVE_ABOUT,
                megagroup=True,
            ))

        chats = getattr(updates, "chats", None) or []
        if not chats:
            raise RuntimeError("Telegram не вернул созданную группу")

        entity = await self._ensure_forum(chats[0])

        self.group = entity
        self.group_id = entity.id
        self._topics.clear()
        self._titles.clear()
        with db() as conn:
            conn.execute("DELETE FROM archive_topics")
        self._set_meta("group_id", entity.id)

        self.main_topic_id = None
        self._set_meta("main_topic_id", "")
        await self._archive_group()
        await self._ensure_main_topic()
        log.info(f"архив: создана группа #{entity.id}")

    async def _archive_group(self):
        """Сразу убирает архивную группу в папку «Архив»."""
        if not self.group:
            return
        try:
            await self.client.edit_folder(self.group, 1)
        except Exception:
            log.warning("архив: не удалось отправить группу в архив")

    async def _ensure_main_topic(self):
        """Логи живут в General; тему закрепляем сверху списка тем."""
        if not self.group_id:
            return
        if self.main_topic_id != GENERAL_TOPIC_ID:
            self.main_topic_id = GENERAL_TOPIC_ID
            self._set_meta("main_topic_id", GENERAL_TOPIC_ID)
        await self._pin_topic(GENERAL_TOPIC_ID)

    async def _pin_topic(self, topic_id):
        try:
            await self.client(UpdatePinnedForumTopicRequest(
                peer=await self._group_input(),
                topic_id=topic_id,
                pinned=True,
            ))
        except Exception:
            log.warning("архив: не удалось закрепить тему General")

    async def _topic_icon_id(self):
        """id кастомного эмодзи-микрофона из стандартного набора тем."""
        if self._mic_checked:
            return self._mic_emoji_id
        self._mic_checked = True
        try:
            result = await self.client(GetStickerSetRequest(
                stickerset=InputStickerSetEmojiDefaultTopicIcons(),
                hash=0,
            ))
            for document in getattr(result, "documents", None) or []:
                for attribute in getattr(document, "attributes", None) or []:
                    if (
                        attribute.__class__.__name__ == "DocumentAttributeCustomEmoji"
                        and getattr(attribute, "alt", "") == TOPIC_EMOJI
                    ):
                        self._mic_emoji_id = document.id
                        return document.id
        except Exception:
            pass
        return None

    async def _create_topic(self, title, icon_index=0):
        title = (title or "").strip()[:TOPIC_TITLE_LIMIT] or "без имени"
        result = await self.client(CreateForumTopicRequest(
            peer=await self._group_input(),
            title=title,
            icon_color=ICON_COLORS[icon_index % len(ICON_COLORS)],
            icon_emoji_id=await self._topic_icon_id(),
            random_id=random.getrandbits(63),
        ))
        topic_id = self._topic_id_from_updates(result)
        if not topic_id:
            topic_id = await self._find_topic(title)
        if not topic_id:
            raise RuntimeError("не удалось определить id созданной темы")
        return topic_id

    @staticmethod
    def _topic_id_from_updates(updates):
        for update in getattr(updates, "updates", None) or []:
            message = getattr(update, "message", None)
            if message is None:
                continue
            action = getattr(message, "action", None)
            if action is not None and action.__class__.__name__ == "MessageActionTopicCreate":
                return message.id
        return None

    async def _find_topic(self, title):
        try:
            result = await self.client(GetForumTopicsRequest(
                peer=await self._group_input(),
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=100,
                q=title,
            ))
        except Exception:
            return None
        for topic in getattr(result, "topics", None) or []:
            if getattr(topic, "title", None) == title:
                return topic.id
        return None

    @staticmethod
    def topic_title(name=None, username=None, user_id=None):
        base = (name or "").strip()
        if username:
            base = f"{base} @{username}" if base else f"@{username}"
        if not base:
            base = str(user_id or "неизвестный")
        base = f"{TOPIC_EMOJI} {base}"
        if user_id is not None:
            suffix = f" · {user_id}"
            base = base[:TOPIC_TITLE_LIMIT - len(suffix)] + suffix
        return base[:TOPIC_TITLE_LIMIT]

    async def user_topic(self, user_id, name=None, username=None, title=None):
        """Возвращает id темы человека, создавая её при необходимости."""
        if not self.group_id:
            return None

        cached = self._topics.get(user_id)
        if cached:
            return cached

        title = title or self.topic_title(name=name, username=username, user_id=user_id)

        async with self._lock:
            cached = self._topics.get(user_id)
            if cached:
                return cached
            for attempt in range(2):
                try:
                    topic_id = await self._create_topic(
                        title, icon_index=abs(int(user_id)) % len(ICON_COLORS)
                    )
                    self._topics[user_id] = topic_id
                    self._titles[user_id] = title
                    self._save_topic(user_id, topic_id, title)
                    return topic_id
                except FloodWaitError as e:
                    if attempt:
                        break
                    await asyncio.sleep(min(int(getattr(e, "seconds", 30) or 30), 60))
                except Exception:
                    log.exception("архив: не удалось создать тему")
                    break
        return None

    def _user_of_topic(self, topic_id):
        for user_id, value in self._topics.items():
            if value == topic_id:
                return user_id
        return None

    # --- отправка ---

    def _resolve_topic(self, topic_id):
        """Если тему пересоздавали, возвращает id актуальной."""
        seen = set()
        while topic_id in self._moved and topic_id not in seen:
            seen.add(topic_id)
            topic_id = self._moved[topic_id]
        return topic_id

    async def _topic_exists(self, topic_id):
        """Проверяет, что тема не удалена. При сомнении считает, что жива."""
        if topic_id == GENERAL_TOPIC_ID:
            return True
        if self._alive_until.get(topic_id, 0) > time.monotonic():
            return True
        try:
            result = await self.client(GetForumTopicsByIDRequest(
                peer=await self._group_input(),
                topics=[topic_id],
            ))
        except FloodWaitError:
            return True
        except Exception:
            return True
        topics = getattr(result, "topics", None) or []
        if not topics or isinstance(topics[0], ForumTopicDeleted):
            self._alive_until.pop(topic_id, None)
            return False
        self._alive_until[topic_id] = time.monotonic() + TOPIC_CHECK_TTL
        return True

    @staticmethod
    def _messages_of(result):
        """Достаёт объекты сообщений из ответа send_message/send_file/Updates."""
        if result is None or isinstance(result, int):
            return []
        if isinstance(result, (list, tuple)):
            out = []
            for item in result:
                out.extend(Archive._messages_of(item))
            return out
        if hasattr(result, "updates"):
            return [
                m for m in (getattr(u, "message", None) for u in result.updates or [])
                if m is not None and hasattr(m, "id")
            ]
        if hasattr(result, "id") and hasattr(result, "peer_id"):
            return [result]
        return []

    @staticmethod
    def _landed_in_general(messages, topic_id):
        """True, если сообщение должно было уйти в тему, а оказалось в General."""
        if topic_id == GENERAL_TOPIC_ID:
            return False
        for message in messages:
            # в теме у сообщения всегда есть reply_to; без него — это General
            if getattr(message, "reply_to", None) is None:
                return True
        return False

    async def _drop_messages(self, messages):
        try:
            await self.client.delete_messages(self.group, [m.id for m in messages])
        except Exception:
            pass

    async def _topic_call(self, topic_id, func):
        topic_id = self._resolve_topic(topic_id)
        for _ in range(4):
            if topic_id is None:
                return None

            if not await self._topic_exists(topic_id):
                topic_id = await self._recreate_topic(topic_id)
                continue

            try:
                result = await func(topic_id)
            except FloodWaitError as e:
                await asyncio.sleep(min(int(getattr(e, "seconds", 5) or 5), 60))
                continue
            except TopicDeletedError:
                self._alive_until.pop(topic_id, None)
                topic_id = await self._recreate_topic(topic_id)
                continue
            except MessageIdInvalidError:
                # тема могла быть удалена вместе с сообщением, на которое отвечали
                if await self._topic_exists(topic_id):
                    raise
                topic_id = await self._recreate_topic(topic_id)
                continue
            except ChannelForumMissingError:
                if self.group is not None:
                    self.group = await self._ensure_forum(self.group)
                continue

            # Telegram не всегда ругается на удалённую тему — иногда он молча
            # кладёт сообщение в General. Ловим это по заголовку ответа.
            messages = self._messages_of(result)
            if self._landed_in_general(messages, topic_id):
                await self._drop_messages(messages)
                self._alive_until.pop(topic_id, None)
                if await self._topic_exists(topic_id):
                    return result
                topic_id = await self._recreate_topic(topic_id)
                continue
            return result
        return None

    async def _recreate_topic(self, topic_id):
        if topic_id == self.main_topic_id:
            # General у форума есть всегда, пересоздавать нечего
            return GENERAL_TOPIC_ID

        user_id = self._user_of_topic(topic_id)
        if user_id is None:
            return None
        title = self._titles.get(user_id)
        self._forget_topic(user_id)
        self._alive_until.pop(topic_id, None)
        new_id = await self.user_topic(user_id, title=title)
        if new_id:
            self._moved[topic_id] = new_id
            log.info(f"архив: тема {topic_id} удалена, создана заново ({new_id})")
        return new_id

    @staticmethod
    def _reply_target(topic_id):
        """В General сообщения уходят без reply_to: тема = отсутствие темы."""
        return None if topic_id == GENERAL_TOPIC_ID else topic_id

    async def send_text(self, topic_id, text, **kwargs):
        if not topic_id or not self.group:
            return None

        def call(tid):
            extra = dict(kwargs)
            reply_to = self._reply_target(tid)
            if reply_to is not None:
                extra["reply_to"] = reply_to
            return self.client.send_message(self.group, text, **extra)

        return await self._topic_call(topic_id, call)

    async def send_file(self, topic_id, file, **kwargs):
        if not topic_id or not self.group:
            return None

        def call(tid):
            extra = dict(kwargs)
            reply_to = self._reply_target(tid)
            if reply_to is not None:
                extra["reply_to"] = reply_to
            return self.client.send_file(self.group, file, **extra)

        return await self._topic_call(topic_id, call)

    async def reply(self, topic_id, message_id, text):
        """Отвечает на архивную копию сообщения внутри темы."""
        if not topic_id or not message_id or not self.group:
            return None

        parsed, entities = markdown.parse(text)

        async def call(tid):
            top = self._reply_target(tid)
            return await self.client(SendMessageRequest(
                peer=await self._group_input(),
                message=parsed,
                entities=entities,
                reply_to=InputReplyToMessage(
                    reply_to_msg_id=message_id,
                    top_msg_id=top,
                ),
                random_id=random.getrandbits(63),
            ))

        try:
            return await self._topic_call(topic_id, call)
        except Exception:
            return None

    async def forward(self, topic_id, from_peer, message_id):
        """Пересылает сообщение в тему, не скачивая файл на диск."""
        if not topic_id or not self.group:
            return None

        async def call(tid):
            result = await self.client(ForwardMessagesRequest(
                from_peer=from_peer,
                id=[message_id],
                to_peer=await self._group_input(),
                random_id=[random.getrandbits(63)],
                top_msg_id=self._reply_target(tid),
            ))
            return result

        result = await self._topic_call(topic_id, call)
        messages = self._messages_of(result)
        return messages[0].id if messages else None

    async def send_logs(self, text):
        """Отправляет готовый текст в главную тему логов."""
        if not self.main_topic_id or not self.group:
            return False
        text = ANSI_RE.sub("", text)
        for chunk in _chunks(text, LOG_CHUNK_LIMIT):
            await self.send_text(self.main_topic_id, chunk, parse_mode="md")
        return True

    # --- логи ---

    def enqueue_log(self, line):
        with self._log_lock:
            self._log_lines.append(line)

    async def _log_worker(self):
        while True:
            try:
                await asyncio.sleep(LOG_BATCH_SECONDS)
                if not self.main_topic_id or not self.group:
                    continue
                with self._log_lock:
                    if not self._log_lines:
                        continue
                    lines = list(self._log_lines)
                    self._log_lines.clear()

                body = ANSI_RE.sub("", "\n".join(lines)).strip()
                if not body:
                    continue
                for chunk in _chunks(body, LOG_CHUNK_LIMIT):
                    await self.send_text(
                        self.main_topic_id,
                        f"```\n{chunk}\n```",
                        parse_mode="md",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # логи не должны ронять воркер и порождать новые логи
                pass


archive = Archive()
