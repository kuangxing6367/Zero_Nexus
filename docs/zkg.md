# 包管理 zkg（服务级）

`service/zkg/` 是 Zeronus 的**包管理器**，提供统一内容来源，减少造轮子。

## 定位

- 位于**服务级**（`service/zkg/`，非 `core/zkg/`）。
- **纯标准库实现，零第三方依赖**。
- 官方包仓库 + 本地仓库（`repo/`）双源。

## 工作机制（manifest 驱动）

1. **scan**：扫描本地 `repo/` 与已配置的官方源，发现所有可用包。
2. **resolve**：按每个包的 `manifest.toml` 中声明的 `dependencies` 解析依赖图。
3. **写 `data/plugins.db`**：记录解析结果。
4. **只加载解析集合**：仅加载被依赖方引用的机制包，未被引用的包不加载。

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

## 与插件的关系

- zkg 负责**机制包**（被框架 / 扩展依赖复用的通用能力）；
- 用户业务插件放在 `software/plugins/`，由插件加载器按 `register(ctx)` 入口加载（见 [writing-plugins.md](writing-plugins.md)、[loader.md](loader.md)）。
