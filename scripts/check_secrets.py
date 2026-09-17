#!/usr/bin/env python3
"""提交/推送前的凭据泄漏检查。

用法：
    python scripts/check_secrets.py            # 查工作区（git 跟踪的文件）
    python scripts/check_secrets.py --staged   # 只查暂存区
    python scripts/check_secrets.py --history  # 连历史一起查（慢，但能查出已提交的泄漏）

退出码 0 = 干净，1 = 发现问题。可以直接挂进 pre-commit / CI。

为什么需要它：`.gitignore` 只挡**文件名**，挡不住「把密钥写进文档示例」这种事——
文件名正常、扩展名正常，只有扫内容才发现得了。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 规则表：按「具体到宽泛」排列，具体的先报，避免宽泛规则淹没关键命中
# ---------------------------------------------------------------------------

RULES: list[tuple[str, str]] = [
    # —— 高置信度：这些形态几乎不可能是误报 ——
    ("Telegram Bot Token", r"\b\d{8,10}:[A-Za-z0-9_-]{33,}\b"),
    ("GitHub Token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("GitHub 细粒度 Token", r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    ("OpenAI Key", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}\b"),
    ("AWS Access Key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Slack Token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    ("私钥文件", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("Google API Key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),

    # —— 中置信度：看上下文 ——
    ("疑似 32 位十六进制密钥", r"\b[0-9a-f]{32}\b"),
    ("疑似长十六进制串", r"\b[0-9a-f]{40,}\b"),

    # —— config 键名后跟了疑似真实值 ——
    ("赋了值的敏感配置项",
     r"(?im)^\s*(API_HASH|BOT_TOKEN|API_ID|PROXY_PASS|PROXY_SECRET)\s*[=:]\s*"
     r"(?!\s*$|<|\$\{|你的|xxx|XXX|your_|YOUR_|\.\.\.|\*{3})"
     r"([A-Za-z0-9_:+/.-]{6,})\s*$"),
]

# 明确放行的占位符特征——命中里含这些就当没事
PLACEHOLDER_HINTS = re.compile(
    r"(?:<[A-Z_]+>|\$\{[A-Z_]+\}|^your_|^YOUR_|^你的|xxx|XXX|\.\.\.|"
    r"^(?:1234567|1234567890|0{8,}|0123456789abcdef|change_?me|placeholder|"
    r"example|dummy|fake|test|sample)$)",
    re.IGNORECASE,
)

# 文档里常见的「示例密钥」序列，出现即视为占位符
KNOWN_EXAMPLE_UNITS = (
    "0123456789abcdef", "0123456789", "abcdef0123456789",
    "1234567890abcdef", "abcdefghijklmnop", "aaaaaaaaaaaaaaaa",
)


def is_low_entropy(value: str) -> bool:
    """判断是不是「一眼假」的示例值。

    真密钥是随机的；文档示例几乎都是顺序串或重复串，比如
    `0123456789abcdef` 重复两遍、`dd` + 同一串、`aaaa...`。
    这类值用熵判断比用白名单判断可靠，也不会漏掉新出现的示例写法。
    """
    v = value.strip()
    # 去掉 MTProxy 的 dd 前缀再判断
    core = v[2:] if v.lower().startswith("dd") and len(v) > 2 else v

    # 1) 是某个短单元的整数次重复
    for unit in KNOWN_EXAMPLE_UNITS:
        if unit and core and core == unit * (len(core) // len(unit)) and len(core) % len(unit) == 0:
            return True

    # 2) 整体是任意短单元的重复（周期 <= 8）
    for period in range(1, 9):
        if len(core) >= period * 2 and core == core[:period] * (len(core) // period):
            if len(set(core[:period])) <= 3:  # 单元本身也很单调
                return True

    # 3) 字符种类太少（真密钥几乎不会只用 2 种字符）
    if len(core) >= 8 and len(set(core)) <= 2:
        return True

    # 4) 整串就是顺序数字（注意：必须是「整串」，不能拿子串去匹配——
    #    否则 `1234567890-abcdefghij` 这种真 token 会因为前缀是顺序数字被放过）
    if core.isdigit() and len(core) >= 6 and core in "012345678901234567890123456789":
        return True

    return False


def looks_like_placeholder(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if PLACEHOLDER_HINTS.search(v):
        return True
    if is_low_entropy(v):
        return True
    # 全是同一个字符，或全是 x / * / 0
    if len(set(v.lower())) <= 2 and len(v) >= 6:
        return True
    return False

# 这些文件根本不看
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".pdf", ".zip", ".gz", ".tar", ".whl", ".exe", ".dll",
    ".session", ".pyc", ".lock",
}
SKIP_NAMES = {".env", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
# 本脚本自己含规则文本，必须排除，否则永远自报
SKIP_PATHS = {"scripts/check_secrets.py"}


def git(*args: str) -> str:
    p = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    return p.stdout if p.returncode == 0 else ""


def should_skip(path: str) -> bool:
    if path in SKIP_PATHS:
        return True
    name = Path(path).name
    if name in SKIP_NAMES:
        return True
    return Path(path).suffix.lower() in SKIP_SUFFIXES


def scan_text(path: str, text: str, origin: str = "") -> list[tuple[str, int, str, str]]:
    """返回 [(文件, 行号, 规则名, 命中片段), ...]"""
    hits: list[tuple[str, int, str, str]] = []
    for rule_name, pattern in RULES:
        for m in re.finditer(pattern, text):
            value = m.group(0)
            # 取最后一个捕获组（如果有），用于判断占位符
            candidate = m.groups()[-1] if m.groups() else value
            if looks_like_placeholder(candidate):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            label = f"{path}{origin}"
            hits.append((label, line_no, rule_name, value.strip()))
    return hits


def scan_worktree() -> list[tuple[str, int, str, str]]:
    files = [f for f in git("ls-files").splitlines() if f.strip()]
    hits = []
    for f in files:
        if should_skip(f):
            continue
        p = Path(f)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits += scan_text(f, text)
    return hits


def scan_staged() -> list[tuple[str, int, str, str]]:
    """只查暂存区内容（还没提交的改动也能拦住）。"""
    files = [f for f in git("diff", "--cached", "--name-only").splitlines() if f.strip()]
    hits = []
    for f in files:
        if should_skip(f):
            continue
        blob = git("show", f":{f}")
        if blob:
            hits += scan_text(f, blob, origin=" (暂存区)")
    return hits


def scan_history(limit: int = 200) -> list[tuple[str, int, str, str]]:
    """扫历史里所有 blob。能查出「已经提交进去、后来删掉」的泄漏。"""
    seen: set[str] = set()
    hits = []
    revs = git("rev-list", "--all", f"--max-count={limit}").split()
    if not revs:
        return hits
    listing = git("rev-list", "--all", f"--max-count={limit}", "--objects")
    for line in listing.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if sha in seen or should_skip(path):
            continue
        seen.add(sha)
        if git("cat-file", "-t", sha).strip() != "blob":
            continue
        text = git("cat-file", "-p", sha)
        if text:
            hits += scan_text(path, text, origin=f" @{sha[:7]}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="提交前的凭据泄漏检查")
    ap.add_argument("--staged", action="store_true", help="只查暂存区")
    ap.add_argument("--history", action="store_true", help="连 git 历史一起查")
    args = ap.parse_args()

    scopes = []
    if args.staged:
        scopes.append(("暂存区", scan_staged))
    else:
        scopes.append(("工作区", scan_worktree))
    if args.history:
        scopes.append(("历史", scan_history))

    total = 0
    for name, fn in scopes:
        hits = fn()
        print(f"--- {name} ---")
        if not hits:
            print("    干净\n")
            continue
        for path, line, rule, value in sorted(hits):
            shown = value if len(value) <= 16 else f"{value[:8]}...{value[-4:]}"
            print(f"    [!] {path}:{line}  [{rule}]  {shown}")
            total += 1
        print()

    if total:
        print(f"发现 {total} 处疑似凭据。")
        print("确认是真实密钥的话：先从文件里删掉，再考虑轮换（轮换才是根本解）。")
        print("注意：已经 push 过的密钥，改写历史也救不回来——必须去服务端作废。")
        return 1

    print("通过：未发现疑似凭据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
