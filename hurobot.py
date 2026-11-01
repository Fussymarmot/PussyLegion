import asyncio
import getpass
import sys
import re
import importlib
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
)
from telethon.errors.rpcerrorlist import TimedOutError

from config import (
    API_ID, API_HASH, VERSION, COLORS, SESSION_DIR, SESSION_PREFIX,
    TEMP_SESSION, PROXY_ENABLED, PROXY_TYPE, PROXY_HOST,
    AUTOSTART_IDS,
    get_telethon_proxy,
)

from logger import info, success, error, warning, exception, log
from database import db, init_db, cleanup_old_data

from commands import get_backgrounds
from core.dispatcher import attach as attach_dispatcher
from core.roles import RoleStore
from core.router import AccountRouter


PHONE_RE = re.compile(r'^\+\d{8,15}$')

_LOADED_MODULES = 0


def load_command_modules():
    global _LOADED_MODULES
    commands_dir = Path(__file__).parent / "commands"
    if not commands_dir.exists():
        return
    loaded = 0
    for f in commands_dir.iterdir():
        if f.suffix != ".py" or f.name.startswith("_"):
            continue
        module_name = f"commands.{f.stem}"
        try:
            importlib.import_module(module_name)
            loaded += 1
        except Exception as e:
            log.exception(f"загрузка {module_name}: {e}")
    _LOADED_MODULES = loaded


def print_block(title, icon="●", color=None):
    color = color or COLORS["accent1"]
    width = 54
    print(f"{COLORS['accent2']}┏{'━' * width}┓{COLORS['reset']}")
    padding = width - len(title) - 4
    print(f"{COLORS['accent2']}┃{COLORS['reset']} {color}{icon}{COLORS['reset']} {COLORS['header']}{title}{COLORS['reset']}"
          f"{' ' * padding}{COLORS['accent2']}┃{COLORS['reset']}")
    print(f"{COLORS['accent2']}┗{'━' * width}┛{COLORS['reset']}")


def print_kv(key, value, color=None):
    color = color or COLORS["input"]
    print(f"  {COLORS['accent2']}›{COLORS['reset']} {COLORS['info']}{key}:{COLORS['reset']} {color}{value}{COLORS['reset']}")


def banner():
    width = 54
    title = "P U S S Y L E G I O N"
    print(f"{COLORS['accent2']}┏{'━' * width}┓{COLORS['reset']}")
    pad = (width - len(title)) // 2
    print(f"{COLORS['accent2']}┃{COLORS['reset']}{' ' * pad}{COLORS['accent1']}{title}{COLORS['reset']}"
          f"{' ' * (width - len(title) - pad)}{COLORS['accent2']}┃{COLORS['reset']}")
    print(f"{COLORS['accent2']}┗{'━' * width}┛{COLORS['reset']}")
    print(f"{COLORS['header']}                    {VERSION}{COLORS['reset']}")


class HuroBot:
    def __init__(self):
        self.accounts = {}
        self.running = set()
        self.clients = {}
        self.pending_auth = {}
        self.pending_phone = None
        self.autostart = self.load_autostart()
        self.started_at = {}
        self.starting = set()
        self.roles = RoleStore()
        self.router = AccountRouter(self)

    @staticmethod
    def load_autostart():
        """Сохранённый список (БД) главнее значения AUTOSTART из .env."""
        try:
            with db() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = 'autostart'").fetchone()
        except Exception:
            row = None
        if row is None:
            return set(AUTOSTART_IDS)
        return {int(x) for x in (row["value"] or "").split(",") if x.strip().isdigit()}

    def set_autostart(self, num, enabled):
        if enabled:
            self.autostart.add(num)
        else:
            self.autostart.discard(num)
        self.save_autostart()

    def save_autostart(self):
        value = ",".join(str(x) for x in sorted(self.autostart))
        with db() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('autostart', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )

    async def load_accounts(self):
        self.accounts = {}
        for f in SESSION_DIR.iterdir():
            if not f.name.startswith(SESSION_PREFIX) or f.suffix != ".session":
                continue
            try:
                client = make_client(str(f))
                async with client:
                    if await client.is_user_authorized():
                        me = await client.get_me()
                        account_id = int(f.stem.split("_")[1])
                        self.accounts[account_id] = {
                            "phone": me.phone,
                            "name": me.username or me.first_name or f"Аккаунт {account_id}",
                            "session": str(f),
                        }
            except Exception:
                exception(f"Загрузка сессии {f.name}")

        self.roles.ensure_defaults(self.accounts.keys())

    async def start_account(self, num):
        if num in self.running:
            return {"ok": True, "message": "уже запущен"}
        if num in self.starting:
            return {"ok": False, "error": "запуск уже идёт"}

        acc = self.accounts.get(num)
        if not acc:
            return {"ok": False, "error": "аккаунт не найден"}

        self.starting.add(num)

        try:
            name = acc["name"]

            client = make_client(acc["session"])

            try:
                await asyncio.wait_for(client.connect(), timeout=15)
            except asyncio.TimeoutError:
                warning(f"аккаунт #{num}: таймаут подключения")
                return {"ok": False, "error": "таймаут подключения"}
            except Exception as e:
                exception("start_account.connect")
                warning(f"аккаунт #{num}: ошибка {e}")
                return {"ok": False, "error": str(e)}

            if not await client.is_user_authorized():
                warning(f"аккаунт #{num}: сессия не авторизована")
                await client.disconnect()
                return {"ok": False, "error": "сессия не авторизована"}

            state = ClientState()
            state.bot = self
            state.account_id = num
            state.role = self.roles.get(num)
            client._hurobot_state = state

            loaded = 0
            if state.role != "assistant":
                @client.on(events.NewMessage(incoming=True))
                async def _stats(event):
                    await stats_collector(event)

                attach_dispatcher(client, state, self, num)
                loaded += 1

                for register_fn in get_backgrounds():
                    try:
                        register_fn(client, state)
                        loaded += 1
                    except Exception:
                        exception(f"Загрузка {register_fn.__name__}")

            # ассистент — чистый исполнитель: ни команд, ни фоновых слушателей
            self.clients[num] = client
            self.running.add(num)
            self.started_at[num] = asyncio.get_event_loop().time()

            role_title = "ассистент" if state.role == "assistant" else state.role
            success(
                f"аккаунт #{num} ({name}) запущен — {role_title}"
                + (f", обработчиков: {loaded}" if loaded else "")
            )

            asyncio.create_task(self._run_client(num, client))

            return {"ok": True, "message": f"аккаунт {name} запущен"}

        finally:
            self.starting.discard(num)

    async def _run_client(self, num, client):
        acc = self.accounts.get(num, {})
        name = acc.get("name", f"#{num}")
        try:
            while num in self.running:
                try:
                    await client.run_until_disconnected()
                    break
                except (ConnectionError, TimeoutError, TimedOutError):
                    warning(f"аккаунт {name}: соединение потеряно, переподключение...")
                    await asyncio.sleep(5)
                    try:
                        await client.connect()
                    except Exception:
                        await asyncio.sleep(30)
        except Exception:
            exception(f"_run_client {num}")
        finally:
            if self.clients.get(num) is client:
                self.running.discard(num)
                self.clients.pop(num, None)
                self.started_at.pop(num, None)
                self.starting.discard(num)
                warning(f"аккаунт {name}: остановлен")

    async def stop_account(self, num):
        acc = self.accounts.get(num, {})
        name = acc.get("name", f"#{num}")
        self.starting.discard(num)
        if num in self.running:
            self.running.discard(num)
        client = self.clients.pop(num, None)
        if client:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass
        self.started_at.pop(num, None)

        warning(f"аккаунт #{num} ({name}) остановлен")
        return {"ok": True}

    async def set_role(self, num, role):
        """Меняет роль аккаунта и при необходимости перезапускает его.

        Назначение нового главного автоматически переводит старого
        в ассистенты — так не остаётся двух управляющих аккаунтов.
        """
        from core.roles import ROLES

        if num not in self.accounts:
            return {"ok": False, "error": "аккаунт не найден"}
        normalized = (role or "").strip().lower()
        if normalized not in ROLES:
            return {"ok": False, "error": "роли: master, assistant, solo"}

        if normalized == "assistant" and self.roles.masters() == [num]:
            return {"ok": False, "error": "это единственный главный — сначала назначь другого"}

        changed = {num}
        if normalized == "master":
            for other in self.roles.masters():
                if other != num:
                    self.roles.set(other, "assistant")
                    changed.add(other)

        new_role = self.roles.set(num, normalized)

        restarted = []
        for account_id in changed:
            if account_id in self.running:
                await self.stop_account(account_id)
                await asyncio.sleep(1)
                await self.start_account(account_id)
                restarted.append(account_id)
        return {"ok": True, "role": new_role, "restarted": restarted}

    async def delete_account(self, num):
        acc = self.accounts.get(num)
        if not acc:
            return {"ok": False, "error": "аккаунт не найден"}

        await self.stop_account(num)

        try:
            p = Path(acc["session"])
            if p.exists():
                p.unlink()
        except Exception:
            exception(f"Удаление {acc['session']}")

        self.accounts.pop(num, None)
        self.set_autostart(num, False)
        self.roles.delete(num)

        warning(f"удалён аккаунт #{num}")
        return {"ok": True}

    async def start_add_account(self, phone):
        clean = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not clean.startswith("+"):
            clean = "+" + clean.lstrip("+")
        if not PHONE_RE.match(clean):
            return {"ok": False, "error": "неверный формат номера. пример: +79123456789"}
        phone = clean

        p = Path(TEMP_SESSION)
        for suffix in ("", "-journal", "-wal", "-shm"):
            fp = Path(str(p) + suffix)
            if fp.exists():
                try:
                    fp.unlink()
                except PermissionError:
                    pass

        client = None
        try:
            info(f"добавление аккаунта {phone}")

            client = make_client(TEMP_SESSION)
            await asyncio.wait_for(client.connect(), timeout=15)
            sent = await client.send_code_request(phone)

            self.pending_auth[phone] = {
                "client": client,
                "phone_code_hash": sent.phone_code_hash,
                "stage": "code",
            }

            success(f"код отправлен на {phone}")
            return {"ok": True, "stage": "code", "phone": phone}

        except PhoneNumberInvalidError:
            if client and client.is_connected():
                await client.disconnect()
            return {"ok": False, "error": "неверный номер"}
        except asyncio.TimeoutError:
            if client and client.is_connected():
                await client.disconnect()
            return {"ok": False, "error": "таймаут подключения"}
        except Exception as e:
            exception("start_add_account")
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return {"ok": False, "error": str(e)}

    async def complete_add_account(self, phone, code=None, password=None):
        pending = self.pending_auth.get(phone)
        if not pending:
            return {"ok": False, "error": "сессия истекла"}

        client = pending["client"]

        try:
            if password is None:
                try:
                    await client.sign_in(phone, code=code, phone_code_hash=pending["phone_code_hash"])
                except SessionPasswordNeededError:
                    info("требуется 2fa пароль")
                    return {"ok": False, "need_password": True}
            else:
                try:
                    await client.sign_in(password=password)
                except PasswordHashInvalidError:
                    warning("неверный 2fa пароль")
                    return {"ok": False, "error": "неверный пароль"}

            me = await client.get_me()
            account_id = max(self.accounts.keys(), default=0) + 1
            new_session = SESSION_DIR / f"{SESSION_PREFIX}{account_id}.session"

            await client.disconnect()
            await asyncio.sleep(1)

            p = Path(TEMP_SESSION)
            if p.exists():
                if new_session.exists():
                    new_session.unlink()
                p.rename(new_session)

            self.accounts[account_id] = {
                "phone": phone,
                "name": me.first_name or me.username or f"Аккаунт {account_id}",
                "session": str(new_session),
            }

            new_role = self.roles.default_role_for_new()
            self.roles.set(account_id, new_role)

            self.pending_auth.pop(phone, None)
            success(f"аккаунт {me.first_name} добавлен ({new_role})")
            return {"ok": True}

        except PhoneCodeInvalidError:
            return {"ok": False, "error": "неверный код"}
        except Exception as e:
            exception("complete_add_account")
            return {"ok": False, "error": str(e)}

    async def autostart_accounts(self):
        # без панели главный/самостоятельный аккаунт должен подниматься сам —
        # иначе некому принимать команды. Ассистенты — по списку автостарта
        # или по требованию (их поднимает диспетчер).
        wanted = set(self.autostart) | {
            a for a in self.accounts if self.roles.get(a) != "assistant"
        }
        for num in sorted(wanted):
            if num in self.accounts and num not in self.running and num not in self.starting:
                await self.start_account(num)
                await asyncio.sleep(1)


class ClientState:
    def __init__(self):
        self.typing_animation = False
        self.active_animation = None
        self.animation_lock = asyncio.Lock()
        self.pending_confirmation = {}
        self.current_message_id = None
        self.last_user_activity = 0
        self.bot = None
        self.account_id = None
        self.role = "solo"

    async def stop_animation(self):
        async with self.animation_lock:
            if self.active_animation:
                self.active_animation.cancel()
                try:
                    await self.active_animation
                except Exception:
                    pass
                self.active_animation = None
            self.current_message_id = None


def ensure_api_credentials():
    """Свои API_ID / API_HASH обязательны (получить: https://my.telegram.org)."""
    import config
    if config.API_ID and config.API_HASH:
        return
    print("нужны собственные api-ключи с https://my.telegram.org -> API development tools")
    api_id = input("API_ID: ").strip()
    api_hash = input("API_HASH: ").strip()
    if not api_id.isdigit() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        error("неверный формат API_ID / API_HASH")
        sys.exit(1)
    env_path = Path(__file__).parent / ".env"
    lines = []
    if env_path.exists():
        lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
                 if not l.startswith(("API_ID=", "API_HASH="))]
    lines += [f"API_ID={api_id}", f"API_HASH={api_hash}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except Exception:
        pass
    config.API_ID, config.API_HASH = int(api_id), api_hash
    globals()["API_ID"], globals()["API_HASH"] = config.API_ID, config.API_HASH


def make_client(session_path):
    proxy, connection = get_telethon_proxy()
    kwargs = {"proxy": proxy} if proxy else {}
    if connection:
        kwargs["connection"] = connection
    return TelegramClient(session_path, API_ID, API_HASH, **kwargs)


async def stats_collector(event):
    from database import db
    try:
        if not event.is_private:
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with db() as conn:
            conn.execute(
                """INSERT INTO stats (user_id, chat_id, date, msg_count) VALUES (?, ?, ?, 1)
                   ON CONFLICT(user_id, chat_id, date) DO UPDATE SET msg_count = msg_count + 1""",
                (event.sender_id, event.chat_id, today),
            )
    except Exception:
        pass


async def console_add_first_account(bot):
    """Первый аккаунт добавляется из консоли — команд ещё некому принимать."""
    ask = lambda prompt: asyncio.to_thread(input, prompt)

    print()
    info("аккаунтов нет — добавим первый, он станет главным")
    phone = None
    while not phone:
        raw = (await ask("номер телефона (+79123456789): ")).strip()
        result = await bot.start_add_account(raw)
        if result.get("ok"):
            phone = result["phone"]
        else:
            error(result.get("error", "не удалось отправить код"))

    need_password = False
    while True:
        if not need_password:
            code = "".join(ch for ch in (await ask("код из Telegram: ")) if ch.isdigit())
            result = await bot.complete_add_account(phone, code=code)
        else:
            password = await asyncio.to_thread(getpass.getpass, "пароль 2FA: ")
            result = await bot.complete_add_account(phone, password=password)

        if result.get("ok"):
            return True
        if result.get("need_password"):
            need_password = True
            continue
        error(result.get("error", "не удалось войти"))
        if result.get("error") == "сессия истекла":
            return False


async def main():
    banner()
    print()

    load_command_modules()
    info(f"загружено модулей команд: {_LOADED_MODULES}")

    ensure_api_credentials()
    if PROXY_ENABLED:
        info(f"прокси: {PROXY_TYPE}://{PROXY_HOST}")
    else:
        info("прокси не задан")

    init_db()
    cleanup_old_data(days=30)

    bot = HuroBot()
    await bot.load_accounts()

    if not bot.accounts:
        if not await console_add_first_account(bot):
            error("аккаунт не добавлен, запусти бота заново")
            return
        await bot.load_accounts()

    print()
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}аккаунтов:{COLORS['reset']} {COLORS['accent3']}{len(bot.accounts)}{COLORS['reset']}")
    masters = bot.roles.masters()
    assistants = bot.roles.assistants()
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}главный:{COLORS['reset']} "
          f"{COLORS['accent4']}{', '.join(f'#{m}' for m in masters) or '—'}{COLORS['reset']}"
          f" {COLORS['accent2']}·{COLORS['reset']} {COLORS['info']}ассистенты:{COLORS['reset']} "
          f"{COLORS['accent3']}{', '.join(f'#{a}' for a in assistants) or '—'}{COLORS['reset']}")
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}управление — командами в Telegram, список: .help{COLORS['reset']}")
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}Ctrl+C — остановить бота{COLORS['reset']}")
    print()

    await bot.autostart_accounts()

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        for num in list(bot.running):
            await bot.stop_account(num)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        info("завершено")
        sys.exit(0)
    except Exception:
        exception("main")
        sys.exit(1)