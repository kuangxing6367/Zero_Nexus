# -*- coding: utf-8 -*-
"""WebUI 会话 token 哈希入库集成测试：

登录 → 库内仅存 sha256 哈希（明文不落库）→ 新 token 可鉴权 →
存量明文 token 兼容 → 错误 token 拒绝 → 64 字符 API Key 不受影响。

使用真实 create_web_app + Flask test client + SQLite 内存库。
脚本式测试（顶层直接运行，不适用 unittest discover）：
    python tests/test_webui_token_hash.py
"""
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
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


class MiniDB:
    """sqlite3 内存库的最小适配：提供项目 Database 的 query_one/query/execute 语义。"""

    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _translate(sql):
        # 测试仅覆盖本次相关 SQL：%s 占位符与 NOW()
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


def build_app():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            token TEXT DEFAULT NULL,
            token_created_at TEXT DEFAULT NULL,
            role TEXT DEFAULT 'admin',
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
    """)
    conn.execute(
        "INSERT INTO admin_users (username, password_hash, role, is_active) VALUES (?,?,?,1)",
        ("admin", hash_password("admin123"), "super"),
    )
    conn.commit()

    framework = MagicMock()
    framework.config = {"web": {}, "security": {}}
    framework.db = MiniDB(conn)
    framework.plugin_loader.plugins_dir = tempfile.mkdtemp(prefix="zrn_plugins_")
    app = create_web_app(framework)
    return conn, app.test_client()


def auth_get(client, token):
    return client.get("/api/me", headers={"Authorization": "Bearer " + token})


def main():
    print("-- WebUI 会话 token 哈希 --")
    conn, client = build_app()

    r = client.post("/api/login", json={"username": "admin", "password": "admin123"})
    body = r.get_json()
    check("登录成功", r.status_code == 200 and body and body.get("code") == 0,
          f"status={r.status_code} body={body}")
    token = (body.get("data") or {}).get("token", "")
    check("新 token 为 64 字符", len(token) == 64, f"len={len(token)}")

    row = conn.execute("SELECT token, token_created_at FROM admin_users WHERE username='admin'").fetchone()
    expect_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    check("库内存的是 sha256 哈希", row["token"] == expect_hash,
          f"stored={row['token'][:24] if row['token'] else None}...")
    check("明文不落库", row["token"] != token)

    r = auth_get(client, token)
    body = r.get_json() or {}
    check("新 token 鉴权通过（哈希比对命中）",
          r.status_code == 200 and body.get("code") == 0
          and (body.get("data") or {}).get("username") == "admin",
          f"status={r.status_code} body={body}")

    r = auth_get(client, "f" * 64)
    check("错误 token 拒绝", r.status_code == 401, f"status={r.status_code}")

    # 存量明文会话兼容：模拟升级前入库的 2048 字符旧 token
    old_token = "A" * 2048
    conn.execute("UPDATE admin_users SET token=?, token_created_at=datetime('now','localtime') WHERE username='admin'",
                 (old_token,))
    conn.commit()
    r = auth_get(client, old_token)
    body = r.get_json() or {}
    check("存量明文 token 兼容（旧会话可继续用）",
          r.status_code == 200 and body.get("code") == 0,
          f"status={r.status_code} body={body}")

    # API Key（64 字符，与新的会话 token 同长度）不受会话分支影响
    api_key = "k" * 64
    conn.execute(
        "INSERT INTO api_tokens (token, name, role, created_at, is_active) VALUES (?,?,?,?,1)",
        (api_key, "ci", "admin", str(int(time.time()))),
    )
    conn.commit()
    r = auth_get(client, api_key)
    body = r.get_json() or {}
    check("API Key 鉴权不受影响",
          r.status_code == 200 and body.get("code") == 0
          and str((body.get("data") or {}).get("id", "")).startswith("api:"),
          f"status={r.status_code} body={body}")

    # 登出：token 清空后原会话失效
    r = client.post("/api/logout", headers={"Authorization": "Bearer " + old_token})
    check("登出成功", r.status_code == 200 and (r.get_json() or {}).get("code") == 0,
          f"status={r.status_code}")
    r = auth_get(client, old_token)
    check("登出后 token 失效", r.status_code == 401, f"status={r.status_code}")

    print(f"\n结果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
