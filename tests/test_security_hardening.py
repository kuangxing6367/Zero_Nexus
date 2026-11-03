# -*- coding: utf-8 -*-
"""安全加固回归测试（P0/P1 加固冲刺）：

1. OneBot 接入端门禁：非回环监听 + 空 token 拒绝启动；回环 + 空 token 可启动
2. OneBot token 工具：回环判定 / 常数时间比对语义
3. zkg 加载器：源未声明 sha256 → 拒绝加载；恶意 tar（路径穿越）→ 拒绝解包；
   正常包（带 sha256）→ 正常加载
4. WebUI 强制改密：must_change_password=1 的账号除改密/登出/自身信息外全部 403，
   改密成功后恢复并清除标记
5. CORS：未配置白名单 → 不回任何 CORS 头；配置白名单 → 仅白名单内 Origin 回显
6. API Key：新令牌 sha256 落库；遗留明文令牌验证通过后就地升级为哈希
7. 框架自更新 fail-closed：admin 角色访问 super 端点 403；super 角色未配置
   update_expected_sha256 时更新被拒绝

脚本式测试（顶层直接运行，不适用 unittest discover）：
    python tests/test_security_hardening.py
"""
import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from software.extensions.webui.webapp import create_web_app
from software.extensions.webui.password import hash_password

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ── 通用 WebUI 测试底座（真实 create_web_app + Flask test client + SQLite 内存库）──

class MiniDB:
    """sqlite3 内存库的最小适配：提供项目 Database 的 query_one/query/execute 语义。"""

    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _translate(sql):
        return sql.replace("%s", "?").replace("NOW()", "datetime('now','localtime')")

    def query_one(self, sql, params=()):
        row = self.conn.execute(self._translate(sql), params).fetchone()
        return dict(row) if row else None

    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(self._translate(sql), params).fetchall()]

    def execute(self, sql, params=()):
        cur = self.conn.execute(self._translate(sql), params)
        self.conn.commit()
        return cur.rowcount

    # migrations._ensure_admin_account 用到的两个方法
    def table_exists(self, name):
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None


_SCHEMA = """
    CREATE TABLE admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        token TEXT DEFAULT NULL,
        token_created_at TEXT DEFAULT NULL,
        role TEXT DEFAULT 'admin',
        must_change_password INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        last_login_at TEXT DEFAULT NULL,
        last_login_ip TEXT DEFAULT NULL
    );
    CREATE TABLE api_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'admin',
        created_by TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL,
        expires_at TEXT DEFAULT NULL,
        last_used_at TEXT DEFAULT NULL,
        is_active INTEGER DEFAULT 1
    );
    CREATE TABLE audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER, admin_name TEXT, action TEXT,
        target_type TEXT, target_name TEXT, detail TEXT,
        ip_address TEXT, result TEXT, error_message TEXT
    );
"""


def build_app(web_cfg=None, security_cfg=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    framework = MagicMock()
    framework.config = {"web": web_cfg or {}, "security": security_cfg or {}}
    framework.db = MiniDB(conn)
    framework.plugin_loader.plugins_dir = tempfile.mkdtemp(prefix="zrn_plugins_")
    app = create_web_app(framework)
    return conn, app.test_client()


def _seed_admin(conn, username="admin", password="admin123", role="super",
                must_change=0):
    conn.execute(
        "INSERT INTO admin_users (username, password_hash, role, must_change_password, is_active) "
        "VALUES (?,?,?,?,1)",
        (username, hash_password(password), role, must_change),
    )
    conn.commit()


def _login(client, username, password):
    r = client.post("/api/login", json={"username": username, "password": password})
    body = r.get_json() or {}
    return (body.get("data") or {}).get("token", ""), body


def _bearer(token):
    return {"Authorization": "Bearer " + token}


# ── 1/2. OneBot 接入端门禁 ──────────────────────────────────────────

def test_onebot_gate():
    print("-- OneBot 接入端门禁 --")
    import software.extensions.onebot_adapter.main as ob

    check("回环判定 127.x/localhost/::1",
          ob._is_loopback_host('127.0.0.1') and ob._is_loopback_host('127.9.9.9')
          and ob._is_loopback_host('localhost') and ob._is_loopback_host('::1'))
    check("非回环判定 0.0.0.0/局域网/空",
          not ob._is_loopback_host('0.0.0.0') and not ob._is_loopback_host('192.168.1.5')
          and not ob._is_loopback_host(''))
    check("token 比对：非空且相等通过", ob._token_ok('tok', 'tok'))
    check("token 比对：不一致拒绝", not ob._token_ok('x', 'tok'))
    check("token 比对：缺失拒绝", not ob._token_ok('', 'tok') and not ob._token_ok(None, 'tok'))
    check("token 比对：期望为空=未启用校验，放行", ob._token_ok('anything', ''))

    class _FakeServices:
        def __init__(self):
            self.names = []

        def register(self, name, obj):
            self.names.append(name)

    def _fw(host, token, port):
        return types.SimpleNamespace(
            config={'onebot': {'enabled': True, 'listen_host': host,
                               'listen_port': port, 'access_token': token},
                    'web': {}},
            services=_FakeServices(),
            build_ssl_context=lambda: None,
        )

    def _register(fw):
        ctx = types.SimpleNamespace(_framework=fw, log=lambda m: None)
        ob.register(ctx)
        return ob._adapter_instance

    # 非回环 + 空 token → 拒绝启动（ws 服务 _running 保持 False）
    port = _free_port()
    inst = _register(_fw('0.0.0.0', '', port))
    check("非回环 + 空 token 拒绝启动",
          inst is not None and inst.ws_server._running is False,
          f"running={inst.ws_server._running if inst else None}")

    # 回环 + 空 token → 允许启动（本地调试不受影响）
    # ws 服务 start() 依赖运行中的事件循环（与生产一致：register 在 loop 线程内被调）
    port2 = _free_port()
    async def _start_on_loopback():
        return _register(_fw('127.0.0.1', '', port2))

    inst2 = asyncio.run(_start_on_loopback())
    check("回环 + 空 token 允许启动", inst2 is not None and inst2.ws_server._running is True,
          f"running={inst2.ws_server._running if inst2 else None}")
    if inst2 is not None:
        asyncio.run(inst2.stop())
    ob._adapter_instance = None


# ── 3. zkg 加载器：完整性强制 + 恶意 tar 防护 ────────────────────────

def _make_tool_tar(pkg_id: str, evil: bool = False) -> bytes:
    """打包一个最小机制包；evil=True 时额外携带一个路径穿越成员。"""
    import io
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        code = f"TOOL_ID = {pkg_id!r}\nclass KV:\n    pass\n".encode('utf-8')
        ti = tarfile.TarInfo(name=f"{pkg_id}/main.py")
        ti.size = len(code)
        tf.addfile(ti, io.BytesIO(code))
        if evil:
            evil_bytes = b'pwned\n'
            te = tarfile.TarInfo(name=f"../evil_{pkg_id}.txt")
            te.size = len(evil_bytes)
            tf.addfile(te, io.BytesIO(evil_bytes))
    return buf.getvalue()


def _make_source(pkg_id: str, data: bytes, with_sha: bool = True) -> str:
    src = tempfile.mkdtemp(prefix="zrn_src_")
    os.makedirs(os.path.join(src, "pool"))
    os.makedirs(os.path.join(src, "dist"))
    with open(os.path.join(src, "pool", f"{pkg_id}.tar.gz"), "wb") as f:
        f.write(data)
    entry = {"id": pkg_id, "url": f"pool/{pkg_id}.tar.gz"}
    if with_sha:
        entry["sha256"] = hashlib.sha256(data).hexdigest()
    with open(os.path.join(src, "dist", "index.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_by": "test", "packages": [entry]}, f)
    return src


def _run_loader(src: str, pkg_id: str):
    from service.zkg import defaults
    from service.zkg.loader import Loader

    tmp = tempfile.mkdtemp(prefix="zrn_ld_")
    plugins = os.path.join(tmp, "plugins", pkg_id)
    os.makedirs(plugins, exist_ok=True)
    with open(os.path.join(plugins, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write(f'[package]\nid = "{pkg_id}_app"\nname = "{pkg_id}_app"\n'
                f'type = "plugin"\nversion = "1.0.0"\ndependencies = ["{pkg_id}"]\n')

    real_root = defaults.PROJECT_ROOT
    defaults.PROJECT_ROOT = tmp          # 屏蔽真实仓库的 dev-mode 目录，强制走拉包路径
    try:
        ld = Loader(tmp, tmp,
                    sources_cfg=[{"id": "local", "type": "local", "path": src,
                                  "enabled": True}],
                    scan_roots=[os.path.join(tmp, "plugins")])
        out = ld.run()
    finally:
        defaults.PROJECT_ROOT = real_root
    return out, tmp


def test_zkg_loader_integrity():
    print("-- zkg 加载器完整性强制 --")
    from service.zkg.loader import Loader

    # 未声明 sha256 → fail closed
    pkg_a = "nosha_tool_xyz"
    src = _make_source(pkg_a, _make_tool_tar(pkg_a), with_sha=False)
    out, _ = _run_loader(src, pkg_a)
    check("源未声明 sha256 → 拒绝加载", out["loaded_tools"] == [],
          f"loaded={out['loaded_tools']}")

    # 恶意 tar（../ 穿越）+ 合法 sha256 → 拒绝解包，且未写出 extract 目录之外
    pkg_b = "evil_tool_xyz"
    src_b = _make_source(pkg_b, _make_tool_tar(pkg_b, evil=True), with_sha=True)
    out_b, tmp_b = _run_loader(src_b, pkg_b)
    escaped = os.path.join(tmp_b, "pkg_cache", pkg_b, f"evil_{pkg_b}.txt")
    check("路径穿越 tar → 拒绝加载", out_b["loaded_tools"] == [],
          f"loaded={out_b['loaded_tools']}")
    check("穿越成员未逃出 extract 目录", not os.path.exists(escaped),
          f"exists={os.path.exists(escaped)}")

    # 正常包（带 sha256）→ 正常加载（门禁不误伤）
    pkg_c = "good_tool_xyz"
    src_c = _make_source(pkg_c, _make_tool_tar(pkg_c), with_sha=True)
    out_c, _ = _run_loader(src_c, pkg_c)
    check("正常包带 sha256 → 正常加载", out_c["loaded_tools"] == [pkg_c],
          f"loaded={out_c['loaded_tools']}")


# ── 4. 强制改密 ─────────────────────────────────────────────────────

def test_must_change_password():
    print("-- WebUI 强制改密 --")
    conn, client = build_app()
    _seed_admin(conn, must_change=1)

    token, body = _login(client, "admin", "admin123")
    check("默认密码账号可登录", token and body.get("code") == 0, f"body={body}")
    check("登录响应携带 must_change_password",
          (body.get("data") or {}).get("must_change_password") is True,
          f"data={body.get('data')}")

    r = client.get("/api/me", headers=_bearer(token))
    check("改密前 /api/me 放行", r.status_code == 200, f"status={r.status_code}")

    r = client.get("/api/plugins", headers=_bearer(token))
    body = r.get_json() or {}
    check("改密前管理接口 403", r.status_code == 403, f"status={r.status_code}")
    check("403 响应标记 must_change_password",
          (body.get("data") or {}).get("must_change_password") is True,
          f"body={body}")

    r = client.post("/api/change_password", headers=_bearer(token),
                    json={"old_password": "admin123", "new_password": "newpass456"})
    check("改密成功", r.status_code == 200 and (r.get_json() or {}).get("code") == 0,
          f"status={r.status_code} body={r.get_json()}")
    row = conn.execute(
        "SELECT must_change_password FROM admin_users WHERE username='admin'").fetchone()
    check("改密后清除强制标记", row["must_change_password"] == 0,
          f"flag={row['must_change_password']}")
    r = client.get("/api/me", headers=_bearer(token))
    check("改密后新密码继续可用", r.status_code == 200, f"status={r.status_code}")


# ── 5. CORS 默认拒绝 ────────────────────────────────────────────────

def test_cors_default_deny():
    print("-- CORS 默认拒绝 --")
    conn, client = build_app()
    r = client.get("/api/version", headers={"Origin": "http://evil.example"})
    check("未配置白名单 → 不回 CORS 头",
          "Access-Control-Allow-Origin" not in r.headers,
          f"headers={dict(r.headers)}")

    conn2, client2 = build_app(
        web_cfg={"cors_allowed_origins": ["http://ok.example"]})
    r = client2.get("/api/version", headers={"Origin": "http://ok.example"})
    check("白名单内 Origin 正常回显",
          r.headers.get("Access-Control-Allow-Origin") == "http://ok.example",
          f"got={r.headers.get('Access-Control-Allow-Origin')}")
    r = client2.get("/api/version", headers={"Origin": "http://evil.example"})
    check("白名单外 Origin 不回显",
          "Access-Control-Allow-Origin" not in r.headers,
          f"got={r.headers.get('Access-Control-Allow-Origin')}")


# ── 6. API Key 哈希落库 ─────────────────────────────────────────────

def test_apikey_hash_at_rest():
    print("-- API Key 哈希落库 --")
    conn, client = build_app()
    _seed_admin(conn)                       # super，无强制改密
    token, _ = _login(client, "admin", "admin123")

    r = client.post("/api/apikeys", headers=_bearer(token),
                    json={"name": "ci", "role": "admin"})
    body = r.get_json() or {}
    api_key = (body.get("data") or {}).get("token", "")
    check("创建 API Key 返回明文（仅一次）", len(api_key) == 64, f"body={body}")
    row = conn.execute("SELECT token FROM api_tokens WHERE name='ci'").fetchone()
    check("库内存 sha256 而非明文",
          row and row["token"] == hashlib.sha256(api_key.encode()).hexdigest()
          and row["token"] != api_key,
          f"stored={row['token'][:16] if row else None}…")
    r = client.get("/api/me", headers=_bearer(api_key))
    check("新 API Key 可鉴权", r.status_code == 200
          and str((r.get_json() or {}).get("data", {}).get("id", "")).startswith("api:"),
          f"status={r.status_code}")

    # 遗留明文 Key：验证通过后就地升级为哈希
    legacy = "L" * 64
    conn.execute(
        "INSERT INTO api_tokens (token, name, role, created_at, is_active) "
        "VALUES (?,?,?,?,1)", (legacy, "legacy", "admin", str(int(time.time()))))
    conn.commit()
    r = client.get("/api/me", headers=_bearer(legacy))
    check("遗留明文 Key 兼容可用", r.status_code == 200, f"status={r.status_code}")
    row = conn.execute("SELECT token FROM api_tokens WHERE name='legacy'").fetchone()
    check("遗留 Key 已就地升级为哈希",
          row["token"] == hashlib.sha256(legacy.encode()).hexdigest(),
          f"stored={row['token'][:16]}…")


# ── 7. 框架更新 fail-closed + super 边界 ────────────────────────────

def test_update_fail_closed():
    print("-- 框架更新 fail-closed --")
    conn, client = build_app()
    _seed_admin(conn)                                  # super
    _seed_admin(conn, username="ops", password="opspass1", role="admin")

    t_ops, _ = _login(client, "ops", "opspass1")
    r = client.post("/api/framework/update", headers=_bearer(t_ops), json={})
    check("admin 角色触发更新 → 403（需 super）", r.status_code == 403,
          f"status={r.status_code}")
    r = client.post("/api/terminal/exec", headers=_bearer(t_ops),
                    json={"command": "help"})
    check("admin 角色执行终端命令 → 403（需 super）", r.status_code == 403,
          f"status={r.status_code}")

    t_super, _ = _login(client, "admin", "admin123")
    r = client.post("/api/framework/update", headers=_bearer(t_super), json={})
    body = r.get_json() or {}
    check("super 未配置校验和 → 更新被拒绝（fail closed）",
          r.status_code == 403 and "update_expected_sha256" in str(body.get("msg", "")),
          f"status={r.status_code} body={body}")


# ── 8. migrations：默认密码账号自动补标记 / bcrypt 遗留随机重置 ───────

def test_admin_account_migration():
    print("-- 管理员账号自愈迁移 --")
    from core.storage.migrations import (
        _ensure_admin_account, _DEFAULT_ADMIN_HASH, _hash_password_stdlib)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    db = MiniDB(conn)

    # 存量默认密码账号 → 补 must_change_password=1（不重置密码）
    conn.execute(
        "INSERT INTO admin_users (username, password_hash, role, is_active) VALUES (?,?,?,1)",
        ("admin", _DEFAULT_ADMIN_HASH, "super"))
    conn.commit()
    _ensure_admin_account(db)
    row = conn.execute("SELECT must_change_password FROM admin_users "
                       "WHERE username='admin'").fetchone()
    check("存量默认密码账号被标记强制改密", row["must_change_password"] == 1,
          f"flag={row['must_change_password']}")

    # 正常改过密码的账号不受影响
    conn.execute("UPDATE admin_users SET password_hash=?, must_change_password=0 "
                 "WHERE username='admin'", (hash_password("real-pass-9"),))
    conn.commit()
    _ensure_admin_account(db)
    row = conn.execute("SELECT must_change_password FROM admin_users "
                       "WHERE username='admin'").fetchone()
    check("已改密账号不被误标", row["must_change_password"] == 0,
          f"flag={row['must_change_password']}")

    # 遗留 bcrypt 且从未登录 → 重置为随机一次性密码（非已知默认密码）+ 强制改密
    # （注入 has_bcrypt=False 模拟「环境未装 bcrypt」，本机可能已安装）
    conn.execute(
        "INSERT INTO admin_users (username, password_hash, role, is_active) VALUES (?,?,?,1)",
        ("legacy", "$2b$12$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "admin"))
    conn.commit()
    _ensure_admin_account(db, has_bcrypt=False)
    row = conn.execute("SELECT password_hash, must_change_password FROM admin_users "
                       "WHERE username='legacy'").fetchone()
    check("遗留 bcrypt 账号被重置为 pbkdf2",
          row["password_hash"].startswith("pbkdf2_sha256$"),
          f"hash={row['password_hash'][:32]}…")
    check("重置不落到已知默认密码",
          row["password_hash"] != _DEFAULT_ADMIN_HASH)
    check("重置账号被标记强制改密", row["must_change_password"] == 1,
          f"flag={row['must_change_password']}")

    # 与 webui/password.py 格式互通
    h = _hash_password_stdlib("x-pass-123")
    from software.extensions.webui.password import verify_password
    check("内核侧哈希与 webui 校验互通", verify_password("x-pass-123", h))


if __name__ == "__main__":
    test_onebot_gate()
    test_zkg_loader_integrity()
    test_must_change_password()
    test_cors_default_deny()
    test_apikey_hash_at_rest()
    test_update_fail_closed()
    test_admin_account_migration()
    print(f"\n结果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
