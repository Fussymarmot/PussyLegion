"""Голосование в опросах со всех запущенных аккаунтов."""

import asyncio
import re

from telethon.errors import FloodWaitError, MessageIdInvalidError
from telethon.tl.functions.messages import SendVoteRequest
from telethon.tl.types import MessageMediaPoll

from commands import command
from commands.utils import (
    fmt, safe_edit, get_usage_instructions, resolve_peer, username_of,
    parse_message_link, send_with_recovery,
)

DELAY = 0.5


def _text(obj):
    """В новых слоях TL текст опроса/варианта — TextWithEntities, в старых — просто str."""
    return getattr(obj, "text", obj)


def _vote_request(peer, msg_id, options):
    return SendVoteRequest(peer=peer, msg_id=msg_id, options=options)


@command(r'^\.vote(?:\s+(\S+))?(?:\s+(\S+))?$', name="vote", scope="local")
async def vote_handler(event, client, state):
    """.vote — ответом на опрос без номера: показывает варианты и их номера.
    .vote 2 — голосует за вариант №2 со всех запущенных аккаунтов.
    .vote 1,3 — несколько вариантов (для multiple-choice опросов).
    .vote <ссылка> — то же самое, но опрос задан ссылкой на сообщение.
    """
    bot = state.bot
    arg1 = event.pattern_match.group(1)
    arg2 = event.pattern_match.group(2)

    username, msg_id, peer_id = None, None, None
    same_chat = True

    if arg1 and any(host in arg1 for host in ("t.me/", "telegram.me/", "tg://")):
        username, msg_id, peer_id = parse_message_link(arg1)
        if not msg_id:
            await safe_edit(event, fmt("ошибка", ["не распознал ссылку на сообщение"]))
            return
        arg = arg2
        same_chat = False
        try:
            target_peer = await resolve_peer(client, peer_id, username)
            message = await client.get_messages(target_peer, ids=msg_id)
        except Exception as e:
            await safe_edit(event, fmt("ошибка", [f"не открыл сообщение по ссылке — `{str(e)[:90]}`"]))
            return
    else:
        arg = arg1
        if not event.is_reply:
            await safe_edit(event, get_usage_instructions("vote"))
            return
        message = await event.get_reply_message()
        if message:
            chat = await event.get_chat()
            username = username_of(chat)
            msg_id = message.id

    if not message or not isinstance(message.media, MessageMediaPoll):
        await safe_edit(event, fmt("ошибка", ["ответь на сообщение с опросом или дай ссылку на него"]))
        return

    poll = message.media.poll
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

    if not bot.clients:
        await safe_edit(event, fmt("ошибка", ["нет запущенных аккаунтов"]))
        return

    await safe_edit(event, fmt("голосую", [f"вариант(ы) — `{', '.join(map(str, indices))}`"]))

    rows = []
    ok = 0
    chat_target = event.chat_id if same_chat else peer_id
    for num, acc_client in list(bot.clients.items()):
        label = bot.router.label(num)
        try:
            peer = await resolve_peer(acc_client, chat_target, username)
            joined = await send_with_recovery(
                acc_client,
                lambda target: _vote_request(target, msg_id, options),
                peer,
                msg_id,
                username=username,
                inviter=client,
                invite_entity=chat_target,
                resolve=lambda c: resolve_peer(c, chat_target, username),
            )
            rows.append(f"{label} — ok" + (" · зашёл в чат" if joined else ""))
            ok += 1
        except FloodWaitError as e:
            rows.append(f"{label} — flood wait {e.seconds}с")
        except MessageIdInvalidError:
            rows.append(f"{label} — сообщение недоступно: не в чате или скрыта история")
        except Exception as e:
            rows.append(f"{label} — `{str(e)[:90]}`")
        await asyncio.sleep(DELAY)

    rows.insert(0, f"успешно — `{ok}/{len(bot.clients)}`")
    await safe_edit(event, fmt("голосование завершено", rows))
