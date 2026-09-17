# ============================================================
# Zeronus / Zero Nexus —— 官方镜像
# 构建：docker build -t zeronus:latest -f deploy/Dockerfile .
# 运行：docker run -d --name zeronus -p 8080:8080 -v zeronus-data:/app/data zeronus:latest
#   Web 后台 http://127.0.0.1:8080（admin / admin123，登录后立即改密）
# ============================================================
FROM python:3.12-slim

WORKDIR /app

# 依赖层单独缓存（内核仅硬依赖 PyYAML，其余为扩展运行时依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 源码
COPY core/ core/
COPY service/ service/
COPY software/ software/
COPY repo/ repo/
COPY main.py VERSION ./

# 运行期数据（数据库 / 日志 / 包缓存 / 插件数据）全部落在 /app/data
VOLUME ["/app/data"]

# 默认只对容器外暴露 Web 后台；其他端口（8090 状态面板 / 6830 OneBot）
# 按需 -p 附加。所有服务默认绑定 127.0.0.1，容器内访问请在 config.yaml
# 显式改绑 0.0.0.0（Docker 场景属合理暴露面）。
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080

CMD ["python", "main.py"]
