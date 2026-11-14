# -*- coding: utf-8 -*-
"""框架源码更新机制（服务级）：校验 → 暂存 → 编译检查 → 备份 → 替换 → 回滚。

设计目标：
  - **fail closed**：默认必须提供目标 ZIP 的 sha256；未提供且未显式允许时拒绝。
  - **先验证后动手**：归档安全校验（core.security.archive_guard）+ 解压到暂存区
    + 对全部 .py 做编译检查，任何一步失败都不触碰线上文件。
  - **可回滚**：替换前把旧文件移动到 ``<root>/.update_backup/<stamp>/``，
    替换中途失败自动恢复；成功后也可显式回滚到任意备份。
  - **保护用户数据**：只替换 ``FRAMEWORK_INCLUDE`` 白名单内的框架代码/文档；
    ``data/``、``repo/``、``config.yaml``、``extensions.yaml`` 的用户改动、
    ``software/plugins/`` 等一律不动（``software`` 只更新 ``extensions`` 与
    ``__init__.py``）。

本模块不负责下载、不负责重启进程：调用方（WebUI / 节点代理）准备好 ZIP
后调用 ``apply_source_zip``；升级后需重启才能加载新代码。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile

from core.security.archive_guard import (
    FRAMEWORK_UPDATE_LIMITS,
    SafeArchiveError,
    safe_extract_zip,
    validate_zip,
)

logger = logging.getLogger('zernus')

__all__ = [
    'UpdateError', 'FRAMEWORK_INCLUDE', 'BACKUP_DIRNAME',
    'sha256_file', 'apply_source_zip', 'rollback_source_update',
    'list_backups',
]

BACKUP_DIRNAME = '.update_backup'

# 只更新框架自身的代码与文档。刻意排除：
#   data/  repo/  tests/  .git/  .github/  .cuckooCode/
#   config.yaml / extensions.yaml（用户配置与官方扩展端口）
#   software/plugins/（用户插件）
FRAMEWORK_INCLUDE = (
    'core',
    'service',
    'sql',
    'deploy',
    'docs',
    'software/extensions',
    'software/__init__.py',
    'main.py',
    'requirements.txt',
    'start.sh',
    'README.md',
    'LICENSE',
    'NOTICE',
    'VERSION',
    'CHANGELOG.md',
    'demo_threelayer.py',
    '.gitignore',
    '.gitattributes',
)


class UpdateError(ValueError):
    """更新包或更新过程不合规。调用方应捕获并拒绝/回滚。"""


def sha256_file(path: str) -> str:
    """分块计算文件 sha256（避免整包读入内存）。"""
    import hashlib

    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(rel: str) -> str:
    """规范化白名单相对路径，拒绝绝对路径 / 穿越 / 盘符。"""
    rel = str(rel).replace('\\', '/').strip('/')
    parts = [p for p in rel.split('/') if p and p != '.']
    if not parts or any(p == '..' for p in parts):
        raise UpdateError(f'非法更新路径: {rel!r}')
    if ':' in parts[0]:
        raise UpdateError(f'非法更新路径（盘符/ADS）: {rel!r}')
    return '/'.join(parts)


def _inside(root: str, path: str) -> bool:
    """path 是否位于 root 内（realpath 归一化，防符号链接逃逸）。"""
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    try:
        return os.path.commonpath([root_real, path_real]) == root_real
    except ValueError:          # 不同盘符
        return False


def _remove(path: str):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.exists(path) or os.path.islink(path):
        os.unlink(path)


def _extract_root(stage: str) -> str:
    """GitHub archive 通常含单层 repo-tag/ 目录；否则以解压根为源。"""
    entries = [e for e in os.listdir(stage) if e != '__MACOSX']
    dirs = [e for e in entries if os.path.isdir(os.path.join(stage, e))]
    files = [e for e in entries if os.path.isfile(os.path.join(stage, e))]
    if len(dirs) == 1 and not files:
        return os.path.join(stage, dirs[0])
    return stage


def _compile_tree(root: str):
    """对暂存区所有 .py 做语法编译检查；失败抛 UpdateError（不落线上）。"""
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for name in filenames:
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, 'rb') as f:
                    compile(f.read(), path, 'exec')
            except (SyntaxError, ValueError, UnicodeError) as e:
                bad.append(f'{os.path.relpath(path, root)}: {type(e).__name__}: {e}')
    if bad:
        raise UpdateError('更新包含无法编译的 Python 文件: ' + '; '.join(bad[:5]))


def _new_backup_dir(root: str, stamp: str, sha: str) -> str:
    base = os.path.join(root, BACKUP_DIRNAME)
    os.makedirs(base, exist_ok=True)
    name = f'{stamp}-{(sha or "unsigned")[:12]}'
    path = os.path.join(base, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(base, f'{name}-{n}')
    os.makedirs(path)
    return path


def apply_source_zip(zip_path: str, root: str, expected_sha256: str = '',
                     allow_unsigned: bool = False, include=None,
                     limits=None, dry_run: bool = False,
                     compile_check: bool = True) -> dict:
    """校验并应用一个框架源码 ZIP。

    :param zip_path: 本地 ZIP 路径（调用方负责下载/清理）
    :param root: 框架根目录（替换目标）
    :param expected_sha256: 期望的 ZIP sha256；为空时须 allow_unsigned=True
    :param include: 覆盖白名单（默认 ``FRAMEWORK_INCLUDE``）
    :param dry_run: True 时只校验/解压/编译，不移动任何线上文件
    :raises UpdateError: 校验失败、包损坏、编译失败或替换失败（已自动回滚）
    :return: 报告 dict（updated / existed / backup / sha256 / dry_run / restart_required）
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise UpdateError(f'框架根目录不存在: {root}')
    if not os.path.isfile(zip_path):
        raise UpdateError(f'升级包不存在: {zip_path}')
    expected = str(expected_sha256 or '').strip().lower()
    if not expected and not allow_unsigned:
        raise UpdateError('缺少目标 ZIP 的 sha256；拒绝未校验的框架更新')
    if expected and (len(expected) != 64 or any(c not in '0123456789abcdef'
                                                for c in expected)):
        raise UpdateError('sha256 必须是 64 位小写十六进制')

    if expected:
        actual = sha256_file(zip_path)
        import hmac
        if not hmac.compare_digest(actual, expected):
            raise UpdateError(f'ZIP sha256 校验失败：期望 {expected[:16]}…，'
                              f'实际 {actual[:16]}…')
    else:
        actual = sha256_file(zip_path)
        logger.warning('框架更新：显式允许未签名更新（allow_unsigned）')

    include = tuple(_safe_rel(p) for p in (include or FRAMEWORK_INCLUDE))
    limits = dict(limits or FRAMEWORK_UPDATE_LIMITS)

    stage = tempfile.mkdtemp(prefix='zernus_fw_stage_')
    staged = tempfile.mkdtemp(prefix='zernus_fw_tree_')
    try:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                validate_zip(zf, limits)
                safe_extract_zip(zf, stage, limits, prevalidated=True)
        except SafeArchiveError as e:
            raise UpdateError(f'更新包校验失败: {e}') from e
        except zipfile.BadZipFile as e:
            raise UpdateError(f'更新包不是有效的 ZIP: {e}') from e
        except OSError as e:
            raise UpdateError(f'解压更新包失败: {e}') from e

        src_root = _extract_root(stage)
        present = []
        for rel in include:
            src = os.path.join(src_root, rel.replace('/', os.sep))
            if not os.path.exists(src):
                continue
            dst = os.path.join(staged, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst,
                                ignore=shutil.ignore_patterns('__pycache__'))
            else:
                shutil.copy2(src, dst)
            present.append(rel)
        if not present:
            raise UpdateError('更新包不包含任何框架文件（白名单未命中）')

        if compile_check:
            _compile_tree(staged)

        existed = {rel: os.path.exists(os.path.join(root, rel.replace('/', os.sep)))
                   for rel in present}
        report = {'updated': present, 'existed': existed, 'sha256': actual,
                  'backup': None, 'root': root, 'dry_run': bool(dry_run),
                  'restart_required': True}
        if dry_run:
            report['valid'] = True
            return report

        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_dir = _new_backup_dir(root, stamp, actual)
        moved, placed = [], []
        try:
            # 1) 旧文件移入备份（保留原相对路径）
            for rel in present:
                dst = os.path.join(root, rel.replace('/', os.sep))
                if not existed[rel]:
                    continue
                bak = os.path.join(backup_dir, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                shutil.move(dst, bak)
                moved.append(rel)
            # 2) 暂存区新文件移入线上
            for rel in present:
                src = os.path.join(staged, rel.replace('/', os.sep))
                dst = os.path.join(root, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                placed.append(rel)
        except Exception as e:
            logger.exception('框架更新替换失败，开始自动回滚')
            _restore(root, backup_dir, placed, moved)
            raise UpdateError(f'替换框架文件失败（已回滚）: {type(e).__name__}: {e}') from e

        manifest = {
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'stamp': stamp, 'sha256': actual, 'updated': present,
            'existed': existed, 'rolled_back_at': None,
        }
        try:
            with open(os.path.join(backup_dir, 'manifest.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.warning('框架更新：备份清单写入失败（回滚仍可用目录）', exc_info=True)

        report['backup'] = os.path.basename(backup_dir)
        logger.info('框架更新已应用: %s 项，备份 %s', len(present), report['backup'])
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)


def _restore(root: str, backup_dir: str, placed, moved):
    """把已替换的条目删掉，再移回备份；尽力而为，单条失败不影响其它条目。"""
    for rel in reversed(list(placed)):
        dst = os.path.join(root, rel.replace('/', os.sep))
        try:
            if _inside(root, dst):
                _remove(dst)
        except Exception:
            logger.exception('回滚：删除新文件失败 %s', rel)
    for rel in reversed(list(moved)):
        bak = os.path.join(backup_dir, rel.replace('/', os.sep))
        dst = os.path.join(root, rel.replace('/', os.sep))
        try:
            if os.path.exists(bak) and _inside(root, dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(bak, dst)
        except Exception:
            logger.exception('回滚：恢复备份失败 %s', rel)


def _load_manifest(backup_dir: str):
    path = os.path.join(backup_dir, 'manifest.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def list_backups(root: str) -> list:
    """列出可回滚的备份（新→旧）。只读，不修改任何文件。"""
    base = os.path.join(os.path.abspath(root), BACKUP_DIRNAME)
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        bdir = os.path.join(base, name)
        if not os.path.isdir(bdir):
            continue
        m = _load_manifest(bdir) or {}
        out.append({
            'name': name,
            'created_at': m.get('created_at') or '',
            'sha256': m.get('sha256') or '',
            'updated_count': len(m.get('updated') or []),
            'rolled_back_at': m.get('rolled_back_at'),
        })
    out.sort(key=lambda x: (x['created_at'], x['name']), reverse=True)
    return out


def rollback_source_update(root: str, backup: str = '') -> dict:
    """回滚到指定备份（为空则最新一份）：删除新文件并移回旧文件。

    只影响该备份 manifest 中记录的条目；不会动用户数据。回滚后仍需重启。
    """
    root = os.path.abspath(root)
    base = os.path.join(root, BACKUP_DIRNAME)
    backups = list_backups(root)
    if not backups:
        raise UpdateError('没有可用的框架更新备份')
    name = str(backup or '').strip()
    if name:
        if os.path.basename(name) != name:
            raise UpdateError(f'非法备份名: {backup!r}')
        names = {b['name'] for b in backups}
        if name not in names:
            raise UpdateError(f'备份不存在: {name}')
    else:
        name = backups[0]['name']
    bdir = os.path.join(base, name)
    if not _inside(base, bdir):
        raise UpdateError('备份路径越界')

    manifest = _load_manifest(bdir)
    if manifest is None:
        raise UpdateError(f'备份缺少 manifest.json，无法安全回滚: {name}')
    updated = [_safe_rel(r) for r in (manifest.get('updated') or [])]
    existed = manifest.get('existed') or {}
    if not updated:
        raise UpdateError(f'备份没有记录任何更新条目: {name}')

    restored, removed = [], []
    for rel in updated:
        dst = os.path.join(root, rel.replace('/', os.sep))
        if not _inside(root, dst):
            raise UpdateError(f'回滚路径越界: {rel}')
        bak = os.path.join(bdir, rel.replace('/', os.sep))
        if existed.get(rel):
            # 备份里还有旧文件才替换；没有说明已回滚过，保持现状（幂等）。
            if os.path.exists(bak):
                if os.path.exists(dst):
                    _remove(dst)
                    removed.append(rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(bak, dst)
                restored.append(rel)
        else:
            # 升级新增的条目：回滚就是删除；不存在时天然幂等。
            if os.path.exists(dst):
                _remove(dst)
                removed.append(rel)

    manifest['rolled_back_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(os.path.join(bdir, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.warning('回滚：更新备份清单失败', exc_info=True)

    logger.info('框架更新已回滚到 %s（恢复 %d 项）', name, len(restored))
    return {'backup': name, 'restored': restored, 'removed': removed,
            'root': root, 'restart_required': True}
