"""Прослойка между событием Telegram и обработчиком команды."""


class EventView:
    """Обёртка над Message-событием.

    Нужна, чтобы подсунуть обработчику pattern_match от команды: у
    настоящего события Telethon такого поля нет.
    """

    def __init__(self, event, text, match=None):
        object.__setattr__(self, "_event", event)
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_match", match)

    # --- подменённые поля ---
    @property
    def text(self):
        return self._text

    @property
    def raw_text(self):
        return self._text

    @property
    def pattern_match(self):
        return self._match

    # --- действия ---
    async def edit(self, *args, **kwargs):
        return await self._event.edit(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        return await self._event.delete(*args, **kwargs)

    async def respond(self, *args, **kwargs):
        return await self._event.respond(*args, **kwargs)

    async def reply(self, *args, **kwargs):
        return await self._event.reply(*args, **kwargs)

    # --- всё остальное прозрачно ---
    def __getattr__(self, name):
        return getattr(self._event, name)

    def __setattr__(self, name, value):
        setattr(self._event, name, value)
