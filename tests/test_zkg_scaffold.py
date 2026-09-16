"""zkg new 脚手架 + ctx.zkg_tool 机制包访问口 测试。"""
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_scaffold_generates_valid_plugin():
    """zkg new 生成合法插件：manifest 可被 zkg 扫描、main.py 可导入且含 register。"""
    from service.zkg.cli import new

    tmp = tempfile.mkdtemp()
    target = new("demo_kv", deps=["store"], desc="演示插件",
                 plugins_dir=tmp)

    assert os.path.isfile(os.path.join(target, "main.py"))
    assert os.path.isfile(os.path.join(target, "manifest.toml"))

    # manifest.toml 能被 zkg scanner 解析，依赖正确
    from service.zkg import scanner
    ms = scanner.scan_dir(tmp)
    assert len(ms) == 1
    assert ms[0].id == "demo_kv"
    assert ms[0].dependencies == ["store"]

    # main.py 可作为模块导入且有 register
    spec = importlib.util.spec_from_file_location(
        "scaffold_demo", os.path.join(target, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.register)


def test_scaffold_rejects_bad_name_and_overwrite():
    from service.zkg.cli import new

    tmp = tempfile.mkdtemp()
    try:
        new("Bad-Name", plugins_dir=tmp)
        assert False, "应拒绝非法名称"
    except ValueError:
        pass
    new("okplugin", plugins_dir=tmp)
    try:
        new("okplugin", plugins_dir=tmp)
        assert False, "应拒绝覆盖已存在目录"
    except FileExistsError:
        pass


def test_ctx_zkg_tool_accessor():
    """ctx.zkg_tool(name) 从 framework.zkg_tools 取机制包；未加载返回 None。"""
    from core.ctx.base import PluginContextBase

    class FakeFramework:
        zkg_tools = {"store": "STORE_MODULE_SENTINEL"}

    base = PluginContextBase.__new__(PluginContextBase)
    base._framework = FakeFramework()
    assert base.zkg_tool("store") == "STORE_MODULE_SENTINEL"

    class NoToolsFramework:
        pass

    base._framework = NoToolsFramework()
    assert base.zkg_tool("store") is None


def test_api_version_compat_rules():
    """api_version 兼容区间解析：裸数字 / 区间 / 未声明 / 坏区间。"""
    from service.zkg.manifest import Manifest

    def m(spec):
        return Manifest(id="x", name="x", type="plugin",
                        version="0.1.0", api_version=spec)

    assert m(None).api_version_ok(1) is True       # 未声明 → 兼容
    assert m("").api_version_ok(1) is True
    assert m("1").api_version_ok(1) is True        # 裸数字 = 主版本精确匹配
    assert m("1").api_version_ok(2) is False
    assert m(">=1").api_version_ok(2) is True
    assert m(">=1,<2").api_version_ok(1) is True
    assert m(">=1,<2").api_version_ok(2) is False
    assert m("!=1").api_version_ok(2) is True
    assert m("garbage").api_version_ok(1) is False  # 坏区间 → 不兼容


def _write_plugin(root: str, pid: str, deps: str) -> str:
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write(
            f'[package]\nid = "{pid}"\nname = "{pid}"\n'
            f'type = "plugin"\nversion = "1.0.0"\n'
            f"dependencies = {deps}\n"
        )
    return d


def _local_sources(src: str) -> list:
    return [{"id": "local", "type": "local", "path": src, "enabled": True}]


def test_loader_flags_api_incompatible():
    """loader 校验：声明不兼容 api_version 的插件被记录进 api_incompatible。"""
    import json as _json
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "dist"))
    os.makedirs(os.path.join(src, "pool"))
    with open(os.path.join(ROOT, "repo", "dist", "index.json"),
              encoding="utf-8") as f:
        idx = _json.load(f)
    with open(os.path.join(src, "dist", "index.json"), "w",
              encoding="utf-8") as f:
        _json.dump({"packages": idx["packages"]}, f)
    shutil.copy(os.path.join(ROOT, "repo", "pool", "store-1.0.0.tar.gz"),
                os.path.join(src, "pool", "store-1.0.0.tar.gz"))

    plugins = os.path.join(tmp, "plugins")
    d = _write_plugin(plugins, "old_app", '["store"]')
    with open(os.path.join(d, "manifest.toml"), "a", encoding="utf-8") as f:
        f.write('api_version = "99"\n')

    ld = Loader(src, tmp, sources_cfg=_local_sources(src),
                scan_roots=[plugins], plugin_api_version=1)
    out = ld.run()
    assert out["api_incompatible"] == ["old_app"]
    # 兼容的插件不受影响
    assert out["loaded_tools"] == ["store"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"结果: {passed}/{len(fns)}")
    sys.exit(0 if passed == len(fns) else 1)
