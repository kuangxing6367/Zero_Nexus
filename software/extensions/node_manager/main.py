# -*- coding: utf-8 -*-
"""
节点管理器（官方扩展，多机管理 L1：监控聚合，只读）

星型拓扑的中心侧（hub）：主动轮询各节点已开放的 status_panel /health，
聚合结果存入 nodes 表（心跳 updated_at），并提供进程内快照接口。
零新协议——节点侧只需开 status_panel 即可被纳管，hub 不反向连接节点。

启用（默认关，仅中心机需要开）：
  extensions.yaml → node_manager:
    enabled: true
    interval: 30          # 轮询周期（秒）
    timeout: 5            # 单节点请求超时（秒）
    nodes:                # 节点清单
      - name: node-1
        url: http://192.168.1.10:8090   # 节点 status_panel 地址

进程内访问：
  mgr = fw.services.get('node_manager')
  mgr.poll_now()          # 立即轮询一轮
  mgr.snapshot()          # [{'name','url','status','version',...}]
"""
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "节点管理器",
    "version": "0.1.0",
    "author": "Zeronus",
    "desc": "多机管理 L1：轮询节点 /health 聚合监控（只读）",
    "priority": 30,
    "official": True,
}

# 回环/局域网请求不走系统代理（与 repo/http 同一约定）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_NODES_DDL = """
CREATE TABLE IF NOT EXISTS nodes (
    name           VARCHAR(64)  NOT NULL PRIMARY KEY,
    url            VARCHAR(255) NOT NULL,
    status         VARCHAR(16)  DEFAULT 'unknown',
    version        VARCHAR(32)  DEFAULT NULL,
    uptime_seconds INTEGER      DEFAULT 0,
    memory_mb      REAL         DEFAULT NULL,
    detail         VARCHAR(2000) DEFAULT NULL,
    last_ok_at     VARCHAR(32)  DEFAULT NULL,
    updated_at     VARCHAR(32)  DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime'))
)
"""


class NodeManager:
    """轮询各节点 /health 并落库（多机 L1，只读）。"""

    def __init__(self, fw, ctx_log=None):
        self.fw = fw
        self._log = ctx_log or (lambda m: logger.info(m))
        cfg = fw.config.get('node_manager', {}) or {}
        self.interval = max(5, int(cfg.get('interval', 30)))
        self.timeout = max(1, int(cfg.get('timeout', 5)))
        raw = cfg.get('nodes', []) or []
        self.nodes = []
        for i, n in enumerate(raw):
            if isinstance(n, dict) and n.get('url'):
                self.nodes.append({'name': str(n.get('name') or f'node-{i + 1}'),
                                   'url': str(n['url']).rstrip('/')})
        self._stop = threading.Event()
        self._thread = None
        self._last_status = {}   # name -> 'up'/'down'，用于状态翻转日志

    # ── 数据库 ────────────────────────────────────────────
    def _ensure_table(self):
        self.fw.db.execute(_NODES_DDL)

    def _save(self, node: dict, status: str, health: dict = None):
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        if status == 'up':
            detail = json.dumps(health or {}, ensure_ascii=False)[:2000]
            fields = ('up',
                      str(health.get('version', '')) if health else '',
                      int(health.get('uptime_seconds', 0)) if health else 0,
                      float(health['memory_mb']) if health and health.get('memory_mb') is not None else None,
                      detail, now, now)
        else:
            fields = ('down', '', 0, None, None, None, now)  # status..last_ok_at, updated_at
        exists = self.fw.db.query_one("SELECT name FROM nodes WHERE name=?",
                                      (node['name'],))
        if exists:
            if status == 'up':
                self.fw.db.execute(
                    "UPDATE nodes SET status=?, version=?, uptime_seconds=?, "
                    "memory_mb=?, detail=?, last_ok_at=?, updated_at=? WHERE name=?",
                    fields + (node['name'],))
            else:
                self.fw.db.execute(
                    "UPDATE nodes SET status='down', updated_at=? WHERE name=?",
                    (now, node['name']))
        elif status == 'up':
            self.fw.db.execute(
                "INSERT INTO nodes (name, url, status, version, uptime_seconds, "
                "memory_mb, detail, last_ok_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (node['name'], node['url']) + fields)
        else:
            self.fw.db.execute(
                "INSERT INTO nodes (name, url, status, updated_at) "
                "VALUES (?, ?, 'down', ?)",
                (node['name'], node['url'], now))

    # ── 轮询 ──────────────────────────────────────────────
    def _probe(self, node: dict):
        """请求单个节点 /health，返回 (status, health_dict|None)。"""
        try:
            resp = _OPENER.open(node['url'] + '/health', timeout=self.timeout)
            data = json.loads(resp.read().decode('utf-8'))
            return ('up', data) if data.get('ok') else ('down', None)
        except Exception:
            return ('down', None)

    def poll_now(self) -> list:
        """立即轮询一轮，返回本轮结果 [{name,url,status,...}]。"""
        if not self.nodes:
            return []
        self._ensure_table()
        results = []
        for node in self.nodes:
            status, health = self._probe(node)
            self._save(node, status, health)
            prev = self._last_status.get(node['name'])
            if prev != status:   # 只记录状态翻转，避免日志刷屏
                self._log(f"节点 {node['name']} ({node['url']}) → {status}")
                self._last_status[node['name']] = status
            row = {'name': node['name'], 'url': node['url'], 'status': status}
            if health:
                row.update({'version': health.get('version'),
                            'uptime_human': health.get('uptime_human'),
                            'memory_mb': health.get('memory_mb'),
                            'plugins': health.get('plugins'),
                            'extensions': health.get('extensions')})
            results.append(row)
        return results

    def snapshot(self) -> list:
        """读取库中最新聚合状态（不做网络请求）。"""
        try:
            return self.fw.db.query(
                "SELECT name, url, status, version, uptime_seconds, memory_mb, "
                "last_ok_at, updated_at FROM nodes ORDER BY name")
        except Exception:
            return []

    # ── 后台循环 ──────────────────────────────────────────
    def start(self):
        if not self.nodes:
            self._log("节点管理器已启动，但 node_manager.nodes 为空，暂无纳管节点")
            return
        self._thread = threading.Thread(target=self._loop,
                                        name='node-manager', daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_now()
            except Exception as e:
                logger.warning(f"[node_manager] 轮询异常: {e}")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


def register(ctx):
    """启动节点轮询（node_manager.enabled 默认 false，仅 hub 开启）"""
    global _manager
    fw = ctx._framework

    cfg = fw.config.get('node_manager', {}) or {}
    if cfg.get('enabled') is not True:
        ctx.log("节点管理器未启用 (node_manager.enabled: false)")
        return

    _manager = NodeManager(fw, ctx_log=ctx.log)
    fw.services.register('node_manager', _manager)
    _manager.start()
    ctx.log(f"节点管理器已启动: 纳管 {len(_manager.nodes)} 个节点，"
            f"轮询周期 {_manager.interval}s")


def unregister():
    global _manager
    if _manager:
        _manager.stop()
        _manager = None


_manager = None
