# 插件加载与模块机制

> **多文件插件必读**。单文件插件可以跳过本篇。

## 一、插件形态

```
plugins/
├── greeter/
│   └── main.py                     # 单文件插件
└── mytool/
    ├── main.py                     # 入口，必须有 register(ctx)
    ├── util.py                     # 顶层子模块
    └── handlers/
        ├── __init__.py
        └── admin.py                # 嵌套包
```

框架**不把插件当常规 Python 包装进 `sys.path`**，而是按文件路径用 `importlib` 现场加载。
为了让相对导入、短名导入、同名模块隔离都能用，实现了「**合成包 + 三层模块名**」机制。

## 二、合成包 `plugin_<插件名>`

加载时，框架为每个插件创建一个合成包模块 `plugin_<插件名>`：

- `module.__path__ = [插件目录]` —— 让导入系统把它当**包**，子模块按插件目录查找；
- `module.__package__ = plugin_<插件名>` —— `main.py` 里的 `from .util import X` 以它为父包；
- `sys.modules['plugin_<插件名>']` 指向插件主模块 —— 这是既有约定，跨插件可用
  `sys.modules.get('plugin_xxx')` 拿到别人的主模块。

> **顺序很重要**：必须先建合成包，再预加载子模块；否则子模块执行相对导入时找不到父包。

## 三、三层模块名

每个顶层子模块都会在 `sys.modules` 里登记**三个名字**（指向同一个模块对象）：

| 名字 | 用途 |
| ---- | ---- |
| `plugin_<插件名>.<模块名>` | 规范点分层级名，插件内**相对导入**解析到它 |
| `plugin_<插件名>_<模块名>` | 旧版下划线唯一名（向后兼容，并保证跨插件同名不冲突） |
| `<模块名>` | **短名**，兼容 `main.py` 里的绝对导入 `import util` / `from util import X` |

这样同一个文件可以同时被这几种写法导入，且不会因为别的插件有同名模块而串味。

## 四、现场编译（不做字节码缓存）

主模块与子模块每次都从 `.py` **源码现场编译**（`_PluginSourceLoader`），原因：

- 热重载时能拿到最新代码，不受旧 `__pycache__` 影响；
- 避免不同插件同名模块命中同一份缓存。

## 五、同名模块隔离

多个插件都有 `util.py` / `db.py` 时，先加载的插件会把短名 `util` 写进 `sys.modules`。
框架的做法是**在加载每个插件前预加载它自己的顶层子模块**，把短名指向"本插件版本"。
因为 `import util` 的绑定发生在导入时，后加载插件覆盖短名不会影响已绑定引用的插件。

> 所以：**插件自己的模块，导入后请在本模块内使用**；跨插件不要依赖短名。

## 六、推荐的导入写法

```python
# main.py —— 推荐相对导入（最稳，天然隔离）
from .util import helper
from .handlers.admin import handle

def register(ctx):
    ctx.command("/x", lambda e, m: helper(ctx, e))
```

```python
# 也可以用短名绝对导入（框架帮你隔离）
import util
from util import helper
```

## 七、插件清单 `plugin.yaml`

放在 `data/plugins_dat/<插件名>/plugin.yaml`（代码目录下的同名文件作为首次加载的 fallback）。
用于记录元信息与配置：

```yaml
name: mytool
version: 1.0.0
description: 示例插件
dependencies: []          # 依赖的其他插件/机制包
# 其余键即插件配置，代码里用 ctx.get_config(key) 读取
greet_text: "Hello"
```

- 后台「插件」页可在线改这些配置；
- 代码目录里的 `plugin.yaml` 会在插件更新时被覆盖，**用户配置请以 `plugins_dat` 为准**。

## 八、热重载流程

后台点「重载」或执行重载命令时：

1. **卸载**：注销命令 / 事件 / 扩展点 / 定时任务 / 原始消息 handler（`clear_plugin` 按 `插件名:` 前缀清理），
   触发 `plugin.unload`；
2. **清理**：把该插件在 `sys.modules` 里的所有模块（合成包、点分层级名、下划线名、短名）按 `__file__` 一并移除，
   避免旧模块残留；
3. **重新加载**：从磁盘重新编译 `main.py` 与子模块，再次执行 `register(ctx)`，触发 `plugin.load`。

## 九、常见坑

| 现象 | 原因 / 解法 |
| ---- | ---- |
| `ImportError: attempted relative import with no known parent package` | 插件不是包。用框架加载（`register(ctx)` 入口），别直接 `python plugins/x/main.py` |
| 两个插件互相串了模块 | 你用了裸短名跨插件引用；改成相对导入 |
| 改了子模块代码但没生效 | 热重载即可；框架按源码现场编译，不需要清 `__pycache__` |
| 插件里有 `core/`、`db.py` 等同名目录/文件 | 会被预加载隔离，但建议避开与标准库/常用名冲突 |
| `sys.modules.get('plugin_xxx')` 拿不到 | 该插件未加载或被禁用；先用 `ctx._framework.plugin_loader` 查状态 |

## 十、调试

```python
# 查看某插件主模块
import sys
mod = sys.modules.get(f"plugin_{ctx.plugin_name}")
ctx.log(f"module file: {mod.__file__}")
```

插件加载相关信息会打进日志（加载成功 / 失败原因 / 子模块回退），后台「日志」页可见。

---

延伸：[编写插件](../guide/writing-plugins.md) · [包管理器 zkg](./zkg.md) · [架构总览](./architecture.md)
