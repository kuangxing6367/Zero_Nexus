"""官方插件装载节点（extensions/ 目录）。

- 双进程角色分派：按插件 ``__plugin_meta__['process']`` 静态解析（不执行插件），
  或按 ``dual_process.extensions`` 显式名单覆盖。
- 装载后调用插件 ``register(ctx)``，并把模块登记进 plugin_loader。
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import os
import sys

from core.kernel.paths import project_root

logger = logging.getLogger("zernus")


def read_plugin_process_tag(main_file: str):
    """不执行插件，静态解析 __plugin_meta__ 里的 process 进程归属标记。

    解析失败返回 None（按插件侧能力处理）。
    """
    try:
        with open(main_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=main_file)
    except Exception:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__plugin_meta__":
                    try:
                        meta = ast.literal_eval(node.value)
                    except Exception:
                        return None
                    if isinstance(meta, dict):
                        return meta.get("process")
    return None


def core_plugin_is_core_side(name: str, main_file: str, explicit_core) -> bool:
    """判断官方插件是否属于核心进程侧（协议/Web 基础设施）"""
    if explicit_core is not None:
        return name in explicit_core
    return read_plugin_process_tag(main_file) == "core"


def load_extensions(fw):
    """加载官方插件（extensions/ 目录）"""
    extensions_dir = os.path.join(project_root(), "extensions")
    if not os.path.isdir(extensions_dir):
        logger.warning(f"extensions 目录不存在: {extensions_dir}")
        return

    # 向后兼容：旧配置键 core_plugins 仍可被识别（新键 extensions 优先）
    core_cfg = fw.config.get("extensions", fw.config.get("core_plugins", {}))

    dual = fw.config.get("dual_process", {})
    _explicit_core = dual.get("extensions", dual.get("core_plugins"))
    if isinstance(_explicit_core, list) and _explicit_core:
        _explicit_core = set(_explicit_core)
    else:
        _explicit_core = None
    _role = getattr(fw, "_role", "standard")

    for name in os.listdir(extensions_dir):
        if name.startswith("_"):
            continue
        plugin_dir = os.path.join(extensions_dir, name)
        main_file = os.path.join(plugin_dir, "main.py")
        if not os.path.isfile(main_file):
            continue

        # 双进程角色分派（单进程 standard 不过滤，全部加载）
        if _role in ("core", "host"):
            _core_side = core_plugin_is_core_side(name, main_file, _explicit_core)
            if _role == "core" and not _core_side:
                continue
            if _role == "host" and _core_side:
                continue

        enabled = core_cfg.get(name, True)
        if enabled is False:
            logger.info(f"官方插件 [{name}] 已禁用 (extensions.{name}: false)")
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"core_plugin_{name}", main_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"core_plugin_{name}"] = module
            spec.loader.exec_module(module)

            from core.ctx import PluginContext
            ctx = PluginContext(f"core:{name}", fw)
            module.ctx = ctx

            if hasattr(module, "register"):
                module.register(ctx)
                logger.info(f"官方插件 [{name}] 已加载")
                fw._loaded_extensions.append(name)

                with fw.plugin_loader._lock:
                    meta = getattr(module, "__plugin_meta__", {})
                    fw.plugin_loader._loaded_plugins[name] = {
                        "module": module,
                        "path": plugin_dir,
                        "meta": meta,
                        "priority": meta.get("priority", 50),
                        "yaml": {},
                    }
            else:
                logger.warning(f"官方插件 [{name}] 无 register 函数")
        except Exception as e:
            logger.error(f"官方插件 [{name}] 加载失败: {e}", exc_info=True)
