import asyncio
import random
import re

from commands import command
from commands.utils import fmt, fmt_uptime, safe_edit, get_usage_instructions
from core.roles import ROLE_TITLES, normalize_role
from logger import LOG_BUFFER

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _role_title(role):
    return ROLE_TITLES.get(role, role)


@command(r'^\.accounts$', name="accounts", scope="local")
async def accounts_handler(event, client, state):
    bot = state.bot
    if not bot.accounts:
        await safe_edit(event, fmt("аккаунтов нет", ["добавь: `.addacc +79123456789`"]))
        return

    loop_time = asyncio.get_event_loop().time()
    rows = []
    for account_id in sorted(bot.accounts):
        role = bot.roles.get(account_id)
        if account_id not in bot.running:
            status = "offline"
        else:
            left = bot.router.cooldown_left(account_id)
            status = f"flood {left}с" if left else "online"
            started = bot.started_at.get(account_id)
            if started:
                status += f" {fmt_uptime(loop_time - started)}"
        name = bot.accounts[account_id].get("name") or "аккаунт"
        auto = " · автостарт" if account_id in bot.autostart else ""
        rows.append(
            f"`#{account_id}` {name} — `{_role_title(role)}` · `{status}`{auto}"
        )

    rows.append("")
    rows.append("`.start <id>` · `.stop <id>` · `.autostart <id> on/off`")
    rows.append("`.addacc <номер>` · `.delacc <id>` · `.role <id> <роль>`")
    rows.append("`.all <команда>` · `.<id> <команда>` · `.me <команда>`")
    await safe_edit(event, fmt("аккаунты", rows))


@command(r'^\.role(?:\s+(\d+))?(?:\s+([a-zA-Z]+))?$', name="role", scope="local")
async def role_handler(event, client, state):
    bot = state.bot
    account_id = event.pattern_match.group(1)
    role = event.pattern_match.group(2)

    if not account_id and not role:
        rows = []
        for acc_id in sorted(bot.accounts):
            rows.append(
                f"`#{acc_id}` — `{_role_title(bot.roles.get(acc_id))}`"
            )
        if not rows:
            await safe_edit(event, fmt("аккаунтов нет"))
            return
        rows.append("")
        rows.append("пример: `.role 2 assistant`")
        await safe_edit(event, fmt("роли", rows))
        return

    if not account_id:
        await safe_edit(event, get_usage_instructions("role"))
        return

    account_id = int(account_id)
    if account_id not in bot.accounts:
        await safe_edit(event, fmt("ошибка", [f"аккаунт `#{account_id}` не найден"]))
        return

    if not role:
        current = bot.roles.get(account_id)
        await safe_edit(event, fmt(f"аккаунт #{account_id}", [
            f"текущая роль — `{_role_title(current)}`",
            "сменить — `.role "
            f"{account_id} <master/assistant/solo>`",
        ]))
        return

    new_role = normalize_role(role)
    if not new_role:
        await safe_edit(event, fmt("ошибка", [
            "роли: `master`, `assistant`, `solo`",
        ]))
        return

    result = await bot.set_role(account_id, new_role)
    if not result.get("ok"):
        await safe_edit(event, fmt("ошибка", [result.get("error", "не удалось сменить роль")]))
        return

    rows = [
        f"аккаунт — `#{account_id}`",
        f"роль — `{_role_title(new_role)}`",
    ]
    if new_role == "master":
        rows.append("прежний главный переведён в ассистенты")
    if result.get("restarted"):
        rows.append("аккаунт перезапущен")
    await safe_edit(event, fmt("роль обновлена", rows))


def _parse_id(event, group=1):
    raw = event.pattern_match.group(group)
    return int(raw) if raw else None


@command(r'^\.start(?:\s+#?(\d+))?$', name="start", scope="local")
async def start_handler(event, client, state):
    bot = state.bot
    account_id = _parse_id(event)
    if account_id is None:
        await safe_edit(event, get_usage_instructions("start"))
        return
    if account_id not in bot.accounts:
        await safe_edit(event, fmt("ошибка", [f"аккаунт `#{account_id}` не найден"]))
        return
    await safe_edit(event, fmt("запускаю", [f"аккаунт `#{account_id}`"]))
    result = await bot.start_account(account_id)
    if not result.get("ok"):
        await safe_edit(event, fmt("ошибка", [result.get("error", "не удалось запустить")]))
        return
    await safe_edit(event, fmt("запущен", [result.get("message", f"аккаунт `#{account_id}`")]))


@command(r'^\.stop(?:\s+#?(\d+))?$', name="stop", scope="local")
async def stop_handler(event, client, state):
    bot = state.bot
    account_id = _parse_id(event)
    if account_id is None:
        await safe_edit(event, get_usage_instructions("stop"))
        return
    if account_id not in bot.accounts:
        await safe_edit(event, fmt("ошибка", [f"аккаунт `#{account_id}` не найден"]))
        return
    if account_id == state.account_id:
        await safe_edit(event, fmt("ошибка", [
            "нельзя остановить аккаунт, с которого идёт команда",
            "иначе бот перестанет отвечать до перезапуска",
        ]))
        return
    if account_id not in bot.running:
        await safe_edit(event, fmt("уже остановлен", [f"аккаунт `#{account_id}`"]))
        return
    await bot.stop_account(account_id)
    await safe_edit(event, fmt("остановлен", [f"аккаунт `#{account_id}`"]))


@command(r'^\.autostart(?:\s+#?(\d+))?(?:\s+(on|off))?$', name="autostart", scope="local")
async def autostart_handler(event, client, state):
    bot = state.bot
    account_id = _parse_id(event)
    action = (event.pattern_match.group(2) or "").lower()

    if account_id is None:
        ids = ", ".join(f"`#{a}`" for a in sorted(bot.autostart)) or "пусто"
        await safe_edit(event, fmt("автостарт", [
            f"в списке — {ids}",
            "главные и самостоятельные аккаунты стартуют всегда",
            "изменить — `.autostart <id> on/off`",
        ]))
        return
    if account_id not in bot.accounts:
        await safe_edit(event, fmt("ошибка", [f"аккаунт `#{account_id}` не найден"]))
        return
    if not action:
        state_text = "включён" if account_id in bot.autostart else "выключен"
        await safe_edit(event, fmt(f"аккаунт #{account_id}", [
            f"автостарт — `{state_text}`",
            f"изменить — `.autostart {account_id} on/off`",
        ]))
        return

    bot.set_autostart(account_id, action == "on")
    await safe_edit(event, fmt("автостарт обновлён", [
        f"аккаунт — `#{account_id}`",
        f"автостарт — `{'включён' if action == 'on' else 'выключен'}`",
    ]))


@command(r'^\.addacc(?:\s+(\S.*))?$', name="addacc", scope="local")
async def addacc_handler(event, client, state):
    bot = state.bot
    phone = (event.pattern_match.group(1) or "").strip()
    if not phone:
        await safe_edit(event, get_usage_instructions("addacc"))
        return

    await safe_edit(event, fmt("отправляю код", ["подожди пару секунд"]))
    result = await bot.start_add_account(phone)
    if not result.get("ok"):
        await safe_edit(event, fmt("ошибка", [result.get("error", "не удалось отправить код")]))
        return

    bot.pending_phone = result["phone"]
    await safe_edit(event, fmt("код отправлен", [
        f"номер — `{result['phone']}`",
        "введи `.code 1-2-3-4-5` — цифры обязательно через дефис или пробел,",
        "иначе Telegram посчитает код слитым и отменит его",
    ]))


@command(r'^\.code(?:\s+(.+))?$', name="code", scope="local")
async def code_handler(event, client, state):
    bot = state.bot
    raw = event.pattern_match.group(1) or ""
    code = "".join(ch for ch in raw if ch.isdigit())
    phone = getattr(bot, "pending_phone", None)

    # сразу затираем код в чате, пока идёт проверка
    await safe_edit(event, fmt("проверяю код", ["подожди пару секунд"]))

    if not phone or phone not in bot.pending_auth:
        await safe_edit(event, fmt("ошибка", ["сначала `.addacc <номер>`"]))
        return
    if not code:
        await safe_edit(event, fmt("ошибка", ["введи цифры кода: `.code 1-2-3-4-5`"]))
        return

    result = await bot.complete_add_account(phone, code=code)
    await _finish_add(event, bot, phone, result)


@command(r'^\.pass(?:\s+(.+))?$', name="pass", scope="local")
async def pass_handler(event, client, state):
    bot = state.bot
    password = event.pattern_match.group(1)
    phone = getattr(bot, "pending_phone", None)

    # сразу затираем пароль в чате
    await safe_edit(event, fmt("проверяю пароль", ["подожди пару секунд"]))

    if not phone or phone not in bot.pending_auth:
        await safe_edit(event, fmt("ошибка", ["сначала `.addacc <номер>`"]))
        return
    if not password:
        await safe_edit(event, fmt("ошибка", ["введи пароль: `.pass <пароль 2FA>`"]))
        return

    result = await bot.complete_add_account(phone, password=password)
    await _finish_add(event, bot, phone, result)


async def _finish_add(event, bot, phone, result):
    if result.get("need_password"):
        await safe_edit(event, fmt("нужен пароль 2FA", [
            "введи `.pass <пароль>` — сообщение сразу заменится",
            "лучше писать это в избранном",
        ]))
        return
    if not result.get("ok"):
        await safe_edit(event, fmt("ошибка", [result.get("error", "не удалось войти")]))
        return

    bot.pending_phone = None
    account_id = max(bot.accounts)
    role = bot.roles.get(account_id)
    rows = [
        f"аккаунт — `#{account_id}`",
        f"роль — `{_role_title(role)}`",
    ]
    if role == "assistant":
        rows.append("ассистент поднимется сам при первой команде или `.start "
                    f"{account_id}`")
    else:
        rows.append(f"запустить — `.start {account_id}`")
    await safe_edit(event, fmt("аккаунт добавлен", rows))


@command(r'^\.delacc(?:\s+#?(\d+))?(?:\s+(\d{4}))?$', name="delacc", scope="local")
async def delacc_handler(event, client, state):
    bot = state.bot
    account_id = _parse_id(event)
    code = event.pattern_match.group(2)

    if account_id is None:
        await safe_edit(event, get_usage_instructions("delacc"))
        return
    if account_id not in bot.accounts:
        await safe_edit(event, fmt("ошибка", [f"аккаунт `#{account_id}` не найден"]))
        return
    if account_id == state.account_id:
        await safe_edit(event, fmt("ошибка", ["нельзя удалить аккаунт, с которого идёт команда"]))
        return

    key = f"acc:{account_id}"
    if not code:
        confirm = "".join(random.choices("0123456789", k=4))
        state.pending_confirmation[key] = confirm
        await safe_edit(event, fmt("подтверждение удаления", [
            f"аккаунт `#{account_id}` и его сессия будут удалены",
            f"введи `.delacc {account_id} {confirm}`",
        ]))
        return

    if state.pending_confirmation.get(key) != code:
        await safe_edit(event, fmt("ошибка", ["неверный код"]))
        return
    state.pending_confirmation.pop(key, None)

    result = await bot.delete_account(account_id)
    if not result.get("ok"):
        await safe_edit(event, fmt("ошибка", [result.get("error", "не удалось удалить")]))
        return
    await safe_edit(event, fmt("аккаунт удалён", [f"`#{account_id}`"]))


@command(r'^\.logs(?:\s+(\d+))?$', name="logs", scope="local")
async def logs_handler(event, client, state):
    count = min(int(event.pattern_match.group(1) or 20), 100)
    lines = [ANSI_RE.sub("", line).replace("`", "'") for line in list(LOG_BUFFER)[-count:]]
    if not lines:
        await safe_edit(event, fmt("логи пусты"))
        return

    # в чат не пишем — там могут быть посторонние, отправляем в избранное
    chunk, size = [], 0
    for line in reversed(lines):
        if size + len(line) + 1 > 3500:
            break
        chunk.append(line)
        size += len(line) + 1
    text = "**✦ логи**\n\n```\n" + "\n".join(reversed(chunk)) + "\n```"
    await client.send_message("me", text, parse_mode="md")
    await safe_edit(event, fmt("логи", [f"отправил в избранное · строк: `{len(chunk)}`"]))
