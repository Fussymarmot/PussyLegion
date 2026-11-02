import re
import subprocess
import requests
import whois as whois_lib
from telethon.tl.functions.users import GetFullUserRequest
from commands import command
from commands.utils import safe_edit, fmt, get_usage_instructions


async def _resolve_full_user(client, reply):
    """get_sender() иногда отдаёт «min»-объект без access_hash (пользователь
    виден в чате, но Telethon ещё не закэшировал его полную сущность —
    типично для старых сообщений в больших группах/каналах). В этом случае
    GetFullUserRequest падает с ошибкой про access_hash, хотя юзер реально
    есть. Пробуем достать полноценную сущность другими путями, прежде чем
    сдаваться."""
    user = await reply.get_sender()
    if user is None:
        input_sender = await reply.get_input_sender()
        if input_sender is None:
            raise ValueError("не удалось определить отправителя сообщения")
        user = await client.get_entity(input_sender)

    try:
        full_user = await client(GetFullUserRequest(user))
    except Exception:
        # первый путь не сработал — переустанавливаем access_hash через
        # input-сущность из самого сообщения и пробуем снова
        input_sender = await reply.get_input_sender()
        user = await client.get_entity(input_sender)
        full_user = await client(GetFullUserRequest(user))

    return user, full_user


@command(r'^\.data$', name="data")
async def data_handler(event, client, state):
    if not event.is_reply:
        await safe_edit(event, get_usage_instructions("data"))
        return
    try:
        reply = await event.get_reply_message()
        user, full_user = await _resolve_full_user(client, reply)

        rows = [f"id — `{user.id}`", f"имя — `{user.first_name or 'нет'}`"]
        if getattr(user, "phone", None):
            rows.append(f"телефон — `{user.phone}`")
        status = getattr(user, "status", None)
        if status:
            if hasattr(status, "was_online"):
                rows.append(f"был онлайн — `{status.was_online.strftime('%Y-%m-%d %H:%M:%S')}`")
            if hasattr(status, "created"):
                rows.append(f"регистрация — `{status.created.strftime('%Y-%m-%d %H:%M:%S')}`")
        about = getattr(full_user, "about", None)
        if about:
            rows.append(f"о себе — `{about[:100]}`")
        await safe_edit(event, fmt("данные пользователя", rows))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


@command(r'^\.osint(?:\s+(.+))?$', name="osint")
async def osint_handler(event, client, state):
    args = event.text.split(" ", 1)
    if len(args) < 2:
        await safe_edit(event, get_usage_instructions("osint"))
        return
    target = args[1].strip()
    try:
        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', target):
            await _ip_lookup(event, target)
        elif "@" in target:
            await _mail_lookup(event, target)
        elif re.match(r'^\+?[\d\s\-()]{7,}$', target):
            await _phone_lookup(event, target)
        else:
            await safe_edit(event, fmt("ошибка", ["неверный формат данных"]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


def _single(value):
    """python-whois иногда возвращает список значений с разных WHOIS-
    серверов (например несколько дублирующихся дат) — берём первое."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _fmt_date(value):
    value = _single(value)
    if value is None:
        return "нет данных"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


@command(r'^\.whois(?:\s+(.+))?$', name="whois")
async def whois_handler(event, client, state):
    args = event.text.split(" ", 1)
    if len(args) < 2:
        await safe_edit(event, get_usage_instructions("whois"))
        return
    domain = args[1].strip()
    try:
        info = whois_lib.whois(domain)
        if not info or not info.domain_name:
            await safe_edit(event, fmt("ошибка", ["домен не найден"]))
            return
        rows = [
            f"домен — `{_single(info.domain_name)}`",
            f"создан — `{_fmt_date(info.creation_date)}`",
            f"истекает — `{_fmt_date(info.expiration_date)}`",
            f"регистратор — `{_single(info.registrar) or 'нет данных'}`",
        ]
        ns = info.name_servers
        if ns:
            if isinstance(ns, (list, tuple, set)):
                ns = sorted({str(n).lower() for n in ns if n})
                ns = ", ".join(ns)
            rows.append(f"ns — `{ns}`")
        await safe_edit(event, fmt("whois", rows))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


async def _ip_lookup(event, ip):
    try:
        data = requests.get(f"http://ipwho.is/{ip}", timeout=10).json()
        if not data.get("success"):
            await safe_edit(event, fmt("ошибка", ["не удалось получить данные"]))
            return
        rows = [
            f"ip — `{ip}`",
            f"провайдер — `{data['connection']['isp']}`",
            f"страна — `{data['country']}`",
            f"город — `{data['city']}`",
            f"координаты — `{data['latitude']}, {data['longitude']}`",
        ]
        await safe_edit(event, fmt("ip", rows))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


async def _phone_lookup(event, phone):
    try:
        r = requests.get(
            f"https://htmlweb.ru/geo/api.php?json&telcod={phone}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = r.json()
        if data.get("limit") == 0:
            await safe_edit(event, fmt("ошибка", ["лимит запросов, включи vpn"]))
            return
        rows = [
            f"номер — `{phone}`",
            f"страна — `{data.get('country', {}).get('name', 'N/A')}`",
            f"оператор — `{data.get('0', {}).get('oper', 'N/A')}`",
        ]
        await safe_edit(event, fmt("номер", rows))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))


async def _mail_lookup(event, mail):
    try:
        if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
            await safe_edit(event, fmt("ошибка", ["неверный формат почты"]))
            return
        result = subprocess.run(
            ["holehe", mail],
            capture_output=True, text=True, shell=False, check=True, timeout=120,
        )
        lines = result.stdout.split("\n")[4:-4]
        output = "\n".join(
            line.replace("[x]", "✖").replace("[-]", "—").replace("[+]", "✔")
            for line in lines
        )
        await safe_edit(event, fmt("почта", [f"`{mail}`", output[:900]]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
