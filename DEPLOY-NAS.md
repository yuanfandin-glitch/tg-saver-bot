# 群晖 NAS 部署清单

按顺序照抄。每一步末尾有 **✅ 检查点**，通过了再走下一步。

一共 6 步，大约 15 分钟。**第 4 步和第 5 步最容易翻车，别跳。**

---

## 开工前，先备齐这四样

在电脑上确认你手上有：

| 项目 | 从哪来 | 长什么样 |
|---|---|---|
| `API_ID` / `API_HASH` | my.telegram.org/apps | 数字 / 32 位十六进制串 |
| `BOT_TOKEN` | @BotFather | `<BOT_TOKEN>` |
| `BOT_USERNAME` | @BotFather | `your_saver_bot`（不带 `@`） |
| `user.session` | 电脑上跑过 `login.py` | 项目 `data/` 文件夹里，28KB 左右 |

前三样现在就在你项目的 `.env` 里，第 3 步要原样搬到群晖。第四样是文件，第 4 步要手动传。

---

## 第 1 步：群晖上建两个文件夹

用 **File Station**（或者 SSH）建：

```
/volume1/docker/tg-saver           ← 放程序
/volume1/telegram                  ← 放下载的文件
```

第二个可以换地方，比如 `/volume1/video/tg下载`。想存哪儿就建哪儿，但**先建好**，别指望 Docker 自己创建。

```bash
# SSH 方式
sudo mkdir -p /volume1/docker/tg-saver
sudo mkdir -p /volume1/telegram
```

> 路径可以用中文，但**别带空格**。带空格的路径在 compose 文件里很麻烦。

**✅ 检查点**：File Station 里能看到这两个文件夹。

---

## 第 2 步：把代码传上去

群晖自带的 **Git** 或者直接 SSH `git clone`：

```bash
cd /volume1/docker
git clone https://github.com/yuanfandin-glitch/tg-saver-bot.git tg-saver
```

**没有 git 的话**，用 File Station 手动传这几个文件，或从电脑上整个文件夹拖进去。

传完之后 `/volume1/docker/tg-saver` 里应该有：

```
config.py          storage.py         main.py
login.py           Dockerfile         docker-compose.yml
requirements.txt   .env.example       LICENSE  README.md
```

**✅ 检查点**：

```bash
ls /volume1/docker/tg-saver
```

看到 `config.py`、`docker-compose.yml`、`Dockerfile` 三个文件，且**没有** `.env`（这个是第 3 步要自己建的）。

> ⚠️ **注意**：clone 下来的仓库里**没有** `.env`，也**没有** `data/user.session`——这两个被 `.gitignore` 挡掉了。这是故意的（里面有密钥），但意味着第 3、4 步必须手动补。

---

## 第 3 步：建 `.env` 并改三个地方 ⚠️ 关键

在群晖上进入项目目录，复制一份：

```bash
cd /volume1/docker/tg-saver
cp .env.example .env
```

然后用文本编辑器（群晖可以装 **Text Editor**，或者 SSH 里用 `vi`）打开 `.env`，**必须改这三处**：

### 3.1 填凭据

```ini
API_ID=<API_ID>
API_HASH=<API_HASH>
BOT_TOKEN=<BOT_TOKEN>
BOT_USERNAME=your_saver_bot
ALLOWED_USERS=<YOUR_USER_ID>
```

直接把你电脑上 `.env` 里的这几行抄过来。

### 3.2 目录改回容器路径 ⚠️

你电脑上的 `.env` 里是 `./downloads` 和 `./data`，**这两行在群晖上必须改掉**：

```ini
DOWNLOAD_DIR=/downloads
SESSION_DIR=/data
```

> **为什么**：`./` 是「相对当前目录」的意思，在容器里会指到 `/app/downloads`（程序代码所在的目录），而不是第 1 步建的那个文件夹。挂载点对不上，文件就消失在了容器内部，容器一删就没了。
>
> 这个错误不会报错，只会表现成「转发了没反应」或「文件找不到」——最难查的一类问题。

### 3.3 代理地址改掉 ⚠️

你电脑上是：

```ini
PROXY_TYPE=socks5
PROXY_HOST=127.0.0.1
PROXY_PORT=7897
```

**`127.0.0.1` 在容器里指向容器自己**，不是 NAS。三种情况对号入座：

**情况 A：NAS 本身能直连 Telegram**（有旁路由 / 透明代理接管流量）

代理整段清空：

```ini
PROXY_TYPE=
PROXY_HOST=
PROXY_PORT=
```

**情况 B：代理跑在 NAS 上**（比如 NAS 上装了 Clash，端口 7897）

填 NAS 的局域网 IP：

```ini
PROXY_TYPE=socks5
PROXY_HOST=192.168.1.10      # ← 换成你 NAS 的实际 IP
PROXY_PORT=7897
```

> 前提：Clash 要开着 **"允许局域网连接" / allow-lan**。只监听 `127.0.0.1` 的代理，容器连不上。

**情况 C：代理跑在路由器 / 旁路由上**

填那台的 IP：

```ini
PROXY_TYPE=socks5
PROXY_HOST=192.168.1.2       # ← 换成路由器 / 旁路由的实际 IP
PROXY_PORT=7890
```

> **怎么查 NAS 的 IP**：群晖 **控制面板 → 网络 → 网络界面**，看「IP 地址」那一栏。

**✅ 检查点**：`.env` 里 `DOWNLOAD_DIR=/downloads`、`SESSION_DIR=/data`，且 `PROXY_HOST` **不是** `127.0.0.1`。

---

## 第 4 步：把 `user.session` 传上去 ⚠️ 最容易漏

这一步在**电脑上**操作，不是群晖。

`user.session` 是你账号的登录凭据，群晖容器里没法交互输入验证码，所以只能在电脑上登录一次、把结果文件搬过去。

**电脑上**：

1. 打开项目文件夹 `tg-saver-bot\data\`，找到 `user.session`（28KB 左右）
2. 用 File Station 网页、或者 **WinSCP / 群晖 Drive**，传到群晖的：

```
/volume1/docker/tg-saver/data/user.session
```

注意是**放进 `data` 文件夹里**，不是项目根目录。`data` 文件夹可能不存在，先建。

SSH 方式（在群晖上执行，把电脑的文件传过去）：

```bash
mkdir -p /volume1/docker/tg-saver/data
# 用 scp / File Station 上传到上面这个目录
```

**✅ 检查点**：

```bash
ls -la /volume1/docker/tg-saver/data/
```

看到 `user.session`，大小两万多字节。**文件是 0 字节就是传坏了**，重传。

> ⚠️ `user.session` 等同于你 Telegram 账号的钥匙。别外传、别丢公开网盘。这是它被 git 挡掉的原因。

---

## 第 5 步：改存储路径

打开 `docker-compose.yml`，找到 `volumes:` 这一段：

```yaml
    volumes:
      - ./data:/data
      - /volume1/telegram:/downloads
```

**规则：一行固定是「群晖上的文件夹 : 容器里的文件夹」，只改冒号左边的。**

- 第 1 行 `./data:/data` —— **不要动**。`./` 指 compose 文件旁边，让它跟着项目走。
- 第 2 行 —— **只改左边**。第 1 步你把下载文件夹建在哪儿，这里就写哪儿。

```yaml
# 建在 /volume1/telegram，就保持原样
      - /volume1/telegram:/downloads

# 建在 /volume1/video/tg下载，就这样写
      - /volume1/video/tg下载:/downloads

# 建在 /volume1/docker/downloads，就这样写
      - /volume1/docker/downloads:/downloads
```

> **右边 `/downloads` 为什么不能改**：这是程序内部写死的目录名，跟 `.env` 里的 `DOWNLOAD_DIR=/downloads` 是一对。改了就找不到落盘位置。

**✅ 检查点**：冒号右边还是 `/downloads`，冒号左边是第 1 步真实建好的目录。

---

## 第 6 步：启动

**图形界面方式**（推荐）：

群晖 **Container Manager → 项目 → 新增** → 路径选 `/volume1/docker/tg-saver` → 它会自动读到 `docker-compose.yml` → 点**下一步** → **完成**。首次会构建镜像，等两三分钟。

**SSH 方式**：

```bash
cd /volume1/docker/tg-saver
sudo docker compose up -d --build
sudo docker compose logs -f
```

**✅ 检查点**：日志里出现这两行：

```
网络：socks5://192.168.1.10:7897
机器人 @your_saver_bot 已就绪，等待转发
```

看到 `网络：直连` 说明代理没配（或按情况 A 清空了），也行。

> 日志卡在 `连接 Telegram...` 不动 → 代理不通，回第 3.3 步。
> 日志报 `找不到 user.session` → 第 4 步没传好，或第 3.2 步目录没改。

---

## 最后一步：试一发

1. 打开 Telegram，找到你的机器人 `@your_saver_bot`
2. 随便找个频道，**转发一个文件**给它
3. 机器人应该先回「收到 N 个文件」，然后进度条滚动，最后给出落盘路径

去群晖 File Station 里核对那个路径，文件应该在。

---

## 出问题了怎么查

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 转发了完全没反应 | 没给机器人发过 `/start` | 在 Telegram 里给机器人发 `/start` |
| 同上 | 日志里有连接错误 | `sudo docker compose logs -f` 看具体报错 |
| 日志卡在「连接 Telegram」 | 代理不通 | 回第 3.3 步，确认 IP 和 allow-lan |
| 报 `找不到 user.session` | 文件没传 / 目录没改 | 回第 4 步 + 检查 3.2 |
| 报「缺少必需配置」 | `.env` 没读到 | 确认 `.env` 在项目根目录、和 compose 同级 |
| 下载完文件找不到 | 挂载写错了 | `sudo docker compose exec tg-saver ls /downloads` 看容器里有没有 |
| 报 `FloodWait` | 短时间拉太多 | 等提示的秒数，降低 `MAX_CONCURRENT` |

**常用命令**：

```bash
cd /volume1/docker/tg-saver
sudo docker compose logs -f          # 看实时日志
sudo docker compose restart          # 重启
sudo docker compose down             # 停掉
sudo docker compose up -d --build    # 改完配置重新构建
sudo docker compose exec tg-saver ls -la /downloads   # 看容器里的落盘目录
```

---

## 配置改了要重启

改完 `.env` 或 `docker-compose.yml`，**必须重新启动容器才生效**：

```bash
sudo docker compose up -d --build
```

光改文件不重启，程序读的还是旧配置。
