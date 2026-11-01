"""Массовый вход всех запущенных аккаунтов в чат/канал."""

import asyncio
import re

from telethon.errors import (
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    ChannelsTooMuchError,
    ChannelPrivateError,
    FloodWaitError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest, ImportChatInviteRequest

from commands import command
from commands.utils import fmt, safe_edit, get_usage_instructions

DELAY = 1.0  # пауза между аккаунтами — вход в чаты чувствителен к флуд-вейтам

HASH_RE = re.compile(r'(?:https?://)?t\.me/(?:\+|joinchat/)([\w-]+)')
USERNAME_RE = re.compile(r'(?:https?://)?t\.me/([A-Za-z0-9_]{5,32})/?$')


def _parse_target(arg):
    """Возвращает (kind, value): ('hash', ...) / ('username', ...) / (None, None)."""
    if not arg:
        return None, None
    m = HASH_RE.search(arg)
    if m:
        return "hash", m.group(1)
    m = USERNAME_RE.search(arg)
    if m:
        return "username", m.group(1)
    clean = arg.lstrip("@").strip()
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", clean):
        return "username", clean
    return None, None


async def _join_one(acc_client, kind, value):
    if kind == "username":
        await acc_client(JoinChannelRequest(value))
    else:
        await acc_client(ImportChatInviteRequest(value))


@command(r'^\.joinall(?:\s+(\S+))?$', name="joinall", scope="local")
async def joinall_handler(event, client, state):
    """.joinall — без аргумента заходит в текущий чат (где уже сидит главный).
    .joinall <ссылка/юзернейм> — заходит по ссылке, не завязано на текущий чат.
    """
    bot = state.bot
    arg = event.pattern_match.group(1)

    kind, value = _parse_target(arg)

    if not kind and not arg:
        # аргумента нет — берём текущий чат
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        if username:
            kind, value = "username", username
        else:
            try:
                invite = await client(ExportChatInviteRequest(chat))
                m = HASH_RE.search(invite.link) or re.search(r'/([\w-]+)$', invite.link)
                if not m:
                    raise ValueError("не смог разобрать ссылку приглашения")
                kind, value = "hash", m.group(1)
            except Exception as e:
                await safe_edit(event, fmt("ошибка", [
                    "не могу сам получить ссылку на этот чат",
                    f"`{str(e)[:90]}`",
                    "пришли ссылку вручную: `.joinall <ссылка>`",
                ]))
                return

    if not kind:
        await safe_edit(event, get_usage_instructions("joinall"))
        return

    if not bot.clients:
        await safe_edit(event, fmt("ошибка", ["нет запущенных аккаунтов"]))
        return

    await safe_edit(event, fmt("захожу всеми аккаунтами", [f"цель — `{value}`"]))

    rows = []
    ok = 0
    for num, acc_client in list(bot.clients.items()):
        label = bot.router.label(num)
        try:
            await _join_one(acc_client, kind, value)
            rows.append(f"{label} — ok")
            ok += 1
        except UserAlreadyParticipantError:
            rows.append(f"{label} — уже в чате")
            ok += 1
        except FloodWaitError as e:
            rows.append(f"{label} — flood wait {e.seconds}с")
        except (InviteHashExpiredError, InviteHashInvalidError):
            rows.append(f"{label} — ссылка недействительна")
        except ChannelsTooMuchError:
            rows.append(f"{label} — превышен лимит каналов/групп на аккаунте")
        except ChannelPrivateError:
            rows.append(f"{label} — доступ закрыт")
        except Exception as e:
            rows.append(f"{label} — `{str(e)[:90]}`")
        await asyncio.sleep(DELAY)

    rows.insert(0, f"успешно — `{ok}/{len(bot.clients)}`")
    await safe_edit(event, fmt("вход выполнен", rows))
