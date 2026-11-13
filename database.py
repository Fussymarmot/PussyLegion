import sqlite3
from contextlib import contextmanager
from config import DB_PATH
from logger import log


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS mutes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                until TEXT,
                reason TEXT,
                shadow INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, target_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS mute_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, word)
            )
        """)

        # архив: id форум-группы, главной темы и темы каждого собеседника
        c.execute("""
            CREATE TABLE IF NOT EXISTS archive_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS archive_topics (
                user_id INTEGER PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # статистика не ведётся: таблица от старой реализации только
        # занимала место
        c.execute("DROP TABLE IF EXISTS stats")
        c.execute("DROP TABLE IF EXISTS stats_old")

        # кэш удалённых сообщений живёт в памяти (commands/media.py);
        # таблица от старой реализации только занимала место
        c.execute("DROP TABLE IF EXISTS msg_cache")

        # таблицы старого мультиаккаунтного режима больше не используются
        c.execute("DROP TABLE IF EXISTS account_roles")
        c.execute("DROP TABLE IF EXISTS settings")

        # чаты, где архивация обычных сообщений отключена вручную
        # (исчезающие фото всё равно сохраняются)
        c.execute("""
            CREATE TABLE IF NOT EXISTS archive_skip (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)

    log.info("База данных инициализирована")