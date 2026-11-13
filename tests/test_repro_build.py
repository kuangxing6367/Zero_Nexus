# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT OR Apache-2.0
"""可复现构建回归测试（repo/build.py）

背景：原实现用 ``tarfile.open(path, "w:gz")``，gzip header 写入当前时间，
且 TarInfo 携带源文件 mtime / 运行账号 uid-gid。结果是同一份源码每次
build 都得到不同字节的 tar.gz 与 sha256：
- pool/*.tar.gz 入库后，任何一次 build（含 CI 与 test_pkg_signature）
  都会把工作区弄脏；
- 索引（dist/index.json，被 .gitignore 忽略）与包池必须同步重建，
  否则 zkg 完整性校验 fail-closed 拒绝加载。

本测试验证：间隔一段时间连续构建两次，包体字节与 sha256 完全一致，
且索引声明与落盘包体逐包相符。

脚本式测试（顶层直接运行，不适用 unittest discover）：
    python tests/test_repro_build.py
"""
import hashlib
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'repo'))

import build as repo_build

passed = 0
failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}  {detail}')


def pool_hashes():
    out = {}
    pool = os.path.join(ROOT, 'repo', 'pool')
    for fn in sorted(os.listdir(pool)):
        if fn.endswith('.tar.gz'):
            with open(os.path.join(pool, fn), 'rb') as f:
                out[fn] = hashlib.sha256(f.read()).hexdigest()
    return out


print('== 1. 连续两次构建字节一致 ==')
first = repo_build.build()
h1 = pool_hashes()
time.sleep(1.1)          # 跨过秒级时间戳，确保旧实现必然露馅
second = repo_build.build()
h2 = pool_hashes()
check('两次构建都产出 15 个包',
      len(first['packages']) == 15 and len(h1) == 15 and len(h2) == 15,
      f"first={len(first['packages'])} pool={len(h1)}/{len(h2)}")
check('两次构建包体字节完全一致（可复现）', h1 == h2,
      str([k for k in h1 if h1.get(k) != h2.get(k)]))
check('两次构建索引声明的 sha256 完全一致',
      [p['sha256'] for p in first['packages']]
      == [p['sha256'] for p in second['packages']])

print('== 2. 索引声明与落盘包体一致 ==')
bad = []
for p in second['packages']:
    fp = os.path.join(ROOT, 'repo', p['url'])
    with open(fp, 'rb') as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if h != p['sha256']:
        bad.append(p['id'])
check('15/15 包体 sha256 与索引一致', not bad, str(bad))

print('== 3. 包体不含随时间变化的元数据 ==')
import gzip
import io
import tarfile

sample = os.path.join(ROOT, 'repo', 'pool', 'store-1.0.0.tar.gz')
with gzip.open(sample, 'rb') as gz:
    raw = gz.read()
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as tf:
    members = tf.getmembers()
check('包体可读且含成员', len(members) > 0)
check('所有成员 mtime 固定为 0', all(m.mtime == 0 for m in members))
check('所有成员 uid/gid 固定为 0', all(m.uid == 0 and m.gid == 0 for m in members))
check('所有成员 uname/gname 为空',
      all(not m.uname and not m.gname for m in members))
check('gzip header 不写原始文件名',
      open(sample, 'rb').read(4)[:3] == b'\x1f\x8b\x08'
      and (open(sample, 'rb').read(4)[3] & 0x08) == 0)

print()
print(f'通过 {passed} 项，失败 {failed} 项')
sys.exit(1 if failed else 0)
