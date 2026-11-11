from commands import command
from commands.utils import safe_edit, COMMAND_INFO, fmt


HELP_TEXT = """**✦ помощь**

**✧ основное**
  `.help` — список команд
  `.help <команда>` — справка по команде
  `.ping` — проверка отклика
  `.bot` — о боте
  `.logs [N]` — логи в избранное

**✧ медиа**
  `.save` — сохранить T-медиа (ответом)
  `.qr <текст>` — QR-код
  `.clone <url>` — клон поста в текущий чат
  `.video <ссылка>` — видео с YouTube / TikTok в чат

**✧ модерация (только в лс)**
  `.mute` — мут в лс / ответом
  `.mute 1h` — на час
  `.unmute` — снять мут
  `.mutes` — список
  `.muteword <слово>` — удалять сообщения со словом
  `.unmuteworlds` — снять мут со всех стоп-слов
  `.tagall` — упомянуть всех
  `.up <N>` — переупоминания (ответом)
  `.iter [-n]` — экспорт участников

**✧ osint**
  `.data` — инфо о юзере (ответом)
  `.bday` — дата рождения и дни до/после (своя или ответом)
  `.osint <значение> [-r]` — IP / телефон / почта (-r — ретрай ошибок)
  `.sherlock <юзернейм>` — поиск по нику
  `.ignorant <код> <номер>` — где привязан номер
  `.mosint <почта>` — отчёт по почте
  `.h8mail <почта>` — почта в брич-базах
  `.whois <домен>` — WHOIS

**✧ спам**
  `.spam <N> <текст>` — рассылка
  `.sti <N>` — стикеры (ответом на стикер)
  `.crash` — краш-стикеры
  `.bomb <номер>` — бомбер

**✧ утилиты**
  `.ani on/off` — анимация набора (в этом чате)
  `.random 1-100` — случайное число
  `.dice` — кубик

**PussyLegion**
"""


@command(r'^\.help(?:\s+([a-zA-Z]+))?$', name="help")
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
