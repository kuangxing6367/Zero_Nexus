# Event 事件对象

> **面向**：插件开发者。handler 的第一个参数就是 `Event`。

`Event` 把接入端上报的原始字典包装成统一对象：既提供扁平字段（`user_id` / `group_id` / `message`…），
也在需要时按需查询权限、解析富媒体段。

> **协议无关**：内核只认这套固定字段。换接入端时，业务插件不必改。

## 一、基础字段

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `event.post_type` | `str` | `message` / `notice` / `request` / `meta_event` |
| `event.message_type` | `str` | `group` / `private` |
| `event.sub_type` | `str` | 子类型（如 `normal` / `anonymous`） |
| `event.self_id` | `int` | 机器人的用户 ID |
| `event.bot_name` | `str` | 消息来源的接入实例名（多实例时用于区分） |
| `event.message` | `str` | **纯文本**（已从消息段提取，供命令匹配用） |
| `event.raw_message` | `str` | 原始文本 |
| `event.message_id` | `int` | 消息 ID |
| `event.user_id` | `int` | 发送者 ID |
| `event.group_id` | `int` | 群 ID（私聊为 0） |
| `event.sender` | `dict` | 原始 sender 对象（`nickname` / `card` / `role` …） |
| `event.font` | `int` | 字体 |
| `event.segments` | `list` | 原始消息段 `[{type, data}, ...]` |

```python
def handler(event, match):
    print(event.user_id, event.group_id, event.message)
```

## 二、便捷属性

| 属性 | 说明 |
| ---- | ---- |
| `event.is_group` / `event.is_private` | 是否群聊 / 私聊 |
| `event.sender_role` | OneBot 上报的 `sender.role`：`owner` / `admin` / `member` |
| `event.role` | 综合身份判定（含超管） |
| `event.sender_nickname` / `event.sender_card` | 昵称 / 群名片 |
| `event.is_admin` / `event.is_superuser` | 是否管理员 / 超管 |
| `event.is_group_owner` / `event.is_group_admin` | 是否群主 / 群管理员 |
| `event.is_blacklisted` | 是否黑名单 |

## 三、富媒体与消息段

| 属性 | 说明 |
| ---- | ---- |
| `event.has_image` / `has_voice` / `has_video` / `has_file` | 是否含该类型段 |
| `event.has_face` / `has_share` / `has_reply` | 表情 / 分享卡片 / 回复 |
| `event.has_at` / `has_at_bot` / `at_all` | 是否 @ 某人 / @ 机器人 / @全体 |
| `event.at_list` | 被 @ 的用户 ID 列表 |
| `event.images` / `event.first_image` | 图片数据列表 / 第一张 |
| `event.share` | 分享卡片数据（title / url / desc） |
| `event.reply_id` | 被回复的消息 ID（非回复则 `None`） |

```python
def handler(event, match):
    if event.has_at_bot:
        ctx.send_msg(group_id=event.group_id, user_id=None, message="你在叫我吗？")
    if event.has_image:
        url = event.first_image.get("url")
```

需要更细的字段时直接遍历 `event.segments`。

## 四、权限查询

| 成员 | 说明 |
| ---- | ---- |
| `event.has_perm(node) -> bool` | 是否有权限节点（三态中的「允许」） |
| `event.check_perm(node)` | 三态：允许 / 拒绝 / 未设置 |
| `event.perms` | 权限快照（`PermissionSet`） |
| `event.perm_groups` | 命中的权限组列表 |
| `event.primary_group` | 主权限组名 |

权限上下文（所在群、身份等）自动从事件构造；底层查询带 60s TTL 内存缓存。
详见[权限系统](../../advanced/permission.md)。

## 五、传播控制

命令/事件可能是"一条消息触发多条规则"，用这两个开关控制是否继续：

| 成员 | 说明 |
| ---- | ---- |
| `event.stop_event()` / `event.is_stopped` | 已停止传播（不再走后续规则） |
| `event.continue_route()` / `event.is_continue_route` | 显式允许后续系统关键词回复继续尝试 |

```python
def on_hello(event, match):
    event.stop_event()      # 这条消息到此为止
    ctx.send_msg(group_id=event.group_id, user_id=None, message="Hello")
```

## 六、事件从哪来

- **命令 handler**：命令命中后调用，签名 `(event, match)`；
- **`ctx.on(name, handler)`**：业务事件订阅（如 `notice.group_recall`）；
- **`ctx.on_raw_message(handler)`**：命令匹配前的原始消息（只有一个参数 `event`）;
- **扩展点**：`event.before_dispatch` / `event.after_dispatch` 收到的是**字典**，不是 `Event` 对象。

## 七、缓存刷新（高级）

角色/权限结果有内存缓存。如果你直接改了数据库里的角色/权限数据，可调用：

```python
from core.messaging.event import invalidate_user_role_cache, invalidate_group_role_cache

invalidate_user_role_cache(user_id)          # 或不传参清空全部
invalidate_group_role_cache(group_id, user_id)
```

框架在后台改权限后会自动失效对应缓存，通常不需要手动调用。

---

延伸：[ctx 参考](./ctx.md) · [权限系统](../../advanced/permission.md) · [扩展点](../advanced/hooks.md)
