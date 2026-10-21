# 离线镜像打包脚本（S4 阶段实现）

> **状态：占位，S4 阶段实现**（架构文档 6.1 目录树标注）。
>
> 目标（总方案 3.6）：私有化交付需提供**完整离线镜像包**，
> 支持无外网环境纯本地运行（`OA_OFFLINE_MODE=true`）。

## 计划交付内容

```
deploy/offline/
├── build-images.sh          # 在有网机器上构建并导出全部镜像
├── load-images.sh           # 在离线机器上导入镜像并重启服务
├── verify-images.sh         # 校验离线包完整性（sha256 清单）
├── images/                  # 镜像 tar 包（.gitignore 排除）
├── manifests/
│   ├── images.lock          # 镜像清单（名称 + tag + digest）
│   └── checksums.sha256     # 完整性校验清单
└── LICENSE.third-party      # 第三方组件与协议声明（总方案 5.3.4）
```

## 镜像清单（与 `docker-compose.yml` 保持一致）

| 组件 | 镜像 | 协议 |
| --- | --- | --- |
| 底座 API/Worker | `langgenius/dify-api:1.0.0` | Apache-2.0 |
| 底座 Web | `langgenius/dify-web:1.0.0` | Apache-2.0 |
| 代码沙箱 | `langgenius/dify-sandbox:0.2.10` | Apache-2.0 |
| 关系库 | `postgres:16-alpine` | PostgreSQL License |
| 缓存 | `valkey/valkey:7.2-alpine` | BSD-3-Clause ⚠️ D-06 |
| 向量库 | `milvusdb/milvus:v2.4.6` | Apache-2.0 |
| etcd | `quay.io/coreos/etcd:v3.5.16` | Apache-2.0 |
| 对象存储 | `minio/minio:RELEASE.2021-04-22T23-11-00Z` | Apache-2.0（AGPL 前版本线）⚠️ D-06 |
| 身份底座 | `quay.io/keycloak/keycloak:25.0.6` | Apache-2.0 |
| 代理 | `squidly/squid:5.2` | GPL-2.0 ⚠️ **灰名单** |
| 监控 | `prom/prometheus:v2.54.0`、`grafana/grafana:11.1.0` | Apache-2.0 |
| 自研适配层 | `officeai/adapter:1.0.0` | 内部交付物 |
| 自研前端 | `officeai/web:1.0.0` | 内部交付物 |

## ⚠️ 合规待决

1. **D-06**：Valkey 与 MinIO 的最终方案需主理人与法务/合规确认。
2. **squid 协议**：`squidly/squid` 为 GPL-2.0，按灰名单规则「仅可独立进程调用、
   不可引入核心代码」处理。本项目仅以独立容器方式调用其进程，不引入任何代码。
   若合规要求进一步收紧，可替换为 `ubuntu/squid` 或改用应用层出站控制。
   **建议主理人确认此项（可并入 D-06 一并决策）。**

## 阻塞项

- 需确认离线包的交付介质（物理介质/内网 FTP/对象存储）与容量规划。
- 需确认客户环境的 CPU 架构（x86_64 / arm64），影响镜像构建目标平台。
