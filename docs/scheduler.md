# 定时任务（scheduler）

定时任务由 `scheduler` 扩展提供，基于 cron 表达式。

## 注册定时任务

在插件的 `register(ctx)` 中调用：

```python
def register(ctx):
    async def _job():
        # 业务逻辑
        ...
    ctx.task("0 0 * * *", _job, description="每日零点任务")
```

- 第一个参数为 **cron 表达式**（分 时 日 月 周）；
- 第二个参数为可执行对象（同步函数或 `async` 协程均可）；
- `description` 仅用于展示与审计。

## 存储与恢复

- 注册的任务持久化到 `tasks` 表（`plugin_name` / `cron_expression` / `handler` / `last_run_at` / `next_run_at` / `run_count` / `last_status`）。
- 重启后自动恢复，无需重新注册。
- 调度心跳由框架统一驱动，单 tick 内按字段匹配执行（匹配逻辑为模块级函数，非实例方法）。

## 注意事项

- cron 字段含义：分钟(0-59) 小时(0-23) 日(1-31) 月(1-12) 星期(0-6)。
- 时区：框架时间处理以本地时区为准（方言层已统一 `strftime` 本地时间）。
- 任务异常会被捕获并记录到 `last_status='error'`，不会中断调度循环。
