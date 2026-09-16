# 安装

## 环境要求

- Python **3.10+**（推荐 3.13）
- 操作系统：Linux / Windows / macOS
- Web 后台前端为已构建产物，无需 Node 环境即可运行；仅在**重新构建前端**时才需要 Node。

## 获取源码

```bash
git clone https://github.com/kuangxing6367/Zero_Nexus.git
cd Zero_Nexus
```

## 安装依赖

方式一：用自带脚本（自动建 venv、装依赖、启动）

```bash
bash start.sh
```

方式二：手动安装

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

> 说明：`main.py` 启动时会尝试自愈缺失的**可选**依赖（如 `pymysql`、`psycopg2`、`ariadne` 等），但生产环境建议在启动前显式装好 `requirements.txt`，避免启动期联网。

## 首次启动

```bash
python main.py
```

首次启动会自动：

1. 生成 `config.yaml`（若缺失）；
2. 建表（`data/zernus.db`，SQLite 默认）并写入默认管理员账号；
3. 拉起内核、sys 服务、user 服务与已启用的软件扩展；
4. 自愈缺失的可选依赖（受 `plugin.auto_install_deps_on_startup` 控制）。

启动后访问 Web 后台：<http://127.0.0.1:8080>，使用 `admin / admin123` 登录。

## 切换数据库

编辑 `config.yaml → database.type`：

- `sqlite`（默认）：零配置，文件在 `data/zernus.db`；
- `mysql`：需手动建库，并安装 `pymysql`；
- `postgresql`：需手动建库，并安装 `psycopg2`。

详见 [database.md](database.md)。
