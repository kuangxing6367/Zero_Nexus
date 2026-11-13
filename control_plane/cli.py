# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""命令行入口：不启动框架、不开 HTTP，直接操作业务内核。

    python -m control_plane.cli --db data/controlplane.db init
    python -m control_plane.cli --db data/controlplane.db health
    python -m control_plane.cli --db data/controlplane.db list --tenant t1
    python -m control_plane.cli --db data/controlplane.db audit --limit 20

刻意**不提供** provision / delete 子命令：那需要注入真实的授权策略与
资源适配器，CLI 里塞一个「演示实现」等于给生产留后门。完整闭环请用
``python -m control_plane.selftest`` 跑（测试夹具不注入生产路径）。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from control_plane.store import ResourceStore, SqliteBackend, StoreError


def _json(value) -> str:
    def conv(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        raise TypeError(f'不可序列化: {type(obj).__name__}')
    return json.dumps(value, ensure_ascii=False, indent=2, default=conv)


def _open(db_path: str) -> ResourceStore:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    return ResourceStore(SqliteBackend(db_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='control_plane',
                                     description='统一资源控制面 · 业务内核 CLI')
    parser.add_argument('--db', default='data/controlplane.db',
                        help='SQLite 文件路径（默认 data/controlplane.db）')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init', help='幂等建表')
    sub.add_parser('health', help='只读探测表与记录数')
    p_list = sub.add_parser('list', help='列出资源台账')
    p_list.add_argument('--tenant')
    p_list.add_argument('--kind')
    p_list.add_argument('--owner')
    p_list.add_argument('--limit', type=int, default=100)
    p_audit = sub.add_parser('audit', help='查看最近审计事件')
    p_audit.add_argument('--limit', type=int, default=50)

    args = parser.parse_args(argv)
    store = None
    try:
        store = _open(args.db)
        if args.cmd == 'init':
            out = {'installed_ddl': store.install(), 'health': store.health()}
        elif args.cmd == 'health':
            out = store.health()
        elif args.cmd == 'list':
            out = {'resources': store.list_resources(
                owner_subject=args.owner, tenant=args.tenant,
                kind=args.kind, limit=args.limit)}
        else:
            out = {'audit_events': store.tail_audit(limit=args.limit)}
        print(_json(out))
        return 0
    except (StoreError, OSError) as exc:
        print(f'错误: {exc}', file=sys.stderr)
        return 1
    finally:
        if store is not None:
            try:
                store.backend.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
