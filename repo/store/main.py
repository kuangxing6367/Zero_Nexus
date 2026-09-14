"""store —— JSON 存储抽象（机制包）。

框架提供的 KV 一等公民：统一路径、原子写，下游不再各自
json.load/dump（minecraftconsole 自写 workflows.json 的教训）。

稳定 API 表面：
    KV(path, default=None)
    .get(key, default=None) / .set(key, value) / .delete(key)
    .update(mapping) / .as_dict()
"""
from __future__ import annotations

import json
import os
import threading


class KV:
    def __init__(self, path: str, default: dict | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._data = dict(default or {})
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._save()

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)
            self._save()

    def update(self, mapping: dict):
        with self._lock:
            self._data.update(mapping)
            self._save()

    def as_dict(self) -> dict:
        return dict(self._data)

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
