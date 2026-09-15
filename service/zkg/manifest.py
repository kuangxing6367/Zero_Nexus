"""包清单（manifest）模型。

每个插件 / 官方工具包在其根目录放一个 ``manifest.toml``，形如：

    [package]
    id = "exec"            # 全局唯一 id
    name = "Exec Tool"     # 展示名
    type = "tool"          # tool（官方机制）| plugin（用户应用）
    version = "1.0.0"
    description = "通用命令执行接口"
    dependencies = []      # 依赖的其他包 id（官方工具或插件）
    provides = ["exec"]    # 本包提供的机制 id（默认 = id）
    entry = "main.py"      # 可选：加载入口

读取用标准库 tomllib（Python 3.11+ 内置），不引入第三方依赖。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Manifest:
    id: str
    name: str
    type: str  # 'tool' | 'plugin'
    version: str
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    entry: Optional[str] = None
    path: str = ""          # 包在文件系统中的根目录（扫描时填入）
    source_id: Optional[str] = None  # 来自哪个源（local / 镜像 id）

    @classmethod
    def load(cls, path: str) -> "Manifest":
        """从 manifest.toml 路径加载。path 为 manifest 文件本身。"""
        with open(path, "rb") as f:
            data = tomllib.load(f)
        pkg = data.get("package", data)
        if "id" not in pkg or "version" not in pkg:
            raise ValueError(f"manifest 缺少必填字段 id/version: {path}")
        mid = pkg["id"]
        return cls(
            id=mid,
            name=pkg.get("name", mid),
            type=pkg.get("type", "plugin"),
            version=pkg["version"],
            description=pkg.get("description", ""),
            dependencies=list(pkg.get("dependencies", [])),
            provides=list(pkg.get("provides", [mid])),
            entry=pkg.get("entry"),
            path=str(path.parent) if hasattr(path, "parent") else "",
            source_id="local",
        )

    @classmethod
    def from_dict(cls, d: dict, source_id: str = "remote") -> "Manifest":
        mid = d["id"]
        return cls(
            id=mid,
            name=d.get("name", mid),
            type=d.get("type", "tool"),
            version=d["version"],
            description=d.get("description", ""),
            dependencies=list(d.get("dependencies", [])),
            provides=list(d.get("provides", [mid])),
            entry=d.get("entry"),
            source_id=source_id,
        )

    def to_index_entry(self) -> dict:
        """转成仓库索引条目（不含本地路径/源信息）。"""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "version": self.version,
            "description": self.description,
            "dependencies": self.dependencies,
            "provides": self.provides,
            "entry": self.entry,
        }
