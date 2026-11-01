"""Маршрутизация команд между главным аккаунтом и ассистентами.

Синтаксис префиксов (пишется сразу после точки):

    .spam 5 привет      — обычный вызов: команда уходит ассистенту
    .all spam 5 привет  — выполнить на всех ассистентах
    .#2 spam 5 привет   — выполнить на аккаунте #2
    .2 spam 5 привет    — то же самое, короткая форма
    .me delme           — выполнить на главном аккаунте
"""

import re
import time
from dataclasses import dataclass, field

DIRECTIVE_RE = re.compile(r"^\.(\+|all|me|#\d+|\d+)(?:\s+|$)", re.IGNORECASE)


@dataclass
class Route:
    kind: str = "default"          # default | all | id | me
    account_id: int | None = None

    def __str__(self):
        if self.kind == "all":
            return "все ассистенты"
        if self.kind == "id":
            return f"аккаунт #{self.account_id}"
        if self.kind == "me":
            return "главный аккаунт"
        return "по умолчанию"


@dataclass
class Resolution:
    targets: list = field(default_factory=list)
    broadcast: bool = False
    error: str | None = None


def parse_directive(text):
    """Возвращает (Route | None, текст команды без префикса)."""
    match = DIRECTIVE_RE.match(text or "")
    if not match:
        return None, text

    token = match.group(1).lower()
    remainder = text[match.end():].strip()
    if not remainder:
        # одинокий «.2» — не префикс, а неизвестная команда
        return None, text

    rest = "." + remainder

    if token in ("+", "all"):
        return Route("all"), rest
    if token == "me":
        return Route("me"), rest
    return Route("id", int(token.lstrip("#"))), rest


class AccountRouter:
    def __init__(self, bot):
        self.bot = bot
        self._rr = 0
        self._cooldown_until = {}

    # --- служебное ---
    def mark_cooldown(self, account_id, seconds):
        seconds = max(1, int(seconds))
        self._cooldown_until[int(account_id)] = time.time() + seconds
        return seconds

    def cooldown_left(self, account_id):
        until = self._cooldown_until.get(int(account_id))
        if not until:
            return 0
        left = int(until - time.time())
        if left <= 0:
            self._cooldown_until.pop(int(account_id), None)
            return 0
        return left

    def is_ready(self, account_id):
        return account_id in self.bot.running and account_id in self.bot.clients \
            and self.cooldown_left(account_id) == 0

    # --- выбор исполнителей ---
    def configured_assistants(self):
        return [a for a in self.bot.roles.assistants() if a in self.bot.accounts]

    def running_assistants(self):
        return [a for a in self.configured_assistants() if self.is_ready(a)]

    def _pick(self, candidates):
        if not candidates:
            return None
        self._rr = (self._rr + 1) % len(candidates)
        return candidates[self._rr]

    def resolve(self, master_id, route, scope, role):
        route = route or Route()
        masters = [master_id] if master_id in self.bot.accounts else []
        assistants_configured = self.configured_assistants()
        assistants_ready = self.running_assistants()

        if route.kind == "me":
            return Resolution(targets=masters)

        if route.kind == "all":
            if not assistants_ready:
                return Resolution(error=self._empty_reason(assistants_configured, assistants_ready))
            return Resolution(targets=assistants_ready, broadcast=True)

        if route.kind == "id":
            target = route.account_id
            if target not in self.bot.accounts:
                return Resolution(error=f"аккаунт #{target} не найден")
            if target not in self.bot.running:
                return Resolution(error=f"аккаунт #{target} не запущен — включи: `.start {target}`")
            left = self.cooldown_left(target)
            if left:
                return Resolution(error=f"аккаунт #{target} во флуд-вейте, ещё {left}с")
            return Resolution(targets=[target])

        # обычный вызов
        if scope == "local" or role == "solo":
            return Resolution(targets=masters)

        if assistants_ready:
            ordered = self._rotated(assistants_ready)
            return Resolution(targets=ordered)

        if assistants_configured:
            if any(a in self.bot.running for a in assistants_configured):
                return Resolution(error="все ассистенты во флуд-вейте")
            return Resolution(error="ассистенты не запущены")

        # ассистентов нет вовсе — не ломаем привычное поведение
        return Resolution(targets=masters)

    def _rotated(self, assistants):
        start = self._rr % len(assistants)
        ordered = assistants[start:] + assistants[:start]
        self._rr = (self._rr + 1) % max(1, len(assistants))
        return ordered

    @staticmethod
    def _empty_reason(configured, ready):
        if not configured:
            return "нет ни одного ассистента — назначь роль: `.role <id> assistant`"
        if not ready:
            return "ассистенты не запущены — запусти: `.start <id>`"
        return "нет доступных ассистентов"

    # --- подписи для отчётов ---
    def label(self, account_id):
        acc = self.bot.accounts.get(account_id, {})
        name = acc.get("name") or "аккаунт"
        return f"#{account_id} {name}"

    def status_line(self, account_id):
        role = self.bot.roles.get(account_id)
        if account_id not in self.bot.running:
            state = "offline"
        else:
            left = self.cooldown_left(account_id)
            state = f"flood {left}с" if left else "online"
        return f"#{account_id} · {role} · {state}"
