"""Массовая постановка реакций со всех запущенных аккаунтов."""

import asyncio

from telethon.errors import MessageIdInvalidError
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

from commands import command
from commands.utils import (
    fmt, safe_edit, get_usage_instructions, resolve_peer, username_of,
    parse_message_link, send_with_recovery,
)

DEFAULT_EMOJI = "❤️"
DELAY = 0.4  # между аккаунтами, чтобы не словить флуд-вейт сразу у всех


def _reaction_request(peer, msg_id, emoji):
    return SendReactionRequest(
        peer=peer,
        msg_id=msg_id,
        reaction=[ReactionEmoji(emoticon=emoji)],
        add_to_recent=True,
    )


@command(r'^\.like(?:\s+(\S+))?(?:\s+(\S+))?$', name="like", scope="local")
async def like_handler(event, client, state):
    """Ставит реакцию на сообщение со всех сейчас запущенных аккаунтов.

    Варианты вызова:
        .like                       — ответом на сообщение, эмодзи по умолчанию
        .like 🔥                    — ответом на сообщение, свой эмодзи
        .like https://t.me/c/123    — по ссылке (без ответа), эмодзи по умолчанию
        .like https://t.me/c/123 🔥 — по ссылке со своим эмодзи
    """
    bot = state.bot
    arg1 = event.pattern_match.group(1)
    arg2 = event.pattern_match.group(2)

    username, msg_id, peer_id = None, None, None
    emoji = DEFAULT_EMOJI
    same_chat = True  # сообщение в том же чате, где написана команда, или в другом (по ссылке)

    if arg1 and any(host in arg1 for host in ("t.me/", "telegram.me/", "tg://")):
        username, msg_id, peer_id = parse_message_link(arg1)
        if not msg_id:
            await safe_edit(event, fmt("ошибка", ["не распознал ссылку на сообщение"]))
            return
        if arg2:
            emoji = arg2
        same_chat = False  # ссылка ведёт в чат, который может отличаться от текущего
    else:
        if not event.is_reply:
            await safe_edit(event, get_usage_instructions("like"))
            return
        reply = await event.get_reply_message()
        if not reply:
            await safe_edit(event, fmt("ошибка", ["не нашёл сообщение, на которое отвечаешь"]))
            return
        msg_id = reply.id
        chat = await event.get_chat()
        username = username_of(chat)
        if arg1:
            emoji = arg1

    if not bot.clients:
        await safe_edit(event, fmt("ошибка", ["нет запущенных аккаунтов"]))
        return

    await safe_edit(event, fmt("ставлю реакции", [f"эмодзи — `{emoji}`"]))

    rows = []
    ok = 0
    chat_target = event.chat_id if same_chat else peer_id
    for num, acc_client in list(bot.clients.items()):
        label = bot.router.label(num)
        try:
            peer = await resolve_peer(acc_client, chat_target, username)
            joined = await send_with_recovery(
                acc_client,
                lambda target: _reaction_request(target, msg_id, emoji),
                peer,
                msg_id,
                username=username,
                inviter=client,
                invite_entity=chat_target,
                resolve=lambda c: resolve_peer(c, chat_target, username),
            )
            rows.append(f"{label} — ok" + (" · зашёл в чат" if joined else ""))
            ok += 1
        except MessageIdInvalidError:
            rows.append(f"{label} — сообщение недоступно: не в чате или скрыта история")
        except Exception as e:
            rows.append(f"{label} — `{str(e)[:90]}`")
        await asyncio.sleep(DELAY)

    rows.insert(0, f"успешно — `{ok}/{len(bot.clients)}`")
    await safe_edit(event, fmt(f"реакция · {emoji}", rows))
