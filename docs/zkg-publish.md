# zkg 发布链路：开发 → 打包 → 签名 → 发布 → 安装 → 升级

本文覆盖 zkg 官方源（zkg.zgric.top）的完整发布流程。
**注意：动服务器（上传）这一步需要人工确认后再执行，脚本只生成待上传产物。**

## 1. 开发

```bash
python -m service.zkg new mytool            # 脚手架：manifest.toml + main.py
# 编辑 repo/mytool/main.py，实现纯 stdlib 机制，稳定 API 写进 docstring
```

约束（zkg 机制包）：
- 纯标准库，不 import 框架内核（自包含，装到任何 Zeronus 实例都能跑）；
- manifest.toml：`id` 全局唯一，`api_version = ">=1"` 声明兼容区间；
- 对外表面只加不减：升级不能改已有函数签名（稳定 ABI）。

## 2. 打包 + 签名

```bash
cd repo
# 本地打包（生成 pool/*.tar.gz + dist/index.json）
python build.py

# 带签名打包（推荐，发布官方源时必须）
export ZKG_SIGNING_KEY=<你的密钥>
python build.py
```

带密钥时 build.py 对每个包体计算 HMAC-SHA256，写入索引条目的 `signature` 字段。
密钥的传递与保管：
- 打包端：环境变量 `ZKG_SIGNING_KEY`（或 `--key-file <path>`）；
- 安装端：同样设置 `ZKG_SIGNING_KEY`，loader 校验通过才加载；
- 签名是**整包 HMAC**：改一个字节都会校验失败；sha256 防「传输损坏」，
  HMAC 防「索引+包体同被替换」的供应链替换（前提：安装端密钥可信下发）。

## 3. 发布（动服务器，需人工确认）

产物 = `repo/dist/index.json` + `repo/pool/*.tar.gz`，上传到官方源：

```bash
# 服务器 139.196.237.40，Nginx 静态目录即源根（URL: https://zkg.zgric.top/zeronus）
scp -i /path/to/id_ed25519 -r repo/dist repo/pool root@139.196.237.40:<源根目录>/
```

约定：
- **只增不删**：已发布版本永不覆盖删除（保证旧安装可复现）；
- 覆盖 `index.json` 原子性：先传 pool 再传 index（避免索引先到、包 404）；
- 密钥只在打包/安装端，**不上服务器**。

> ⚠️ 此步涉及生产服务器，执行前需用户确认（私钥位于 E:\工程\passwd\）。

## 4. 安装

实例侧 `config.yaml` 指向官方源后，任何插件声明依赖即自动安装：

```yaml
zkg:
  official_source: "https://zkg.zgric.top/zeronus"
```

```toml
# software/plugins/myapp/manifest.toml
[package]
id = "myapp"
dependencies = ["cron"]        # 启动时 zkg 自动从官方源拉取并加载 cron
```

加载端校验链：sha256（索引声明 vs 落盘包体）→ HMAC 签名（若索引带 signature，
本机必须设 ZKG_SIGNING_KEY，不设则拒绝加载）→ api_version 兼容区间。

手动方式：`python -m service.zkg`（CLI 查看源/包信息）。

## 5. 升级

1. 改 `manifest.toml` 的 `version`（semver：只加不改=minor/patch）；
2. 重新 `python build.py`（或带签名打包）；
3. 上传新 pool 包与更新后的 index.json；
4. 各实例重启后 resolver 按 version 解析到新版本并重拉。

## 演示包

`repo/cron`（cron 表达式机制包）+ `software/plugins/demo_cron`（声明依赖并
消费）即端到端演示：`build.py` 后由 loader 从源安装加载，插件内
`ctx.zkg_tool('cron')` 直接可用。
