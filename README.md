<div align="center">

  <img src="https://i.ibb.co/v08LpSt/IMG-20260409-190254-955.jpg" width="150" alt="PussyLegion Logo" style="border-radius: 18px;" />

  <h1 align="center" style="font-style: italic; color: #ff4500;">PussyLegion</h1>

  <p align="center">
    <strong>Многофункциональный Telegram-userbot для автоматизации, модерации и OSINT</strong>
  </p>

  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=20&duration=3000&pause=1000&color=FF4500&center=true&vCenter=true&width=700&lines=Single-Account+Userbot;Media+Save+%2B+Auto-Archive;OSINT+Toolkit;Spam+%2B+Utilities;Termux+Ready" alt="Typing SVG" />

  <br>

  <img src="https://img.shields.io/badge/PussyLegion-v1.4.0-ff4500?style=for-the-badge" alt="Version"/>
  <a href="https://t.me/hurodev">
    <img src="https://img.shields.io/badge/Telegram-@hurodev-26A5E4?style=for-the-badge&color=ff4500&logo=telegram&logoColor=white" alt="Telegram"/>
  </a>

</div>

<br>

### <img src="https://api.iconify.design/ph:lightning-duotone.svg?color=%23ff4500" width="22" align="top"> Что это

PussyLegion — userbot на Telethon для одного аккаунта: сохраняет медиа, чистит чаты, модерирует личку, пробивает данные и делает всю рутину, которую обычно делают руками.

Запускается на Termux, VPS или локальной машине. Данные — только у тебя. Никаких ботов-посредников, никаких облаков.

<br>

### <img src="https://api.iconify.design/ph:sparkle-duotone.svg?color=%23ff4500" width="22" align="top"> Возможности

| Категория | Функции |
|---|---|
| **Аккаунт** | Одна сессия, вход по номеру/коду/2FA, автопереподключение |
| **Медиа** | Автосохранение T-медиа, сжатие изображений, резерв удалённых сообщений, QR, клонирование постов |
| **Модерация** | Мут в личке, стоп-слова, упоминания, экспорт участников |
| **Спам** | Рассылки, стикер-бомбы, краш-атаки, смс-бомбер |
| **OSINT** | Геолокация IP, пробив номеров и почт, WHOIS доменов |
| **Утилиты** | Анимация текста, случайные числа, кубик, логи |

<br>

### <img src="https://api.iconify.design/ph:download-simple-duotone.svg?color=%23ff4500" width="22" align="top"> Установка

```
cd PussyLegion
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python hurobot.py
```

При первом запуске бот попросит в консоли номер телефона, код и пароль 2FA —
сессия сохранится в `PussyLegion_data/sessions/main.session`. Свои `API_ID` /
`API_HASH` берутся на [my.telegram.org](https://my.telegram.org) и кладутся
в `.env` (пример — `.env.example`).

Если в `sessions/` остались сессии старого мультиаккаунтного формата
(`account_1.session` и т.п.), бот при старте оставит одну — основную — и
удалит остальные. Основная выбирается по роли `master` из старой базы,
затем по `MASTER_IDS` в `.env`, иначе берётся самый первый аккаунт.

<br>

### <img src="https://api.iconify.design/ph:terminal-window-duotone.svg?color=%23ff4500" width="22" align="top"> Команды

#### ℹ️ Основное

```
.help [команда]  — список команд или справка по конкретной
.ping            — проверка отклика
.bot             — информация о боте
.logs [N]        — последние N строк лога в избранное
```

#### 💾 Медиа

```
.save          — сохранить самоудаляющийся файл (ответом на сообщение)
.clone [URL]   — скопировать пост из канала
.qr [текст]    — сгенерировать QR-код
```

#### 🛡 Модерация (только в личке)

```
.mute [срок]       — замутить (навсегда или 1m / 1h / 1d)
.unmute            — снять мут
.mutes             — список замученных
.muteword [слово]  — удалять сообщения со словом
.tagall            — упомянуть всех участников
.up [N]            — переупоминания (ответом на сообщение)
.iter [-n]         — экспорт участников чата (-n только с номерами)
```

#### 💥 Спам

```
.spam [N] [текст]  — рассылка сообщений
.sti [N]           — спам стикерами (ответом на стикер)
.crash             — отправка ресурсоёмких стикеров
.bomb [номер]      — бомбер по номеру телефона (5 минут)
```

#### 🕵️ OSINT

```
.data                      — информация о пользователе (ответом)
.osint [значение] [-r]     — проверка IP / номера / почты (holehe); -r — повторный прогон по доменам, упавшим с ошибкой
.sherlock [юзернейм]       — поиск аккаунта по нику на множестве площадок
.ignorant [код] [номер]    — на каких сервисах привязан номер телефона
.mosint [почта]            — отчёт по почте: утечки, соцсети
.h8mail [почта]            — поиск почты в брич-базах
.whois [домен]             — WHOIS-данные
```

#### ⚡ Утилиты

```
.ani on/off        — анимация набора текста (в этом чате)
.random 1-100      — случайное число
.dice              — кубик
```

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
