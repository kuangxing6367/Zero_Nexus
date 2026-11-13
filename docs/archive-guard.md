# 归档解包安全校验（archive_guard）

`core/security/archive_guard.py` 是全站统一的归档解包安全层，**纯标准库、自包含**，
不依赖框架内核。所有会落地外部文件的入口（框架自更新、插件包上传、zkg 机制包安装、
终端更新命令）都必须经过它。

本文中的 API 名、参数、配额均对应当前源码；修改前请核验 `core/security/archive_guard.py`。

## 为什么需要独立一层

标准库 `zipfile.extractall()` / `tarfile.extractall()` 本身**不是安全边界**：

- `zipfile` 文档明确警告「绝不要未经预先检验就从不可靠的源中提取归档文件」；
  它会剥离盘符、前导斜杠与 `..`，但不会检查资源配额、文件名可控性与归一化碰撞。
- `tarfile` 的 PEP 706 extraction filters 在 Python 3.12–3.13 未显式传 `filter` 时
  回退 `fully_trusted`（危险行为），3.14 起默认才改为 `data`。且官方文档明确：
  **「没有一种 filter 能挡住所有危险归档特性」**。
- `data` filter 不覆盖条目数、总展开体积、单文件体积、压缩比等资源配额，
  也不处理 Windows 保留设备名、NTFS ADS、NFC + casefold 归一化碰撞。

因此本模块在标准库之上补四类独立校验，并在**任何不可逆动作之前**完成。

## 校验清单

| 类别 | 拒绝条件 | 依据 |
| --- | --- | --- |
| 路径穿越 | 绝对路径、盘符前缀、`..` 段、反斜杠、`commonpath` 越界 | CWE-22 / CWE-73 |
| 文件名 | 控制字符、NUL、冒号（NTFS ADS）、尾随点/空格、`CON`/`NUL`/`COM1..9`/`LPT1..9`、UTF-8 字节长度 > 255、路径深度超限 | MS Learn 命名规范 |
| 成员类型 | ZIP 特殊文件类型（symlink / char dev / block dev / FIFO / socket）、TAR 非普通文件/目录成员 | PEP 706 |
| 资源配额 | 条目数、总展开体积、单文件体积、整体/单条压缩比 | CWE-409 / CWE-400 / OWASP WSTG |
| 归一化碰撞 | 成员名经 NFC + casefold 后重复（Windows / macOS 默认文件系统会静默覆盖） | UTR #36 / UTS #39 |

单条压缩比只对 **> 1 MiB** 的成员判定，避免误伤正常的小文本与清单文件。

## 三档配额

| 常量 | 适用 | max_entries | max_total_bytes | max_single_bytes | max_ratio | max_depth |
| --- | --- | --- | --- | --- | --- | --- |
| `DEFAULT_LIMITS` | 插件包、模板、机制包 | 1000 | 64 MiB | 16 MiB | 200 | 16 |
| `FRAMEWORK_UPDATE_LIMITS` | GitHub archive、框架自更新包 | 12000 | 512 MiB | 64 MiB | 200 | 24 |
| `SOURCE_TARBALL_LIMITS` | 源码 tarball（如 nginx 编译） | 同 `FRAMEWORK_UPDATE_LIMITS` | | | | |

`max_name_len` 统一 255。三档均可通过 `limits=` 参数按字段覆盖，覆盖值与默认值合并。

## API

```python
from core.security import (
    SafeArchiveError,                 # ValueError 子类
    DEFAULT_LIMITS, FRAMEWORK_UPDATE_LIMITS, SOURCE_TARBALL_LIMITS,
    validate_zip, safe_extract_zip,
    validate_tar, safe_extract_tar,
)
```

| 函数 | 作用 | 返回 |
| --- | --- | --- |
| `validate_zip(zf, limits=None)` | 只校验、不落盘 | 统计 dict：`entries` / `compressed_bytes` / `uncompressed_bytes` / `ratio` |
| `safe_extract_zip(zf, dest, limits=None, prevalidated=False)` | 校验 + containment 复核 + 落盘 | 统计 dict |
| `validate_tar(tf, dest, limits=None)` | 只校验、不落盘（含逐成员 containment） | 统计 dict：`entries` / `uncompressed_bytes` |
| `safe_extract_tar(tf, dest, limits=None, prevalidated=False)` | 逐成员预检 + `filter='data'` 解包 | 统计 dict |

不合规一律抛 `SafeArchiveError`（`ValueError` 子类），调用方**必须捕获并拒绝请求**，
不能吞掉异常后继续。

`prevalidated=True` 的语义：调用方已在不可逆操作前跑过 `validate_*`，此处跳过重复遍历；
**canonical containment 复核仍会执行**，安全语义不变。适用场景是先校验、再移走旧目录、
最后落盘——此时归档对象仍可复用，但不能再遍历一遍浪费开销。

## 必须遵守的调用顺序

```
收到外部归档
  → validate_zip / validate_tar（只校验，不落盘；可在此返回 HTTP 400）
  → 捕获 SafeArchiveError 并拒绝请求
  → 执行不可逆动作（移走旧插件目录、覆盖框架代码文件等）
  → safe_extract_zip / safe_extract_tar（prevalidated=True）
```

**校验必须先于不可逆动作。** 插件包上传此前是先备份/移走旧目录再 `extractall`，
一旦校验失败会把旧版插件一起弄丢。框架自更新同理：必须在覆盖任何代码文件前完成校验。

四个现有调用点：

| 位置 | 归档类型 | 配额 | 失败处理 |
| --- | --- | --- | --- |
| `software/extensions/webui/plugins.py` | 插件 ZIP | `DEFAULT_LIMITS` | HTTP 400 `插件包校验失败` |
| `software/extensions/webui/framework_ops.py` | 框架自更新 ZIP | `FRAMEWORK_UPDATE_LIMITS` | HTTP 400 `更新包校验失败` |
| `core/terminal/builtins.py` | 终端更新 ZIP | `FRAMEWORK_UPDATE_LIMITS` | 抛 `SafeArchiveError` |
| `service/zkg/loader.py` | 机制包 TAR | `DEFAULT_LIMITS` | 记日志并返回 `False` |

## 已知边界（必须知道）

1. **解包不保证原子性**。PEP 706 明确：解包中止时可能留下部分已落盘文件、不会自动清理。
   本模块已在落盘前完成全部成员预检，正常情况不会触发中止；需要强原子性的调用方
   **应解包到临时目录，校验通过后再整体移动**。
2. **不替代签名校验**。`archive_guard` 只管归档结构，不管来源可信度。zkg 链路的
   sha256 完整性校验与 HMAC 签名仍是独立的前置条件，见 [zkg-publish.md](zkg-publish.md)。
3. **不做内容扫描**。不检测归档内的恶意代码、不扫描软链接指向的内容，
   只保证「成员名与类型可控、资源用量可控、不会写出目标目录」。
4. `tar` 在 Python < 3.11.4 无 `filter` 参数时回退逐成员白名单解包；
   此时 `validate_tar` 的类型白名单与 containment 预检是唯一防线，不可省略。

## 回归测试

`tests/test_archive_guard.py` 覆盖：正常归档放行、穿越/绝对路径/盘符、ADS、
Windows 保留名、控制字符、归一化碰撞、特殊成员类型、条目数与体积配额、
压缩比、prevalidated 语义、以及 `repo/pool/*.tar.gz` 与 `dist/index.json` 哈希一致性。

CI 中逐文件执行（脚本式测试，顶层 `sys.exit`），不使用 `unittest discover`。
