"""zkg 全链路端到端测试（P0-1）。

覆盖：
1. 下载路径加载：插件目录不在仓库根（无 dev-mode 目录），工具经
   fetch_package → sha256 校验 → 解压 → import 完整跑通。
2. 多扫描根合并：主根（仓库）+ 插件根（用户插件 manifest 声明依赖），
   模拟 startup.py 生产接线；同 id 去重主根优先。
3. sha256 篡改拒绝：索引声明的哈希与包体不符时拒绝加载。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _make_source(tmp: str, tamper_sha: bool = False) -> str:
    """构造一个最小本地源：dist/index.json（只含 store）+ pool 包体。"""
    src = os.path.join(tmp, "repo_src")
    os.makedirs(os.path.join(src, "dist"))
    os.makedirs(os.path.join(src, "pool"))
    with open(os.path.join(ROOT, "repo", "dist", "index.json"),
              encoding="utf-8") as f:
        idx = json.load(f)
    store = next(p for p in idx["packages"] if p["id"] == "store")
    if tamper_sha:
        store["sha256"] = "0" * 64
    with open(os.path.join(src, "dist", "index.json"), "w",
              encoding="utf-8") as f:
        json.dump({"generated_by": "test", "packages": [store]}, f)
    shutil.copy(os.path.join(ROOT, "repo", "pool", "store-1.0.0.tar.gz"),
                os.path.join(src, "pool", "store-1.0.0.tar.gz"))
    return src


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


def test_full_chain_download_and_load():
    """下载路径（无 dev-mode 目录）完整跑通：解析→拉包→校验→加载→API 可用。"""
    from service.zkg import defaults
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    src = _make_source(tmp)
    plugins = os.path.join(tmp, "plugins")
    _write_plugin(plugins, "myapp", '["store"]')

    # 屏蔽真实仓库的 dev-mode 目录，强制走「拉包→校验→解压→导入」下载路径
    real_root = defaults.PROJECT_ROOT
    defaults.PROJECT_ROOT = tmp
    try:
        ld = Loader(src, tmp, sources_cfg=_local_sources(src),
                    scan_roots=[plugins])
        out = ld.run()
    finally:
        defaults.PROJECT_ROOT = real_root

    assert out["plugin_count"] == 1
    assert out["loaded_tools"] == ["store"]
    kv = ld.loaded_tools()["store"].KV(
        os.path.join(tmp, "kv.json"))
    kv.set("k", "v")
    assert kv.get("k") == "v"


def test_multi_scan_root_dedupe():
    """主根（仓库包）+ 插件根（用户插件）合并；主根同 id 优先。"""
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    src = _make_source(tmp)
    plugins = os.path.join(tmp, "plugins")
    _write_plugin(plugins, "myapp", '["store"]')

    ld = Loader(ROOT, tmp, sources_cfg=_local_sources(src),
                scan_roots=[plugins])
    out = ld.run()
    # 主根=整个项目（含 repo 5 工具包与真实 software/plugins 插件）+ 1 个临时插件
    from service.zkg import scanner as _scanner
    n_main = len(_scanner.scan_dir(ROOT))
    assert out["plugin_count"] == n_main + 1
    assert out["loaded_tools"] == ["store"]
    # demo_kv（真实插件）与 myapp（临时插件）都依赖 store
    assert set(ld.depdb.tool_dependents("store")) >= {"demo_kv", "myapp"}


def test_sha256_mismatch_rejected():
    """索引哈希被篡改时，拉包校验失败 → 拒绝加载。"""
    from service.zkg import defaults
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    src = _make_source(tmp, tamper_sha=True)
    plugins = os.path.join(tmp, "plugins")
    _write_plugin(plugins, "myapp", '["store"]')

    # 同样屏蔽 dev-mode 目录，确保校验真的发生在下载路径上
    real_root = defaults.PROJECT_ROOT
    defaults.PROJECT_ROOT = tmp
    try:
        ld = Loader(src, tmp, sources_cfg=_local_sources(src),
                    scan_roots=[plugins])
        out = ld.run()
    finally:
        defaults.PROJECT_ROOT = real_root
    assert out["loaded_tools"] == []


def test_production_wiring_real_repo():
    """生产接线验证：真实 repo/ 主根 + 真实 software/plugins/（demo_kv 插件），
    模拟 startup.py 的 zkg 初始化路径。"""
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    ld = Loader(os.path.join(ROOT, "repo"), tmp,
                sources_cfg=_local_sources(os.path.join(ROOT, "repo")),
                scan_roots=[os.path.join(ROOT, "software", "plugins")])
    out = ld.run()

    ids = {m.id for m in __import__("service.zkg.scanner", fromlist=["scan_dir"])
           .scan_dir(os.path.join(ROOT, "software", "plugins"))}
    assert "demo_kv" in ids
    # 主根（repo/ 全部工具包）+ 插件根全部插件
    n_tools = len(__import__("service.zkg.scanner", fromlist=["scan_dir"])
                  .scan_dir(os.path.join(ROOT, "repo")))
    assert out["plugin_count"] == n_tools + len(ids)
    # demo_kv 依赖 store → store 被按需加载并暴露
    assert "store" in out["loaded_tools"]
    assert callable(ld.loaded_tools()["store"].KV)


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
    import sys
    sys.exit(0 if passed == len(fns) else 1)
