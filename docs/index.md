# Zeronus 开发文档

> 本目录为**扁平 Markdown** 文档：所有 `.md` 平铺在 `docs/` 根，无子目录、无构建步骤。直接用任意 Markdown 预览器或编辑器打开即可。

## 这是什么

Zeronus 是一个基于 Python 的**软件宿主框架**，采用三层架构（内核级 / 服务级 / 软件级），事件驱动，通过程序化扩展（非插件化）承载各类能力。

- 内核（`core/`）提供数据库、事件总线、Hook、权限、认证、协议抽象等基础能力；
- 服务（`service/`）提供 zkg 包管理、启停、看门狗、安全传输原语；
- 软件（`software/`）承载官方扩展与用户插件。

## 文档地图

| 主题 | 文件 |
| --- | --- |
| 安装 | [installation.md](installation.md) |
| 开始使用 | [getting-started.md](getting-started.md) |
| 配置 | [configuration.md](configuration.md) |
| 架构详解 | [architecture.md](architecture.md) |
| 数据库 | [database.md](database.md) |
| 包管理 zkg | [zkg.md](zkg.md) |
| 编写插件 | [writing-plugins.md](writing-plugins.md) |
| 定时任务 | [scheduler.md](scheduler.md) |
| 权限系统 | [permission.md](permission.md) |
| 会话 | [session.md](session.md) |
| 加载器 | [loader.md](loader.md) |
| API 参考 | [api.md](api.md) |
| 安全传输 | [transport.md](transport.md) |
| 归档解包安全 | [archive-guard.md](archive-guard.md) |
| 接入 IM（OneBot） | [connect-im.md](connect-im.md) |
| 部署上线 | [deployment.md](deployment.md) |
| 最佳实践 | [best-practices.md](best-practices.md) |

## 约定

- 文档中所有路径、配置键、API 名称均以**源码为准**（修改文档前请先核验源码）。
- 监听地址默认均为 `127.0.0.1`；公网 / 局域网访问需显式放开并结合 `ssl` 与反向代理。
- 首次启动种入 `admin / admin123` 并立即标记 `must_change_password=1`：改密前除改密 / 登出 / `/api/me` 外的管理接口一律 403。密码使用框架自带的 `pbkdf2_sha256`，不依赖可选的 bcrypt。详见 [database.md](database.md)。
- 所有处理外部归档的入口统一走 `core/security/archive_guard.py`，见 [archive-guard.md](archive-guard.md)。
