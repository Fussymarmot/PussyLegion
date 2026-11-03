import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import whois as whois_lib
from telethon.tl.functions.users import GetFullUserRequest
from commands import command
from commands.utils import safe_edit, fmt, get_usage_instructions
from config import CACHE_DIR

# кэш последнего прогона holehe по каждой почте — нужен для .osint <почта> -r
OSINT_MAIL_CACHE_DIR = CACHE_DIR / "osint_mail"
OSINT_MAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


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

    # -r (в любом месте строки) — ретрай: для почты значит "прогнать holehe
    # ещё раз и подтянуть статус по доменам, упавшим с [x] в прошлый раз".
    # На IP/телефон не влияет — там и так один сетевой запрос, ретраить нечего.
    retry = False
    parts = target.split()
    if "-r" in parts:
        retry = True
        parts = [p for p in parts if p != "-r"]
        target = " ".join(parts).strip()

    if not target:
        await safe_edit(event, get_usage_instructions("osint"))
        return

    try:
        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', target):
            await _ip_lookup(event, target)
        elif "@" in target:
            await _mail_lookup(event, target, retry=retry)
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


def _find_holehe():
    """Ищет исполняемый holehe: сначала в PATH, затем рядом с текущим
    интерпретатором (venv/bin) — именно там pip кладёт консольный скрипт,
    даже если сама bin-папка venv не попала в системный PATH."""
    path = shutil.which("holehe")
    if path:
        return path
    candidate = Path(sys.executable).with_name(
        "holehe.exe" if sys.platform == "win32" else "holehe"
    )
    if candidate.is_file():
        return str(candidate)
    return None


def _mail_cache_path(mail):
    h = hashlib.sha256(mail.strip().lower().encode()).hexdigest()[:20]
    return OSINT_MAIL_CACHE_DIR / f"{h}.json"


def _load_mail_cache(mail):
    path = _mail_cache_path(mail)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_mail_cache(mail, found, not_found, errored):
    path = _mail_cache_path(mail)
    try:
        path.write_text(
            json.dumps(
                {"mail": mail, "found": found, "not_found": not_found, "errored": errored, "ts": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass  # кэш — не критичная штука, без него просто нельзя будет -r


async def _mail_lookup(event, mail, retry=False):
    try:
        if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
            await safe_edit(event, fmt("ошибка", ["неверный формат почты"]))
            return

        prev = None
        if retry:
            prev = _load_mail_cache(mail)
            if prev is None:
                await safe_edit(event, fmt("ошибка", [
                    "нет сохранённого первого прогона для этой почты",
                    "сначала выполни `.osint почта` без `-r`",
                ]))
                return
            if not prev.get("errored"):
                await safe_edit(event, fmt("почта", [
                    f"`{mail}`",
                    "в прошлый раз ошибок не было — подтягивать нечего",
                ]))
                return

        holehe_path = _find_holehe()
        if holehe_path is None:
            try:
                import holehe  # noqa: F401 — пакет есть, просто нет консольного скрипта в venv
            except ImportError:
                await safe_edit(event, fmt("ошибка", [
                    "`holehe` не установлен",
                    "выполни: `pip install holehe` (или `pip install -r requirements.txt`) и перезапусти бота",
                ]))
                return
            await safe_edit(event, fmt("ошибка", [
                "пакет `holehe` установлен, но консольный скрипт не найден",
                "проверь, что venv ставился с `pip install holehe` (а не только `pip install -e .` без entry_points), "
                "или добавь папку `.venv/bin` в `PATH`",
            ]))
            return

        result = subprocess.run(
            [holehe_path, mail],
            capture_output=True, text=True, shell=False, check=True, timeout=120,
        )

        # holehe размечает каждую проверку одним из трёх статусов:
        #   [+] — аккаунт найден
        #   [-] — аккаунт точно не найден
        #   [x] — проверка не удалась (рейтлимит/блок сайта/таймаут) —
        #         это НЕ "не зарегистрирован", а "не смогли узнать".
        # Раньше [x] рисовался крестиком ✖, который визуально читался как
        # "точно нет", из-за чего реальные аккаунты на заблокированных
        # сайтах выглядели как "не найдено". Плюс сырой вывод резался по
        # 900 символам ДО сортировки — находки (`[+]`), если шли по
        # алфавиту позже, вообще не попадали в сообщение.
        # строки результата — ровно "[метка] домен", один токен без пробелов
        # и запятых. Так отсеивается легенда, которую holehe печатает перед
        # результатами ("[+] Email used, [-] Email not used, [x] Rate
        # limit") — она тоже начинается с "[+]", но доменом не является.
        result_line = re.compile(r'^\[([+\-x])\]\s+(\S+)$')
        found, not_found, errored = [], [], []
        for line in result.stdout.split("\n"):
            m = result_line.match(line.strip())
            if not m:
                continue
            mark, site = m.group(1), m.group(2)
            if mark == "+":
                found.append(site)
            elif mark == "-":
                not_found.append(site)
            else:
                errored.append(site)

        resolved_found, resolved_not_found, still_errored = [], [], []
        if retry:
            # мёржим: прошлые found/not_found остаются как есть (это уже
            # достоверный результат), а по доменам, упавшим в прошлый раз
            # с [x], берём статус из этого, второго, полного прогона.
            found_set, not_found_set, errored_set = set(found), set(not_found), set(errored)
            found = list(prev.get("found", []))
            not_found = list(prev.get("not_found", []))
            for site in prev.get("errored", []):
                if site in found_set:
                    found.append(site)
                    resolved_found.append(site)
                elif site in not_found_set:
                    not_found.append(site)
                    resolved_not_found.append(site)
                else:
                    # либо снова [x], либо модуль для этого домена пропал
                    # из вывода (версия holehe поменялась) — считаем ошибкой
                    still_errored.append(site)
            errored = still_errored

        _save_mail_cache(mail, found, not_found, errored)

        rows = [f"`{mail}`"]
        if retry:
            rows.append(f"повторный прогон — подтянуто `{len(resolved_found) + len(resolved_not_found)}` из `{len(prev.get('errored', []))}` ошибок")
        if found:
            shown = ", ".join(found[:50])
            if len(found) > 50:
                shown += f" … (+{len(found) - 50})"
            rows.append(f"✔ найдено ({len(found)}) — {shown}")
        else:
            rows.append("✔ найдено — ничего")
        if retry and resolved_found:
            rows.append(f"  ↳ новое в найденном: {', '.join(resolved_found[:50])}")
        rows.append(f"— точно не найдено — `{len(not_found)}`")
        if errored:
            shown = ", ".join(errored[:50])
            if len(errored) > 50:
                shown += f" … (+{len(errored) - 50})"
            rows.append(f"⚠ не удалось проверить ({len(errored)}, не значит «нет») — {shown}")
        else:
            rows.append("⚠ не удалось проверить — ничего")

        await safe_edit(event, fmt("почта" + (" (ретрай)" if retry else ""), rows))
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


def _find_exe(name):
    """Общий поиск консольного скрипта: PATH, затем рядом с текущим
    интерпретатором (venv/bin) — туда pip кладёт entry_points."""
    path = shutil.which(name)
    if path:
        return path
    candidate = Path(sys.executable).with_name(f"{name}.exe" if sys.platform == "win32" else name)
    if candidate.is_file():
        return str(candidate)
    return None


@command(r'^\.sherlock(?:\s+(.+))?$', name="sherlock")
async def sherlock_handler(event, client, state):
    """.sherlock <юзернейм> — поиск аккаунта на площадках через sherlock-project."""
    args = event.text.split(" ", 1)
    if len(args) < 2:
        await safe_edit(event, get_usage_instructions("sherlock"))
        return
    username = args[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", username):
        await safe_edit(event, fmt("ошибка", ["неверный формат юзернейма"]))
        return

    exe = _find_exe("sherlock")
    if exe is None:
        await safe_edit(event, fmt("ошибка", [
            "`sherlock` не найден",
            "выполни: `pip install sherlock-project` (или `pip install -r requirements.txt`) и перезапусти бота",
        ]))
        return

    try:
        # --print-all: по умолчанию sherlock печатает только найденное,
        # нам нужны и не-найденные, чтобы отделить их от реальных ошибок.
        # --no-color, иначе в выводе ANSI-коды, которые сломают парсинг.
        result = subprocess.run(
            [exe, username, "--print-all", "--no-color", "--timeout", "15"],
            capture_output=True, text=True, shell=False, timeout=180,
        )
    except subprocess.TimeoutExpired:
        await safe_edit(event, fmt("ошибка", ["sherlock не уложился в таймаут (180с)"]))
        return
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
        return

    found, not_found, errored = [], [], []
    # формат строки: "[+] Site: URL" / "[-] Site: Not Found!" / "[-] Site: Error ..."
    line_re = re.compile(r'^\[([+\-])]\s+([^:]+):\s*(.+)$')
    for line in result.stdout.split("\n"):
        m = line_re.match(line.strip())
        if not m:
            continue
        mark, site, rest = m.group(1), m.group(2).strip(), m.group(3).strip()
        if mark == "+":
            found.append((site, rest))
        elif "not found" in rest.lower():
            not_found.append(site)
        else:
            errored.append(site)

    rows = [f"`{username}`"]
    if found:
        shown = "\n".join(f"  {site} — {url}" for site, url in found[:40])
        rows.append(f"✔ найдено ({len(found)}):\n{shown}")
        if len(found) > 40:
            rows.append(f"  … (+{len(found) - 40})")
    else:
        rows.append("✔ найдено — ничего")
    rows.append(f"— не найдено — `{len(not_found)}`")
    if errored:
        rows.append(f"⚠ не удалось проверить — `{len(errored)}`")
    await safe_edit(event, fmt("sherlock", rows))


@command(r'^\.ignorant(?:\s+(.+))?$', name="ignorant")
async def ignorant_handler(event, client, state):
    """.ignorant <код_страны> <номер> — где засвечен номер (ignorant).
    Формат как у самого CLI ignorant: код страны и номер отдельно,
    например `.ignorant 7 9123456789`, без + и без пробелов внутри номера."""
    args = event.text.split()
    if len(args) != 3 or not args[1].isdigit() or not re.fullmatch(r"\d{4,15}", args[2]):
        await safe_edit(event, get_usage_instructions("ignorant"))
        return
    country_code, number = args[1], args[2]

    exe = _find_exe("ignorant")
    if exe is None:
        await safe_edit(event, fmt("ошибка", [
            "`ignorant` не найден",
            "выполни: `pip install ignorant` (или `pip install -r requirements.txt`) и перезапусти бота",
        ]))
        return

    try:
        result = subprocess.run(
            [exe, country_code, number],
            capture_output=True, text=True, shell=False, timeout=120,
        )
    except subprocess.TimeoutExpired:
        await safe_edit(event, fmt("ошибка", ["ignorant не уложился в таймаут (120с)"]))
        return
    except Exception as e:
        await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
        return

    found, not_found, errored = [], [], []
    # формат строки: "[+] Site" / "[-] Site" / "[x] Site" — без двоеточия
    # и без сообщения после имени сайта (в отличие от holehe/sherlock).
    line_re = re.compile(r'^\[([+\-x])]\s+(\S.*\S|\S)\s*$')
    for line in result.stdout.split("\n"):
        m = line_re.match(line.strip())
        if not m:
            continue
        mark, site = m.group(1), m.group(2).strip()
        if mark == "+":
            found.append(site)
        elif mark == "-":
            not_found.append(site)
        else:
            errored.append(site)

    rows = [f"`+{country_code}{number}`"]
    rows.append(f"✔ привязан — {', '.join(found) if found else 'ничего'}")
    rows.append(f"— не привязан — `{len(not_found)}`")
    if errored:
        rows.append(f"⚠ не удалось проверить — `{len(errored)}`")
    await safe_edit(event, fmt("ignorant", rows))


@command(r'^\.mosint(?:\s+(.+))?$', name="mosint")
async def mosint_handler(event, client, state):
    """.mosint <почта> — отчёт mosint (брич-базы, соцсети и т.п.) по почте."""
    args = event.text.split(" ", 1)
    if len(args) < 2:
        await safe_edit(event, get_usage_instructions("mosint"))
        return
    mail = args[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
        await safe_edit(event, fmt("ошибка", ["неверный формат почты"]))
        return

    # mosint — Go-бинарь, не pip-пакет: ставится через
    # `go install github.com/alpkeskin/mosint/v3/cmd/mosint@latest`
    exe = shutil.which("mosint")
    if exe is None:
        await safe_edit(event, fmt("ошибка", [
            "`mosint` не найден в PATH",
            "ставится через go: `go install github.com/alpkeskin/mosint/v3/cmd/mosint@latest`",
        ]))
        return

    # Можно явно задать путь в окружении сервиса через MOSINT_CONFIG.
    # Если переменная не задана, сначала ищем конфиг в HOME процесса,
    # затем — в /home/fussy (частый случай, когда бот запущен через sudo
    # или systemd от root, а конфиг создан интерактивным пользователем).
    configured_path = os.environ.get("MOSINT_CONFIG")
    if configured_path:
        mosint_config = Path(configured_path).expanduser()
    else:
        home_config = Path.home() / ".mosint.yaml"
        user_config = Path("/home/fussy/.mosint.yaml")
        mosint_config = home_config if home_config.is_file() else user_config

    if not mosint_config.is_file():
        await safe_edit(event, fmt("ошибка", [
            f"нет конфига `{mosint_config}`",
            "создай хотя бы с пустыми ключами (см. example-config.yaml в репозитории mosint)",
        ]))
        return

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "mosint.json"
        try:
            # v3: `mosint <email> -o файл.json -s -c конфиг`. -o — файл
            # вывода (не -j — это флаг из другой версии/форка). -s —
            # silent, чтобы не ловить ANSI-мусор в stdout. -c — явный путь
            # к конфигу, см. комментарий выше про $HOME.
            result = subprocess.run(
                [exe, mail, "-o", str(out_path), "-s", "-c", str(mosint_config)],
                capture_output=True, text=True, shell=False, timeout=180,
            )
        except subprocess.TimeoutExpired:
            await safe_edit(event, fmt("ошибка", ["mosint не уложился в таймаут (180с)"]))
            return
        except Exception as e:
            await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
            return

        if not out_path.is_file():
            # stderr — приоритетный источник причины падения; раньше тут
            # было `stdout or stderr`, из-за чего баннер в stdout забивал
            # собой реальную ошибку, если она вся ушла в stderr
            err = (result.stderr or "").strip()
            out = (result.stdout or "").strip()
            raw = err or out
            # для паник важно начало ("panic: ..."), не хвост стектрейса —
            # поэтому голова + немного хвоста, а не просто [-800:]
            head = (raw[:900] + " …") if len(raw) > 900 else raw
            rows = [f"`{mail}`", f"не нашёл json-отчёт (код возврата `{result.returncode}`)"]
            if "mosint.yaml" in raw or "config" in raw.lower():
                rows.append("похоже, нет конфига — создай хотя бы пустой `~/.mosint.yaml`")
            rows.append(f"{'stderr' if err else 'stdout'}:\n```\n{head}\n```")
            await safe_edit(event, fmt("mosint", rows))
            return

        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            await safe_edit(event, fmt("ошибка", ["не удалось распарсить json-отчёт mosint"]))
            return

    # схема json v3 (проверено на реальном прогоне): верхнеуровневые ключи
    # email/verified/emailrep/breachdirectory/haveibeenpwned/hunter/
    # instagram_exists/spotify_exists/twitter_exists/google_search/dns_records
    rows = [f"`{mail}`"]
    rows.append(f"синтаксис валиден — `{'да' if data.get('verified') else 'нет'}`")

    socials = [name for key, name in (
        ("instagram_exists", "instagram"),
        ("spotify_exists", "spotify"),
        ("twitter_exists", "twitter"),
    ) if data.get(key)]
    rows.append(f"аккаунты — {', '.join(socials) if socials else 'не найдено'}")

    leaks = []
    bd = data.get("breachdirectory") or {}
    if bd.get("found"):
        leaks.append(f"breachdirectory ({bd['found']})")
    hibp = data.get("haveibeenpwned")
    if isinstance(hibp, list) and hibp:
        leaks.append(f"haveibeenpwned ({len(hibp)})")
    details = (data.get("emailrep") or {}).get("details") or {}
    if details.get("credentials_leaked") or details.get("data_breach"):
        leaks.append("emailrep")
    rows.append(f"утечки — {', '.join(leaks) if leaks else 'не найдено'}")

    if (data.get("emailrep") or {}).get("suspicious"):
        rows.append("⚠ emailrep отмечает почту как подозрительную")

    await safe_edit(event, fmt("mosint", rows))


@command(r'^\.h8mail(?:\s+(.+))?$', name="h8mail")
async def h8mail_handler(event, client, state):
    """.h8mail <почта> — поиск почты в утечках/брич-базах через h8mail."""
    args = event.text.split(" ", 1)
    if len(args) < 2:
        await safe_edit(event, get_usage_instructions("h8mail"))
        return
    mail = args[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", mail):
        await safe_edit(event, fmt("ошибка", ["неверный формат почты"]))
        return

    exe = _find_exe("h8mail")
    if exe is None:
        await safe_edit(event, fmt("ошибка", [
            "`h8mail` не найден",
            "выполни: `pip install h8mail` (или `pip install -r requirements.txt`) и перезапусти бота",
        ]))
        return

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "h8mail.json"
        try:
            # без своих API-ключей (config.ini / -c) h8mail проверит только
            # бесплатные/локальные источники — этого достаточно для базовой
            # проверки "светилась ли почта хоть где-то в утечках".
            # важно: у h8mail нет "тихого" режима — "-q" это --query (тип
            # цели: email/username/ip/domain), а не флаг громкости.
            result = subprocess.run(
                [exe, "-t", mail, "-j", str(out_path)],
                capture_output=True, text=True, shell=False, timeout=180,
            )
        except subprocess.TimeoutExpired:
            await safe_edit(event, fmt("ошибка", ["h8mail не уложился в таймаут (180с)"]))
            return
        except Exception as e:
            await safe_edit(event, fmt("ошибка", [f"`{e}`"]))
            return

        if not out_path.is_file():
            err = (result.stderr or "").strip()
            out = (result.stdout or "").strip()
            raw = err or out
            head = (raw[:900] + " …") if len(raw) > 900 else raw
            await safe_edit(event, fmt("h8mail", [
                f"`{mail}`",
                f"не нашёл json-отчёт (код возврата `{result.returncode}`)",
                f"{'stderr' if err else 'stdout'}:\n```\n{head}\n```",
            ]))
            return

        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            await safe_edit(event, fmt("ошибка", ["не удалось распарсить json-отчёт h8mail"]))
            return

    targets = data.get("targets") if isinstance(data, dict) else data
    rows = [f"`{mail}`"]
    if isinstance(targets, list) and targets:
        t = targets[0]
        pwn_num = t.get("pwn_num", 0)
        rows.append(f"засветок в утечках — `{pwn_num}`")
        sources = t.get("data", {})
        if isinstance(sources, dict) and sources:
            shown = list(sources.keys())[:20]
            rows.append(f"источники — {', '.join(shown)}")
    else:
        rows.append("структура ответа не распознана, сырой json ниже")
        rows.append("```\n" + json.dumps(data, ensure_ascii=False, indent=2)[:1200] + "\n```")
    await safe_edit(event, fmt("h8mail", rows))
