"""core.kernel 细模块测试（从 framework/core.py 抽出的职责）。

覆盖：banner 渲染 / read_version / logging_setup / data_dirs 迁移 / stats_writer flush。
均不依赖 framework.*，用标准库 + 临时目录即可跑。
"""
import logging
import os
import tempfile


# ── banner ──

def test_render_banner_standard():
    from core.kernel.banner import render_banner
    lines = render_banner(
        version="9.9.9",
        project_root="E:/工程/zernus",
        core_loaded=["onebot_adapter", "webui"], user_loaded=["demo"],
        config={"database": {"type": "sqlite", "path": "data/zernus.db"},
                "onebot": {"listen_port": 6830}, "web": {"port": 8080}},
    )
    text = "\n".join(lines)
    assert "ZER NUS v9.9.9" in text
    assert "OneBot WS : 0.0.0.0:6830" in text
    assert "WebUI      : http://127.0.0.1:8080" in text
    assert "官方扩展 : 2 个" in text
    assert "用户插件 : 1 个" in text


def test_read_version():
    from core.kernel.banner import read_version
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "VERSION"), "w", encoding="utf-8") as f:
        f.write("9.9.9-test\n")
    assert read_version(tmp) == "9.9.9-test"
    assert read_version(os.path.join(tmp, "nope")) == "?"


# ── logging_setup ──

def test_setup_logging_creates_file():
    from core.kernel.logging_setup import setup_logging
    tmp = tempfile.mkdtemp()
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logfile = setup_logging({"log": {"level": "INFO"}}, tmp)
        assert os.path.isabs(logfile)
        logging.getLogger("zernus.test").info("hello-kernel")
        for h in root.handlers:
            try:
                h.flush()
            except Exception:
                pass
        assert os.path.isfile(logfile)
        with open(logfile, "r", encoding="utf-8") as f:
            assert "hello-kernel" in f.read()
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)


# ── data_dirs ──

def test_migrate_legacy_data_dirs():
    from core.kernel.data_dirs import migrate_legacy_data_dirs
    tmp = tempfile.mkdtemp()
    old_logs = os.path.join(tmp, "logs")
    os.makedirs(old_logs)
    with open(os.path.join(old_logs, "a.log"), "w") as f:
        f.write("x")
    migrate_legacy_data_dirs(tmp)
    assert os.path.isdir(os.path.join(tmp, "data", "logs"))
    assert not os.path.isdir(old_logs)


# ── stats_writer ──

class _FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def test_stats_writer_flush():
    from core.kernel.stats_writer import AsyncStatsWriter
    db = _FakeDB()
    sw = AsyncStatsWriter(db)
    assert sw._cmd_hits == {} and sw._kw_hits == {}
    sw.command_hit(7)
    sw.keyword_hit(3)
    sw.register_user(100, {"nickname": "n"}, "private")
    sw._flush()
    sqls = "\n".join(c[0] for c in db.calls)
    assert "UPDATE commands" in sqls
    assert "UPDATE dynamic_commands" in sqls
    assert "INSERT INTO users" in sqls
    # flush 后内存计数清空
    assert sw._cmd_hits == {} and sw._kw_hits == {}


def test_stats_writer_group_register():
    from core.kernel.stats_writer import AsyncStatsWriter
    db = _FakeDB()
    sw = AsyncStatsWriter(db)
    sw.register_user(200, {"nickname": "g", "role": "admin"}, "group", group_id=999)
    sw._flush()
    sqls = "\n".join(c[0] for c in db.calls)
    assert "INSERT INTO groups_info" in sqls
    assert "INSERT INTO group_members" in sqls
