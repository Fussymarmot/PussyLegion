from commands import command
from commands.utils import safe_edit, fmt, get_moscow_time
from config import VERSION


@command(r'^\.bot$', name="bot", scope="local")
async def bot_info_handler(event, client, state):
    await safe_edit(event, fmt("pussylegion", [
        f"версия — `{VERSION}`",
        f"ассистенты — `{len(state.bot.roles.assistants())}`",
        "разработчик — @pussymamont",
        "канал — @rubyland_fun",
        "лицензия — MIT",
        f"время — `{get_moscow_time()}`",
    ]))
