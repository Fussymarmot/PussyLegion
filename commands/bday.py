from datetime import date, datetime

import pytz
from telethon.tl.functions.users import GetFullUserRequest

from commands import command
from commands.osint import _full_attr, _resolve_full_user
from commands.utils import fmt, safe_edit

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _today():
    return datetime.now(pytz.timezone("Europe/Moscow")).date()


def _safe_date(year, month, day):
    """29 февраля в невисокосный год переносим на 28 февраля."""
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def _plural(n, one, few, many):
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _days(n):
    return f"{n} {_plural(n, 'день', 'дня', 'дней')}"


def _years(n):
    return f"{n} {_plural(n, 'год', 'года', 'лет')}"


def _calc_rows(day, month, year=None):
    today = _today()

    this_year = _safe_date(today.year, month, day)
    if this_year >= today:
        next_bd = this_year
        last_bd = _safe_date(today.year - 1, month, day)
    else:
        next_bd = _safe_date(today.year + 1, month, day)
        last_bd = this_year

    left = (next_bd - today).days
    passed = (today - last_bd).days

    born = f"{day} {MONTHS[month - 1]}" + (f" {year}" if year else "")
    rows = [f"дата рождения — `{born}`"]

    if year:
        age = today.year - year - ((today.month, today.day) < (month, day))
        rows.append(f"возраст — `{_years(age)}`")
        try:
            lived = (today - _safe_date(year, month, day)).days
            rows.append(f"прожито — `{_days(lived)}`")
        except Exception:
            pass

    if left == 0:
        rows.append("**сегодня день рождения** 🎉")
        rows.append(f"с прошлого — `{_days(passed)}`")
    else:
        rows.append(f"до дня рождения — `{_days(left)}`")
        rows.append(f"прошло с дня рождения — `{_days(passed)}`")
    return rows


@command(r'^\.bday$', name="bday")
async def bday_handler(event, client, state):
    if event.is_reply:
        reply = await event.get_reply_message()
        user, full_user = await _resolve_full_user(client, reply)
        name = (user.first_name or "").strip() or str(user.id)
        title = f"день рождения · {name}"
        hidden = "дата рождения скрыта или не указана"
    else:
        full_user = await client(GetFullUserRequest("me"))
        title = "мой день рождения"
        hidden = "в твоём профиле не указана дата рождения"

    bd = _full_attr(full_user, "birthday")
    if bd is None:
        await safe_edit(event, fmt(title, [hidden]))
        return

    day = getattr(bd, "day", None)
    month = getattr(bd, "month", None)
    year = getattr(bd, "year", None)
    if not day or not month:
        await safe_edit(event, fmt(title, [hidden]))
        return

    await safe_edit(event, fmt(title, _calc_rows(day, month, year)))
