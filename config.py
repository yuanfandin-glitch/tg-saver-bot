"""配置读取。全部来自环境变量（或同目录的 .env）。"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
    _DOTENV_OK = True
except ImportError:
    _DOTENV_OK = False


def _str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _bool(key: str, default: bool = False) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _int(key: str, default: int = 0) -> int:
    raw = _str(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _ids(key: str) -> set[int]:
    raw = _str(key).replace("，", ",").replace(" ", "")
    out: set[int] = set()
    for part in raw.split(","):
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


# ---- 凭据 ----
API_ID = _int("API_ID")
API_HASH = _str("API_HASH")
BOT_TOKEN = _str("BOT_TOKEN")
BOT_USERNAME = _str("BOT_USERNAME").lstrip("@")

# ---- 目录 ----
_SESSION_RAW = _str("SESSION_DIR", "/data")
_DOWNLOAD_RAW = _str("DOWNLOAD_DIR", "/downloads")

# /data 和 /downloads 是给容器用的路径。在 Windows 上，这两个值会被当成
# 「当前盘符的根目录」，也就是 C:\data、C:\downloads —— 文件散落到系统盘根目录，
# 谁都找不到。所以本地跑（且用户没主动改过默认值）时，自动改成项目目录下的相对路径。
if os.name == "nt":
    if _SESSION_RAW == "/data":
        _SESSION_RAW = "./data"
    if _DOWNLOAD_RAW == "/downloads":
        _DOWNLOAD_RAW = "./downloads"

SESSION_DIR = Path(_SESSION_RAW).expanduser().resolve()
USER_SESSION = str(SESSION_DIR / "user")
BOT_SESSION = str(SESSION_DIR / "bot")
DOWNLOAD_DIR = Path(_DOWNLOAD_RAW).expanduser().resolve()

# ---- 行为 ----
ALLOWED_USERS = _ids("ALLOWED_USERS")
NOTIFY_USER = _int("NOTIFY_USER") or None
GROUP_BY_SOURCE = _bool("GROUP_BY_SOURCE", True)
GROUP_BY_TYPE = _bool("GROUP_BY_TYPE", True)
MAX_CONCURRENT = max(1, _int("MAX_CONCURRENT", 2))
MAX_FILE_MB = max(0, _int("MAX_FILE_MB", 2000))
SKIP_DUPLICATE = _bool("SKIP_DUPLICATE", True)

# ---- 网络代理 ----
# 留空则直连。中国大陆访问 Telegram 必须配，否则连不上。
PROXY_TYPE = _str("PROXY_TYPE").lower()
PROXY_HOST = _str("PROXY_HOST")
PROXY_PORT = _int("PROXY_PORT")
PROXY_USER = _str("PROXY_USER")
PROXY_PASS = _str("PROXY_PASS")
PROXY_SECRET = _str("PROXY_SECRET")

_SOCKS_KINDS = {"socks5", "socks4", "http"}


def client_kwargs() -> dict:
    """给 TelegramClient 的连接参数。没配代理就返回空 dict。"""
    if not (PROXY_TYPE and PROXY_HOST and PROXY_PORT):
        return {}

    if PROXY_TYPE == "mtproxy":
        if not PROXY_SECRET:
            raise SystemExit("PROXY_TYPE=mtproxy 时必须同时填 PROXY_SECRET")
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

        return {
            "connection": ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": (PROXY_HOST, PROXY_PORT, PROXY_SECRET),
        }

    if PROXY_TYPE in _SOCKS_KINDS:
        try:
            import python_socks  # noqa: F401
        except ImportError:
            raise SystemExit(
                "用 SOCKS / HTTP 代理需要 python-socks，请先执行：\n"
                "    pip install \"python-socks[asyncio]\"\n"
                "（Docker 镜像里已内置，本地运行才需要手动装）"
            )
        return {
            "proxy": {
                "proxy_type": PROXY_TYPE,
                "addr": PROXY_HOST,
                "port": PROXY_PORT,
                "username": PROXY_USER or None,
                "password": PROXY_PASS or None,
                "rdns": True,
            }
        }

    raise SystemExit(
        f"不支持的 PROXY_TYPE：{PROXY_TYPE}（可选 socks5 / socks4 / http / mtproxy）"
    )


def describe_proxy() -> str:
    if not (PROXY_TYPE and PROXY_HOST and PROXY_PORT):
        return "直连"
    return f"{PROXY_TYPE}://{PROXY_HOST}:{PROXY_PORT}"


def _missing(names: list[str], purpose: str) -> str:
    text = f"缺少必需配置：{'、'.join(names)}（{purpose}）"
    if not _DOTENV_OK:
        text += (
            "\n\n注意：当前环境没装 python-dotenv，.env 文件根本不会被读取，"
            "所以就算你在 .env 里填了也读不到。\n请先执行：pip install -r requirements.txt"
        )
    else:
        text += "\n请检查项目目录下的 .env 文件，确认这几项填了、且没有多余空格。"
    return text


def validate_api() -> None:
    missing = [name for name, value in (("API_ID", API_ID), ("API_HASH", API_HASH)) if not value]
    if missing:
        raise SystemExit(_missing(missing, "在 https://my.telegram.org/apps 申请"))


def validate() -> None:
    validate_api()
    missing = [name for name, value in (("BOT_TOKEN", BOT_TOKEN), ("BOT_USERNAME", BOT_USERNAME)) if not value]
    if missing:
        raise SystemExit(_missing(missing, "用 @BotFather 创建机器人"))
