# 智慧大棚数据管理平台 — 生产镜像
# 基础依赖 + 可靠的 ML 轮子（scikit-learn / statsmodels），预测覆盖 朴素 / SARIMA / 随机森林并自动降级。
# 若需 Prophet 预测，请安装 [ml] 额外依赖（需 C++ 编译工具链）。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 编译工具链：覆盖 cryptography / pyarrow / Prophet 等可能的源码构建
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制源码并安装（hatchling 从 src 构建 wheel）
COPY . .
RUN pip install --upgrade pip \
    && pip install . \
    && pip install "scikit-learn>=1.4" "statsmodels>=0.14"

# 非 root 运行
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /app/docker-entrypoint.sh
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
