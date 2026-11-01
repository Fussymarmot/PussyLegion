"""Реестр команд.

Каждая команда описывается регуляркой и функцией-обработчиком:

    @command(r'^\\.ping$', name="ping", scope="local")
    async def ping_handler(event, client, state):
        ...

`scope`:
    local  — выполняется на аккаунте, который поймал команду;
    remote — по умолчанию уходит ассистентам;
    auto   — ассистентам, если они есть, иначе локально.

`@background` помечает функцию, которая вешает свои слушатели на клиент
(автосохранение медиа, мьюты, анимация и т.п.). Такие слушатели вешаются
только на главные и самостоятельные аккаунты — ассистенты остаются
чистыми исполнителями.
"""

import re

SCOPES = ("local", "remote", "auto")


class Command:
    __slots__ = ("name", "pattern", "handler", "scope", "description")

    def __init__(self, name, pattern, handler, scope="auto", description=""):
        self.name = name
        self.pattern = pattern
        self.handler = handler
        self.scope = scope
        self.description = description

    def match(self, text):
        return self.pattern.match(text)

    def __repr__(self):
        return f"<Command {self.name} scope={self.scope}>"


COMMANDS = []
_BY_NAME = {}
BACKGROUND = []


def command(pattern, name=None, scope="auto", description=""):
    if scope not in SCOPES:
        raise ValueError(f"неизвестный scope: {scope}")

    compiled = re.compile(pattern, re.IGNORECASE)

    def decorator(fn):
        cmd_name = (name or fn.__name__.removesuffix("_handler")).lower()
        if cmd_name in _BY_NAME:
            raise ValueError(f"команда .{cmd_name} уже зарегистрирована")
        cmd = Command(cmd_name, compiled, fn, scope=scope, description=description)
        COMMANDS.append(cmd)
        _BY_NAME[cmd_name] = cmd
        return fn

    return decorator


def background(fn):
    BACKGROUND.append(fn)
    return fn


def get_commands():
    return list(COMMANDS)


def get_command(name):
    return _BY_NAME.get((name or "").lower())


def get_backgrounds():
    return list(BACKGROUND)


def find_command(text):
    """Возвращает (Command, Match) для текста вида `.spam 5 привет`."""
    if not text or not text.startswith("."):
        return None, None
    head = text[1:].split(None, 1)
    if not head:
        return None, None
    cmd = get_command(head[0])
    if not cmd:
        return None, None
    match = cmd.match(text)
    if not match:
        return None, None
    return cmd, match
