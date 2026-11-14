# -*- coding: utf-8 -*-
"""配置热重载 / 配置下发测试。

覆盖：
  - 托管覆盖层 config.managed.yaml 的合并优先级（高于 config.yaml / extensions.yaml）
  - Framework.reload_config()：就地更新、派生字段刷新、config.reload 扩展点
  - node_agent：config.status / config.reload / config.push
  - 闸门、sha256 强校验、YAML 校验、大小上限、dry_run、原子写、失败回滚、clear
  - node_control.push_config：规范化 YAML + sha256 的载荷构造
"""
import hashlib
import importlib
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core.config as ccfg
from core.engine import Framework
from core.hooks import HookPoints, HookRegistry

_agent_mod = importlib.import_module("software.extensions.node_agent.main")
_ctl_mod = importlib.import_module("software.extensions.node_control.main")
NodeAgent = _agent_mod.NodeAgent
NodeControl = _ctl_mod.NodeControl
_apply_hot_config = _agent_mod._apply_hot_config

PASS = []
_ORIG_AUTOLOAD = ccfg._autoload_extensions
_TMP = None


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + name)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _agent(directory, cfg_extra=None, config=None):
    """构造一个只依赖配置文件路径的 NodeAgent。"""
    cfg = {'name': 'unit-node', 'secret': 'unit-secret'}
    cfg.update(cfg_extra or {})
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, 'config.yaml')
    if not os.path.isfile(path):
        _write(path, 'node_agent:\n  name: unit-node\n  secret: unit-secret\n')
    fw = types.SimpleNamespace(config=config or {'node_agent': cfg},
                               config_path=path)
    return NodeAgent(fw), fw


def _framework(cfg_path):
    fw = object.__new__(Framework)
    fw.config_path = cfg_path
    fw.config = ccfg.load_config(cfg_path)
    fw._heartbeat_interval = fw.config['plugin'].get('heartbeat_interval', 60)
    fw._memory_limit_mb = 256
    fw._memory_check_interval = 30
    fw.hooks = HookRegistry()
    return fw


def main():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='zn-config-test-')
    d = _TMP
    try:
        # ── 1. 托管覆盖层优先级 ─────────────────────────────
        check('managed_config_path 规则',
              ccfg.managed_config_path(os.path.join(d, 'config.yaml'))
              == os.path.join(d, 'config.managed.yaml'))

        cfg_path = os.path.join(d, 'config.yaml')
        _write(cfg_path, 'node_agent:\n  allow_shell: false\n  interval: 30\n'
                        'web:\n  port: 8080\n')

        # 模拟 extensions.yaml 合并：它写入的值应被覆盖层压过
        def _fake_autoload(cfg):
            cfg.setdefault('node_agent', {})['allow_shell'] = False
            cfg.setdefault('node_agent', {})['interval'] = 30
            cfg.setdefault('web', {})['host'] = '127.0.0.1'
            return {}

        ccfg._autoload_extensions = _fake_autoload
        _write(os.path.join(d, 'config.managed.yaml'),
               'node_agent:\n  allow_shell: true\nweb:\n  port: 9000\n')
        merged = ccfg.load_config(cfg_path)
        check('覆盖层压过 config.yaml / extensions.yaml',
              merged['node_agent']['allow_shell'] is True
              and merged['web']['port'] == 9000)
        check('覆盖层按 dict 深度合并（保留未覆盖键）',
              merged['node_agent']['interval'] == 30
              and merged['web']['host'] == '127.0.0.1')
        check('加载覆盖层不修改用户 config.yaml',
              'allow_shell: false' in _read(cfg_path).decode('utf-8'))
        os.unlink(os.path.join(d, 'config.managed.yaml'))

        # ── 2. Framework.reload_config() ────────────────────
        reload_dir = os.path.join(d, 'reload')
        os.makedirs(reload_dir)
        rc_path = os.path.join(reload_dir, 'config.yaml')
        _write(rc_path, 'database:\n  type: sqlite\n  path: data/x.db\n'
                        'plugin:\n  heartbeat_interval: 60\n'
                        'service:\n  watchdog:\n    max_memory_mb: 256\n'
                        '    interval: 30\n')
        ccfg._autoload_extensions = lambda cfg: {}
        fw = _framework(rc_path)
        seen = []
        fw.hooks.register(HookPoints.CONFIG_RELOAD, 'test:capture',
                          lambda old, new: seen.append((old, new)))
        old_id = id(fw.config)

        _write(os.path.join(reload_dir, 'config.managed.yaml'),
               'plugin:\n  heartbeat_interval: 15\n'
               'service:\n  watchdog:\n    max_memory_mb: 512\n    interval: 5\n'
               'node_agent:\n  allow_shell: true\n')
        info = fw.reload_config()
        check('reload_config 就地更新（对象身份不变）', id(fw.config) == old_id)
        check('reload_config 刷新 heartbeat/watchdog 派生值',
              fw._heartbeat_interval == 15 and fw._memory_limit_mb == 512
              and fw._memory_check_interval == 5)
        check('reload_config 返回变化的顶层段',
              set(info['changed']) == {'plugin', 'service', 'node_agent'})
        check('reload_config 触发 config.reload 扩展点',
              len(seen) == 1 and seen[0][0]['plugin']['heartbeat_interval'] == 60
              and seen[0][1]['plugin']['heartbeat_interval'] == 15)

        _write(os.path.join(reload_dir, 'config.managed.yaml'),
               'node_agent:\n  allow_shell: false\n')
        fw.reload_config()
        check('二次重载后旧值被新覆盖层替换',
              fw.config['node_agent']['allow_shell'] is False)

        # 缺必需段 / 根节点非映射 → 拒绝且不改动现有配置
        before = dict(fw.config)
        _write(rc_path, 'plugin:\n  heartbeat_interval: 5\n')
        try:
            fw.reload_config()
            missing_rejected = False
        except ValueError:
            missing_rejected = True
        check('reload_config 拒绝缺 database/plugin 的配置', missing_rejected)
        check('reload_config 失败不破坏现有配置',
              fw.config['database']['type'] == before['database']['type'])
        _write(rc_path, '- not-a-map\n')
        try:
            fw.reload_config()
            nondict_rejected = False
        except ValueError:
            nondict_rejected = True
        check('reload_config 拒绝非映射根节点', nondict_rejected)

        # ── 3. node_agent 闸门与只读状态 ─────────────────────
        off_dir = os.path.join(d, 'agent-off')
        os.makedirs(off_dir)
        off, _ = _agent(off_dir, {'allow_config': False})
        res = off._exec('config.push', {'yaml': 'a: 1\n', 'sha256': _sha('a: 1\n')})
        check('allow_config=false 拒绝 config.push',
              res['ok'] is False and 'allow_config' in res['data'])
        res = off._exec('config.reload', {})
        check('allow_config=false 拒绝 config.reload',
              res['ok'] is False and 'allow_config' in res['data'])
        res = off._exec('config.status', {})
        check('config.status 无需开闸（只读）',
              res['ok'] is True and res['data']['allow_config'] is False)
        check('capabilities 反映 allow_config',
              off.capabilities()['config.push'] is False
              and off.capabilities()['config.status'] is True)

        # ── 4. config.push 正常路径 ─────────────────────────
        on_dir = os.path.join(d, 'agent-on')
        os.makedirs(on_dir)
        agent, fw2 = _agent(on_dir, {'allow_config': True, 'interval': 30})
        calls = []

        def _reload_ok():
            calls.append('reload')
            return {'changed': ['node_agent']}

        fw2.reload_config = _reload_ok
        managed = os.path.join(on_dir, 'config.managed.yaml')
        original = _read(os.path.join(on_dir, 'config.yaml'))

        raw = 'node_agent:\n  interval: 60\n  allow_shell: true\n'
        res = agent._exec('config.push', {'yaml': raw, 'sha256': _sha(raw)})
        check('config.push 写入托管覆盖层',
              res['ok'] is True and os.path.isfile(managed))
        check('覆盖层内容与推送字节一致', _read(managed) == raw.encode('utf-8'))
        check('config.push 触发一次热重载', calls == ['reload']
              and res['data']['reloaded']['reloaded'] is True)
        check('config.push 不改动用户 config.yaml',
              _read(os.path.join(on_dir, 'config.yaml')) == original)
        check('config.push 返回 sha256 / 顶层键',
              res['data']['sha256'] == _sha(raw)
              and res['data']['top_keys'] == ['node_agent'])
        check('原子写不残留临时文件',
              not [n for n in os.listdir(on_dir) if n.startswith('.config-')])

        res = agent._exec('config.status', {})
        st = res['data']
        check('config.status 报告覆盖层大小/sha256',
              res['ok'] and st['managed_exists'] and st['managed_bytes'] == len(raw)
              and st['managed_sha256'] == _sha(raw)
              and st['managed_top_keys'] == ['node_agent'])
        check('config.status 报告配置路径',
              st['config_path'] == os.path.abspath(os.path.join(on_dir, 'config.yaml'))
              and st['managed_path'] == os.path.abspath(managed))

        # ── 5. 校验失败路径 ─────────────────────────────────
        for label, args in [
            ('sha256 不匹配', {'yaml': raw, 'sha256': '0' * 64}),
            ('sha256 格式非法', {'yaml': raw, 'sha256': 'abc'}),
            ('缺少 yaml', {'sha256': _sha(raw)}),
            ('yaml 为空', {'yaml': '   ', 'sha256': _sha('   ')}),
            ('根节点为列表', {'yaml': '- a\n- b\n', 'sha256': _sha('- a\n- b\n')}),
            ('根节点为空映射', {'yaml': '{}\n', 'sha256': _sha('{}\n')}),
            ('不接受已解析对象', {'yaml': {'a': 1}, 'sha256': '0' * 64}),
            ('clear 与 yaml 互斥', {'clear': True, 'yaml': raw,
                                     'sha256': _sha(raw)}),
        ]:
            res = agent._exec('config.push', args)
            check(f'config.push 拒绝：{label}', res['ok'] is False)
        check('校验失败不破坏已有覆盖层', _read(managed) == raw.encode('utf-8'))

        big = 'a: ' + ('x' * (_agent_mod._MAX_CONFIG_BYTES + 16)) + '\n'
        res = agent._exec('config.push', {'yaml': big, 'sha256': _sha(big)})
        check('config.push 拒绝超限配置',
              res['ok'] is False and '过大' in res['data'])
        check('超限配置不落盘', _read(managed) == raw.encode('utf-8'))

        # dry_run：只校验不写盘、不重载
        calls.clear()
        raw2 = 'node_agent:\n  interval: 90\n'
        res = agent._exec('config.push',
                          {'yaml': raw2, 'sha256': _sha(raw2), 'dry_run': True})
        check('dry_run 只校验不写盘',
              res['ok'] and res['data']['dry_run'] is True
              and res['data']['written'] is False
              and _read(managed) == raw.encode('utf-8'))
        check('dry_run 不触发热重载', calls == [])

        # reload=false：写盘但不重载
        calls.clear()
        res = agent._exec('config.push',
                          {'yaml': raw2, 'sha256': _sha(raw2), 'reload': False})
        check('reload=false 写盘但不重载',
              res['ok'] and res['data']['reloaded'] is None
              and _read(managed) == raw2.encode('utf-8') and calls == [])

        # ── 6. 重载失败自动回滚 ─────────────────────────────
        prev = _read(managed)
        raw3 = 'node_agent:\n  interval: 5\n'
        boom = [0]

        def _reload_boom():
            boom[0] += 1
            raise RuntimeError('reload failed')

        fw2.reload_config = _reload_boom
        res = agent._exec('config.push', {'yaml': raw3, 'sha256': _sha(raw3)})
        check('重载失败时 config.push 报错并标注已回滚',
              res['ok'] is False and '已回滚' in res['data'])
        check('重载失败后覆盖层恢复到推送前字节', _read(managed) == prev)
        check('回滚后再次尝试重载以恢复运行时', boom[0] >= 1)

        # ── 7. config.reload / clear ────────────────────────
        calls.clear()
        fw2.reload_config = _reload_ok
        res = agent._exec('config.reload', {})
        check('config.reload 调用框架入口',
              res['ok'] and calls == ['reload']
              and res['data']['reloaded'] is True)

        res = agent._exec('config.push', {'clear': True, 'dry_run': True})
        check('clear dry_run 不删除覆盖层',
              res['ok'] and res['data']['cleared'] is True
              and os.path.isfile(managed))
        calls.clear()
        res = agent._exec('config.push', {'clear': True})
        check('clear 删除覆盖层并重载',
              res['ok'] and res['data']['cleared'] is True
              and not os.path.isfile(managed) and calls == ['reload'])
        res = agent._exec('config.push', {'clear': True})
        check('覆盖层不存在时 clear 幂等',
              res['ok'] and res['data']['cleared'] is False)

        # ── 8. 热更新回调只改运行期安全字段 ─────────────────
        hot, _ = _agent(os.path.join(d, 'agent-hot'),
                        {'allow_shell': False, 'allow_packages': False,
                         'allow_config': False, 'interval': 30,
                         'name': 'n1', 'hub_host': '10.0.0.1'})
        logs = []
        restart = _apply_hot_config(
            hot,
            {'node_agent': {'name': 'n1', 'hub_host': '10.0.0.1'}},
            {'node_agent': {'name': 'n2', 'hub_host': '10.0.0.2',
                            'allow_shell': True, 'allow_packages': True,
                            'allow_config': True, 'interval': 10}},
            logs.append)
        check('热更新生效：allow_* 与 interval',
              hot.allow_shell and hot.allow_packages and hot.allow_config
              and hot.interval == 10)
        check('热更新不改连接类字段并提示重启',
              hot.name == 'n1' and hot.hub_host == '10.0.0.1'
              and restart == ['name', 'hub_host']
              and any('需重启' in m for m in logs))

        no_reload, _ = _agent(os.path.join(d, 'agent-noreload'),
                              {'allow_config': True})
        check('运行时不支持热重载时如实报告',
              no_reload._do_reload().get('reloaded') is False)

        # ── 9. node_control.push_config 载荷构造 ────────────
        hub = NodeControl(types.SimpleNamespace(
            config={'node_control': {'enabled': False, 'nodes': [], 'groups': {}}}))
        captured = {}

        def _fake_send_group(group=None, cmd='', args=None, **kw):
            captured.update({'group': group, 'cmd': cmd, 'args': args or {},
                             'kw': kw})
            return {'ok': 1}

        hub.send_group = _fake_send_group
        import yaml as _yaml
        res = hub.push_config('edge', {'node_agent': {'interval': 60}},
                              dry_run=True, reload=False, timeout=5,
                              concurrency=2, stagger=0.5)
        sent = captured['args']
        check('push_config 走 config.push 命令',
              res == {'ok': 1} and captured['cmd'] == 'config.push'
              and captured['group'] == 'edge')
        check('push_config 传规范化 YAML + 匹配 sha256',
              _yaml.safe_load(sent['yaml']) == {'node_agent': {'interval': 60}}
              and sent['sha256'] == _sha(sent['yaml']))
        check('push_config 透传 dry_run/reload 与调度参数',
              sent['dry_run'] is True and sent['reload'] is False
              and captured['kw'] == {'timeout': 5, 'concurrency': 2,
                                     'stagger': 0.5})
        try:
            hub.push_config('edge', {})
            empty_rejected = False
        except ValueError:
            empty_rejected = True
        check('push_config 拒绝空配置', empty_rejected)
        check('config_status / reload_config 命令名正确',
              hub.__class__.config_status.__name__ == 'config_status'
              and hub.__class__.reload_config.__name__ == 'reload_config')

    finally:
        ccfg._autoload_extensions = _ORIG_AUTOLOAD
        if _TMP and os.path.isdir(_TMP):
            shutil.rmtree(_TMP, ignore_errors=True)

    failed = [n for n, ok in PASS if not ok]
    print(f"\nresult: {len(PASS) - len(failed)}/{len(PASS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
