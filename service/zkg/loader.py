"""加载器（依赖驱动加载）。

做法：
    scan(plugins) -> resolve(依赖图) -> rebuild(plugins.db)
    -> 仅加载「有依赖方」的官方工具

每个官方工具在 plugins.db 里对应一张 pkg_<T> 表（见 depdb），
零行 = 无依赖 = 不加载。
"""
from __future__ import annotations

import importlib.util
import os
import tarfile
from typing import List, Optional

from . import defaults, scanner, resolver, depdb, sources


class Loader:
    def __init__(self, plugins_dir: str, data_dir: str,
                 sources_cfg: Optional[list] = None):
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        cfg = {"sources": sources_cfg} if sources_cfg else \
              {"sources": defaults.get_default_sources()}
        self.registry = sources.SourceRegistry.from_config(cfg)
        self.resolver = resolver.Resolver(self.registry)
        self.depdb = depdb.DepDB(os.path.join(data_dir, "plugins.db"))
        self._loaded: dict = {}

    def run(self) -> dict:
        plugin_manifests = scanner.scan_dir(self.plugins_dir)
        res = self.resolver.resolve(plugin_manifests)
        stats = self.depdb.rebuild(plugin_manifests, res)
        for tid in res.needed:
            if self.depdb.is_loaded(tid):
                self._load_tool(tid, res.needed[tid])
        return {
            "stats": stats,
            "plugin_count": len(plugin_manifests),
            "loaded_tools": sorted(self._loaded.keys()),
        }

    def _load_tool(self, tid: str, v: dict):
        mod = self._ensure_and_import(tid, v)
        if mod is not None:
            self._loaded[tid] = mod

    def _ensure_and_import(self, tid: str, v: dict) -> Optional[object]:
        mani = v["manifest"]
        entry = mani.get("entry") or "main.py"
        # 1) 优先本地 repo 目录（开发态，免下载）
        local_pkg = os.path.join(defaults.PROJECT_ROOT, "repo", tid)
        if os.path.isdir(local_pkg):
            return self._import_file(f"{tid}_tool",
                                      os.path.join(local_pkg, entry))
        # 2) 否则从源拉取（本地 pool / 远程下载；url 可为相对或绝对 http(s) 地址）
        cache_dir = os.path.join(self.data_dir, "pkg_cache", tid)
        src = next((s for s in self.registry.enabled()
                    if s.id == v["source_id"]), None)
        if src is None:
            print(f"[loader] 找不到提供 {tid} 的源，跳过")
            return None
        local_file = src.fetch_package(mani.get("url", f"{tid}.tar.gz"), cache_dir)
        extract = os.path.join(cache_dir, "extracted")
        os.makedirs(extract, exist_ok=True)
        with tarfile.open(local_file) as tf:
            tf.extractall(extract)
        for root, _d, files in os.walk(extract):
            if entry in files:
                return self._import_file(f"{tid}_tool",
                                          os.path.join(root, entry))
        return None

    @staticmethod
    def _import_file(modname: str, path: str):
        import sys
        spec = importlib.util.spec_from_file_location(modname, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod  # 必须先注册，否则 @dataclass 取模块字典失败
        spec.loader.exec_module(mod)
        return mod

    def loaded_tools(self) -> dict:
        return self._loaded
