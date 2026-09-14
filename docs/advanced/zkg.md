# 包管理器 zkg

> **本篇面向**：想理解「官方能力如何按依赖加载」的 B/C，以及要发布官方工具包 / 社区包的维护者。

## 是什么

`zkg` 是 Zeronus 内置的 **apt 式包管理器**，位于 `core/zkg/`，**纯标准库实现，零第三方依赖**。
它负责：扫描插件声明 → 解析依赖 → 决定加载哪些「官方工具包」→ 从本地仓库或远程镜像源拉取并导入。

命名对齐官方镜像源域名 `zkg.zgric.top`（发布目录 `https://zkg.zgric.top/zeronus`）。

## 设计哲学（类 Linux 内核）

- **内核本身「啥都不做」**，只提供最小运行时（事件总线、上下文、存储、IPC 等原语）。
- **官方能力 = 官方工具包（tool package）**，默认随发行包 `bundled`（本地 `./repo`），
  也可从远程「镜像源」（类 apt `sources.list`）拉取。
- **插件在 `manifest.toml` 里声明 `dependencies`**；框架启动扫描全部插件后，
  按声明解析需要加载哪些官方工具——**未被任何插件依赖的工具直接跳过**，不占内存、不导入。
- 依赖图记录在**独立的 `data/plugins.db`**（与运行时 / 配置 DB 物理分离），**每次启动重建**，是派生缓存而非真相源。

## 启动流程

```
Loader.run()
  ├─ scan(plugins_dir)         # scanner.scan_dir：递归找 manifest.toml
  ├─ resolve(plugin_manifests) # resolver：汇总各源工具 + 计算 dependents / missing
  ├─ rebuild(plugin_manifests, res)  # depdb：重建 data/plugins.db
  └─ for tid in needed:
        if depdb.is_loaded(tid):   # 有依赖方（pkg_<T> 表非空）才加载
            _load_tool(tid, v)
```

`_load_tool` 的取包顺序：
1. **优先本地 `./repo/<tool_id>` 目录**（开发态，免下载，直接 import `entry`）；
2. 否则按 `source_id` 找到对应源，`fetch_package(url, cache_dir)` 下载 / 复制
   （本地 pool 复制或远程 urllib 下载），解包后 import `entry`；
3. 加载结果缓存于 `Loader.loaded_tools`（模块对象映射）。

## 组件一览

| 模块 | 类 / 函数 | 职责 |
|------|-----------|------|
| `manifest.py` | `Manifest` | 解析 `manifest.toml`；`to_index_entry()` 生成仓库索引条目 |
| `sources.py` | `Source` / `SourceRegistry` | 源抽象（local / http）、索引分片自动合并、包下载 |
| `resolver.py` | `Resolver` / `Resolution` | 多源工具汇总、dependents 计算、missing 检测 |
| `scanner.py` | `scan_dir()` | 递归扫描目录收集 manifest |
| `depdb.py` | `DepDB` | 依赖状态库（类 dpkg），建 `plugins` 表与 `pkg_<T>` 表 |
| `credentials.py` | `CredentialStore` | 源的凭据存储（默认 `auth: none`，远程公开只读） |
| `loader.py` | `Loader` | 编排 scan → resolve → rebuild → load |

## 包清单 manifest.toml

每个官方工具包 / 用户插件根目录放一个 `manifest.toml`：

```toml
[package]
id = "exec"            # 全局唯一 id
name = "Exec Tool"     # 展示名
type = "tool"          # tool（官方机制）| plugin（用户应用）
version = "1.0.0"
description = "通用命令执行接口"
dependencies = []      # 依赖的其他包 id（官方工具或插件）
provides = ["exec"]    # 本包提供的机制 id（默认 = id）
entry = "main.py"      # 可选：加载入口
```

读取用标准库 `tomllib`（Python 3.11+ 内置）。

## 源（source）配置

源分两类，由 `kind` 标记，且**官方源与社区源必须分开配置为不同的源**：

- `official`（官方，默认）：由官方维护仓库。
- `community`（社区）：由社区维护仓库，使用与官方**完全相同的 JSON 索引 schema**，
  每个条目既描述「程序」（包元数据），也给出「下载地址」`url` 与完整性校验 `sha256`。

**默认源**（`core/zkg/defaults.py`，出厂自带，可在 `config.yaml` 的 `pkg.sources` 覆盖 / 追加）：

```python
DEFAULT_SOURCES = [
    {"id": "local", "type": "local", "path": "<项目根>/repo",
     "enabled": True, "auth": "none", "kind": "official"},
    {"id": "zkg", "type": "http", "url": "https://zkg.zgric.top/zeronus",
     "enabled": True, "auth": "none", "kind": "official"},
]
```

- `local`：本地仓库目录，结构 `<path>/dist/index.json` + `<path>/pool/*.tar.gz`，作为离线兜底。
- `http`：远程镜像源，同样提供 `/dist/index.json` 与 `/pool/*.tar.gz`，用标准库 `urllib` 拉取，无第三方依赖。

用户可在 `pkg.sources` 追加私有源 / 换镜像；`SourceRegistry.from_config()` 从配置构建。

### 同名包：官方优先

`Resolver._collect_tools()` 按顺序 **官方源在前、社区源在后** 遍历；
同名包「先到的源不覆盖后到的」——**官方源优先胜出，社区包不得覆盖官方包**。

### 索引分片（自动合并）

一个源可提供多份 index JSON（包过多时拆分），框架启动时自动合并：

- **本地**：除主文件 `dist/index.json` 外，自动扫描 `dist/indices/*.json` 与 `dist/index-*.json` 作为分片；
  主文件也可用 `"splits": [...]` 显式列出分片。
- **远程**：主文件 `dist/index.json` 通过 `"splits": [...]` 列出分片（HTTP 无法列目录），框架逐个拉取合并。

每份分片是 `{"packages": [...]}` 结构，框架把它们的 `packages` 合并成一份完整索引。

索引条目（官方 / 社区同 schema）示例：

```json
{
  "packages": [
    {"id": "exec", "name": "Exec Tool", "type": "tool", "version": "1.0.0",
     "description": "通用命令执行接口", "dependencies": [],
     "provides": ["exec"], "entry": "main.py",
     "url": "pool/exec-1.0.0.tar.gz", "sha256": "<hex>"}
  ]
}
```

`url` 支持：
- **绝对 http(s) 地址**（社区包可托管在任意位置）；
- **相对路径**（如 `pool/xxx.tar.gz`），相对源根，本地 / 远程均支持（`Source.fetch_package` 二选一）。

## 依赖状态库 plugins.db

`DepDB` 在 `data/plugins.db` 重建：

- `plugins` 表：扫描到的用户插件（`id` / `path` / `enabled`）。
- 每个官方工具建一张 `pkg_<T>` 表（工具 id 只允许 `[A-Za-z0-9_]`，非法字符拒绝），
  **按行记录「谁依赖它」**（列 `plugin`）。**零行 = 无依赖 → 框架不加载该工具**。

运行时删插件 → 重刷依赖库 → 某工具依赖方变空 → 不再加载（剪枝）。
`DepDB.is_loaded(tool_id)` 即「有依赖方吗」→ 是否加载。

## 发布官方 / 社区包

仓库目录 `repo/` 放 5 个机制包（exec / ws / udp / store / system）。
用构建脚本生成索引与包体：

```bash
python repo/build.py --split 4   # 生成 dist/index.json（含 splits 分片）+ pool/*.tar.gz
```

- 官方包上传到镜像源 `zkg.zgric.top/zeronus` 的 `dist/` 与 `pool/`；
- 社区包由社区方自行维护独立仓库，配置为 `kind: community` 的源，框架按源解析、合并。

## 运行时 API（插件侧）

插件**不直接调用 zkg**——它只需在 `manifest.toml` 声明 `dependencies`，
框架启动期自动解析并把对应官方工具加载进内核。插件通过内核原语（`ctx` / 服务注册表 / 机制接口）
取用这些能力，加载与否由依赖图决定，无需插件关心。

> 内部入口：`from core.zkg.loader import Loader`；旧 `core/loader.py` 仅作兼容薄壳 `from core.zkg.loader import *`。
