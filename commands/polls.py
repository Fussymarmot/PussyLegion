"""Голосование в опросах со всех запущенных аккаунтов."""

import asyncio
import re

from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import SendVoteRequest
from telethon.tl.types import MessageMediaPoll

from commands import command
from commands.utils import fmt, safe_edit, get_usage_instructions, resolve_peer, username_of

DELAY = 0.5


def _text(obj):
    """В новых слоях TL текст опроса/варианта — TextWithEntities, в старых — просто str."""
    return getattr(obj, "text", obj)


@command(r'^\.vote(?:\s+(\S+))?$', name="vote", scope="local")
async def vote_handler(event, client, state):
    """.vote — ответом на опрос без номера: показывает варианты и их номера.
    .vote 2 — голосует за вариант №2 со всех запущенных аккаунтов.
    .vote 1,3 — несколько вариантов (для multiple-choice опросов).
    """
    bot = state.bot
    arg = event.pattern_match.group(1)

    if not event.is_reply:
        await safe_edit(event, get_usage_instructions("vote"))
        return

    reply = await event.get_reply_message()
    if not reply or not isinstance(reply.media, MessageMediaPoll):
        await safe_edit(event, fmt("ошибка", ["ответь на сообщение с опросом"]))
        return

    poll = reply.media.poll
    answers = poll.answers

    if not arg:
        rows = [f"`{i + 1}` — {_text(a.text)}" for i, a in enumerate(answers)]
        if poll.multiple_choice:
            rows.append("")
            rows.append("несколько вариантов — через запятую: `.vote 1,3`")
        if poll.quiz:
            rows.append("")
            rows.append("это квиз — правильный ответ один")
        await safe_edit(event, fmt("варианты опроса", rows))
        return

    indices = []
    for part in re.split(r"[,\s]+", arg.strip()):
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(answers) and idx not in indices:
                indices.append(idx)

    if not indices:
        await safe_edit(event, fmt("ошибка", [f"укажи номер варианта от 1 до {len(answers)}: `.vote 2`"]))
        return

    if poll.closed:
        await safe_edit(event, fmt("ошибка", ["опрос уже завершён"]))
        return

    options = [answers[i - 1].option for i in indices]

    chat = await event.get_chat()
    username = username_of(chat)
    msg_id = reply.id

    await safe_edit(event, fmt("голосую", [f"вариант(ы) — `{', '.join(map(str, indices))}`"]))

    rows = []
    ok = 0
    for num, acc_client in list(bot.clients.items()):
        label = bot.router.label(num)
        try:
            peer = await resolve_peer(acc_client, event.chat_id, username)

            # даже у реального участника чата sendVote иногда отвечает
            # MESSAGE_ID_INVALID на свежее сообщение, если сессия этого
            # аккаунта ещё не синхронизировала его саму — явный get_messages
            # догоняет состояние перед голосованием и заодно подтверждает,
            # что сообщение этому аккаунту вообще видно
            target = await acc_client.get_messages(peer, ids=msg_id)
            if target is None:
                rows.append(f"{label} — сообщение не видно этому аккаунту")
                await asyncio.sleep(DELAY)
                continue

            await acc_client(SendVoteRequest(peer=peer, msg_id=msg_id, options=options))
            rows.append(f"{label} — ok")
            ok += 1
        except FloodWaitError as e:
            rows.append(f"{label} — flood wait {e.seconds}с")
        except Exception as e:
            rows.append(f"{label} — `{str(e)[:90]}`")
        await asyncio.sleep(DELAY)

    rows.insert(0, f"успешно — `{ok}/{len(bot.clients)}`")
    await safe_edit(event, fmt("голосование завершено", rows))
