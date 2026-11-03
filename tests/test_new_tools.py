"""新增机制包测试：cache / ratelimit / text / pathsafe / hashcode 五包 API + 全链路加载。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _import_tool(tid: str):
    import importlib.util
    path = os.path.join(ROOT, "repo", tid, "main.py")
    spec = importlib.util.spec_from_file_location(f"tool_{tid}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"tool_{tid}"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── cache ──

def test_cache_lru_and_ttl():
    c = _import_tool("cache")
    kv = c.Cache(maxsize=2)
    kv.set("a", 1)
    kv.set("b", 2)
    assert kv.get("a") == 1
    kv.set("c", 3)                    # 超容量淘汰最久未用（b）
    assert kv.get("b") is None
    assert kv.get("a") == 1 and kv.get("c") == 3
    st = kv.stats()
    assert st["size"] == 2 and st["hits"] >= 1

    # TTL 过期
    t = c.Cache(maxsize=4)
    t.set("x", 1, ttl=0.05)
    assert t.get("x") == 1
    time.sleep(0.08)
    assert t.get("x") is None

    # 装饰器缓存
    calls = {"n": 0}

    @c.cached(c.Cache(maxsize=4))
    def f(n):
        calls["n"] += 1
        return n * 2

    assert f(3) == 6 and f(3) == 6
    assert calls["n"] == 1


# ── ratelimit ──

def test_ratelimit_token_bucket():
    r = _import_tool("ratelimit")
    tb = r.TokenBucket(rate=100, burst=2)
    assert tb.try_acquire() is True
    assert tb.try_acquire() is True
    assert tb.try_acquire() is False   # 桶空
    assert tb.acquire(1, timeout=1.0) is True   # 等补充后成功
    # 容量上限：长时间闲置不会超过 burst
    tb2 = r.TokenBucket(rate=1000, burst=1)
    time.sleep(0.02)
    assert tb2.tokens <= 1.0


# ── text ──

def test_text_utils():
    t = _import_tool("text")
    assert t.width("ab") == 2
    assert t.width("中文") == 4         # 东亚宽字符算 2
    assert t.width("a中") == 3
    assert t.truncate("abcdef", 10) == "abcdef"
    assert t.truncate("中文字符串测试", 6) == "中文…"
    assert t.mask("13800138000", 3, 4) == "138****8000"
    assert t.mask("ab", 1, 1) == "ab"   # 保留位 >= 长度，原样返回
    assert t.slugify("Hello World 测试") == "hello-world"


# ── pathsafe ──

def test_pathsafe_join():
    p = _import_tool("pathsafe")
    base = os.path.join(ROOT, "data")
    inside = p.safe_join(base, "a", "b.txt")
    assert p.is_within(base, inside)
    assert not p.is_within(base, os.path.join(ROOT, "config.yaml"))
    try:
        p.safe_join(base, "..", "..", "escape.txt")
        assert False, "应抛 PathTraversalError"
    except p.PathTraversalError:
        pass


# ── hashcode ──

def test_hashcode_utils():
    h = _import_tool("hashcode")
    assert h.sha256("x") == (
        "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881")
    assert len(h.sha1("x")) == 40
    assert len(h.md5("x")) == 32
    assert h.b64encode("中文") and h.b64decode(h.b64encode("中文")) == "中文"
    assert h.hex_decode(h.hex_encode("abc")) == "abc"
    assert h.urldecode(h.urlencode("a b/中")) == "a b/中"
    assert len(h.short_id(8)) == 8
    assert h.short_id() != h.short_id()
    assert len(h.uuid4()) == 36


# ── 全链路：依赖声明 → loader 按需加载 ──

def test_loader_loads_new_tools():
    import tempfile
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    plugins = os.path.join(tmp, "plugins")
    d = os.path.join(plugins, "newtoolapp")
    os.makedirs(d)
    deps = ["cache", "ratelimit", "text", "pathsafe", "hashcode"]
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write('[package]\nid = "newtoolapp"\nname = "newtoolapp"\n'
                'type = "plugin"\nversion = "1.0.0"\n'
                f'dependencies = {deps!r}\n'.replace("'", '"'))

    real_root = os.getcwd()
    os.chdir(ROOT)
    try:
        ld = Loader(os.path.join(ROOT, "repo"), tmp,
                    sources_cfg=[{"id": "local", "type": "local",
                                  "path": os.path.join(ROOT, "repo"),
                                  "enabled": True}],
                    scan_roots=[plugins])
        out = ld.run()
    finally:
        os.chdir(real_root)

    for tid in deps:
        assert tid in out["loaded_tools"], out["loaded_tools"]
    tools = ld.loaded_tools()
    assert callable(tools["cache"].Cache)
    assert callable(tools["text"].truncate)
    assert callable(tools["pathsafe"].safe_join)


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
