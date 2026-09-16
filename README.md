# tg-saver-bot

把文件转发给机器人，自动下载到 NAS 磁盘。

## 工作原理

```
Telegram 频道  ──转发──▶  你的机器人
                              │
                              │ user_client 监听「你发给机器人的消息」
                              ▼
                    user_client（你自己的账号）
                              │  MTProto 下载，单文件可达 2GB
                              ▼
                       /downloads/来源频道/类型/
```

进程里跑两个客户端，各司其职：

| 客户端 | 身份 | 干什么 |
|---|---|---|
| `user_client` | 你自己的账号 | 监听转发消息、真正下载文件 |
| `bot_client` | 机器人 | 回复下载状态、响应 `/start` `/help` `/stats` |

**为什么下载不用机器人自己干：** 官方 Bot API 的 `getFile` 硬上限 20MB，一个大视频就废了。改用你自己的账号走 MTProto，单文件可到 2GB（Premium 4GB），也不消耗机器人的下载配额。

## 前置准备

### 1. 拿 API_ID / API_HASH

打开 <https://my.telegram.org/apps>，用你的手机号登录，创建一个应用，记下 `api_id` 和 `api_hash`。

> 创建完应用后，页面上还会出现一栏 **Available MTProto servers**，列着 `Test configuration: 149.154.167.40:443` 和 `Production configuration: 149.154.167.50:443`。
> **这一栏不用管，也不用填到任何地方。** 它只是 Telegram 自己的数据中心入口地址，纯参考信息。Telethon 连上之后会自动拉取完整的数据中心列表（5 个 DC）并选择正确的那个，你的配置里只需要 `api_id` 和 `api_hash`。

### 2. 创建机器人

在 Telegram 里找 [@BotFather](https://t.me/BotFather)：

```
/newbot
```

按提示起名字，拿到形如 `1234567890:AAxxxx...` 的 token 和机器人用户名。

### 3. 记下自己的用户 ID

找 [@userinfobot](https://t.me/userinfobot) 发一句话，它会回你的数字 ID。

### 4. 给机器人发一条消息

在 Telegram 里打开你的机器人，发一个 `/start`。这一步必须做——否则后面的会话解析不到这个机器人。

## 网络代理（中国大陆必看）

Telegram 的服务器在境外，中国大陆直连是连不上的。如果 NAS 本身有透明代理 / 旁路由接管流量，跳过这一节；否则必须在 `.env` 里配代理，否则程序启动时会卡住或报连接失败。

支持四种代理，按你手头的类型选一个填：

**SOCKS5**（Clash、v2rayN 等本地代理软件最常用）

```ini
PROXY_TYPE=socks5
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
PROXY_USER=
PROXY_PASS=
```

**HTTP**

```ini
PROXY_TYPE=http
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
```

**MTProxy**（Telegram 官方代理协议）

```ini
PROXY_TYPE=mtproxy
PROXY_HOST=你的代理服务器IP
PROXY_PORT=443
PROXY_SECRET=dd0123456789abcdef0123456789abcdef
```

`PROXY_SECRET` 必须是 **hex 格式**（32 位十六进制，或者带 `dd` 前缀的 34 位）。如果你拿到的是 base64 格式的 secret，要先转成 hex，Telethon 不认 base64。

**群晖上的地址怎么填**

代理如果跑在另一台机器（路由器、旁路由）上，直接填那台的局域网 IP，例如 `PROXY_HOST=192.168.1.2`。

代理如果就跑在同一台 NAS 上（比如 NAS 上开了 Clash），容器里不能用 `127.0.0.1`——那指向容器自己。要填 NAS 的局域网 IP，例如 `PROXY_HOST=192.168.1.10`。

配好之后启动日志里会打印一行 `网络：socks5://192.168.1.2:7890`，可以用它确认配置生效了。

## 部署到群晖

### 第 1 步：在电脑上生成登录会话

群晖的 Docker 容器里没法交互输入验证码，所以先在电脑上登录一次。

```bash
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```

编辑 `.env`，至少填上 `API_ID`、`API_HASH`、`BOT_TOKEN`、`BOT_USERNAME`。

**这一步是在 Windows 上跑，所以目录要填本地相对路径**：

```ini
DOWNLOAD_DIR=./downloads
SESSION_DIR=./data
```

`./` 表示「跟 `.env` 在同一个目录」，也就是项目文件夹里面。等传到群晖时要改回容器路径，第 3 步会讲。

然后运行：

```bash
python login.py
```

依次输入手机号（带国家码，如 `+8613800138000`）、Telegram 收到的验证码、两步验证密码（如果开了）。

跑完最后会打印一行 **会话文件已生成：** 带完整路径。确认那个文件真的在——没打印这行就是没生成成功，别急着往下走。

> **踩过这个坑：** 如果 `SESSION_DIR` 填的是 `/data`，会话文件会跑到 `C:\data\` 去（Windows 把 `/data` 当成当前盘符的根目录），在项目里当然找不到。去 `C:\data\` 把 `user.session` 挪到项目的 `data\` 文件夹，再把 `.env` 改成 `./data` 就行。

> `user.session` 等同于你账号的钥匙。别外传、别提交到 git、别丢进公开网盘。

### 第 2 步：把文件传到群晖

在群晖上建一个目录，比如 `/volume1/docker/tg-saver`，把这几个文件传上去：

```
config.py  storage.py  main.py  requirements.txt
Dockerfile  docker-compose.yml  .env
data/user.session
```

### 第 3 步：把 .env 里的目录改回容器路径

**这一步最容易忘，忘了的后果是「转发了没反应」或者「文件不知道存哪去了」。**

生成会话时用的是 `./data`，但容器里认的是绝对路径，必须改回来：

```ini
DOWNLOAD_DIR=/downloads
SESSION_DIR=/data
```

顺便检查代理：容器里的 `127.0.0.1` 指向容器自己，不是 NAS。代理跑在 NAS 或别的机器上，就要改成那台的局域网 IP，例如 `PROXY_HOST=192.168.1.10`。如果 NAS 本身能直连 Telegram，把 `PROXY_TYPE` 那几项全部清空即可。

### 第 4 步：改存储路径

打开 `docker-compose.yml`，找到 `volumes:` 这一段。

**Docker 的写法固定是「群晖上的文件夹 : 容器里的文件夹」，中间一个冒号隔开。你只改冒号左边，右边一个字都别动。**

```yaml
volumes:
  - ./data:/data                    # 存凭据和下载记录，不用改
  - /volume1/telegram:/downloads    # ← 只改这一行的左边
```

`/volume1` 是群晖的固定写法，`telegram` 是共享文件夹的名字。想存到别处，就把左边这后半截换掉：

| 你想把文件存到 | 这一行就写成 |
|---|---|
| 「video」共享文件夹下的「tg下载」 | `- /volume1/video/tg下载:/downloads` |
| 「docker」共享文件夹下的「downloads」 | `- /volume1/docker/downloads:/downloads` |
| 「homes」里你自己的目录 | `- /volume1/homes/你的用户名/telegram:/downloads` |

两点注意：

1. **冒号左边的目录要先在群晖上建好**（File Station 里右键新建文件夹）。别指望它自己出现。
2. 路径可以用中文，但**别带空格**。

> 为什么右边不能改？`/downloads` 是程序内部认的目录名，改了就找不到落盘位置。左边才是告诉 Docker「这个目录实际在群晖的哪里」。

### 第 5 步：启动

群晖 **Container Manager → 项目 → 新增**，选到 `tg-saver` 目录，它会自动识别 `docker-compose.yml`，点构建并启动。

或者 SSH 进 NAS：

```bash
cd /volume1/docker/tg-saver
sudo docker compose up -d --build
sudo docker compose logs -f
```

看到 `机器人 @xxx 已就绪` 就成了。

### 第 6 步：试一发

在 Telegram 里随便转发一个文件给你的机器人。机器人会先回一条「收到 N 个文件」，下载过程中持续更新进度条，完成后给出落盘路径。

## 在 Windows 上直接跑（不装 Docker）

```bash
pip install -r requirements.txt
copy .env.example .env
python login.py     # 首次
python main.py
```

`.env` 里的目录用相对路径就行，文件都落在项目文件夹里：

```ini
DOWNLOAD_DIR=./downloads
SESSION_DIR=./data
```

想存到别处就写绝对路径：

```ini
DOWNLOAD_DIR=D:\Telegram
SESSION_DIR=D:\Telegram\.session
```

## 配置说明

| 变量 | 默认 | 说明 |
|---|---|---|
| `API_ID` / `API_HASH` | — | my.telegram.org 申请，必填 |
| `BOT_TOKEN` | — | @BotFather 给的 token，必填 |
| `BOT_USERNAME` | — | 机器人用户名，不带 `@`，必填 |
| `ALLOWED_USERS` | 空 | 允许触发的用户 ID，逗号分隔。留空 = 不限制 |
| `NOTIFY_USER` | 空 | 状态消息发给谁。留空 = 用 `ALLOWED_USERS` 第一个 |
| `DOWNLOAD_DIR` | `/downloads` | 落盘根目录。这是容器路径，本机跑要改成 `./downloads` |
| `SESSION_DIR` | `/data` | 会话文件与台账目录。容器路径，本机跑要改成 `./data` |
| `PROXY_TYPE` | 空 | `socks5` / `socks4` / `http` / `mtproxy`，留空 = 直连 |
| `PROXY_HOST` / `PROXY_PORT` | 空 | 代理地址与端口 |
| `PROXY_USER` / `PROXY_PASS` | 空 | SOCKS / HTTP 代理的账密，不需要就留空 |
| `PROXY_SECRET` | 空 | 仅 MTProxy 用，必须是 hex 格式 |
| `GROUP_BY_SOURCE` | `true` | 按来源频道建子目录 |
| `GROUP_BY_TYPE` | `true` | 按类型建子目录 |
| `MAX_CONCURRENT` | `2` | 同时下载数 |
| `MAX_FILE_MB` | `2000` | 单文件上限，`0` = 不限制 |
| `SKIP_DUPLICATE` | `true` | 同一文件重复转发自动跳过 |

## 落盘目录长这样

```
/downloads/
├── 某某影视频道/
│   ├── 视频/
│   │   └── 纪录片第一集.mp4
│   └── 图片/
│       └── photo_20260916_121500_1234.jpg
└── 资料分享群/
    └── 文档/
        └── 白皮书.pdf
```

把 `GROUP_BY_SOURCE` / `GROUP_BY_TYPE` 关掉就是全部平铺在根目录。

## 常见问题

**转发了没反应？**
先确认前置准备第 4 步给机器人发过 `/start`；再 `docker compose logs -f` 看有没有报错。

**提示找不到 user.session？**
三种可能，对号入座：

1. **在 Windows 上跑** —— `SESSION_DIR` 填成了 `/data`，文件跑到 `C:\data\user.session` 去了。挪回项目的 `data\` 文件夹，把 `.env` 改成 `SESSION_DIR=./data`。
2. **在群晖上跑** —— 文件没传上去，或者 `.env` 里还是 `./data` 没改回 `/data`。
3. **挂载写错** —— 确认 compose 里 `./data:/data` 这一行没动过。

**报「缺少必需配置」，但我明明在 .env 里填了？**
多半是没装 `python-dotenv`，`.env` 根本没被读取。跑一下 `pip install -r requirements.txt`。程序现在会直接提示这一点。

**文件超过 2GB 下不动？**
这是 Telegram 的硬限制。普通账号 2GB，Premium 4GB。超过就只能换别的渠道。

**下载一半断了？**
半截文件会被自动清掉，重新转发即可。进度会从头开始（Telethon 未启用断点续传）。

**会不会因为用自己账号登录被封？**
Telethon 走的是官方 MTProto 协议，和第三方客户端同源，正常使用风险很低。但别拿它做批量爬取——短时间高频拉取会触发限流（`FloodWait`），严重的会限制账号。自己手动转发着用没问题。

**受限频道（禁止转发/保存）能下吗？**
这个方案不行——受限内容根本转发不出来。要处理那种得让程序以你的账号身份直接进源频道抓，需要另做。

## 免责声明

本工具通过 Telegram 官方 MTProto 协议、以**你自己的账号身份**运行，只应用于下载你有权访问的内容。

请遵守 [Telegram 服务条款](https://telegram.org/tos) 与当地法律法规，不要用它批量抓取、传播侵权内容或进行任何滥用行为。因使用本工具产生的一切后果，由使用者自行承担。

## 许可证

[MIT](LICENSE)
