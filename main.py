"""Telegram 文件下载机器人 —— 转发即落盘。

架构
    user_client   用你自己的账号登录（Telethon / MTProto）
                  监听你转发给机器人的消息，负责真正下载
    bot_client    机器人身份，负责回复状态、响应 /start /help /stats

为什么下载交给 user_client
    官方 Bot API 的 getFile 硬上限 20MB，一个大视频就废了。
    用你自己的账号走 MTProto，单文件可到 2GB（Premium 4GB），也不消耗 Bot 的下载配额。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageMediaPhoto,
)

import config as cfg
import storage

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("saver")


TYPE_DIRS = {
    "photo": "图片",
    "video": "视频",
    "audio": "音频",
    "voice": "语音",
    "document": "文档",
    "other": "其他",
}

HELP_TEXT = (
    "转发文件给我，自动下载到服务器磁盘。\n\n"
    "· 支持文档 / 视频 / 音频 / 图片 / 语音\n"
    "· 相册会自动整组下载\n"
    "· 自动按「来源频道 / 文件类型」建子目录\n"
    "· 同一文件重复转发会自动跳过\n\n"
    "命令：\n"
    "/stats  查看本次运行统计"
)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def attrs_of(document) -> dict:
    return {type(a).__name__: a for a in document.attributes}


def classify(message) -> str:
    media = message.media
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    document = getattr(media, "document", None)
    if document is None:
        return "other"
    attrs = attrs_of(document)
    if "DocumentAttributeVideo" in attrs:
        video = attrs["DocumentAttributeVideo"]
        return "voice" if getattr(video, "round_message", False) else "video"
    if "DocumentAttributeAudio" in attrs:
        audio = attrs["DocumentAttributeAudio"]
        return "voice" if getattr(audio, "voice", False) else "audio"
    return "document"


def guess_name(message, kind: str) -> str:
    document = getattr(message.media, "document", None)
    stamp = message.date.strftime("%Y%m%d_%H%M%S")

    if document is not None:
        for attr in document.attributes:
            if isinstance(attr, DocumentAttributeFilename) and attr.file_name:
                return storage.safe_name(attr.file_name)
        attrs = attrs_of(document)
        if kind == "video":
            return f"video_{stamp}.mp4"
        if kind == "voice":
            return f"voice_{stamp}.ogg"
        if kind == "audio":
            audio = attrs.get("DocumentAttributeAudio")
            title = (getattr(audio, "title", "") or "").strip()
            performer = (getattr(audio, "performer", "") or "").strip()
            base = " - ".join(x for x in (performer, title) if x)
            return storage.safe_name(base) + ".mp3" if base else f"audio_{stamp}.mp3"
        return f"{kind}_{stamp}.bin"

    if kind == "photo":
        return f"photo_{stamp}_{message.id}.jpg"
    return f"file_{message.id}.bin"


def source_title(message):
    """取转发来源的频道 / 群组名，取不到返回 None。"""
    forward = getattr(message, "forward", None)
    if forward is not None:
        chat = getattr(forward, "chat", None)
        if getattr(chat, "title", None):
            return chat.title
        sender = getattr(forward, "sender", None)
        if sender is not None:
            name = getattr(sender, "title", None)
            if not name:
                name = " ".join(
                    x for x in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if x
                )
            if name:
                return name
        if getattr(forward, "from_name", None):
            return forward.from_name

    fwd_from = getattr(message, "fwd_from", None)
    if fwd_from is not None and getattr(fwd_from, "from_name", None):
        return fwd_from.from_name
    return None


def dest_dir(message) -> Path:
    parts = []
    if cfg.GROUP_BY_SOURCE:
        title = source_title(message)
        if title:
            parts.append(storage.safe_name(title))
    if cfg.GROUP_BY_TYPE:
        parts.append(TYPE_DIRS[classify(message)])
    target = cfg.DOWNLOAD_DIR.joinpath(*parts) if parts else cfg.DOWNLOAD_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


class Saver:
    def __init__(self, user: TelegramClient, bot: TelegramClient):
        self.user = user
        self.bot = bot
        self.ledger = storage.Ledger(cfg.SESSION_DIR / "downloaded.json")
        self.sem = asyncio.Semaphore(cfg.MAX_CONCURRENT)
        self.stats = {"files": 0, "bytes": 0, "failed": 0}

    # ---------- 通知 ----------

    def notify_target(self):
        if cfg.NOTIFY_USER:
            return cfg.NOTIFY_USER
        if cfg.ALLOWED_USERS:
            return next(iter(cfg.ALLOWED_USERS))
        return None

    async def say(self, text: str):
        target = self.notify_target()
        if target is None:
            log.info("%s", text.replace("\n", " | "))
            return None
        try:
            return await self.bot.send_message(target, text)
        except Exception as exc:
            log.warning("状态消息发送失败：%s", exc)
            return None

    # ---------- 下载 ----------

    @staticmethod
    def file_key(message) -> str:
        """同一份文件在 Telegram 上 id 全局唯一，用它做去重键。"""
        media = getattr(message, "media", None)
        document = getattr(media, "document", None)
        if document is not None and getattr(document, "id", None):
            return f"doc:{document.id}"
        photo = getattr(media, "photo", None)
        if photo is not None and getattr(photo, "id", None):
            return f"photo:{photo.id}"
        return f"msg:{message.chat_id}:{message.id}"

    async def fetch(self, message, label: str, status):
        path = storage.unique_path(dest_dir(message) / guess_name(message, classify(message)))
        started = time.monotonic()
        state = {"last": 0.0}

        async def progress(current: int, total: int):
            if status is None:
                return
            now = time.monotonic()
            if now - state["last"] < 5 and current < total:
                return
            state["last"] = now
            elapsed = max(now - started, 0.001)
            speed = human(current / elapsed)
            if total:
                percent = current * 100 / total
                filled = min(20, int(percent / 5))
                bar = "█" * filled + "░" * (20 - filled)
                body = f"{bar} {percent:.1f}%\n{human(current)} / {human(total)}  ·  {speed}/s"
            else:
                body = f"{human(current)}  ·  {speed}/s"
            try:
                await status.edit(f"{label}\n{body}")
            except Exception:
                pass

        async with self.sem:
            try:
                await self.user.download_media(message, file=str(path), progress_callback=progress)
            except Exception:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        return path

    # ---------- 主流程 ----------

    async def handle(self, messages) -> None:
        messages = [m for m in messages if m is not None and m.media]
        if not messages:
            return

        first = messages[0]
        if cfg.ALLOWED_USERS and not first.out and first.sender_id not in cfg.ALLOWED_USERS:
            log.warning("忽略未授权来源：%s", first.sender_id)
            return

        label = source_title(first) or "未知来源"
        count = len(messages)
        total_size = sum((getattr(m.file, "size", 0) or 0) for m in messages)
        header = f"收到 {count} 个文件" + (f"（{human(total_size)}）" if total_size else "")
        status = await self.say(f"{header}\n来源：{label}\n排队中…")

        saved, skipped, failed = [], 0, 0
        for index, message in enumerate(messages, 1):
            key = self.file_key(message)
            if cfg.SKIP_DUPLICATE and self.ledger.seen(key):
                skipped += 1
                log.info("跳过重复：%s", key)
                continue

            size = getattr(message.file, "size", 0) or 0
            if cfg.MAX_FILE_MB and size > cfg.MAX_FILE_MB * 1024 * 1024:
                failed += 1
                log.warning("超过 %dMB 上限，跳过：%s", cfg.MAX_FILE_MB, human(size))
                continue

            name = guess_name(message, classify(message))
            tag = f"[{index}/{count}] {name}" if count > 1 else name
            try:
                path = await self.fetch(message, tag, status)
            except FloodWaitError as exc:
                failed += 1
                log.warning("触发限流，需等待 %s 秒", exc.seconds)
            except Exception as exc:
                failed += 1
                log.exception("下载失败：%s", exc)
            else:
                if key:
                    self.ledger.add(key)
                self.stats["files"] += 1
                self.stats["bytes"] += size
                saved.append(path)
                log.info("已保存 %s（%s）", path, human(size))

        lines = [header, f"来源：{label}"]
        if saved:
            lines.append(f"完成 {len(saved)} 个 → {saved[0].parent}")
        if skipped:
            lines.append(f"跳过重复 {skipped} 个")
        if failed:
            lines.append(f"失败 {failed} 个")
        summary = "\n".join(lines)

        if status is not None:
            try:
                await status.edit(summary)
                return
            except Exception:
                pass
        await self.say(summary)


async def amain() -> None:
    cfg.validate()
    cfg.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cfg.SESSION_DIR.mkdir(parents=True, exist_ok=True)

    if not Path(cfg.USER_SESSION + ".session").exists():
        raise SystemExit(
            "未找到用户会话文件 user.session。\n"
            "请先在本地电脑运行 `python login.py` 完成登录，"
            f"再把生成的文件放到 {cfg.SESSION_DIR} 下。"
        )

    connect = cfg.client_kwargs()
    log.info("网络：%s", cfg.describe_proxy())

    user = TelegramClient(cfg.USER_SESSION, cfg.API_ID, cfg.API_HASH, **connect)
    bot = TelegramClient(cfg.BOT_SESSION, cfg.API_ID, cfg.API_HASH, **connect)

    try:
        await bot.start(bot_token=cfg.BOT_TOKEN)
        await user.start()
    except Exception as exc:
        raise SystemExit(
            f"连接 Telegram 失败：{exc}\n\n"
            "如果你在中国大陆，直连是连不上的，需要在 .env 里配代理，例如：\n"
            "    PROXY_TYPE=socks5\n"
            "    PROXY_HOST=127.0.0.1\n"
            "    PROXY_PORT=7890\n"
            "把地址端口换成你自己代理的即可。详见 README 的「网络代理」一节。"
        ) from exc

    me = await bot.get_me()
    saver = Saver(user, bot)

    @user.on(events.NewMessage(chats=cfg.BOT_USERNAME, func=lambda e: bool(e.message and e.message.media)))
    async def on_single(event):
        if event.message.grouped_id:
            return
        await saver.handle([event.message])

    @user.on(events.Album(chats=cfg.BOT_USERNAME))
    async def on_album(event):
        await saver.handle(list(event.messages))

    @bot.on(events.NewMessage(pattern=r"^/(start|help)(?:@\w+)?$"))
    async def on_help(event):
        await event.respond(HELP_TEXT)

    @bot.on(events.NewMessage(pattern=r"^/stats(?:@\w+)?$"))
    async def on_stats(event):
        s = saver.stats
        await event.respond(
            f"本次运行：\n"
            f"已保存 {s['files']} 个文件\n"
            f"累计 {human(s['bytes'])}\n"
            f"失败 {s['failed']} 个\n"
            f"台账记录 {len(saver.ledger.keys)} 条"
        )

    log.info("机器人 @%s 已就绪", me.username)
    log.info("下载目录 %s", cfg.DOWNLOAD_DIR)
    log.info("监听对话 @%s", cfg.BOT_USERNAME)

    await saver.say(
        f"服务已启动。\n"
        f"把文件转发给 @{cfg.BOT_USERNAME} 即可自动下载。\n"
        f"保存目录：{cfg.DOWNLOAD_DIR}"
    )

    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("已停止")
