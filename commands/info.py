import re

from commands import command
from commands.utils import safe_edit, fmt, get_moscow_time, get_usage_instructions
from config import VERSION, CHANNEL
from logger import LOG_BUFFER

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@command(r'^\.bot$', name="bot")
async def bot_info_handler(event, client, state):
    await safe_edit(event, fmt("pussylegion", [
        f"версия — `{VERSION}`",
        "разработчик — @pussymamont",
        f"канал — {CHANNEL}",
        "лицензия — MIT",
        f"время — `{get_moscow_time()}`",
    ]))


@command(r'^\.logs(?:\s+(\d+))?$', name="logs")
async def logs_handler(event, client, state):
    limit_arg = event.pattern_match.group(1)
    if limit_arg is None and event.raw_text.strip() != ".logs":
        await safe_edit(event, get_usage_instructions("logs"))
        return

    limit = int(limit_arg) if limit_arg else 100
    limit = max(1, min(limit, len(LOG_BUFFER) or 1))

    lines = list(LOG_BUFFER)[-limit:]
    if not lines:
        await safe_edit(event, fmt("логи пусты"))
        return

    clean = [ANSI_RE.sub("", line) for line in lines]
    body = "\n".join(clean)
    if len(body) > 3500:
        body = body[-3500:]

    header = fmt("логи", [
        f"строк — `{len(clean)}`",
        f"время — `{get_moscow_time()}`",
    ])

    try:
        await client.send_message("me", f"{header}\n\n```\n{body}\n```", parse_mode="md")
        await safe_edit(event, fmt("логи отправлены", ["в избранное"]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"не удалось отправить логи — `{str(e)[:120]}`"]))
