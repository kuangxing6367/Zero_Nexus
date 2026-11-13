# -*- coding: utf-8 -*-
"""归档解包安全校验（自包含，只用标准库）。

依据（均为官方规范 / 官方文档，非经验推断）：
  · Python 3.13 ``tarfile`` 文档「Extraction filters」与 PEP 706：
    3.12-3.13 未显式传 filter 时回退 ``fully_trusted``（危险行为），
    3.14 起默认才改为 ``data``；``data`` 会拒绝越界链接、绝对路径与设备文件。
  · Python 3.14 ``tarfile`` 文档「Hints for further verification」明确要求：
    限制条目数、总展开体积、单文件体积与文件名长度；检查文件名字符白名单；
    检查大小写不敏感文件系统上的遮蔽（shadowed files）。
    同时警告 ``None of the available filters blocks all dangerous archive features``。
  · Python ``zipfile`` 文档：``extractall`` 会剥离盘符/前导斜杠与 ``..``，
    但官方仍警告「绝不要未经预先检验就从不可靠的源中提取归档文件」。
    因此经典 Zip Slip 会被标准库部分挡住，本模块的价值在 canonical
    containment 复核 + 资源配额 + 命名可控性。
  · CWE-22（路径穿越）/ CWE-409（高度压缩数据）/ CWE-400（资源消耗）/
    CWE-73（外部控制文件名）/ CWE-434（危险文件上传）。
  · OWASP WSTG「Test Upload of Malicious Files」：Archive Directory Traversal
    与 Zip Bombs 需显式防护。
  · Unicode UTR #36 / UTS #39：NFC + casefold 归一化碰撞会在 Windows / macOS
    默认文件系统上造成静默覆盖。
  · Microsoft Learn「Naming Files, Paths, and Namespaces」：CON/NUL/COM1..9/
    LPT1..9、尾随点空格、冒号（NTFS ADS）不可用作普通文件名。

用法：在**任何不可逆动作之前**（移走旧目录、删旧代码、覆盖框架文件）
先调用 ``validate_zip`` / ``validate_tar``，通过后再调用
``safe_extract_zip`` / ``safe_extract_tar`` 落盘。不合规一律抛
``SafeArchiveError``（ValueError 子类），调用方必须捕获并拒绝请求。
"""
import os
import re
import tarfile
import unicodedata
import zipfile

__all__ = [
    'SafeArchiveError', 'DEFAULT_LIMITS', 'FRAMEWORK_UPDATE_LIMITS',
    'SOURCE_TARBALL_LIMITS', 'validate_zip', 'safe_extract_zip',
    'validate_tar', 'safe_extract_tar',
]

# 插件 / 模板 / 机制包：体积与条目数按常规发布包收紧
DEFAULT_LIMITS = {
    'max_entries': 1000,
    'max_total_bytes': 64 * 1024 * 1024,
    'max_single_bytes': 16 * 1024 * 1024,
    'max_ratio': 200,
    'max_depth': 16,
    'max_name_len': 255,
}
# GitHub archive / 框架自更新包：条目数远多于插件包，单独放宽
FRAMEWORK_UPDATE_LIMITS = {
    'max_entries': 12000,
    'max_total_bytes': 512 * 1024 * 1024,
    'max_single_bytes': 64 * 1024 * 1024,
    'max_ratio': 200,
    'max_depth': 24,
    'max_name_len': 255,
}
# 源码 tarball（nginx 源码编译等）：同样放宽
SOURCE_TARBALL_LIMITS = dict(FRAMEWORK_UPDATE_LIMITS)

_WINDOWS_RESERVED = frozenset(
    ['CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'] +
    ['COM%d' % i for i in range(1, 10)] +
    ['LPT%d' % i for i in range(1, 10)]
)
# UNIX 特殊文件类型（symlink / char dev / block dev / FIFO / socket 等）
_FORBIDDEN_ZIP_TYPES = frozenset(
    [0x1000, 0x2000, 0x3000, 0x5000, 0x6000, 0xA000, 0xB000, 0xC000, 0xD000]
)
# tarfile.FilterError 只在 3.11.4+ / 3.12+ 存在，旧版本需兼容
_FILTER_ERRORS = tuple(
    x for x in (getattr(tarfile, 'FilterError', None),) if x is not None
)


class SafeArchiveError(ValueError):
    """归档不合规，拒绝解压。"""


def _limits(overrides=None):
    lim = dict(DEFAULT_LIMITS)
    if overrides:
        lim.update(overrides)
    return lim


def _check_name(name, lim):
    """成员名白名单校验：穿越、盘符、ADS、控制字符、大小写/归一化等。"""
    if not isinstance(name, str) or not name:
        raise SafeArchiveError('empty archive member name')
    if len(name.encode('utf-8', 'surrogatepass')) > 255 or len(name) > lim['max_name_len']:
        raise SafeArchiveError('archive member name too long: %r' % name[:80])
    if '\x00' in name or any(ord(c) < 32 for c in name):
        raise SafeArchiveError('control character in archive member name')
    if '\\' in name:
        raise SafeArchiveError('backslash in archive member name: %r' % name[:80])
    if name.startswith('/'):
        raise SafeArchiveError('absolute archive member path: %r' % name[:80])
    if re.match(r'^[A-Za-z]:', name):
        raise SafeArchiveError('drive-qualified archive member path: %r' % name[:80])
    parts = [p for p in name.split('/') if p not in ('', '.')]
    if not parts:
        return
    if len(parts) > lim['max_depth']:
        raise SafeArchiveError('archive member path too deep')
    for part in parts:
        if part == '..':
            raise SafeArchiveError('path traversal segment in %r' % name[:80])
        if ':' in part:
            raise SafeArchiveError('NTFS alternate data stream / colon in %r' % part[:80])
        if part != part.strip() or part.endswith('.'):
            raise SafeArchiveError('trailing dot/space in %r' % part[:80])
        stem = part.rsplit('.', 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise SafeArchiveError('Windows reserved device name %r' % part[:80])


def _unique_key(name, seen):
    key = unicodedata.normalize('NFC', name).casefold()
    if key in seen:
        raise SafeArchiveError('case/normalization collision: %r' % name[:80])
    seen.add(key)


def _contained(dest_real, name):
    """canonical containment 复核（纵深防御，不依赖标准库的剥离行为）。"""
    target = os.path.realpath(os.path.join(dest_real, name))
    try:
        return os.path.commonpath([dest_real, target]) == dest_real
    except ValueError:
        return False


# ---------------------------------------------------------------- ZIP

def validate_zip(zf, limits=None):
    """只校验、不落盘。返回统计字典；不合规抛 SafeArchiveError。

    必须先于「移动旧目录 / 覆盖代码」等不可逆动作调用。
    """
    lim = _limits(limits)
    infos = zf.infolist()
    n = len(infos)
    if n > lim['max_entries']:
        raise SafeArchiveError('too many archive entries: %d > %d'
                               % (n, lim['max_entries']))

    total = 0
    compressed = 0
    seen = set()
    for info in infos:
        name = info.filename
        _check_name(name, lim)
        _unique_key(name, seen)

        mode = (info.external_attr >> 16) & 0xFFFF
        if (mode & 0xF000) in _FORBIDDEN_ZIP_TYPES:
            raise SafeArchiveError('special file type (symlink/device/FIFO): %r' % name[:80])

        size = int(info.file_size or 0)
        csize = int(info.compress_size or 0)
        compressed += csize
        if size > lim['max_single_bytes']:
            raise SafeArchiveError('single member too large: %d bytes' % size)
        # 单条压缩比只对大文件判定，避免误伤正常的小文本/清单文件
        if size > 1024 * 1024 and csize > 0 and size / csize > lim['max_ratio']:
            raise SafeArchiveError('single member compression ratio too high: %.0f' % (size / csize))
        total += size
        if total > lim['max_total_bytes']:
            raise SafeArchiveError('total uncompressed size exceeds %d bytes'
                                   % lim['max_total_bytes'])

    ratio = total / max(compressed, 1)
    if ratio > lim['max_ratio']:
        raise SafeArchiveError('archive compression ratio too high: %.0f (> %d), zip bomb?'
                               % (ratio, lim['max_ratio']))
    return {'entries': n, 'compressed_bytes': compressed,
            'uncompressed_bytes': total, 'ratio': round(ratio, 2)}


def safe_extract_zip(zf, dest, limits=None, prevalidated=False):
    """校验并解压；解压前逐条做 canonical containment 复核。

    ``prevalidated=True`` 用于调用方已在不可逆操作前跑过 validate_zip 的场景，
    避免重复遍历（不改变安全语义，containment 复核仍执行）。
    """
    stats = None if prevalidated else validate_zip(zf, limits)
    dest_real = os.path.realpath(dest)
    os.makedirs(dest_real, exist_ok=True)
    for info in zf.infolist():
        if not _contained(dest_real, info.filename):
            raise SafeArchiveError('member escapes destination: %r' % info.filename[:80])
    zf.extractall(dest_real)
    if stats is None:
        stats = validate_zip(zf, limits)
    return stats


# ---------------------------------------------------------------- TAR

def validate_tar(tf, dest, limits=None):
    """只校验、不落盘。逐成员类型白名单 + canonical containment + 配额。

    PEP 706 的 ``data`` filter 不覆盖资源配额，且官方明确说明
    「没有一种 filter 能挡住所有危险特性」，因此这里做独立预检，
    与随后传入的 ``filter='data'`` 形成双保险。
    """
    lim = _limits(limits)
    dest_real = os.path.realpath(dest)
    members = tf.getmembers()
    n = len(members)
    if n > lim['max_entries']:
        raise SafeArchiveError('too many archive entries: %d > %d'
                               % (n, lim['max_entries']))
    total = 0
    seen = set()
    for m in members:
        name = m.name or ''
        _check_name(name, lim)
        _unique_key(name, seen)
        # 只允许普通文件与目录；symlink/hardlink/device/FIFO 一律拒绝
        if not (m.isfile() or m.isdir()):
            raise SafeArchiveError('unsafe tar member type: %r' % name[:80])
        size = int(m.size or 0)
        if size > lim['max_single_bytes']:
            raise SafeArchiveError('single member too large: %d bytes' % size)
        total += size
        if total > lim['max_total_bytes']:
            raise SafeArchiveError('total uncompressed size exceeds %d bytes'
                                   % lim['max_total_bytes'])
        if not _contained(dest_real, name):
            raise SafeArchiveError('member escapes destination: %r' % name[:80])
    return {'entries': n, 'uncompressed_bytes': total}


def safe_extract_tar(tf, dest, limits=None, prevalidated=False):
    """逐成员预检后以 ``filter='data'`` 解包；旧版本 Python 回退逐成员白名单。

    注意：PEP 706 明确说明解包中止时可能留下部分已解包文件、不会自动清理。
    本函数已在落盘前完成全部成员预检，正常情况下不会触发中止；
    调用方若仍需原子性，应解包到临时目录后再整体移动。
    """
    stats = None if prevalidated else validate_tar(tf, dest, limits)
    dest_real = os.path.realpath(dest)
    os.makedirs(dest_real, exist_ok=True)
    try:
        try:
            tf.extractall(dest_real, filter='data')
        except TypeError:
            # Python < 3.11.4 / 3.12 之前无 filter 参数：
            # validate_tar 已完成类型白名单与 containment 预检，按旧语义解包。
            tf.extractall(dest_real)
    except (tarfile.TarError, OSError, ValueError, EOFError) + _FILTER_ERRORS as e:
        raise SafeArchiveError('tar extraction rejected: %s: %s'
                               % (type(e).__name__, e)) from e
    if stats is None:
        stats = validate_tar(tf, dest, limits)
    return stats
