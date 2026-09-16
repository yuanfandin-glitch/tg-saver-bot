"""首次登录：在本地电脑跑一次，生成 data/user.session。

    python login.py

按提示依次输入：
    1. 手机号（带国家码，例如 +8613800138000）
    2. Telegram 里收到的登录验证码
    3. 如果开了两步验证，再输入密码

成功后 data/ 目录下会出现 user.session —— 它等同于你账号的钥匙。
把它连同 data/ 整个目录一起拷到 NAS 的挂载点即可，之后就不需要再登录了。

注意：这个文件切勿外传、切勿提交到 git。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

import config as cfg

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


async def main() -> None:
    cfg.validate_api()
    cfg.SESSION_DIR.mkdir(parents=True, exist_ok=True)

    session_file = Path(cfg.USER_SESSION + ".session")
    print(f"会话文件将生成到：{session_file}")
    print(f"网络：{cfg.describe_proxy()}")

    connect = cfg.client_kwargs()
    client = TelegramClient(cfg.USER_SESSION, cfg.API_ID, cfg.API_HASH, **connect)
    try:
        await client.start()
    except Exception as exc:
        raise SystemExit(
            f"连接 Telegram 失败：{exc}\n\n"
            "如果你在中国大陆，直连是连不上的，需要在 .env 里配代理，例如：\n"
            "    PROXY_TYPE=socks5\n"
            "    PROXY_HOST=127.0.0.1\n"
            "    PROXY_PORT=7890\n"
            "把地址端口换成你自己代理的即可。"
        ) from exc

    me = await client.get_me()
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or str(me.id)
    await client.disconnect()

    if not session_file.exists():
        raise SystemExit(
            f"登录流程走完了，但会话文件没有落盘：{session_file}\n"
            f"请确认这个目录存在且可写：{cfg.SESSION_DIR}"
        )

    print(f"\n登录成功：{name}（@{me.username or me.id}）")
    print(f"会话文件已生成：{session_file}")
    print(f"大小 {session_file.stat().st_size} 字节")
    print(f"\n接下来把 {cfg.SESSION_DIR} 整个目录拷到 NAS 的挂载点即可。")


if __name__ == "__main__":
    asyncio.run(main())
