# Standalone Cloudflare Turnstile Solver — 体积优化版
# 大头是 Camoufox 浏览器本体（~550–700MB）；其余靠多阶段 + 裁剪压到最低
#
# 构建: docker build -t turnstile-solver:local .
# 多架构: 见 .github/workflows/docker-image.yml
#
# 体积构成（约）:
#   python slim  ~150MB
#   apt 运行库   ~200–350MB（GTK/NSS/X11/Xvfb）
#   venv 依赖    ~200–250MB（playwright wheel + numpy + …）
#   Camoufox     ~550–700MB（不可再砍太多，是解题核心）

# ─── Stage 1: 安装 Python 依赖 + 预取 Camoufox ───────────────────────────────
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/root \
    DEBIAN_FRONTEND=noninteractive \
    # 禁止 playwright 顺带拉 Chromium（我们只用 Camoufox 自带 Firefox）
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /build

# strip 仅用于浏览器主程序；构建完不进最终镜像
RUN apt-get update && apt-get install -y --no-install-recommends binutils ca-certificates \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /build/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /build/requirements.txt

# 预取 Camoufox：失败必须让构建失败（旧版 fetch 网络中断仍可能 exit 0）
# 重试数次；GHA 可通过 --secret id=github_token 注入 GITHUB_TOKEN 降限流
RUN --mount=type=secret,id=github_token,required=false \
    set -eux; \
    if [ -f /run/secrets/github_token ]; then \
      export GITHUB_TOKEN="$(cat /run/secrets/github_token)"; \
    fi; \
    ok=0; \
    for i in 1 2 3 4 5; do \
      echo "camoufox fetch attempt $i/5"; \
      if python -m camoufox fetch; then \
        if find "${HOME}/.cache/camoufox" -type f \( -name 'camoufox-bin' -o -name 'camoufox' \) 2>/dev/null | grep -q .; then \
          ok=1; break; \
        fi; \
        echo "fetch returned but no browser binary found"; \
      fi; \
      echo "fetch failed, sleeping before retry..."; \
      sleep $((i * 10)); \
    done; \
    if [ "$ok" != "1" ]; then \
      echo "ERROR: camoufox fetch failed after retries" >&2; \
      exit 1; \
    fi

# ── 裁剪 Camoufox 安装树 + venv ─────────────────────────────────────────────
# 路径: platformdirs user_cache_dir("camoufox") → /root/.cache/camoufox
RUN set -eux; \
    CAMOU="${HOME}/.cache/camoufox"; \
    # 残留压缩包 / 临时文件
    find "$CAMOU" -type f \( -name '*.zip' -o -name '*.tmp' -o -name '*.part' \) -delete 2>/dev/null || true; \
    # Firefox 辅助进程 / 调试符号（运行解题不需要）
    find "$CAMOU" -type f \( \
        -name 'crashreporter' -o -name 'crashreporter.ini' -o \
        -name 'minidump-analyzer' -o -name 'updater' -o -name 'updater.ini' -o \
        -name 'pingsender' -o -name 'default-browser-agent' -o \
        -name '*.dbg' -o -name '*.debug' -o -name 'Throbber-small.gif' \
      \) -delete 2>/dev/null || true; \
    # 多语言包：只留 en-US（Turnstile 解题页不依赖本地化 UI）
    find "$CAMOU" -type d \( -name 'en-US' -o -name 'en-US.lproj' \) -prune -o \
        -type d \( -name 'locale-*' -o -name 'langpack-*' \) -print 2>/dev/null \
        | while read -r d; do rm -rf "$d"; done; \
    find "$CAMOU" -type d -name 'localization' 2>/dev/null | while read -r d; do \
        find "$d" -mindepth 1 -maxdepth 1 -type d ! -name 'en-US' -exec rm -rf {} + 2>/dev/null || true; \
      done; \
    find "$CAMOU" -type d -name 'dictionaries' -exec rm -rf {} + 2>/dev/null || true; \
    # 只 strip 浏览器主程序；.so / venv 扩展库不要 strip（numpy OpenBLAS 会坏）
    find "$CAMOU" -type f \( -name 'camoufox-bin' -o -name 'camoufox' \) \
      -exec strip --strip-unneeded {} + 2>/dev/null || true; \
    # venv：只清缓存与字节码。
    # 切勿删除名为 tests/test/testing 的目录——quart 等包内有 runtime 模块 quart/testing。
    find /opt/venv -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true; \
    find /opt/venv -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true; \
    rm -rf /root/.cache/pip /tmp/* /var/tmp/*; \
    # 体积摘要（构建日志里可见）
    echo "=== size summary ==="; \
    du -sh /opt/venv "$CAMOU" 2>/dev/null || true; \
    du -sh "$CAMOU"/* 2>/dev/null || true; \
    # 最终再校验浏览器在
    find "$CAMOU" -type f \( -name 'camoufox-bin' -o -name 'camoufox' \) | head -5

# ─── Stage 2: 运行时（仅共享库 + venv + 浏览器 + 应用代码）──────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/root \
    PATH="/opt/venv/bin:$PATH" \
    TURNSTILE_HOST=0.0.0.0 \
    TURNSTILE_PORT=5072 \
    TURNSTILE_THREAD=2 \
    TURNSTILE_BROWSER_TYPE=camoufox \
    TURNSTILE_DEBUG=0 \
    TURNSTILE_LAZY=1 \
    TURNSTILE_IDLE_SEC=180 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /app

# Camoufox/Firefox 最小运行时依赖（无 curl：健康检查改用 Python）
# 字体只留 liberation，去掉 emoji 大包
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        libxss1 \
        libxtst6 \
        xvfb \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /usr/share/doc /usr/share/man /usr/share/locale \
    && find /usr/share/fonts -type f ! -name '*.ttf' ! -name '*.otf' -delete 2>/dev/null || true

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/camoufox /root/.cache/camoufox

COPY api_solver.py browser_configs.py db_results.py /app/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/logs /app/keys \
    && python -c "\
from quart import Quart; \
from camoufox.async_api import AsyncCamoufox; \
import api_solver; \
print('imports ok')"

EXPOSE 5072

# 不装 curl，用标准库做健康检查
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=8 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('TURNSTILE_PORT','5072'), timeout=4)"

ENTRYPOINT ["/app/entrypoint.sh"]
