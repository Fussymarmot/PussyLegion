<div align="center">

  <img src="https://i.ibb.co/v08LpSt/IMG-20260409-190254-955.jpg" width="150" alt="PussyLegion Logo" style="border-radius: 18px;" />

  <h1 align="center" style="font-style: italic; color: #ff4500;">PussyLegion</h1>

  <p align="center">
    <strong>Многофункциональный Telegram-userbot для управления аккаунтами, автоматизации и OSINT</strong>
  </p>

  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=20&duration=3000&pause=1000&color=FF4500&center=true&vCenter=true&width=700&lines=Multi-Account+Management;Media+Save+%2B+Auto-Archive;OSINT+Toolkit;Spam+%2B+Utilities;Termux+Ready" alt="Typing SVG" />

  <br>

  <img src="https://img.shields.io/badge/PussyLegion-v1.4.0-ff4500?style=for-the-badge" alt="Version"/>
  <a href="https://t.me/hurodev">
    <img src="https://img.shields.io/badge/Telegram-@hurodev-26A5E4?style=for-the-badge&color=ff4500&logo=telegram&logoColor=white" alt="Telegram"/>
  </a>

</div>

<br>

### <img src="https://api.iconify.design/ph:lightning-duotone.svg?color=%23ff4500" width="22" align="top"> Что это

PussyLegion — userbot на Telethon, который держит несколько аккаунтов сразу, сохраняет медиа, чистит чаты, пробивает данные и делает всю рутину, которую обычно делают руками.

Запускается на Termux, VPS или локальной машине. Данные — только у тебя. Никаких ботов-посредников, никаких облаков.

<br>

### <img src="https://api.iconify.design/ph:sparkle-duotone.svg?color=%23ff4500" width="22" align="top"> Возможности

| Категория | Функции |
|---|---|
| **Аккаунты** | Мультиаккаунтность, 2FA, сессии, управление прямо из Telegram командами |
| **Ассистенты** | Главный аккаунт управляет пулом рабочих аккаунтов, маршрутизация команд, рассылка на всех |
| **Медиа** | Автосохранение T-медиа, сжатие изображений, резерв удалённых сообщений, массовые реакции |
| **Спам** | Рассылки, стикер-бомбы, краш-атаки, упоминания, смс-бомбер |
| **OSINT** | Геолокация IP, пробив номеров и почт, WHOIS доменов |
| **Утилиты** | Экспорт чатов, анимация текста, массовый вход в чаты/каналы, голосование в опросах |

<br>

### <img src="https://api.iconify.design/ph:download-simple-duotone.svg?color=%23ff4500" width="22" align="top"> Установка

```
cd HuroBot_tg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python hurobot.py
```

При первом запуске бот попросит в консоли номер телефона, код и пароль 2FA
первого аккаунта — он станет главным. Дальше всё управляется командами
в Telegram, отдельной панели нет. Свои `API_ID` / `API_HASH` берутся
на [my.telegram.org](https://my.telegram.org) и кладутся в `.env`
(пример — `.env.example`).

<br>

### <img src="https://api.iconify.design/ph:terminal-window-duotone.svg?color=%23ff4500" width="22" align="top"> Команды

#### 🔐 Аккаунты

```
.accounts          — роли, статус и аптайм аккаунтов
.role [id] [роль]  — главный / ассистент / самостоятельный
.start [id]        — запустить аккаунт
.stop [id]         — остановить аккаунт
.autostart [id] [on/off] — автозапуск аккаунта при старте бота
.addacc [номер]    — добавить аккаунт (дальше .code, при 2FA — .pass)
.code [1-2-3-4-5]  — код из Telegram, цифры через дефис
.pass [пароль]     — пароль 2FA
.delacc [id]       — удалить аккаунт и сессию (нужно подтверждение)
.logs [N]          — последние строки лога, придут в избранное
.delme [код]       — полная очистка чата (нужно подтверждение)
```

#### 💾 Медиа

```
.save          — сохранить самоудаляющийся файл (ответом на сообщение)
.clone [URL]   — скопировать пост из канала
.like [эмодзи] — реакция на соо со всех запущенных аккаунтов (ответом или по ссылке)
.joinall [ссылка] — завести все запущенные аккаунты в чат/канал
.vote [номер] — проголосовать в опросе (ответом или по ссылке) со всех аккаунтов
```

#### 💥 Спам

```
.spam [N] [текст]  — рассылка сообщений
.sti [N]           — спам стикерами (ответом на стикер)
.crash             — отправка ресурсоёмких стикеров (20 шт.)
.bomb              — бомбер по номеру телефона (5 минут)
```

#### 🕵️ OSINT

```
.data              — информация о пользователе (ответом)
.osint [значение]  — проверка IP / номера / почты
.whois [домен]     — WHOIS-данные
```

#### ⚡ Утилиты

```
.tagall            — упоминание всех участников
.iter [-n]         — экспорт участников чата (-n для номеров)
```

<br>

### <img src="https://api.iconify.design/ph/users-three-duotone.svg?color=%23ff4500" width="22" align="top"> Ассистенты

Один аккаунт — главный, остальные — ассистенты. Ты пишешь команду с главного,
а выполняют её рабочие аккаунты. Главный при этом остаётся в чате и показывает
ответ, а действия (сообщения, стикеры, упоминания) идут от ассистентов.

Роли задаются командой:

```
.role                     — показать роли
.role 2 assistant         — сделать аккаунт #2 ассистентом
.role 1 master            — сделать аккаунт #1 главным
```

Если роли ещё не заданы, главным автоматически становится первый аккаунт,
все остальные — ассистентами. Добавляемые позже аккаунты тоже попадают
в ассистенты. Можно зафиксировать главного через `MASTER_IDS=1` в `.env`.

Маршрутизация команд:

```
.spam 5 привет        — выполнит один свободный ассистент
.all spam 5 привет    — выполнят все ассистенты (сводный отчёт в чате)
.#2 spam 5 привет     — только аккаунт #2
.2 spam 5 привет      — то же самое, короткая форма
.me delme             — выполнить на главном аккаунте
```

Что уходит ассистентам: `.spam`, `.sti`, `.crash`, `.tagall`, `.up`.
Команды, завязанные на твой аккаунт (`.save`, `.clone`, `.delme`, `.mute`,
`.iter`, `.osint` и т.п.), выполняются на главном, чтобы сохранять в твоё
избранное и работать с твоими чатами.

Ассистенты:
* не реагируют на команды и не ведут автосохранение — они только исполняют;
* запускаются автоматически при первой команде, если ещё лежат;
* при флуд-вейте помечаются кулдауном, и следующая команда идёт другому;
* если ассистент не состоит в чате, в отчёте будет ошибка именно по нему.

<br>

### <img src="https://api.iconify.design/ph:warning-duotone.svg?color=%23ff4500" width="22" align="top"> Важно

**Легальность.** Некоторые функции (`.crash`, `.spam`) могут нарушать правила Telegram. Используй только в тестовых чатах.

**Безопасность.** Храни файлы сессий в защищённом месте.

**Ответственность.** Перед использованием протестируй бота в тестовом чате. Автор не несёт ответственности за блокировки аккаунтов.

<br>

### <img src="https://api.iconify.design/ph:code-duotone.svg?color=%23ff4500" width="22" align="top"> Стек

<div align="left">
  <a href="https://skillicons.dev">
    <img src="https://skillicons.dev/icons?i=python,sqlite,linux&theme=dark" alt="PussyLegion Tech Stack" />
  </a>
</div>

<br>

### <img src="https://api.iconify.design/ph:heart-duotone.svg?color=%23ff4500" width="22" align="top"> Поддержка

Официальный канал: [@hurodev](https://t.me/hurodev)

<br>

<div align="left">
  <a href="https://t.me/hurodev">
    <img src="https://img.shields.io/badge/Telegram-@hurodev-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram"/>
  </a>
</div>
