from datetime import datetime, timedelta, timezone
from telethon import events, Button
from commands import background, command
from database import db
from logger import exception
from commands.utils import fmt, safe_edit, get_moscow_time, mention_markdown


async def _self_id(client, state):
    """id владельца единственного аккаунта — ключ, под которым хранятся
    муты и стоп-слова."""
    owner_id = getattr(state, "owner_id", None)
    if owner_id is None:
        me = await client.get_me()
        owner_id = me.id
        state.owner_id = owner_id
    return owner_id


def fmt_time(seconds):
    if seconds < 60:
        return f"{seconds}с"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}м"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}ч {m}м"
    d, h = divmod(h, 24)
    return f"{d}д {h}ч"


@command(r'^\.mute(?:\s+(\S+))?$', name="mute")
async def mute_handler(event, client, state):
    if not event.is_private:
        await safe_edit(event, fmt("ошибка", ["мут работает только в лс"]))
        return

    target = None
    if event.is_reply:
        reply = await event.get_reply_message()
        if reply:
            target = await reply.get_sender()
    else:
        target = await event.get_chat()

    if not target:
        await safe_edit(event, fmt("ошибка", ["не могу определить цель"]))
        return

    duration_arg = event.pattern_match.group(1) or "forever"

    if duration_arg == "forever":
        until = None
        delta = None
    else:
        delta = None
        if duration_arg.endswith("m"):
            delta = timedelta(minutes=int(duration_arg[:-1]))
        elif duration_arg.endswith("h"):
            delta = timedelta(hours=int(duration_arg[:-1]))
        elif duration_arg.endswith("d"):
            delta = timedelta(days=int(duration_arg[:-1]))
        else:
            await safe_edit(event, fmt("ошибка", ["формат: .mute / .mute 1h / .mute 1d"]))
            return
        until = (datetime.now(timezone.utc) + delta).isoformat()

    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO mutes
               (user_id, target_id, until, reason, shadow, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (event.sender_id, target.id, until, None, datetime.now(timezone.utc).isoformat()),
        )

    name = getattr(target, "first_name", None) or getattr(target, "username", None) or str(target.id)
    time_str = fmt_time(int(delta.total_seconds())) if until else "навсегда"

    try:
        await event.delete()
    except Exception:
        pass

    buttons = [[Button.inline("размутить", data=f"unmute:{target.id}".encode())]]

    try:
        await client.send_message(
            event.chat_id,
            fmt("замучен", [f"имя `{name}`", f"до `{time_str}`", f"id `{target.id}`"]),
            buttons=buttons,
            parse_mode="md",
        )
    except Exception:
        exception("mute_handler")


@command(r'^\.unmute$', name="unmute")
async def unmute_handler(event, client, state):
    if not event.is_private:
        await safe_edit(event, fmt("ошибка", ["только в лс"]))
        return
    target = await event.get_chat()
    if not target:
        return
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM mutes WHERE user_id = ? AND target_id = ?",
            (event.sender_id, target.id),
        )
    if cur.rowcount:
        try:
            await event.delete()
        except Exception:
            pass
        try:
            await client.send_message(event.chat_id, fmt("размучен"), parse_mode="md")
        except Exception:
            pass
    else:
        await safe_edit(event, fmt("ошибка", ["этот пользователь не в муте"]))


@command(r'^\.mutes$', name="mutes")
async def mutes_list_handler(event, client, state):
    with db() as conn:
        rows = conn.execute(
            "SELECT target_id, until FROM mutes WHERE user_id = ? ORDER BY created_at DESC LIMIT 30",
            (event.sender_id,),
        ).fetchall()
    if not rows:
        await safe_edit(event, fmt("никто не замучен"))
        return
    lines = []
    for row in rows:
        if row["until"]:
            dt = datetime.fromisoformat(row["until"])
            rem = dt - datetime.now(timezone.utc)
            secs = max(0, int(rem.total_seconds()))
            lines.append(f"`{row['target_id']}` — осталось `{fmt_time(secs)}`")
        else:
            lines.append(f"`{row['target_id']}` — навсегда")
    await safe_edit(event, fmt("замученные", lines))


@command(r'^\.muteword\s+(.+)$', name="muteword")
async def muteword_handler(event, client, state):
    word = event.pattern_match.group(1).strip().lower()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mute_words (user_id, word, created_at) VALUES (?, ?, ?)",
            (event.sender_id, word, datetime.now(timezone.utc).isoformat()),
        )
    await safe_edit(event, fmt("слово в муте", [f"`{word}`"]))


@command(r'^\.unmuteworlds$', name="unmuteworlds")
async def unmuteworlds_handler(event, client, state):
    """Снимает мут со всех стоп-слов (.muteword), заданных владельцем."""
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM mute_words WHERE user_id = ?",
            (event.sender_id,),
        )
    if cur.rowcount:
        await safe_edit(event, fmt("стоп-слова сняты", [f"удалено — `{cur.rowcount}`"]))
    else:
        await safe_edit(event, fmt("нет стоп-слов"))


@command(r'^\.tagall$', name="tagall")
async def tagall_handler(event, client, state):
    try:
        participants = await client.get_participants(event.chat_id)
        candidates = [p for p in participants if not getattr(p, "bot", False) and not getattr(p, "deleted", False)]
        if not candidates:
            await safe_edit(event, fmt("ошибка", ["нет участников"]))
            return

        # у юзернеймных — @username, у остальных — упоминание по id
        # (работает для ЛЮБОГО участника, юзернейм не нужен)
        mentions = [mention_markdown(p) for p in candidates]

        # Telegram ограничивает число entities в сообщении (~100) и длину —
        # берём упоминания целиком, не разрезая их посередине
        chunk, length = [], 0
        for m in mentions:
            if len(chunk) >= 90 or length + len(m) + 1 > 3500:
                break
            chunk.append(m)
            length += len(m) + 1

        await safe_edit(event, fmt("упоминание", [" ".join(chunk)]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


@command(r'^\.up\s+(\d+)\s*$', name="up")
async def up_handler(event, client, state):
    if not event.is_reply:
        return
    count = min(int(event.pattern_match.group(1)), 50)
    reply = await event.get_reply_message()
    user = await reply.get_sender()
    if not user:
        await safe_edit(event, fmt("ошибка", ["не нашёл отправителя сообщения"]))
        return

    # @username, если есть, иначе упоминание по id — юзернейм не обязателен
    mention = mention_markdown(user)

    for _ in range(count):
        msg = await client.send_message(event.chat_id, mention, parse_mode="md")
        await msg.delete()


@command(r'^\.iter(?:\s+(-n))?$', name="iter")
async def iter_handler(event, client, state):
    only_phone = event.pattern_match.group(1) == "-n"
    try:
        chat = await event.get_chat()
        chat_link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else "чат без ссылки"

        # у личных чатов (диалог 1-на-1) нет списка "участников" —
        # get_participants рассчитан только на группы/каналы и там либо
        # падает с ошибкой, либо отдаёт пусто, из-за чего казалось, что
        # команда "не видит" собеседника. В личке участник только один —
        # сам собеседник, берём его напрямую.
        if getattr(chat, "is_user", False) or event.is_private:
            participants = [chat]
        else:
            participants = await client.get_participants(event.chat_id)

        if not participants:
            await safe_edit(event, fmt("ошибка", ["нет участников"]))
            return
        path = f"members_{event.id}.txt"
        with open(path, "w", encoding="utf-8") as f:
            for p in participants:
                if not only_phone or p.phone:
                    f.write(f"{p.id} | @{p.username or 'нет'} | {p.phone or 'нет'}\n")
        caption = fmt("экспорт участников", [
            f"чат — {chat_link}",
            f"время — `{get_moscow_time()}`",
        ])
        await client.send_file("me", path, caption=caption, parse_mode="md")
        import os
        os.remove(path)
        await safe_edit(event, fmt("экспорт выполнен", ["сохранено в избранное"]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


@background
def register_mute(client, state):
    @client.on(events.CallbackQuery(pattern=r"^unmute:(\d+)$"))
    async def unmute_callback(event):
        target_id = int(event.pattern_match.group(1))
        with db() as conn:
            row = conn.execute(
                "SELECT user_id FROM mutes WHERE target_id = ?", (target_id,)
            ).fetchone()
        if not row:
            await event.answer("уже размучен", alert=False)
            try:
                await event.delete()
            except Exception:
                pass
            return
        if event.sender_id != row["user_id"]:
            await event.answer("кнопка не для тебя", alert=True)
            return
        with db() as conn:
            conn.execute(
                "DELETE FROM mutes WHERE user_id = ? AND target_id = ?",
                (row["user_id"], target_id),
            )
        await event.answer("размучен", alert=False)
        try:
            await event.edit(fmt("размучен"), parse_mode="md")
        except Exception:
            pass

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def shadow_mute_checker(event):
        if event.out:
            return
        try:
            owner_id = await _self_id(client, state)
            with db() as conn:
                rows = conn.execute(
                    "SELECT user_id, until FROM mutes WHERE target_id = ? AND user_id = ?",
                    (event.sender_id, owner_id),
                ).fetchall()
            if not rows:
                return
            now = datetime.now(timezone.utc)
            still_muted = False
            for row in rows:
                if row["until"]:
                    until = datetime.fromisoformat(row["until"])
                    if now > until:
                        with db() as conn:
                            conn.execute(
                                "DELETE FROM mutes WHERE user_id = ? AND target_id = ?",
                                (row["user_id"], event.sender_id),
                            )
                        continue
                still_muted = True
                break
            if still_muted:
                await event.delete()
        except Exception:
            exception("shadow_mute_checker")

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def mute_word_checker(event):
        if event.out or not event.text:
            return
        try:
            owner_id = await _self_id(client, state)
            text = event.text.lower()
            with db() as conn:
                rows = conn.execute(
                    "SELECT word FROM mute_words WHERE user_id = ?", (owner_id,)
                ).fetchall()
            for row in rows:
                if row["word"] in text:
                    await event.delete()
                    return
        except Exception:
            exception("mute_word_checker")
