# 开始使用

## 一、启动

```bash
python main.py
```

启动后会打印结构化横幅，确认这几项即可：

```
 Zeronus v0.0.1-beta.0-alpha.0
 进程模式: 单进程 (standard)
 数据目录 : <项目>/data
 数据库   : SQLite → data/zernus.db
 官方插件 : 5 个 → image_renderer, onebot_adapter, scheduler, session, webui
 用户插件 : 0 个 → (无)
 监听端口 :
   - OneBot WS : 0.0.0.0:6830
   - WebUI      : http://127.0.0.1:8080
```

> 首次启动会自动生成 `config.yaml`、建表（`data/zernus.db`），并在缺依赖时自动补齐。

## 二、进入 Web 管理后台

浏览器打开 **<http://127.0.0.1:8080>**。

默认管理员账号（首次启动自动创建）：

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | super |

> **生产环境请立刻在「设置 → 管理员」里改密码。**

后台里可以做：看仪表盘、装/卸扩展、管命令与别名、管权限组与用户、看日志、改配置、管接口令牌（API Key）、重启框架。

## 三、接入 IM 平台（可选）

Zeronus 不直接连平台，需要 OneBot 实现端以**反向 WebSocket** 连入。以 NapCat / Lagrange 为例：

1. 在 `extensions.yaml` 确认 `onebot_adapter` 为 `enabled: true`（默认开），监听 `0.0.0.0:6830`；
2. 在 NapCat / Lagrange 里添加反向 WS 地址：`ws://<服务器IP>:6830/onebot/v11/ws`；
3. 连接成功后，向机器人发消息即可。

**不想接 IM 也能用**：在 `extensions.yaml` 里开启 `http_inject`，就能用 HTTP 把事件推进来：

```bash
curl -X POST http://127.0.0.1:8901/hook \
     -H 'Content-Type: application/json' \
     -d '{"type":"message","user_id":10001,"message":"/hello"}'
```

事件会被归一化成统一的事件对象，进入同一个内核——命令、会话、权限都照常工作。

详见[对接 IM 平台](./connect-im.md)。

## 四、装一个插件

用户插件放在 `plugins/<名字>/`，入口 `main.py`：

```python
# plugins/greeter/main.py
def register(ctx):
    ctx.command("/hello", on_hello, description="打个招呼")

def on_hello(event, match):
    ctx.send_msg(group_id=event.group_id, user_id=None, message="Hello, World!")
```

放好后在后台「插件」页启用，或重启框架。

## 五、常用端到端检查

- 后台能打开、能登录 → Web 与鉴权正常；
- 「插件」页能看到官方扩展与你的用户插件 → 加载器正常；
- 发 `/hello` 有回执 → 接入端到命令链路正常；
- 「日志」页实时刷新 → 日志代理正常。

## 六、停止

前台运行时按 `Ctrl+C`；框架会依次停掉 Web、接入端、调度器并落日志。

---

下一步：[配置系统](./configuration.md) ／ [编写插件](./writing-plugins.md)。
