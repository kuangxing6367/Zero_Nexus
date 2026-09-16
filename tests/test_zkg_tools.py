"""zkg 官方机制包工具箱测试：http / retry / lock / validate 四包 API + 全链路加载。"""
import os
import shutil
import sys
import tempfile

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


# ── http ──

def test_http_local_roundtrip():
    """对本地 HTTP 服务做 GET/POST/JSON 往返 + 错误不抛异常。"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"method": "GET", "q": self.path})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            data = _json.loads(self.rfile.read(n) or b"{}")
            self._send(201, {"echo": data, "ct": self.headers.get("Content-Type", "")})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        http = _import_tool("http")
        r = http.get(f"http://127.0.0.1:{port}/x", params={"a": "1"})
        assert r.ok and r.status == 200 and r.data["method"] == "GET"
        assert "a=1" in r.data["q"]

        r = http.post(f"http://127.0.0.1:{port}/x", json={"k": "中文"})
        assert r.ok and r.status == 201
        assert r.data["echo"]["k"] == "中文"
        assert "application/json" in r.data["ct"]

        r = http.get("http://127.0.0.1:1/nope")   # 端口必拒
        assert not r.ok and r.error
        assert r.data is None
    finally:
        srv.shutdown()


# ── retry ──

def test_retry_success_and_exhaustion():
    retry = _import_tool("retry")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("boom")
        return "ok"

    assert retry.retry(flaky, attempts=5, base_delay=0.001) == "ok"
    assert calls["n"] == 3

    calls["n"] = 0
    try:
        retry.retry(lambda: (_ for _ in ()).throw(ValueError("always")),
                    attempts=2, base_delay=0.001, exceptions=(ValueError,))
        assert False, "应抛 RetryExhausted"
    except retry.RetryExhausted as e:
        assert isinstance(e.last, ValueError)

    # 非白名单异常立即穿透，不重试
    calls["n"] = 0
    try:
        retry.retry(lambda: (_ for _ in ()).throw(KeyError("x")),
                    attempts=3, base_delay=0.001, exceptions=(OSError,))
        assert False
    except KeyError:
        pass

    # 装饰器形式
    @retry.retryable(attempts=2, base_delay=0.001)
    def twice():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("first")
        return "done"

    assert twice() == "done" and calls["n"] == 2


# ── lock ──

def test_lock_named_and_filelock():
    import threading
    lock = _import_tool("lock")

    # 命名锁单例
    assert lock.named("a") is lock.named("a")
    assert lock.named("a") is not lock.named("b")

    # 进程内互斥（非阻塞二次获取失败）
    lk = lock.named("probe")
    assert lk.acquire(blocking=False) is True
    assert lk.acquire(blocking=False) is False
    lk.release()

    # 文件锁：同进程重入（同路径不同实例）+ 释放后可再取
    tmp = tempfile.mkdtemp()
    with lock.FileLock(os.path.join(tmp, "l.lock")) as fl:
        with lock.FileLock(os.path.join(tmp, "l.lock")) as fl2:
            assert fl2._local_depth == 1
            assert lock._fl_registry[os.path.abspath(fl.path)]["depth"] == 2
    assert os.path.abspath(fl.path) not in lock._fl_registry
    with lock.FileLock(os.path.join(tmp, "l.lock"), timeout=1.0):
        pass

    # 跨线程互斥：持有者线程外获取应超时
    def _try_lock():
        try:
            lock.FileLock(os.path.join(tmp, "m.lock"), timeout=0.3).acquire()
            return True
        except lock.LockTimeout:
            return False

    done = {}
    with lock.FileLock(os.path.join(tmp, "m.lock")):
        t = threading.Thread(target=lambda: done.setdefault("ok", _try_lock()))
        t.start()
        t.join()
    assert done["ok"] is False


# ── validate ──

def test_validate_check():
    v = _import_tool("validate")
    schema = {
        "name": {"type": str, "required": True, "min": 2, "max": 10},
        "age": {"type": int, "min": 0, "max": 150, "default": 18},
        "level": {"choices": ["a", "b"], "default": "a"},
        "score": {"type": float},
        "active": {"type": bool},
        "extra": None,   # 规则为 None 也应可用（仅 required 语义）
    }
    out = v.check({"name": " 张三 ", "age": "30", "score": "9.5",
                   "active": "yes", "junk": 1}, schema)
    assert out["name"] == "张三"          # strip
    assert out["age"] == 30              # str→int 收敛
    assert out["level"] == "a"           # default
    assert out["score"] == 9.5
    assert out["active"] is True
    assert "junk" not in out             # 未声明字段丢弃

    try:
        v.check({"name": "x", "age": 999, "level": "c"}, schema)
        assert False, "应抛 ValidationError"
    except v.ValidationError as e:
        fields = {f for f, _ in e.errors}
        assert "name" in fields and "age" in fields and "level" in fields


# ── 全链路：多扫描根 + 依赖加载新工具 ──

def test_loader_loads_new_tools():
    """临时插件依赖全部 4 个新工具 → loader 从本地仓库按需加载。"""
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp()
    plugins = os.path.join(tmp, "plugins")
    d = os.path.join(plugins, "toolapp")
    os.makedirs(d)
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write('[package]\nid = "toolapp"\nname = "toolapp"\n'
                'type = "plugin"\nversion = "1.0.0"\n'
                'dependencies = ["http", "retry", "lock", "validate"]\n')

    real_root = os.getcwd()
    os.chdir(ROOT)  # loader dev-mode 依赖 PROJECT_ROOT，这里直接给主根即可
    try:
        ld = Loader(os.path.join(ROOT, "repo"), tmp,
                    sources_cfg=[{"id": "local", "type": "local",
                                  "path": os.path.join(ROOT, "repo"),
                                  "enabled": True}],
                    scan_roots=[plugins])
        out = ld.run()
    finally:
        os.chdir(real_root)

    for tid in ("http", "retry", "lock", "validate"):
        assert tid in out["loaded_tools"], out["loaded_tools"]
    # API 真的可用
    assert callable(ld.loaded_tools()["http"].get)
    assert callable(ld.loaded_tools()["validate"].check)


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
