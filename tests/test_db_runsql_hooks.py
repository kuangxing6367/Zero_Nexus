# -*- coding: utf-8 -*-
"""Database 公共执行路径（_run_sql）专项冒烟：

- 五个 CRUD 入口（query/query_one/execute/execute_many/insert）行为不变
- NOW() 本地时间替换（execute / execute_many / insert）
- db.query / db.execute / db.insert 三组 hook 均触发（insert 为新增扩展点）
- 限速放行 / 事务感知提交

脚本式测试（顶层直接运行）：
    python tests/test_db_runsql_hooks.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.storage.engine import Database, RateLimitTimeout
from core.hooks import HookRegistry, HookPoints

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


def main():
    db_path = os.path.join(tempfile.mkdtemp(prefix="zrn_db_"), "t.db")
    db = Database({"type": "sqlite", "path": db_path})
    hooks = HookRegistry()
    db._hooks = hooks  # 引擎注入扩展点（与 Framework 启动流程一致）

    fired = []

    def make_probe(point):
        def _probe(**kw):
            fired.append(point)
        return _probe

    for point in (HookPoints.DB_QUERY_BEFORE, HookPoints.DB_QUERY_AFTER,
                  HookPoints.DB_EXECUTE_BEFORE, HookPoints.DB_EXECUTE_AFTER,
                  HookPoints.DB_INSERT_BEFORE, HookPoints.DB_INSERT_AFTER):
        hooks.register(point, "probe", make_probe(point))

    # 建表
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts DATETIME)")

    # execute + NOW() 替换
    n = db.execute("INSERT INTO t (name, ts) VALUES (%s, NOW())", ("a",))
    check("execute 返回受影响行数", n == 1, f"n={n}")
    row = db.query_one("SELECT name, ts FROM t WHERE name = %s", ("a",))
    check("query_one 返回 dict", isinstance(row, dict) and row["name"] == "a", f"row={row}")
    check("NOW() 已替换为本地时间（非 UTC 占位）",
          row and row["ts"] and "T" not in str(row["ts"]) and len(str(row["ts"])) >= 19,
          f"ts={row and row['ts']}")

    # insert 返回自增 ID
    new_id = db.insert("INSERT INTO t (name) VALUES (%s)", ("b",))
    check("insert 返回自增 ID", new_id == 2, f"id={new_id}")

    # query 返回 list[dict]
    rows = db.query("SELECT id, name FROM t ORDER BY id")
    check("query 返回 list[dict]", isinstance(rows, list) and len(rows) == 2
          and isinstance(rows[0], dict), f"rows={rows}")

    # execute_many + NOW() 逐行替换
    n = db.execute_many(
        "INSERT INTO t (name, ts) VALUES (%s, NOW())",
        [("c1",), ("c2",), ("c3",)])
    check("execute_many 返回受影响行数", n == 3, f"n={n}")
    cnt = db.count("SELECT COUNT(*) AS n FROM t")
    check("count 汇总正确", cnt == 5, f"cnt={cnt}")

    # hook 触发断言
    check("db.query.before/after 触发",
          fired.count(HookPoints.DB_QUERY_BEFORE) == 3
          and fired.count(HookPoints.DB_QUERY_AFTER) == 3,
          f"fired={fired}")
    check("db.execute.before/after 触发",
          fired.count(HookPoints.DB_EXECUTE_BEFORE) == 3
          and fired.count(HookPoints.DB_EXECUTE_AFTER) == 3,
          f"fired={fired}")
    check("db.insert.before/after 触发（新增扩展点）",
          fired.count(HookPoints.DB_INSERT_BEFORE) == 1
          and fired.count(HookPoints.DB_INSERT_AFTER) == 1,
          f"fired={fired}")

    # 事务：块内失败整体回滚，块内走 pin 连接不自动提交
    try:
        with db.transaction() as txn:
            db.execute("INSERT INTO t (name) VALUES (%s)", ("txn-ok",))
            raise RuntimeError("rollback-probe")
    except RuntimeError:
        pass
    cnt = db.count("SELECT COUNT(*) AS n FROM t")
    check("事务异常回滚", cnt == 5, f"cnt={cnt}")

    with db.transaction():
        db.execute("INSERT INTO t (name) VALUES (%s)", ("txn-ok",))
    cnt = db.count("SELECT COUNT(*) AS n FROM t")
    check("事务正常提交", cnt == 6, f"cnt={cnt}")

    # exists / scalar 快捷方式
    check("exists", db.exists("SELECT 1 FROM t WHERE name = %s", ("txn-ok",)) is True)
    check("scalar", db.scalar("SELECT name FROM t WHERE id = %s", (1,)) == "a")

    # 限速：qps=2 + burst=1 时，第二条查询在等待上限内拿不到令牌 → 超时抛错
    db2 = Database({"type": "sqlite", "path": db_path,
                    "rate_limit_qps": 2, "rate_limit_burst": 1,
                    "rate_limit_wait_timeout": 0.1})
    db2.query("SELECT 1")  # 消耗桶内唯一令牌
    try:
        db2.query("SELECT 1")
        check("限速超时抛 RateLimitTimeout", False, "未触发超时")
    except RateLimitTimeout:
        check("限速超时抛 RateLimitTimeout", True)
    except Exception as e:  # noqa: BLE001
        check("限速超时抛 RateLimitTimeout", False, f"异常类型 {type(e).__name__}: {e}")

    print(f"\n结果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
