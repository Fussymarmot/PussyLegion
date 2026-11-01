"""Роли аккаунтов: главный / ассистент / самостоятельный."""

from datetime import datetime, timezone

from database import db
from config import MASTER_IDS
from logger import info

ROLES = ("master", "assistant", "solo")
ROLE_TITLES = {
    "master": "главный",
    "assistant": "ассистент",
    "solo": "самостоятельный",
}


def normalize_role(role):
    role = (role or "").strip().lower()
    return role if role in ROLES else None


class RoleStore:
    def __init__(self):
        self._cache = {}

    def load(self):
        with db() as conn:
            rows = conn.execute("SELECT account_id, role FROM account_roles").fetchall()
        self._cache = {int(r["account_id"]): r["role"] for r in rows}
        return dict(self._cache)

    def get(self, account_id, default="solo"):
        return self._cache.get(int(account_id), default)

    def set(self, account_id, role):
        account_id = int(account_id)
        role = normalize_role(role)
        if not role:
            raise ValueError(f"неизвестная роль: {role}")
        now = datetime.now(timezone.utc).isoformat()
        with db() as conn:
            conn.execute(
                """INSERT INTO account_roles (account_id, role, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET role = excluded.role,
                                                         updated_at = excluded.updated_at""",
                (account_id, role, now),
            )
        self._cache[account_id] = role
        info(f"аккаунт #{account_id}: роль «{ROLE_TITLES[role]}»")
        return role

    def delete(self, account_id):
        self._cache.pop(int(account_id), None)
        with db() as conn:
            conn.execute("DELETE FROM account_roles WHERE account_id = ?", (int(account_id),))

    def by_role(self, role):
        return sorted(a for a, r in self._cache.items() if r == role)

    def masters(self):
        return self.by_role("master")

    def assistants(self):
        return self.by_role("assistant")

    def all(self):
        return dict(self._cache)

    def ensure_defaults(self, account_ids):
        """Если роли ещё не заданы — назначает главного и ассистентов.

        Логика: MASTER_IDS из .env, иначе первый по id аккаунт становится
        главным, остальные — ассистентами. Это ровно тот сценарий, ради
        которого делался функционал: загрузил несколько аккаунтов, и
        старший управляет остальными.
        """
        account_ids = sorted(int(a) for a in account_ids)
        if not account_ids:
            return

        self.load()
        known = set(self._cache)
        # отбрасываем записи об удалённых аккаунтах
        for stale in known - set(account_ids):
            self.delete(stale)
        known &= set(account_ids)

        if known:
            # если главного не осталось (удалили/сменили вручную) — назначаем
            if not any(self._cache.get(a) == "master" for a in known):
                self.set(min(known), "master")
            # новые сессии, добавленные в обход бота, тоже получают роль
            for account_id in account_ids:
                if account_id in known:
                    continue
                role = "assistant" if any(
                    self._cache.get(a) == "master" for a in known
                ) else "master"
                self.set(account_id, role)
                known.add(account_id)
            return

        explicit = {a for a in MASTER_IDS if a in account_ids}
        if explicit:
            masters = explicit
        else:
            masters = {account_ids[0]}

        for account_id in account_ids:
            self._cache[account_id] = "master" if account_id in masters else "assistant"

        now = datetime.now(timezone.utc).isoformat()
        with db() as conn:
            for account_id, role in self._cache.items():
                conn.execute(
                    """INSERT OR REPLACE INTO account_roles (account_id, role, updated_at)
                       VALUES (?, ?, ?)""",
                    (account_id, role, now),
                )
        info(
            "роли по умолчанию: главный "
            + ", ".join(f"#{a}" for a in sorted(masters))
            + (f", ассистенты {', '.join(f'#{a}' for a in account_ids if a not in masters)}"
               if len(account_ids) > len(masters) else "")
        )

    def default_role_for_new(self):
        """Новый аккаунт: ассистент, если главный уже есть, иначе главный."""
        if any(r == "master" for r in self._cache.values()):
            return "assistant"
        return "master"

    def describe(self, account_id):
        return ROLE_TITLES.get(self.get(account_id), self.get(account_id))
