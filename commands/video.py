"""`.video <ссылка>` — скачать ролик с YouTube / TikTok, отправить в чат
и удалить файл с устройства."""

import asyncio
import re
import shutil
from urllib.parse import urlparse

from telethon.tl.types import DocumentAttributeVideo

from commands import command
from commands.utils import fmt, safe_edit, get_usage_instructions
from config import TEMP_DIR
from logger import exception

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# лимит Telegram на файл для обычного аккаунта — 2 ГБ
MAX_BYTES = 2000 * 1024 * 1024

_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)

_ALLOWED_HOSTS = ("youtube.com", "youtu.be", "tiktok.com")

# одновременно качаем одно видео, чтобы не забить канал/диск
_LOCK = asyncio.Lock()


def _host_ok(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS)


def _ydl_opts(workdir, has_ffmpeg):
    if has_ffmpeg:
        fmt_sel = (
            f"bv*[ext=mp4][filesize<{MAX_BYTES}]+ba[ext=m4a]/"
            f"b[ext=mp4][filesize<{MAX_BYTES}]/"
            f"bv*+ba/b"
        )
    else:
        # без ffmpeg склеивать дорожки нельзя — берём готовый одиночный файл
        fmt_sel = f"b[ext=mp4][filesize<{MAX_BYTES}]/b[ext=mp4]/b"
    opts = {
        "format": fmt_sel,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 30,
        "retries": 3,
        "max_filesize": MAX_BYTES,
    }
    if has_ffmpeg:
        opts["merge_output_format"] = "mp4"
    return opts


def _download_sync(url, workdir):
    """Блокирующая загрузка (запускается в потоке). Возвращает (путь, info)."""
    has_ffmpeg = shutil.which("ffmpeg") is not None
    with yt_dlp.YoutubeDL(_ydl_opts(workdir, has_ffmpeg)) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("не удалось получить информацию о видео")
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise RuntimeError("видео не найдено")
            info = entries[0]
        path = None
        for item in info.get("requested_downloads") or []:
            if item.get("filepath"):
                path = item["filepath"]
        if not path:
            path = ydl.prepare_filename(info)
    return path, info


def _find_file(workdir, path):
    from pathlib import Path
    if path and Path(path).exists():
        return Path(path)
    files = [p for p in workdir.iterdir() if p.is_file() and p.suffix != ".part"]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def _clean_error(exc):
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(exc))
    text = text.replace("ERROR:", "").strip()
    return text[:140] + "…" if len(text) > 140 else text


@command(r'^\.video(?:\s+(.+))?$', name="video")
async def video_handler(event, client, state):
    arg = event.pattern_match.group(1)

    # ссылку можно взять и из сообщения, на которое отвечаешь
    if not arg and event.is_reply:
        reply = await event.get_reply_message()
        arg = (getattr(reply, "raw_text", None) or "") if reply else ""

    m = _URL_RE.search(arg or "")
    if not m:
        await safe_edit(event, get_usage_instructions("video"))
        return
    url = m.group(0).rstrip(").,]>")

    if not _host_ok(url):
        await safe_edit(event, fmt("ошибка", ["поддерживаются только ссылки YouTube и TikTok"]))
        return

    if yt_dlp is None:
        await safe_edit(event, fmt("ошибка", [
            "библиотека `yt-dlp` не установлена",
            "поставь: `pip install -U yt-dlp` и перезапусти бота",
        ]))
        return

    if _LOCK.locked():
        await safe_edit(event, fmt("подожди", ["уже идёт другая загрузка"]))
        return

    async with _LOCK:
        workdir = TEMP_DIR / f"video_{event.id}"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            await safe_edit(event, fmt("скачиваю видео", ["это может занять время"]))
            loop = asyncio.get_running_loop()
            try:
                path, info = await loop.run_in_executor(None, _download_sync, url, workdir)
            except Exception as e:
                exception("video_handler.download")
                await safe_edit(event, fmt("не удалось скачать", [f"`{_clean_error(e)}`"]))
                return

            file = _find_file(workdir, path)
            if not file:
                await safe_edit(event, fmt("ошибка", ["файл не найден после загрузки"]))
                return
            if file.stat().st_size > MAX_BYTES:
                await safe_edit(event, fmt("ошибка", ["видео больше 2 ГБ — Telegram не примет"]))
                return

            attrs = []
            if info.get("duration") and info.get("width") and info.get("height"):
                attrs.append(DocumentAttributeVideo(
                    duration=int(info["duration"]),
                    w=int(info["width"]),
                    h=int(info["height"]),
                    supports_streaming=True,
                ))

            try:
                await client.send_file(
                    event.chat_id,
                    str(file),
                    supports_streaming=True,
                    attributes=attrs or None,
                    reply_to=event.reply_to_msg_id,
                )
            except Exception as e:
                exception("video_handler.send")
                await safe_edit(event, fmt("не удалось отправить", [f"`{_clean_error(e)}`"]))
                return

            try:
                await event.delete()
            except Exception:
                pass
        finally:
            # видео всегда удаляется с устройства — и при успехе, и при ошибке
            shutil.rmtree(workdir, ignore_errors=True)
