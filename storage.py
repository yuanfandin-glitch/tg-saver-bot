"""文件名净化、重名避让、已下载台账。"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED |= {f"COM{i}" for i in range(1, 10)}
_RESERVED |= {f"LPT{i}" for i in range(1, 10)}

_MAX_LEN = 150


def safe_name(name: str, fallback: str = "file") -> str:
    """清掉各平台非法字符，避免出现空名或超长名。"""
    name = unicodedata.normalize("NFC", name or "")
    name = _ILLEGAL.sub("_", name).strip().strip(". ")
    if not name:
        return fallback

    stem, dot, ext = name.rpartition(".")
    if dot and stem.upper() in _RESERVED:
        name = "_" + name

    if len(name) > _MAX_LEN:
        stem, dot, ext = name.rpartition(".")
        if dot:
            keep = _MAX_LEN - len(ext) - 1
            name = f"{stem[:keep]}.{ext}"
        else:
            name = name[:_MAX_LEN]
    return name


def unique_path(path: Path) -> Path:
    """重名时追加 (1) (2)…，不覆盖已有文件。"""
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


class Ledger:
    """已下载文件台账，防止同一文件重复转发时反复落盘。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except Exception:
            self.keys = set()
            return
        if isinstance(raw, list):
            self.keys = {str(x) for x in raw}
        elif isinstance(raw, dict):
            self.keys = {str(x) for x in raw.get("keys", [])}
        else:
            self.keys = set()

    def seen(self, key: str) -> bool:
        return bool(key) and key in self.keys

    def add(self, key: str) -> None:
        if not key or key in self.keys:
            return
        self.keys.add(key)
        self.flush()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(sorted(self.keys), ensure_ascii=False), "utf-8")
        tmp.replace(self.path)
