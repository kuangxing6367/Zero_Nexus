# 安装

## 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | **3.8+**（推荐 3.10 ~ 3.13） |
| 操作系统 | Windows / Linux / macOS 均可 |
| 数据库 | 默认 SQLite（零配置）；可选 MySQL（自动装驱动） |
| Node.js | **仅**在你要自己构建后台前端（`webui/`）时需要；直接跑框架不需要 |

## 一、拿到代码

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
```

## 二、安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 里的都是框架与官方扩展的常用依赖：

| 依赖 | 用在哪 |
| --- | --- |
| `websockets` | OneBot 反向 WS 接入端 |
| `apscheduler` | 定时任务 |
| `flask` / `flask-cors` | Web 后台与 REST API |
| `waitress` | 生产级 WSGI 服务器（Web 默认用它） |
| `pyyaml` | 配置读写 |
| `bcrypt` | 管理员口令哈希 |
| `requests` / `psutil` | 系统信息、出站请求 |

MySQL 驱动（`pymysql` / `DBUtils`）**不需要手动装**：检测到 MySQL 配置时框架会自动安装。

> 首次启动还有一层**依赖自检**：扫描 `requirements.txt`，发现缺失会走清华源自动补齐，
> 所以在干净机器上直接 `python main.py` 通常也能跑起来。

## 三、一键脚本（可选）

`start.sh` 会自动建 venv、装依赖、启动：

```bash
bash start.sh
```

## 四、目录一览

```
.
├── main.py              # 启动入口：python main.py [自定义配置路径]
├── config.yaml          # 主配置（首次启动生成）
├── extensions.yaml      # 官方扩展开关与参数（软件级扩展清单）
├── core/                # 内核级（机制；不含任何 OneBot 实现）
├── service/             # 服务级（zkg 包管理、启停编排、看门狗）
├── software/            # 软件级（extensions/ 官方扩展 + plugins/ 用户插件）
├── web/  webui/         # 后台前端产物 / 源码
├── repo/                # zkg 本地包仓库（离线兜底包源）
├── sql/                 # 建表 SQL
├── data/                # 运行时数据（数据库、日志、插件私有数据）
└── docs/                # 本文档
```

## 五、首次启动

```bash
python main.py
```

看到启动横幅（版本 / 数据目录 / 数据库 / 已加载扩展 / 监听端口）即成功。
默认端口：WebUI `127.0.0.1:8080`、OneBot 反向 WS `0.0.0.0:6830`。

`config.yaml` 与 `data/` 会在首次启动时自动生成。

## 六、升级

```bash
git pull
pip install -r requirements.txt
python main.py
```

- 数据库表结构会在启动时自动补齐；
- 用户插件放在 `software/plugins/`，升级框架不会覆盖；插件私有数据在 `data/plugins_dat/`，同样保留；
- `config.yaml` 与 `extensions.yaml` 属于你的本地配置，升级不会覆盖。

---

下一步：[开始使用](./getting-started.md)。
