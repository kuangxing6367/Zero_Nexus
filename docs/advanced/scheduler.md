# 定时任务

定时任务由官方扩展 `scheduler` 提供（`extensions.yaml` 里 `scheduler.enabled`，默认开），
底层用 **APScheduler** 的 cron 触发器。

## 一、声明一个定时任务

```python
def register(ctx):
    ctx.task("0 8 * * *", daily_report, description="每日报表")

def daily_report():
    ctx.log("跑每日报表")
```

`ctx.task(cron_expr, executor, description=None)`：

| 参数 | 说明 |
| ---- | ---- |
| `cron_expr` | **5 字段** cron：`分 时 日 月 周` |
| `executor` | 可调用对象（同步或异步函数都行） |
| `description` | 说明文本，后台可见 |

## 二、cron 表达式速查

```
┌────────── 分钟 (0-59)
│ ┌──────── 小时 (0-23)
│ │ ┌────── 日 (1-31)
│ │ │ ┌──── 月 (1-12)
│ │ │ │ ┌── 星期 (0-6，0=周日；也可用 mon..sun)
│ │ │ │ │
* * * * *
```

| 表达式 | 含义 |
| ---- | ---- |
| `*/5 * * * *` | 每 5 分钟 |
| `0 * * * *` | 每小时整点 |
| `0 8 * * *` | 每天 08:00 |
| `0 9 * * mon-fri` | 工作日 09:00 |
| `30 3 1 * *` | 每月 1 日 03:30 |

## 三、动态增删任务

```python
def register(ctx):
    ctx.command("/remind", on_remind)

def on_remind(event, match):
    def notify():
        ctx.send_msg(group_id=event.group_id, user_id=None, message="提醒时间到！")

    key = ctx.add_job(notify, "0 9 * * *", description="每日提醒", job_id="remind")
    ctx.send_msg(group_id=event.group_id, user_id=None, message=f"已创建：{key}")

def on_cancel(event, match):
    ctx.remove_job("remind")
```

| 方法 | 说明 |
| ---- | ---- |
| `ctx.add_job(handler, cron_expression, description='', job_id=None) -> str` | 注册任务，返回任务键 |
| `ctx.remove_job(job_id)` | 按 id 移除 |

> `job_id` 缺省取函数名；**相同 id 重复注册会覆盖**。

## 四、任务状态与后台管理

调度器支持暂停 / 恢复：

```
TaskScheduler.pause_task(task_key)     # 暂停
TaskScheduler.resume_task(task_key)    # 恢复
TaskScheduler.remove_job(task_key)     # 移除
TaskScheduler.remove_plugin_tasks(plugin_name)   # 移除某插件的全部任务
```

后台「任务」页可以查看所有任务、手动触发、暂停/恢复。

任务状态（上次执行结果/时间）会写进数据库 `tasks` 表，供后台展示。

## 五、执行语义

- 任务在**主事件循环**里执行；异步函数会被 `await`，同步函数直接调用；
- 单次执行抛异常只记日志，不会摘掉任务；
- 插件被禁用/卸载时，其任务会一并移除（`remove_plugin_tasks`）。

## 六、扩展点：任务触发前后

| 扩展点 | 时机 | 参数 |
| ---- | ---- | ---- |
| `cron.task.trigger.before` | 任务触发、执行前 | `plugin_name, handler_name` |
| `cron.task.trigger.after` | 任务执行后 | `plugin_name, handler_name, status` |

```python
def register(ctx):
    ctx.hook("cron.task.trigger.after", on_task_done)

async def on_task_done(plugin_name=None, handler_name=None, status=None, **kw):
    if status != "success":
        ctx.log(f"任务 {plugin_name}.{handler_name} 异常：{status}", level="error")
```

手动触发（后台「任务」页）走同一组扩展点。

## 七、实践建议

- **别把长任务塞在 cron 里**：任务和消息共用一个事件循环，长阻塞会拖慢一切；
  重活请 `ctx.run_async(...)` 或丢到线程池 / 独立进程；
- **幂等**：任务可能因为重启等原因被重复触发，逻辑要能重复执行；
- **用任务做健康检查**：结合 `cron.task.trigger.after` 的 `status` 做失败告警；
- **时区**：按服务器本地时间调度；容器里注意设置 `TZ`。

---

延伸：[扩展点](../api/advanced/hooks.md) · [数据库](./database.md) · [ctx 定时任务](../api/basic/ctx.md#十定时任务与任务调度)
