# 离线介质包（3A 构建侧已实现）

> **状态：3A 已落地**（2026-11-07）：构建与校验由
> `deploy/validate_offline_media.py` 提供；介质内容、生成/校验步骤、体积预期与
> 已知限制见 [`docs/offline-package.md`](../../docs/offline-package.md)。
> 目标机加载/升级/回滚（3B）尚未实现。
>
> 说明：`OA_OFFLINE_MODE` 已于 2026-11-04 审计确认为无消费方的死配置并删除；
> 本轮不新增环境变量（版本/架构/垂直均为命令行参数）。

## 已交付内容（3A）

```text
deploy/
├── validate_offline_media.py   # 构建（build）/ 校验（verify）/ 自检（self-test）/ 就绪检查
└── offline/
    ├── plugins/                # 离线插件包（.difypkg，当前含 deepseek-0.0.25）
    └── README.md               # 本文件（内容清单与合规登记）
```

介质包产出在仓库外的 `dist/offline/moy-offline-v<version>-x86_64/`（`dist/` 已忽略），
包含 OCI 镜像归档、Python wheels、前端 npm 缓存、迁移/初始化/回滚 SQL、插件、
SBOM、NOTICE、`MANIFEST.json`、`SHA256SUMS` 与 cosign 签名。

## 镜像清单（以 `deploy/images.lock` 为唯一事实来源）

| 组件 | 镜像 | 协议 |
| --- | --- | --- |
| 底座 API/Worker | `langgenius/dify-api:1.17.1` | Apache-2.0 |
| 底座 Web | `langgenius/dify-web:1.17.1` | Apache-2.0 |
| 代码沙箱 | `langgenius/dify-sandbox:0.2.15` | Apache-2.0（修改版，见 images.lock 说明） |
| 插件守护进程 | `langgenius/dify-plugin-daemon:0.6.10-local` | Apache-2.0（修改版，见 images.lock 说明） |
| 关系库 | `pgvector/pgvector:0.8.7-pg18-bookworm`（PostgreSQL 18.6） | PostgreSQL License |
| 缓存 | `valkey/valkey:9.2-alpine` | BSD-3-Clause ✅ D-06 已批准 |
| 向量库 | `milvusdb/milvus:v3.0.3` | Apache-2.0 |
| etcd | `quay.io/coreos/etcd:v3.7.2` | Apache-2.0 |
| 对象存储 | `chrislusf/seaweedfs:4.48` | Apache-2.0（白名单，S3 兼容）✅ D-06 已批准 |
| 身份底座 | `quay.io/keycloak/keycloak:26.8.1` | Apache-2.0 |
| 反向代理 | `nginx:1.28-alpine` | BSD-2-Clause |
| 代理 | `ubuntu/squid:6.13-25.04_beta` | GPL-2.0 ⚠️ **灰名单** |
| PG 指标采集 | `prometheuscommunity/postgres-exporter:v0.17.1` | Apache-2.0 |
| Valkey 指标采集 | `oliver006/redis_exporter:v1.67.0` | MIT |
| 监控 | `prom/prometheus:v3.15.0` | Apache-2.0 |
| 适配层构建/运行基础镜像 | `python:3.13-slim` | Python-2.0-or-later / PSF-2.0（白名单） |
| 前端构建/运行基础镜像 | `node:22-alpine` | MIT（Node.js）/ BSD-2-Clause（依赖），白名单 |
| 自研适配层 | `officeai/adapter:1.0.0` | 内部交付物 |
| 自研前端 | `officeai/web:1.0.0` | 内部交付物 |

## 合规状态

1. **D-06**：✅ 已于 2026-10-21 由创始人授权主理人正式批准（Valkey + SeaweedFS，
   白名单协议双落地，`docs/decisions/0004`）。
2. **Grafana**：✅ V1.0 已移除（AGPL-3.0 且 AGPL 前版本线在 Docker Hub 已不存在，
   看板不属 V1.0 验收项；`docs/decisions/0004` 第二章）。
3. **Dify**：⚠️ 修改版 Apache-2.0（多租户与商标限制）——三条规避约束已拍板：
   SaaS 不用 Dify 多租户（租户隔离由适配层 + Postgres RLS 实现）、不修改代码、
   不展示品牌标识（`docs/decisions/0004` 第三章）。
4. **squid 协议**：`ubuntu/squid` 为 GPL-2.0，按灰名单规则「仅可独立进程调用、
   不可引入核心代码」处理。本项目仅以独立容器方式调用其进程，不引入任何代码。
   若合规要求进一步收紧，可改用应用层出站控制替代代理方案。
   **建议主理人确认此项（可并入 D-06 一并决策）。**

## 阻塞项

- 需确认离线包的交付介质（物理介质/内网 FTP/对象存储）与容量规划。
- 需确认客户环境的 CPU 架构（x86_64 / arm64），影响镜像构建目标平台。

## 插件离线包（2026-10-21 新增）

私有化离线环境无法访问 Dify 插件市场（marketplace.dify.ai），
模型提供方插件须随离线包分发并在部署时本地安装：

| 插件 | 文件 | 协议 |
| --- | --- | --- |
| DeepSeek 模型提供方 v0.0.25 | `plugins/deepseek-0.0.25.difypkg` | Apache-2.0（Dify 官方插件） |

安装：`python apps/adapter/qa_probe/bootstrap_dify.py --pkg deploy/offline/plugins/deepseek-0.0.25.difypkg ...`
（引导脚本会自动上传并安装，无需外网）
