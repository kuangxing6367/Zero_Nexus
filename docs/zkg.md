# 包管理 zkg（服务级）

`service/zkg/` 是 Zeronus 的**包管理器**，提供统一内容来源，减少造轮子。

## 定位

- 位于**服务级**（`service/zkg/`，非 `core/zkg/`）。
- **纯标准库实现，零第三方依赖**。
- 官方包仓库 + 本地仓库（`repo/`）双源。

## 工作机制（manifest 驱动）

1. **scan**：扫描本地 `repo/`（主根）与 `software/plugins/`（插件根，经
   `Loader(scan_roots=...)` 接入），加上已配置的官方源，汇总可用包与插件清单。
2. **resolve**：按每个包的 `manifest.toml` 中声明的 `dependencies` 解析依赖图；
   用户插件在 `software/plugins/<pkg>/manifest.toml` 的 `dependencies` 里
   声明所需官方工具，zkg 据此决定加载哪些。同 id 以主扫描根优先。
3. **写 `data/plugins.db`**：记录解析结果。
4. **只加载解析集合**：仅加载被依赖方引用的机制包，未被引用的包不加载。

## 包完整性校验

- 索引（`dist/index.json`）中每个包自带 `sha256`；加载器从源拉取包体后
  **先校验哈希再解压导入**，不一致即拒绝加载并记录错误日志。
- 开发态例外：本地 `repo/<pkg>/` 目录存在时直接加载源码（dev-mode），
  不经下载路径，故不做哈希校验。
- 签名校验（密钥级）在 roadmap 中，当前为 SHA-256 完整性校验。

## 镜像源

- **本地 bundled**：内置仓库存于 `repo/`，离线可用。
- **远程**：默认 `https://zkg.zgric.top/zeronus`（需 SSH 私钥），默认**不主动连接**。
- `Source.kind`：`official`（官方）/ `community`（社区）；**官方优先**。

## 启用 / 配置

`config.yaml`：

```yaml
zkg:
  local_dir: repo              # 本地包仓库目录
  official_source: ""          # 官方源地址（默认不主动连接；留空则仅本地）
```

## 插件脚手架

```bash
python -m service.zkg new myplugin --deps store --desc 我的插件
```

在 `software/plugins/myplugin/` 生成骨架：`manifest.toml`（zkg 依赖声明）、
`main.py`（register(ctx) 入口与命令示例）、`requirements.txt`。
名称规则：小写字母开头，仅含小写字母/数字/下划线；已存在目录会拒绝生成。

现成样例：`software/plugins/demo_kv/` —— 声明 `store` 机制包依赖并消费
KV 的最小完整插件，可作为第一个练手参照。

## 插件 API 版本（稳定 ABI）

- 框架在 `core/ctx.PLUGIN_API_VERSION` 维护插件 API 主版本；
  破坏性变更才 +1，向后兼容的新增不改主版本。插件内可用 `ctx.api_version` 读取。
- 插件在 `manifest.toml` 声明 `api_version`（`"1"` = 主版本精确匹配，
  `">=1,<2"` = 区间）；zkg 加载器启动时校验，不兼容的插件记录进
  `run()` 结果的 `api_incompatible` 列表并告警。未声明不设限（兼容旧插件）。

## 与插件的关系

- zkg 负责**机制包**（被框架 / 扩展依赖复用的通用能力）；
- 用户业务插件放在 `software/plugins/`，由插件加载器按 `register(ctx)` 入口加载（见 [writing-plugins.md](writing-plugins.md)、[loader.md](loader.md)）；
- 插件在 `manifest.toml` 的 `dependencies` 声明所需机制包，运行时经
  `ctx.zkg_tool("<id>")` 取用（未声明/未加载返回 `None`）。
