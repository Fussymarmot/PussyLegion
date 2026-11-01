"""Прослойки между событием Telegram и обработчиком команды."""


class _SilentMessage:
    """Заглушка для broadcast-режима: правки и удаления ничего не делают."""

    async def edit(self, *args, **kwargs):
        return self

    async def delete(self, *args, **kwargs):
        return None

    async def reply(self, *args, **kwargs):
        return self


class EventView:
    """Обёртка над Message-событием.

    Нужна для двух вещей:
      * подменить текст команды на очищенный (без префикса маршрутизации);
      * подсунуть свой pattern_match;
      * в режиме broadcast-рассылки погасить правки и удаление сообщения
        у всех исполнителей, кроме первого.
    """

    def __init__(self, event, text, match=None, silent=False, allow_delete=True):
        object.__setattr__(self, "_event", event)
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_match", match)
        object.__setattr__(self, "_silent", silent)
        object.__setattr__(self, "_allow_delete", allow_delete)

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

    # --- действия, которые зависят от режима ---
    async def edit(self, *args, **kwargs):
        if self._silent:
            return _SilentMessage()
        return await self._event.edit(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        if self._silent or not self._allow_delete:
            return None
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
