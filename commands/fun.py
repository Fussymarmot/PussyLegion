import random
from commands import command
from commands.utils import fmt, safe_edit


@command(r'^\.random(?:\s+(\d+)[-\s](\d+))?$', name="random")
async def random_handler(event, client, state):
    args = event.pattern_match.groups()
    if not args or not args[0] or not args[1]:
        await safe_edit(event, fmt("ошибка", ["формат: `.random 1-100`"]))
        return

    try:
        low, high = int(args[0]), int(args[1])
        if low > high:
            low, high = high, low
        result = random.randint(low, high)
        await safe_edit(event, fmt("случайное число", [f"`{result}`"]))
    except ValueError:
        await safe_edit(event, fmt("ошибка", ["нужны числа"]))


@command(r'^\.dice$', name="dice")
async def dice_handler(event, client, state):
    value = random.randint(1, 6)
    await safe_edit(event, fmt("кубик", [f"выпало `{value}`"]))
