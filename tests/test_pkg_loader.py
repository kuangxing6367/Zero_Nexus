"""多源 + 索引 + 依赖驱动加载 测试。

验证：
- 本地源（repo/ 含 5 包）索引可取
- 样例插件依赖 [exec, ws] -> pkg_exec/pkg_ws 有行(加载), 其余零行(跳过)
- 依赖方正确记录（运行时删插件可据此剪枝）
"""
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_plugin(tmp: str, pid: str, deps: str):
    d = os.path.join(tmp, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write(
            f'[package]\nid = "{pid}"\nname = "{pid}"\n'
            f'type = "plugin"\nversion = "1.0.0"\n'
            f"dependencies = {deps}\n"
        )


def _local_sources():
    return [{
        "id": "local", "type": "local",
        "path": os.path.join(ROOT, "repo"), "enabled": True,
    }]


def test_local_index_has_five_packages():
    from service.zkg import sources
    reg = sources.SourceRegistry.from_config({"sources": _local_sources()})
    idx = reg.sources[0].fetch_index()
    ids = {p["id"] for p in idx["packages"]}
    assert ids == {"exec", "ws", "udp", "store", "system"}
    # 每个条目都带 url + sha256（索引完整性）
    for p in idx["packages"]:
        assert p["url"].startswith("pool/")
        assert len(p["sha256"]) == 64


def test_depdb_skips_undepended_tools():
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    _write_plugin(tmp, "myapp", '["exec", "ws"]')

    ld = Loader(plugins_dir=tmp, data_dir=tmp, sources_cfg=_local_sources())
    out = ld.run()
    stats = out["stats"]

    assert stats["tools_total"] == 5
    assert stats["tools_loaded"] == 2       # exec, ws
    assert stats["tools_skipped"] == 3      # udp, store, system
    assert out["loaded_tools"] == ["exec", "ws"]

    # 依赖方记录（剪枝依据）
    assert ld.depdb.is_loaded("exec") is True
    assert ld.depdb.is_loaded("udp") is False
    assert ld.depdb.tool_dependents("exec") == ["myapp"]
    assert ld.depdb.tool_dependents("udp") == []


def test_loaded_tool_api_callable():
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    _write_plugin(tmp, "app2", '["exec", "system"]')

    ld = Loader(plugins_dir=tmp, data_dir=tmp, sources_cfg=_local_sources())
    ld.run()
    # exec 工具 API 可直接调用
    r = ld.loaded_tools()["exec"].run("echo hello")
    assert r.ok and "hello" in r.stdout
    info = ld.loaded_tools()["system"].get_system_info()
    assert "python_version" in info
