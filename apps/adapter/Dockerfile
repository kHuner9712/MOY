# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# 自研统一适配层镜像（apps/adapter）
#
# 设计要点：
# - 多阶段构建：builder 装依赖，runtime 只带运行期文件，镜像更小；
# - 非 root 用户运行，符合容器安全基线；
# - 不内置任何底座地址与密钥，全部通过 OA_* 环境变量注入；
# - 健康检查走 /readyz（免鉴权）：除连通性外还自检业务库账号是否带 BYPASSRLS，
#   避免超级用户 DSN 让 RLS 双保险静默失效（G-25）。
# ---------------------------------------------------------------------------

# ---------- builder ----------
# ENG-04（审计 2026-10-28）：FROM 锁 digest（Hub 实查 2026-10-28；登记见
# deploy/images.lock 的 python 条目）——builder 镜像可被上游重推覆盖 tag，
# 锁 digest 才能保证构建可复现；升级 = 改两处 FROM + 更新 images.lock。
FROM python:3.13-slim@sha256:5dde09372dfa8e7762892b2a12619fcf6c23b0b78bce1058a9fa5a3a7a40e68e AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# build-essential 供 psycopg/asyncpg 等含扩展依赖编译使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# R2-ARC-02（审计 2026-10-29）：生产镜像必须消费 `uv.lock`。
#
# 原实现只 COPY `pyproject.toml` + `app/`，由 `pip install .` 在**构建当天**现场
# 解析 `>=` 区间——CI 用 `uv sync --frozen` 验的是锁树，交付的却是"当天 PyPI
# 最新匹配版本"的树，两者可以不同（如 sqlalchemy 2.1.6 vs 2.2.x）。
#
# 现方案：单一真源仍是 `uv.lock`（不新增入库的 requirements 文件，避免双锁漂移）。
# 镜像内固定版本装 uv，用 `uv export --frozen` 导出**精确 `==` 清单**，再交给 pip：
#   - `--frozen` 保证 uv 不会在镜像内改写锁文件；
#   - `--no-dev` 不把测试依赖带进生产镜像；
#   - `--no-emit-project` 让项目自身不进入清单，改由下一步 `--no-deps` 安装，
#     避免 pip 重新解析项目依赖（那样又会落回"现场解析"）。
# 同时删除 `pip install --upgrade pip`：让包管理器自身也浮动，等于锁住了依赖却
# 没锁解析器。
ARG UV_VERSION=0.13.1

COPY pyproject.toml README.md uv.lock ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "uv==${UV_VERSION}" \
    && /opt/venv/bin/uv export --frozen --no-dev --no-hashes --no-emit-project \
         --output-file /tmp/requirements.lock \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.lock \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps . \
    && rm -f /tmp/requirements.lock

# ---------- runtime ----------
FROM python:3.13-slim@sha256:5dde09372dfa8e7762892b2a12619fcf6c23b0b78bce1058a9fa5a3a7a40e68e AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    OA_ENV=production \
    OA_HOST=0.0.0.0 \
    OA_PORT=8000 \
    OA_LOG_FORMAT=json

# curl 用于容器健康检查；libreoffice-writer + libreoffice-calc 供离线 Office →
# PDF 预览（2026-11-05 B2）。**不装 impress**：pptx 按矩阵默认决策降级为"仅下载"，
# 少一份组件就少一份体积与攻击面（镜像预计 +350–500MB）。
# fonts-noto-cjk 必须装：缺中文字形时转换出来的 PDF 不是乱码而是**方框**，
# 而且只在真机预览时才看得出来（CI 无 soffice，测不到）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         curl libreoffice-writer libreoffice-calc fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 adapter \
    && useradd --system --uid 1001 --gid adapter --home /app adapter

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=adapter:adapter app ./app
COPY --chown=adapter:adapter pyproject.toml README.md ./

# ---------------------------------------------------------------------------
# 构建期冒烟断言（2026-11-05 B2）：LibreOffice 只进运行镜像，CI 无法验证真实
# 转换；这一层的唯一目的就是让「没装成功 / 转不出来」在**构建阶段**直接失败，
# 而不是等部署后用户点预览才发现（那时只能看到一个 502）。
#
# 不只 `soffice --version`：版本能起来不代表组件齐。这里用 venv 里的
# python-docx / openpyxl 现场生成最小 docx 与 xlsx，各转一次 PDF，并校验
# 输出以 `%PDF-` 开头；同时用 `fc-list` 校验中文字体真的落盘（缺字形时
# PDF 里的中文会变成方框，而"转换成功"的退出码仍是 0）。
# ---------------------------------------------------------------------------
RUN soffice --version \
    && test -x "$(command -v soffice)" \
    && fc-list | grep -qi "Noto Sans CJK" \
    && mkdir -p /tmp/office-smoke/doc-out /tmp/office-smoke/sheet-out /tmp/office-smoke/profile \
    && python -c "from docx import Document; Document().save('/tmp/office-smoke/smoke.docx')" \
    && python -c "from openpyxl import Workbook; w = Workbook(); w.active['A1'] = 'ok'; w.save('/tmp/office-smoke/smoke.xlsx')" \
    && soffice --headless --norestore -env:UserInstallation=file:///tmp/office-smoke/profile \
         --convert-to pdf --outdir /tmp/office-smoke/doc-out /tmp/office-smoke/smoke.docx \
    && soffice --headless --norestore -env:UserInstallation=file:///tmp/office-smoke/profile \
         --convert-to pdf --outdir /tmp/office-smoke/sheet-out /tmp/office-smoke/smoke.xlsx \
    && test -s /tmp/office-smoke/doc-out/smoke.pdf \
    && test -s /tmp/office-smoke/sheet-out/smoke.pdf \
    && head -c 5 /tmp/office-smoke/doc-out/smoke.pdf | grep -q '%PDF-' \
    && head -c 5 /tmp/office-smoke/sheet-out/smoke.pdf | grep -q '%PDF-' \
    && rm -rf /tmp/office-smoke \
    && echo "office preview runtime assets OK"

USER adapter

EXPOSE 8000

# 存活探针（架构文档 10.2 D-07）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
