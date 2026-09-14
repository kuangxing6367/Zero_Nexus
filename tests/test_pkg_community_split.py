"""社区包 + 多份索引自动合并 + 官方/社区分离 测试。

验证用户新增规则：
- 社区包与官方包同 schema（JSON 提供程序元数据 + 下载地址 url + sha256）
- 一个源可拆多份 index JSON，框架自动合并（显式 splits / 本地 glob）
- 官方源与社区源分开；同名包官方优先胜出
- 下载地址 url 支持绝对 http(s)/file URL
"""
import json
import os
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _make_local_repo(base, *, with_splits=False, with_glob=False):
    """造一个本地仓库目录，返回 dist 根。"""
    dist = os.path.join(base, "dist")
    os.makedirs(dist, exist_ok=True)
    pkgs_a = [
        {"id": "alpha", "name": "Alpha", "type": "tool", "version": "1.0.0",
         "url": "pool/alpha.tar.gz", "sha256": "0" * 64},
    ]
    pkgs_b = [
        {"id": "beta", "name": "Beta", "type": "tool", "version": "1.0.0",
         "url": "pool/beta.tar.gz", "sha256": "1" * 64},
    ]
    if with_splits:
        _write_json(os.path.join(dist, "index.json"),
                    {"generated_by": "t", "splits": ["indices/index-0.json"], "packages": []})
        _write_json(os.path.join(dist, "indices", "index-0.json"), {"packages": pkgs_a + pkgs_b})
    elif with_glob:
        _write_json(os.path.join(dist, "index.json"), {"packages": []})
        _write_json(os.path.join(dist, "indices", "index-0.json"), {"packages": pkgs_a})
        _write_json(os.path.join(dist, "index-1.json"), {"packages": pkgs_b})
    else:
        _write_json(os.path.join(dist, "index.json"), {"packages": pkgs_a + pkgs_b})
    return dist


def test_multi_file_index_via_splits():
    from core.zkg.sources import Source
    tmp = tempfile.mkdtemp()
    _make_local_repo(tmp, with_splits=True)
    src = Source(id="t", type="local", path=tmp, kind="community")
    idx = src.fetch_index()
    ids = {p["id"] for p in idx["packages"]}
    assert ids == {"alpha", "beta"}, ids
    # 下载地址字段存在
    assert all("url" in p and "sha256" in p for p in idx["packages"])


def test_multi_file_index_via_local_glob():
    from core.zkg.sources import Source
    tmp = tempfile.mkdtemp()
    _make_local_repo(tmp, with_glob=True)
    src = Source(id="t", type="local", path=tmp, kind="official")
    idx = src.fetch_index()
    ids = {p["id"] for p in idx["packages"]}
    assert ids == {"alpha", "beta"}, ids


def test_official_priority_over_community():
    from core.zkg.sources import Source, SourceRegistry
    from core.zkg.resolver import Resolver

    base = tempfile.mkdtemp()
    off = os.path.join(base, "official")
    com = os.path.join(base, "community")
    os.makedirs(off)
    os.makedirs(com)
    _write_json(os.path.join(off, "dist", "index.json"),
                {"packages": [{"id": "demo", "name": "Demo", "type": "tool",
                               "version": "1.0.0", "url": "pool/demo.tar.gz", "sha256": "0" * 64}]})
    _write_json(os.path.join(com, "dist", "index.json"),
                {"packages": [{"id": "demo", "name": "Demo", "type": "tool",
                               "version": "2.0.0", "url": "pool/demo.tar.gz", "sha256": "0" * 64}]})

    reg = SourceRegistry([
        Source(id="com", type="local", path=com, kind="community"),
        Source(id="off", type="local", path=off, kind="official"),
    ])
    assert {s.id for s in reg.official()} == {"off"}
    assert {s.id for s in reg.community()} == {"com"}

    tools = Resolver(reg)._collect_tools()
    # 同名包官方胜出
    assert tools["demo"]["manifest"]["version"] == "1.0.0"
    assert tools["demo"]["source_kind"] == "official"


def test_fetch_package_absolute_url():
    from core.zkg.sources import Source

    tmp = tempfile.mkdtemp()
    # 造一个 tar 包，用 file:// 绝对地址模拟「任意托管位置的下载地址」
    pkg_path = os.path.join(tmp, "remote-pkg.tar.gz")
    with open(pkg_path, "wb") as f:
        f.write(b"fake-tar-content")
    file_url = "file://" + urllib.request.pathname2url(pkg_path)

    src = Source(id="t", type="local", path=tmp, kind="community")
    dst = src.fetch_package(file_url, os.path.join(tmp, "cache"))
    assert os.path.isfile(dst)
    with open(dst, "rb") as f:
        assert f.read() == b"fake-tar-content"


def test_default_sources_are_official():
    from core.zkg import defaults
    for s in defaults.get_default_sources():
        assert s["kind"] == "official", s
