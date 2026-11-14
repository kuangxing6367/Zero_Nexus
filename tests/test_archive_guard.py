# -*- coding: utf-8 -*-
"""归档解包安全层回归测试（core/security/archive_guard.py）

覆盖：
1. 正常 ZIP / TAR 放行并返回统计；safe_extract_* 真实落盘
2. 路径穿越 / 绝对路径 / 盘符 / 反斜杠 / 控制字符 / NTFS ADS / Windows 保留名
3. NFC + casefold 归一化碰撞
4. ZIP 特殊成员类型（symlink / device / FIFO）与 TAR 非普通成员
5. 资源配额：条目数、总体积、单文件体积、整体与单条压缩比、路径深度、名字长度
6. containment 复核与 prevalidated 语义
7. 三档配额常量与 limits 覆盖
8. repo/pool/*.tar.gz 与 repo/dist/index.json 的 sha256 一致性（15/15）
9. 四个外部归档入口确实接入 archive_guard

脚本式测试（顶层直接运行，不适用 unittest discover）：
    python tests/test_archive_guard.py
"""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import types
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security import (
    SafeArchiveError, DEFAULT_LIMITS, FRAMEWORK_UPDATE_LIMITS,
    SOURCE_TARBALL_LIMITS, validate_zip, safe_extract_zip,
    validate_tar, safe_extract_tar,
)

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


def _raw_info(name, size=0, csize=0, mode=0o600):
    """不经过 zipfile.ZipInfo 的原始成员对象。

    ZipInfo 会在构造时把 ``\\`` 规范化为 ``/``、在 NUL 处截断文件名，
    用它无法构造含非法字符的成员名。但真实归档可能由任意解析器产生，
    guard 必须对原始字符串独立生效，故这里用 SimpleNamespace 绕开规范化。
    """
    return types.SimpleNamespace(
        filename=name, external_attr=mode << 16,
        file_size=size, compress_size=csize,
    )


def rejects_zip(name, entries, limits=None, pre=False, raw=False):
    """构造 ZIP 并断言校验拒绝。entries: [(filename, data, external_attr|None)]

    raw=True 时直接把原始成员名喂给 guard（不经过标准库规范化），
    用于验证「标准库无法构造、但外部解析器可能产出」的非法名字。
    """
    if raw:
        infos = [_raw_info(item[0], len(item[1]), len(item[1]),
                           item[2] if len(item) > 2 and item[2] is not None else 0o600)
                 for item in entries]
        zf = _FakeZip(infos)
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zw:
            for item in entries:
                fn, data = item[0], item[1]
                info = zipfile.ZipInfo(fn)
                info.create_system = 3
                if len(item) > 2 and item[2] is not None:
                    info.external_attr = item[2] << 16
                zw.writestr(info, data)
        buf.seek(0)
        zf = zipfile.ZipFile(buf)
    try:
        if pre:
            safe_extract_zip(zf, tempfile.mkdtemp(), limits, prevalidated=True)
        else:
            validate_zip(zf, limits)
    except SafeArchiveError:
        check(name, True)
        return
    except Exception as e:
        check(name, False, f"抛出了非 SafeArchiveError: {type(e).__name__}: {e}")
        return
    finally:
        if not raw:
            zf.close()
    check(name, False, "恶意归档被放行")


def make_zip(entries, limits=None):
    """返回 (ZipFile 已打开的只读对象, buffer)。调用方负责关闭。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for fn, data in entries:
            info = zipfile.ZipInfo(fn)
            info.create_system = 3
            zf.writestr(info, data)
    buf.seek(0)
    return zipfile.ZipFile(buf), buf


class _FakeZip:
    """只暴露 infolist() 的轻量替身，用于精确构造配额边界而不写真实数据。"""
    def __init__(self, infos):
        self._infos = infos

    def infolist(self):
        return self._infos


def _zi(name, size=0, csize=0):
    i = zipfile.ZipInfo(name)
    i.create_system = 3
    i.file_size = size
    i.compress_size = csize
    return i


print("== 1. 模块与常量 ==")
check('SafeArchiveError 是 ValueError 子类', issubclass(SafeArchiveError, ValueError))
check('DEFAULT_LIMITS 常规配额', DEFAULT_LIMITS['max_entries'] == 1000
      and DEFAULT_LIMITS['max_total_bytes'] == 64 * 1024 * 1024
      and DEFAULT_LIMITS['max_ratio'] == 200)
check('FRAMEWORK_UPDATE_LIMITS 放宽条目数', FRAMEWORK_UPDATE_LIMITS['max_entries'] == 12000
      and FRAMEWORK_UPDATE_LIMITS['max_total_bytes'] == 512 * 1024 * 1024)
check('SOURCE_TARBALL_LIMITS 与框架包同档且非同一对象',
      SOURCE_TARBALL_LIMITS == FRAMEWORK_UPDATE_LIMITS
      and SOURCE_TARBALL_LIMITS is not FRAMEWORK_UPDATE_LIMITS)

print("== 2. 正常归档放行与落盘 ==")
zf, buf = make_zip([('pkg/main.py', b'print(1)'), ('pkg/readme.txt', b'hello')])
try:
    st = validate_zip(zf)
    check('validate_zip 返回统计', st['entries'] == 2 and st['uncompressed_bytes'] == 13
          and 'ratio' in st, str(st))
    with tempfile.TemporaryDirectory() as d:
        st2 = safe_extract_zip(zf, d)
        check('safe_extract_zip 真实落盘', os.path.isfile(os.path.join(d, 'pkg', 'main.py'))
              and st2['entries'] == 2)
finally:
    zf.close()

with tempfile.TemporaryDirectory() as d:
    zf2, buf2 = make_zip([('a.txt', b'A'), ('sub/b.txt', b'BB')])
    try:
        st3 = safe_extract_zip(zf2, d, prevalidated=True)
        check('prevalidated=True 仍落盘且返回统计',
              os.path.isfile(os.path.join(d, 'a.txt')) and st3['entries'] == 2)
    finally:
        zf2.close()

print("== 3. 路径与文件名拒绝 ==")
rejects_zip('绝对路径', [('/etc/passwd', b'x')])
rejects_zip('盘符前缀', [('C:/windows/evil', b'x')])
rejects_zip('父目录穿越', [('../evil.txt', b'x')])
rejects_zip('内嵌穿越段', [('a/../../evil', b'x')])
# 反斜杠 / NUL 会被 zipfile.ZipInfo 自动规范化或截断，故用 raw=True
# 直接喂原始名字，验证 guard 不依赖标准库的规范化行为。
rejects_zip('反斜杠（原始名字）', [('a\\b\\evil', b'x')], raw=True)
rejects_zip('NTFS ADS 冒号', [('a.txt:stream', b'x')])
rejects_zip('NUL 字节（原始名字）', [('a\x00b.txt', b'x')], raw=True)
rejects_zip('控制字符', [('a\nb.txt', b'x')])
rejects_zip('Windows 保留名 CON', [('CON', b'x')])
rejects_zip('Windows 保留名 COM1', [('sub/COM1.txt', b'x')])
rejects_zip('尾随点', [('evil.', b'x')])
rejects_zip('尾随空格', [('evil ', b'x')])
rejects_zip('路径过深', [('a/b/c/d/e/f.txt', b'x')], {'max_depth': 3})
rejects_zip('成员名过长', [('x' * 260 + '.txt', b'x')])

print("== 4. 归一化碰撞 ==")
rejects_zip('casefold 碰撞', [('A.txt', b'1'), ('a.txt', b'2')])
rejects_zip('NFC 碰撞（é 组合/预组合）',
           [('cafe\u0301.txt', b'1'), ('caf\u00e9.txt', b'2')])

print("== 5. 特殊成员类型 ==")
rejects_zip('ZIP symlink 成员', [('link', b'target', 0o120777)])
rejects_zip('ZIP FIFO 成员', [('fifo', b'', 0o010600)])

print("== 6. 资源配额 ==")
try:
    validate_zip(_FakeZip([_zi('a', 1), _zi('b', 1), _zi('c', 1)]), {'max_entries': 2})
    check('条目数超限被拒绝', False, '被放行')
except SafeArchiveError:
    check('条目数超限被拒绝', True)
try:
    validate_zip(_FakeZip([_zi('big', 999)]), {'max_total_bytes': 100})
    check('总展开体积超限被拒绝', False, '被放行')
except SafeArchiveError:
    check('总展开体积超限被拒绝', True)
try:
    validate_zip(_FakeZip([_zi('one', 500)]), {'max_single_bytes': 100})
    check('单文件体积超限被拒绝', False, '被放行')
except SafeArchiveError:
    check('单文件体积超限被拒绝', True)
try:
    # 单条 < 1MiB 不触发单条比例，但整体 ratio = 1000000/10 = 100000 > 200
    infos = [_zi('f%d' % i, 100000, 1) for i in range(10)]
    validate_zip(_FakeZip(infos))
    check('整体压缩比（zip bomb）被拒绝', False, '被放行')
except SafeArchiveError:
    check('整体压缩比（zip bomb）被拒绝', True)
try:
    validate_zip(_FakeZip([_zi('huge', 4 * 1024 * 1024, 1024)]))
    check('单条高压缩比被拒绝', False, '被放行')
except SafeArchiveError:
    check('单条高压缩比被拒绝', True)
try:
    # 小文本文件即便压缩比偏高也应放行（不误伤）
    validate_zip(_FakeZip([_zi('small.txt', 10, 1)]))
    check('小文本文件不被压缩比误伤', True)
except SafeArchiveError as e:
    check('小文本文件不被压缩比误伤', False, str(e))

print("== 7. TAR 校验与落盘 ==")
def make_tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        for name, data, mtype in members:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            if mtype is not None:
                ti.type = mtype
                ti.linkname = 'target'
            tf.addfile(ti, io.BytesIO(data))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode='r')

tf = make_tar([('pkg/main.py', b'print(1)', None), ('pkg/', b'', tarfile.DIRTYPE)])
try:
    st = validate_tar(tf, tempfile.mkdtemp())
    check('validate_tar 返回统计', st['entries'] == 2 and st['uncompressed_bytes'] == 8, str(st))
    with tempfile.TemporaryDirectory() as d:
        st2 = safe_extract_tar(tf, d)
        check('safe_extract_tar 真实落盘', os.path.isfile(os.path.join(d, 'pkg', 'main.py'))
              and st2['entries'] == 2)
finally:
    tf.close()

for label, members in [
    ('TAR symlink 成员', [('link', b'', tarfile.SYMTYPE)]),
    ('TAR 绝对路径', [('/etc/evil', b'x', None)]),
    ('TAR 父目录穿越', [('../evil', b'x', None)]),
    ('TAR 盘符路径', [('C:/evil', b'x', None)]),
]:
    t = make_tar(members)
    try:
        with tempfile.TemporaryDirectory() as d:
            try:
                validate_tar(t, d)
                check(label, False, '被放行')
            except SafeArchiveError:
                check(label, True)
    finally:
        t.close()

print("== 8. containment 复核（prevalidated 不可绕过） ==")
# prevalidated=True 跳过前置遍历，但 safe_extract_zip 仍逐条做 commonpath 复核
class _EvilZip:
    def __init__(self):
        self.calls = 0
    def infolist(self):
        self.calls += 1
        return [_zi('../escape.txt', 1, 1)]


ez = _EvilZip()
try:
    safe_extract_zip(ez, tempfile.mkdtemp(), prevalidated=True)
    check('prevalidated 下 containment 仍拒绝越界成员', False, '被放行')
except SafeArchiveError:
    check('prevalidated 下 containment 仍拒绝越界成员', True)
except Exception as e:
    check('prevalidated 下 containment 仍拒绝越界成员', False,
          f'异常类型不符: {type(e).__name__}: {e}')

print("== 9. repo/pool 与 dist/index.json 一致性 ==")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx_path = os.path.join(root, 'repo', 'dist', 'index.json')
with open(idx_path, encoding='utf-8') as f:
    idx = json.load(f)
bad = []
for p in idx['packages']:
    fp = os.path.join(root, 'repo', p['url'])
    if not os.path.isfile(fp):
        bad.append((p['id'], 'MISSING'))
        continue
    with open(fp, 'rb') as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if h != p.get('sha256'):
        bad.append((p['id'], 'MISMATCH'))
check('全部机制包 sha256 与索引一致 (%d 个)' % len(idx['packages']),
      not bad, str(bad))

print("== 10. 外部归档入口已接入 ==")
for rel, keyword in [
    ('core/terminal/builtins.py', 'safe_extract_zip'),
    ('service/updater.py', 'validate_zip'),
    ('service/updater.py', 'safe_extract_zip'),
    ('software/extensions/webui/plugins.py', 'validate_zip'),
    ('service/zkg/loader.py', 'safe_extract_tar'),
]:
    with open(os.path.join(root, rel), encoding='utf-8') as f:
        src = f.read()
    check('%s 已接入 %s' % (rel, keyword),
          'archive_guard' in src and keyword in src)

# 框架更新已收口到 service/updater.py：WebUI 必须走该机制，
# 不得自行解压/覆盖（否则会绕过备份回滚与暂存编译检查）。
with open(os.path.join(root, 'software/extensions/webui/framework_ops.py'),
          encoding='utf-8') as f:
    fw_src = f.read()
check('framework_ops.py 通过 service.updater 应用更新',
      'service.updater' in fw_src and 'apply_source_zip' in fw_src
      and 'safe_extract_zip' not in fw_src)

print()
print(f"通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
