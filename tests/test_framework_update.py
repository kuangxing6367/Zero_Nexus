# -*- coding: utf-8 -*-
"""框架自升级测试：sha256 / 归档校验 / 暂存编译 / 备份回滚 / 节点闸门。

覆盖：
  - service.updater.apply_source_zip：fail closed、sha256、路径穿越、编译失败、
    白名单（保护 config.yaml / data / software/plugins）、dry_run、备份
  - rollback_source_update / list_backups
  - node_agent：framework.status / framework.update / framework.rollback 与闸门
  - node_control.push_framework / rollback_framework / framework_status 载荷构造
"""
import base64
import hashlib
import importlib
import io
import os
import shutil
import sys
import tempfile
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core.kernel.paths as kpaths
import service.updater as updater
from service.updater import (
    UpdateError, apply_source_zip, list_backups, rollback_source_update)

_agent_mod = importlib.import_module("software.extensions.node_agent.main")
_ctl_mod = importlib.import_module("software.extensions.node_control.main")
NodeAgent = _agent_mod.NodeAgent
NodeControl = _ctl_mod.NodeControl

PASS = []
_TMP = None


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + name)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_bytes(files: dict, prefix='Zero_Nexus-test/') -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for rel, content in files.items():
            zf.writestr(prefix + rel,
                        content if isinstance(content, bytes)
                        else content.encode('utf-8'))
    return buf.getvalue()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _make_root(base):
    root = os.path.join(base, 'root')
    _write(os.path.join(root, 'core', 'a.py'), 'OLD = 1\n')
    _write(os.path.join(root, 'main.py'), 'print("old")\n')
    _write(os.path.join(root, 'config.yaml'), 'user: true\n')
    _write(os.path.join(root, 'extensions.yaml'), 'user_ext: true\n')
    _write(os.path.join(root, 'software', 'plugins', 'p.py'), 'USER_PLUGIN = 1\n')
    _write(os.path.join(root, 'data', 'user.db'), 'user-data\n')
    return root


def _zip_file(base, name, files, prefix='Zero_Nexus-test/'):
    path = os.path.join(base, name)
    with open(path, 'wb') as f:
        f.write(_zip_bytes(files, prefix))
    return path


def _new_files():
    return {
        'core/a.py': 'NEW = 2\n',
        'main.py': 'print("new")\n',
        'software/extensions/e.py': 'EXT = 1\n',
        'software/plugins/p.py': 'EVIL_PLUGIN = 1\n',
        'config.yaml': 'evil: true\n',
        'extensions.yaml': 'evil_ext: true\n',
        'data/user.db': 'evil-data\n',
        'sql/x.sql': 'select 1;\n',
    }


def main():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='zn-update-test-')
    d = _TMP
    try:
        # ── 1. fail closed 与 sha256 ────────────────────────
        root = _make_root(d)
        zp = _zip_file(d, 'ok.zip', _new_files())
        data = open(zp, 'rb').read()
        digest = _sha(data)

        try:
            apply_source_zip(zp, root)
            no_sha_rejected = False
        except UpdateError:
            no_sha_rejected = True
        check('未提供 sha256 时 fail closed', no_sha_rejected)
        check('fail closed 不改动任何文件',
              _read(os.path.join(root, 'core', 'a.py')) == 'OLD = 1\n')

        try:
            apply_source_zip(zp, root, expected_sha256='0' * 64)
            bad_sha_rejected = False
        except UpdateError:
            bad_sha_rejected = True
        check('sha256 不匹配时拒绝', bad_sha_rejected)
        check('sha256 不匹配不改动任何文件',
              _read(os.path.join(root, 'core', 'a.py')) == 'OLD = 1\n')

        try:
            apply_source_zip(zp, root, expected_sha256='abc')
            bad_format_rejected = False
        except UpdateError:
            bad_format_rejected = True
        check('sha256 格式非法时拒绝', bad_format_rejected)

        # ── 2. dry_run ─────────────────────────────────────
        rep = apply_source_zip(zp, root, expected_sha256=digest, dry_run=True)
        check('dry_run 返回校验结果', rep['dry_run'] is True
              and rep['valid'] is True and 'core' in rep['updated'])
        check('dry_run 不改动文件、不建备份',
              _read(os.path.join(root, 'core', 'a.py')) == 'OLD = 1\n'
              and list_backups(root) == [])

        # ── 3. 正常升级与用户数据保护 ───────────────────────
        rep = apply_source_zip(zp, root, expected_sha256=digest)
        check('升级替换框架文件', _read(os.path.join(root, 'core', 'a.py')) == 'NEW = 2\n'
              and _read(os.path.join(root, 'main.py')) == 'print("new")\n')
        check('升级新增官方扩展文件',
              _read(os.path.join(root, 'software', 'extensions', 'e.py')) == 'EXT = 1\n')
        check('升级不覆盖用户 config.yaml',
              _read(os.path.join(root, 'config.yaml')) == 'user: true\n')
        check('升级不覆盖用户 extensions.yaml',
              _read(os.path.join(root, 'extensions.yaml')) == 'user_ext: true\n')
        check('升级不覆盖 software/plugins',
              _read(os.path.join(root, 'software', 'plugins', 'p.py')) == 'USER_PLUGIN = 1\n')
        check('升级不覆盖 data/',
              _read(os.path.join(root, 'data', 'user.db')) == 'user-data\n')
        check('升级记录备份与文件清单',
              rep['backup'] and os.path.isdir(os.path.join(root, updater.BACKUP_DIRNAME,
                                                           rep['backup']))
              and 'core' in rep['updated'] and rep['restart_required'] is True)
        backups = list_backups(root)
        check('list_backups 返回刚创建的备份',
              len(backups) == 1 and backups[0]['name'] == rep['backup']
              and backups[0]['rolled_back_at'] is None)
        check('备份内含旧文件',
              _read(os.path.join(root, updater.BACKUP_DIRNAME, rep['backup'],
                                 'core', 'a.py')) == 'OLD = 1\n')

        # ── 4. 回滚 ────────────────────────────────────────
        _write(os.path.join(root, 'core', 'a.py'), 'HACKED = 9\n')
        rb = rollback_source_update(root)
        check('回滚恢复旧文件', _read(os.path.join(root, 'core', 'a.py')) == 'OLD = 1\n')
        check('回滚删除升级新增的文件',
              not os.path.exists(os.path.join(root, 'software', 'extensions', 'e.py')))
        check('回滚不动用户数据',
              _read(os.path.join(root, 'config.yaml')) == 'user: true\n'
              and _read(os.path.join(root, 'software', 'plugins', 'p.py')) == 'USER_PLUGIN = 1\n')
        check('回滚标记写入备份清单',
              rb['backup'] and list_backups(root)[0]['rolled_back_at'])
        check('回滚后再次回滚同一备份幂等',
              rollback_source_update(root, rb['backup'])['restored'] == []
              and _read(os.path.join(root, 'core', 'a.py')) == 'OLD = 1\n')

        try:
            rollback_source_update(root, '../evil')
            bad_backup_rejected = False
        except UpdateError:
            bad_backup_rejected = True
        check('非法备份名被拒绝', bad_backup_rejected)

        # ── 5. 编译失败不落盘 ───────────────────────────────
        root2 = _make_root(os.path.join(d, 'compile'))
        bad_zip = _zip_file(os.path.join(d, 'compile'), 'bad.zip',
                            {'core/bad.py': 'def broken(:\n'})
        bad_sha = _sha(open(bad_zip, 'rb').read())
        try:
            apply_source_zip(bad_zip, root2, expected_sha256=bad_sha)
            compile_rejected = False
        except UpdateError as e:
            compile_rejected = '编译' in str(e)
        check('暂存区编译失败时拒绝升级', compile_rejected)
        check('编译失败不改动线上文件、不建备份',
              _read(os.path.join(root2, 'core', 'a.py')) == 'OLD = 1\n'
              and list_backups(root2) == [])

        # ── 6. 归档路径穿越 ────────────────────────────────
        root3 = _make_root(os.path.join(d, 'trav'))
        trav_zip = _zip_file(os.path.join(d, 'trav'), 'trav.zip',
                             {'../evil.py': 'EVIL = 1\n'})
        trav_sha = _sha(open(trav_zip, 'rb').read())
        try:
            apply_source_zip(trav_zip, root3, expected_sha256=trav_sha)
            trav_rejected = False
        except UpdateError as e:
            trav_rejected = '校验失败' in str(e)
        check('路径穿越包被归档校验拒绝', trav_rejected)

        # ── 7. 节点侧闸门与命令 ─────────────────────────────
        node_root = _make_root(os.path.join(d, 'node'))
        orig_root = kpaths.project_root
        kpaths.project_root = lambda: node_root
        try:
            cfg = {'name': 'n1', 'secret': 's1', 'allow_upgrade': False}
            off = NodeAgent(types.SimpleNamespace(
                config={'node_agent': cfg}, config_path=os.path.join(d, 'c.yaml')))
            check('allow_upgrade=false 拒绝 framework.update',
                  off._exec('framework.update', {})['ok'] is False)
            check('allow_upgrade=false 拒绝 framework.rollback',
                  off._exec('framework.rollback', {})['ok'] is False)
            check('framework.status 无需开闸',
                  off._exec('framework.status', {})['ok'] is True)
            check('capabilities 反映 allow_upgrade',
                  off.capabilities()['framework.update'] is False
                  and off.capabilities()['framework.status'] is True)

            cfg2 = {'name': 'n2', 'secret': 's2', 'allow_upgrade': True}
            on = NodeAgent(types.SimpleNamespace(
                config={'node_agent': cfg2}, config_path=os.path.join(d, 'c2.yaml')))
            b64 = base64.b64encode(data).decode('ascii')

            res = on._exec('framework.update',
                           {'zip_b64': b64, 'sha256': '0' * 64})
            check('节点校验升级包 sha256', res['ok'] is False
                  and 'sha256' in res['data'])
            res = on._exec('framework.update',
                           {'zip_b64': 'not-base64!!', 'sha256': digest})
            check('节点拒绝非法 base64', res['ok'] is False)
            res = on._exec('framework.update',
                           {'zip_b64': b64, 'sha256': digest, 'dry_run': True})
            check('节点 dry_run 不落盘',
                  res['ok'] and res['data']['dry_run'] is True
                  and _read(os.path.join(node_root, 'core', 'a.py')) == 'OLD = 1\n')

            res = on._exec('framework.update',
                           {'zip_b64': b64, 'sha256': digest})
            check('节点执行升级',
                  res['ok'] and _read(os.path.join(node_root, 'core', 'a.py')) == 'NEW = 2\n'
                  and res['data']['backup'])

            st = on._exec('framework.status', {})
            check('framework.status 返回备份与版本',
                  st['ok'] and st['data']['allow_upgrade'] is True
                  and len(st['data']['backups']) == 1
                  and st['data']['restart_required_after_update'] is True)

            res = on._exec('framework.rollback', {})
            check('节点执行回滚',
                  res['ok'] and _read(os.path.join(node_root, 'core', 'a.py')) == 'OLD = 1\n')

            orig_max = _agent_mod._MAX_UPDATE_BYTES
            _agent_mod._MAX_UPDATE_BYTES = 8
            try:
                res = on._exec('framework.update',
                               {'zip_b64': base64.b64encode(b'x' * 32).decode(),
                                'sha256': _sha(b'x' * 32)})
            finally:
                _agent_mod._MAX_UPDATE_BYTES = orig_max
            check('节点拒绝超限升级包', res['ok'] is False and '过大' in res['data'])
        finally:
            kpaths.project_root = orig_root

        # ── 8. hub 侧 push_framework 载荷构造 ───────────────
        hub = NodeControl(types.SimpleNamespace(
            config={'node_control': {'enabled': False, 'nodes': [], 'groups': {}}}))
        captured = {}

        def _fake_send_group(group=None, cmd='', args=None, **kw):
            captured.update({'group': group, 'cmd': cmd, 'args': args or {},
                             'kw': kw})
            return {'ok': 1}

        hub.send_group = _fake_send_group
        res = hub.push_framework('edge', zp, dry_run=True, timeout=9,
                                 concurrency=2, stagger=0.5)
        sent = captured['args']
        check('push_framework 走 framework.update',
              res == {'ok': 1} and captured['cmd'] == 'framework.update'
              and captured['group'] == 'edge')
        check('push_framework 传 base64 原文与匹配 sha256',
              base64.b64decode(sent['zip_b64']) == data
              and sent['sha256'] == digest)
        check('push_framework 透传 dry_run 与调度参数',
              sent['dry_run'] is True
              and captured['kw'] == {'timeout': 9, 'concurrency': 2, 'stagger': 0.5})

        for label, fn in [
            ('push_framework 拒绝不存在的文件',
             lambda: hub.push_framework('edge', os.path.join(d, 'nope.zip'))),
        ]:
            try:
                fn()
                rejected = False
            except ValueError:
                rejected = True
            check(label, rejected)

        orig_max = _ctl_mod._MAX_UPDATE_BYTES
        _ctl_mod._MAX_UPDATE_BYTES = 8
        try:
            try:
                hub.push_framework('edge', zp)
                over_rejected = False
            except ValueError:
                over_rejected = True
        finally:
            _ctl_mod._MAX_UPDATE_BYTES = orig_max
        check('push_framework 拒绝超限包', over_rejected)

        captured.clear()
        hub.rollback_framework('edge', '20260101-000000-abcdef123456')
        check('rollback_framework 命令与参数',
              captured['cmd'] == 'framework.rollback'
              and captured['args']['backup'] == '20260101-000000-abcdef123456')
        captured.clear()
        hub.framework_status('edge')
        check('framework_status 命令名正确',
              captured['cmd'] == 'framework.status' and captured['group'] == 'edge')

    finally:
        if _TMP and os.path.isdir(_TMP):
            shutil.rmtree(_TMP, ignore_errors=True)

    failed = [n for n, ok in PASS if not ok]
    print(f"\nresult: {len(PASS) - len(failed)}/{len(PASS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
