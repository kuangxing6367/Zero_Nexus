# 包管理器 zkg

Zeronus 的官方"机制包"不是无条件全部加载的，而是由 **zkg（依赖驱动加载器）** 按需装配。

## 一、为什么要有它

内核提供了若干**机制包**（能力底座）。如果每次启动都全量加载，会带来不必要的开销与依赖。
zkg 的思路是：**谁声明了依赖，才加载谁**。

```
plugins/*（用户插件）+ extensions/*（官方扩展）
        │  各自的 manifest 声明 dependencies
        ▼
  scan  →  resolve（依赖图）  →  rebuild（plugins.db）  →  只加载「有依赖方」的机制包
```

## 二、三张表看懂它

| 概念 | 说明 |
| ---- | ---- |
| **manifest** | 一个包/扩展的清单：id、版本、`dependencies` 等 |
| **`plugins.db`** | 独立 SQLite（`data/plugins.db`），**每次启动重建** |
| **`pkg_<T>` 表** | 每个机制包 T 一张表，按行记它的**依赖方**（列 `plugin`） |

规则很简单：**某张 `pkg_<T>` 表零行 = 没人依赖 T = 不加载 T**。

另外还有一张 `plugins` 表记录用户插件。

## 三、多源与索引

一个"源"就是一个能提供包索引与包文件的仓库。

| 源类型 | 说明 |
| ---- | ---- |
| `local` | 本地目录（默认 bundled 内置源，随仓库分发） |
| `http` | 远程镜像源，如 `https://zkg.zgric.top/zeronus` |

每个源用 `kind` 标记：

- `official`（官方，默认）
- `community`（社区）

**同名包官方优先胜出**：解析时先遍历官方源，再遍历社区源。

**一个源可以拆成多份索引**，框架自动合并：

- 本地：`dist/index.json`、`dist/indices/*.json`、`dist/index-*.json`
- 远程：主 `index.json` 里的 `splits` 字段列出分片

## 四、包下载地址

`url` 支持两种形式：

- **绝对地址**：`http(s)://…` 或 `file://…`（社区包可托管在任意位置）；
- **相对路径**：相对源根解析（如 `pool/foo-1.0.0.tar.gz`）。

## 五、默认源配置

`core/zkg/defaults.py` 提供默认源：

```python
# 示意
[
  {"type": "local", "path": "<repo>/dist", "kind": "official"},
  # 远程源默认不主动连接，需要时可加入：
  # {"type": "http", "url": "https://zkg.zgric.top/zeronus", "kind": "official"},
]
```

> 远程源**默认不主动连**——离线/受限环境也能正常启动。

## 六、构建你自己的源

仓库里的 `repo/` 就是官方源的构建工程，每个机制包一个目录：

```
repo/
├── build.py                 # 生成 dist/index.json 与 pool/
├── exec/                    # 一个机制包：manifest.toml + main.py
│   ├── manifest.toml
│   └── main.py
├── store/  system/  udp/  ws/
├── pool/                    # 打包产物：<id>-<版本>.tar.gz
└── dist/                    # 生成的索引：index.json（+ indices/index-*.json）
```

```bash
python repo/build.py             # 生成 dist/index.json + pool/*.tar.gz
python repo/build.py --split 4   # 拆成 4 份分片（dist/indices/index-0..3.json）
```

产出的 `dist/` 即可作为 `local` 源，或上传到你的 HTTP 镜像作为 `http` 源。

## 七、加载流程（源码视角）

```python
# core/zkg/loader.py —— Loader.run()
plugin_manifests = scanner.scan_dir(plugins_dir)      # 扫清单
res              = resolver.resolve(plugin_manifests) # 解析依赖图
stats            = depdb.rebuild(plugin_manifests, res)  # 重建 plugins.db
for tid in res.needed:                                # 只加载「被依赖」的
    if self.depdb.is_loaded(tid):
        self._load_tool(tid, res.needed[tid])
```

`_load_tool` 会先确保包文件存在（本地没有就从源拉），再导入对应模块。

## 八、写一个机制包清单

机制包的清单是 `repo/<id>/manifest.toml`，用 `[package]` 段声明（对照 `repo/exec/manifest.toml`）：

```toml
# repo/example/manifest.toml
[package]
id = "example"
name = "Example Tool"
type = "tool"              # tool（机制包）/ plugin
version = "1.0.0"
description = "示例机制包"
dependencies = []          # 依赖的其他机制包 id
provides = ["example"]     # 对外提供的能力名
entry = "main.py"          # 入口模块，与 manifest 同级
```

用户插件在 `plugin.yaml` 里声明对它的依赖：

```yaml
dependencies: ["example"]
```

## 九、与插件加载器的区别

| | `core/zkg/` | `core/plugin_loader/` |
| --- | --- | --- |
| 管什么 | **机制包**（能力底座）的按需装配 | **插件**（官方扩展 + 用户插件）的扫描/加载/热重载 |
| 触发 | 启动时按依赖解析 | 启动与热重载 |
| 产物 | `data/plugins.db` | `sys.modules` 里的合成包 + 命令/任务注册 |

两者职责不同，别混淆。

---

延伸：[架构总览](./architecture.md) · [插件加载与模块机制](./loader.md)
