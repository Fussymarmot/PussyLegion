import re
import shutil
import subprocess
import sys
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


def _fmt_status(status):
    """Превращает TL UserStatus* в человекочитаемую строку."""
    if status is None:
        return "скрыт"
    cls = status.__class__.__name__
    if cls == "UserStatusOnline":
        return "в сети"
    if cls == "UserStatusOffline":
        was_online = getattr(status, "was_online", None)
        if was_online:
            return f"был(а) в сети `{was_online.strftime('%Y-%m-%d %H:%M:%S')}`"
        return "не в сети"
    if cls == "UserStatusRecently":
        return "был(а) недавно"
    if cls == "UserStatusLastWeek":
        return "был(а) на этой неделе"
    if cls == "UserStatusLastMonth":
        return "был(а) в этом месяце"
    return "скрыт"


def _full_attr(full_user, name, default=None):
    """GetFullUserRequest в разных версиях Telethon возвращает поля
    (about/common_chats_count/blocked) то прямо на результате, то во
    вложенном .full_user — проверяем оба варианта."""
    value = getattr(full_user, name, None)
    if value is not None:
        return value
    nested = getattr(full_user, "full_user", None)
    if nested is not None:
        return getattr(nested, name, default)
    return default


def _username_list(user):
    primary = getattr(user, "username", None)
    extra = [u.username for u in (getattr(user, "usernames", None) or []) if u.username and u.username != primary]
    names = [n for n in ([primary] if primary else []) + extra if n]
    return ", ".join(f"@{n}" for n in names) if names else None


@command(r'^\.data$', name="data")
async def data_handler(event, client, state):
    if not event.is_reply:
        await safe_edit(event, get_usage_instructions("data"))
        return
    try:
        reply = await event.get_reply_message()
        user, full_user = await _resolve_full_user(client, reply)

        rows = [f"id — `{user.id}`"]

        usernames = _username_list(user)
        if usernames:
            rows.append(f"юзернейм — {usernames}")

        rows.append(f"имя — `{user.first_name or 'нет'}`")
        if getattr(user, "last_name", None):
            rows.append(f"фамилия — `{user.last_name}`")

        if getattr(user, "phone", None):
            rows.append(f"телефон — `+{user.phone}`")
        else:
            rows.append("телефон — `скрыт/недоступен`")

        rows.append(f"статус — {_fmt_status(getattr(user, 'status', None))}")

        lang = getattr(user, "lang_code", None)
        if lang:
            rows.append(f"язык — `{lang}`")

        flags = []
        if getattr(user, "premium", False):
            flags.append("premium")
        if getattr(user, "verified", False):
            flags.append("verified")
        if getattr(user, "bot", False):
            flags.append("бот")
        if getattr(user, "scam", False):
            flags.append("scam")
        if getattr(user, "fake", False):
            flags.append("fake")
        if getattr(user, "restricted", False):
            flags.append("restricted")
        if getattr(user, "deleted", False):
            flags.append("удалён")
        if flags:
            rows.append(f"метки — `{', '.join(flags)}`")

        about = _full_attr(full_user, "about")
        if about:
            rows.append(f"о себе — `{about[:150]}`")

        common = _full_attr(full_user, "common_chats_count")
        if common:
            rows.append(f"общих чатов — `{common}`")

        if _full_attr(full_user, "blocked", False):
            rows.append("в чёрном списке — `да`")

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


def _holehe_cmd(mail):
    """Ищет исполняемый holehe: сначала в PATH, затем как модуль того же
    интерпретатора (pip install мог поставить его в окружение, где bin-папка
    не попала в PATH — частая причина 'No such file or directory')."""
    path = shutil.which("holehe")
    if path:
        return [path, mail]
    return [sys.executable, "-m", "holehe", mail]


async def _mail_lookup(event, mail):
    try:
        if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
            await safe_edit(event, fmt("ошибка", ["неверный формат почты"]))
            return

        if shutil.which("holehe") is None:
            try:
                import holehe  # noqa: F401 — проверяем, что пакет хотя бы импортируется
            except ImportError:
                await safe_edit(event, fmt("ошибка", [
                    "`holehe` не установлен",
                    "выполни: `pip install holehe` (или `pip install -r requirements.txt`) и перезапусти бота",
                ]))
                return

        result = subprocess.run(
            _holehe_cmd(mail),
            capture_output=True, text=True, shell=False, check=True, timeout=120,
        )
        lines = result.stdout.split("\n")[4:-4]
        output = "\n".join(
            line.replace("[x]", "✖").replace("[-]", "—").replace("[+]", "✔")
            for line in lines
        )
        await safe_edit(event, fmt("почта", [f"`{mail}`", output[:900]]))
    except FileNotFoundError:
        await safe_edit(event, fmt("ошибка", [
            "`holehe` не найден",
            "выполни: `pip install holehe` (или `pip install -r requirements.txt`) и перезапусти бота",
        ]))
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        await safe_edit(event, fmt("ошибка", [f"holehe упал — `{stderr[:200] or e}`"]))
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
