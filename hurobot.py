import asyncio
import getpass
import sys
import re
import importlib
import time
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
    MAIN_SESSION, PROXY_ENABLED, PROXY_TYPE, PROXY_HOST,
    MASTER_IDS, get_telethon_proxy,
)

from logger import info, success, error, warning, exception, log
from database import db, init_db, cleanup_old_data

from commands import get_backgrounds
from core.dispatcher import attach as attach_dispatcher


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


def _session_variants(base):
    """Все файлы, которые SQLite/Telethon могут создать рядом с сессией."""
    return [Path(str(base) + suffix) for suffix in ("", "-journal", "-wal", "-shm")]


def _remove_session(base):
    for path in _session_variants(base):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            exception(f"удаление сессии {path}")


def _legacy_sessions():
    """Старые сессии мультиаккаунтного режима: account_1.session и т.п."""
    found = []
    for f in SESSION_DIR.iterdir():
        if not f.name.startswith(SESSION_PREFIX) or f.suffix != ".session":
            continue
        num = f.stem[len(SESSION_PREFIX):]
        if num.isdigit():
            found.append((int(num), f))
    return sorted(found)


def _legacy_master_id():
    """Роль master из старой базы — приоритетный кандидат в основную сессию."""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT account_id FROM account_roles WHERE role = 'master' "
                "ORDER BY account_id LIMIT 1"
            ).fetchone()
        return int(row["account_id"]) if row else None
    except Exception:
        return None


def migrate_sessions():
    """Оставляет одну основную сессию, остальные удаляет.

    Порядок выбора: master из старой базы → MASTER_IDS из .env → самый
    первый account_N. Если main.session уже есть, старые файлы просто
    вычищаются. Возвращает путь к основной сессии.
    """
    legacy = _legacy_sessions()

    if MAIN_SESSION.exists() or not legacy:
        for _, path in legacy:
            _remove_session(path)
        return str(MAIN_SESSION)

    wanted = _legacy_master_id()
    if wanted is None and MASTER_IDS:
        wanted = min(MASTER_IDS)

    chosen_num, chosen = legacy[0]
    if wanted is not None:
        for num, path in legacy:
            if num == wanted:
                chosen_num, chosen = num, path
                break

    info(f"миграция: основной аккаунт #{chosen_num} ({chosen.name})")

    for suffix in ("-journal", "-wal", "-shm"):
        src = Path(str(chosen) + suffix)
        if src.exists():
            try:
                src.replace(Path(str(MAIN_SESSION) + suffix))
            except Exception:
                exception(f"перенос {src}")

    try:
        chosen.replace(MAIN_SESSION)
    except Exception:
        exception(f"перенос {chosen}")
        return str(chosen)

    for num, path in legacy:
        if path == chosen:
            continue
        _remove_session(path)
        warning(f"удалена лишняя сессия #{num}")

    return str(MAIN_SESSION)


class ClientState:
    def __init__(self):
        self.animated_chats = set()  # id чатов, где включена .ani — своя анимация на чат
        self.active_animation = None
        self.animation_lock = asyncio.Lock()
        self.pending_confirmation = {}
        self.current_message_id = None
        self.last_user_activity = 0
        self.bot = None
        self.owner_id = None

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


class HuroBot:
    def __init__(self):
        self.client = None
        self.state = None
        self.me = None
        self.started_at = None

    async def start(self, session_path=None):
        session_path = session_path or migrate_sessions()
        self.client = make_client(session_path)

        try:
            await asyncio.wait_for(self.client.connect(), timeout=20)
        except asyncio.TimeoutError:
            error("таймаут подключения к Telegram")
            return False
        except Exception as e:
            exception("start.connect")
            error(f"не удалось подключиться: {e}")
            return False

        try:
            if not await self.client.is_user_authorized():
                warning("сессия не авторизована — вход из консоли")
                if not await self.console_login():
                    return False
        except Exception:
            exception("start.authorize")
            return False

        self.me = await self.client.get_me()
        self.state = ClientState()
        self.state.bot = self
        self.state.owner_id = self.me.id

        attach_dispatcher(self.client, self.state)

        @self.client.on(events.NewMessage(incoming=True))
        async def _stats(event):
            await stats_collector(event)

        loaded = 1
        for register_fn in get_backgrounds():
            try:
                register_fn(self.client, self.state)
                loaded += 1
            except Exception:
                exception(f"Загрузка {register_fn.__name__}")

        self.started_at = time.time()
        name = self.me.username or self.me.first_name or str(self.me.id)
        success(f"аккаунт {name} (#{self.me.id}) запущен, обработчиков: {loaded}")
        return True

    async def console_login(self):
        """Первый вход: номер, код, при необходимости пароль 2FA."""
        ask = lambda prompt: asyncio.to_thread(input, prompt)

        while True:
            raw = (await ask("номер телефона (+79123456789): ")).strip()
            phone = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if not phone.startswith("+"):
                phone = "+" + phone.lstrip("+")
            if not PHONE_RE.match(phone):
                error("неверный формат номера, пример: +79123456789")
                continue
            try:
                sent = await self.client.send_code_request(phone)
                break
            except PhoneNumberInvalidError:
                error("неверный номер")
            except Exception as e:
                exception("console_login.send_code")
                error(f"не удалось отправить код: {e}")

        need_password = False
        while True:
            if not need_password:
                code = "".join(ch for ch in (await ask("код из Telegram: ")) if ch.isdigit())
                try:
                    await self.client.sign_in(phone, code=code, phone_code_hash=sent.phone_code_hash)
                    return True
                except SessionPasswordNeededError:
                    need_password = True
                    continue
                except PhoneCodeInvalidError:
                    error("неверный код")
                    continue
                except Exception as e:
                    exception("console_login.sign_in")
                    error(f"не удалось войти: {e}")
                    return False
            else:
                password = await asyncio.to_thread(getpass.getpass, "пароль 2FA: ")
                try:
                    await self.client.sign_in(password=password)
                    return True
                except PasswordHashInvalidError:
                    error("неверный пароль")
                    continue
                except Exception as e:
                    exception("console_login.password")
                    error(f"не удалось войти: {e}")
                    return False

    async def run(self):
        """Держит соединение живым, переподключаясь при обрывах."""
        while True:
            try:
                await self.client.run_until_disconnected()
                break
            except (ConnectionError, TimeoutError, TimedOutError):
                warning("соединение потеряно, переподключение...")
                await asyncio.sleep(5)
                try:
                    await self.client.connect()
                except Exception:
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                exception("run")
                await asyncio.sleep(10)
                try:
                    await self.client.connect()
                except Exception:
                    await asyncio.sleep(30)

    async def stop(self):
        await self.state.stop_animation() if self.state else None
        if self.client and self.client.is_connected():
            try:
                await self.client.disconnect()
            except Exception:
                pass
        if self.me:
            warning(f"аккаунт {self.me.username or self.me.first_name or self.me.id} остановлен")


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

    # миграция идёт до init_db: тот удаляет старую таблицу ролей, по которой
    # выбирается основная сессия
    session_path = migrate_sessions()

    init_db()
    cleanup_old_data(days=30)

    bot = HuroBot()
    if not await bot.start(session_path):
        error("бот не запущен")
        return

    print()
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}аккаунт:{COLORS['reset']} "
          f"{COLORS['accent4']}{bot.me.username or bot.me.first_name or bot.me.id}{COLORS['reset']} "
          f"{COLORS['accent2']}(#{bot.me.id}){COLORS['reset']}")
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}управление — командами в Telegram, список: .help{COLORS['reset']}")
    print(f"  {COLORS['accent2']}→{COLORS['reset']} {COLORS['info']}Ctrl+C — остановить бота{COLORS['reset']}")
    print()

    try:
        await bot.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await bot.stop()


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
