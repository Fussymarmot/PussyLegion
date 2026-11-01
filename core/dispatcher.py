"""Единая точка входа для команд.

На главных (и самостоятельных) аккаунтах вешается один слушатель на
исходящие сообщения. Он ловит текст вида `.команда ...`, разбирает
префикс маршрутизации и выполняет команду нужным аккаунтом.
"""

import asyncio

from telethon import events
from telethon.errors import FloodWaitError

from commands import find_command
from commands.utils import fmt
from core.router import parse_directive
from core.views import EventView
from logger import exception, info

REPORT_TTL = 20


async def _delete_later(message, seconds):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


def _short(exc):
    text = str(exc) or exc.__class__.__name__
    return text if len(text) <= 140 else text[:137] + "…"


async def notify(client, event, title, rows, delete_after=REPORT_TTL):
    text = fmt(title, rows)
    try:
        await event.edit(text, parse_mode="md")
        return
    except Exception:
        pass
    try:
        msg = await client.send_message(event.chat_id, text, parse_mode="md")
        if delete_after:
            asyncio.create_task(_delete_later(msg, delete_after))
    except Exception:
        exception("dispatcher.notify")


async def _start_assistants(bot, account_ids=None):
    ids = account_ids if account_ids is not None else bot.roles.assistants()
    pending = [
        bot.start_account(a)
        for a in ids
        if a in bot.accounts and a not in bot.running and a not in bot.starting
    ]
    if not pending:
        return
    info(f"поднимаю ассистентов: {len(pending)}")
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=25)
    except asyncio.TimeoutError:
        pass


def _needed_assistants(bot, route):
    configured = bot.roles.assistants()
    if not configured:
        return []
    if route is not None and route.kind == "all":
        return configured
    if route is not None and route.kind == "id":
        return [route.account_id] if route.account_id in configured else []
    # для обычной команды хватит одного
    return configured[:1]


async def _run_one(cmd, event_view, client, state):
    """Выполняет команду у одного исполнителя.

    Возвращает (ok, текст_ошибки, секунды_флуда).
    """
    try:
        await cmd.handler(event_view, client, state)
        return True, None, 0
    except FloodWaitError as e:
        return False, None, int(getattr(e, "seconds", 60) or 60)
    except Exception as e:
        exception(f"команда .{cmd.name}")
        return False, _short(e), 0


async def _dispatch_impl(event, client, state, bot, account_id):
    text = (event.raw_text or "").strip()
    if not text.startswith("."):
        return

    route, cmd_text = parse_directive(text)
    cmd, match = find_command(cmd_text)
    if not cmd:
        return

    role = state.role
    resolution = bot.router.resolve(account_id, route, cmd.scope, role)

    # ассистенты есть, но лежат — поднимем и попробуем ещё раз
    if not resolution.targets and resolution.error and (
        resolution.error == "ассистенты не запущены" or "не запущен" in resolution.error
    ):
        try:
            await event.edit(
                fmt("поднимаю ассистентов", ["это займёт пару секунд"]),
                parse_mode="md",
            )
        except Exception:
            pass
        await _start_assistants(bot, _needed_assistants(bot, route))
        resolution = bot.router.resolve(account_id, route, cmd.scope, role)

    if not resolution.targets:
        await notify(client, event, "некому выполнять", [
            f"команда — `.{cmd.name}`",
            resolution.error or "нет исполнителей",
            "назначь роли: `.role <id> assistant`",
        ])
        return

    # --- одиночное выполнение (с фолбэком по пулу) ---
    if not resolution.broadcast:
        errors = []
        for index, target in enumerate(resolution.targets):
            executor = bot.clients.get(target) or (client if target == account_id else None)
            if executor is None:
                continue
            view = EventView(event, cmd_text, match)
            ok, detail, flood = await _run_one(cmd, view, executor, state)
            if ok:
                if index > 0:
                    info(f".{cmd.name}: выполнил {bot.router.label(target)}")
                return
            if flood:
                left = bot.router.mark_cooldown(target, flood)
                errors.append(f"{bot.router.label(target)} — flood wait {left}с")
            else:
                errors.append(f"{bot.router.label(target)} — `{detail}`")
            if route is not None and route.kind == "id":
                break

        await notify(client, event, "команда не выполнена", errors or ["нет доступного исполнителя"])
        return

    # --- рассылка на всех ---
    rows = []
    for skipped in bot.roles.assistants():
        if skipped in resolution.targets:
            continue
        left = bot.router.cooldown_left(skipped)
        reason = f"flood wait {left}с" if left else (
            "не запущен" if skipped not in bot.running else "недоступен"
        )
        rows.append(f"{bot.router.label(skipped)} — пропущен · {reason}")

    for index, target in enumerate(resolution.targets):
        executor = bot.clients.get(target)
        if executor is None:
            rows.append(f"{bot.router.label(target)} — не запущен")
            continue
        view = EventView(event, cmd_text, match, silent=index > 0, allow_delete=index == 0)
        ok, detail, flood = await _run_one(cmd, view, executor, state)
        if ok:
            rows.append(f"{bot.router.label(target)} — ok")
        elif flood:
            left = bot.router.mark_cooldown(target, flood)
            rows.append(f"{bot.router.label(target)} — flood wait {left}с")
        else:
            rows.append(f"{bot.router.label(target)} — `{detail}`")

    await notify(client, event, f"рассылка · .{cmd.name}", rows)


def attach(client, state, bot, account_id):
    """Вешает диспетчер на клиент главного/самостоятельного аккаунта."""

    @client.on(events.NewMessage(outgoing=True, func=lambda e: bool(e.raw_text) and e.raw_text.startswith(".")))
    async def _dispatcher(event):
        try:
            await _dispatch_impl(event, client, state, bot, account_id)
        except Exception:
            exception("dispatcher")

    return _dispatcher
