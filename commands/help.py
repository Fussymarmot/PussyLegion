from commands import command
from commands.utils import safe_edit, COMMAND_INFO, fmt


HELP_TEXT = """**✦ помощь**

**✧ маршрутизация**
  `.spam ...` — выполнит ассистент
  `.all spam ...` — все ассистенты
  `.2 spam ...` — аккаунт #2
  `.me delme` — главный аккаунт

**✧ аккаунты**
  `.accounts` — роли и статус
  `.role [id] [master/assistant/solo]`
  `.start <id>` · `.stop <id>` — запуск / остановка
  `.autostart [id] [on/off]` — автозапуск
  `.addacc <номер>` → `.code 1-2-3-4-5` → `.pass <2FA>`
  `.delacc <id>` — удалить аккаунт
  `.logs [N]` — логи в избранное
  `.delme` — очистка переписки

**✧ основное**
  `.help` — список команд
  `.ping` — проверка
  `.bot` — о боте

**✧ медиа**
  `.save` — сохранить T-медиа
  `.qr <текст>` — QR-код
  `.clone <url>` — клон поста
  `.like [эмодзи]` — реакция ответом на соо, со всех аккаунтов
  `.like <t.me ссылка> [эмодзи]` — реакция по ссылке
  `.joinall` — завести все аккаунты в текущий чат
  `.joinall <ссылка>` — завести все аккаунты по ссылке
  `.vote` — ответом на опрос: показать варианты
  `.vote <номер>` — проголосовать со всех аккаунтов
  `.vote <t.me ссылка> [номер]` — то же, но опрос по ссылке

**✧ модерация (только в лс)**
  `.mute` — мут в лс / ответом
  `.mute 1h` — на час
  `.unmute` — снять мут
  `.mutes` — список
  `.muteword <слово>` — мут по слову
  `.tagall` — упомянуть всех
  `.up <N>` — переупоминания
  `.iter [-n]` — экспорт участников

**✧ osint**
  `.data` — инфо о юзере
  `.osint <значение>` — IP / телефон / почта
  `.whois <домен>` — WHOIS

**✧ спам**
  `.spam <N> <текст>` — рассылка
  `.sti <N>` — стикеры
  `.crash` — краш-стикеры
  `.bomb <номер>` — бомбер

**✧ утилиты**
  `.ani on/off` — анимация набора (только в этом чате)
  `.random 1-100` — случайное число
  `.dice` — кубик

**PussyLegion**
"""


@command(r'^\.help(?:\s+([a-zA-Z]+))?$', name="help", scope="local")
async def help_handler(event, client, state):
    name = event.pattern_match.group(1)
    if not name:
        await safe_edit(event, HELP_TEXT)
        return
    name = name.lower()
    if name not in COMMAND_INFO:
        await safe_edit(event, fmt("ошибка", [f"команда `{name}` не найдена"]))
        return
    info = COMMAND_INFO[name]
    text = (
        f"**✦ команда: {name}**\n\n"
        f"**✧** описание — {info['description']}\n"
        f"**✧** синтаксис — {info['syntax']}\n"
        f"**✧** пример — {info['example']}\n\n"
        f"**PussyLegion**"
    )
    await safe_edit(event, text)
