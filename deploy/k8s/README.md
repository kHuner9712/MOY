# K8s 集群编排（SaaS 生产环境）

> **状态：占位，S4 阶段实现**（架构文档 6.1 目录树标注）。
>
> 目标（SaaS 多租户，架构文档 1.3）：
> - K8s + HPA 弹性扩缩容，web Pod ×N（HPA 2-10）、adapter Pod ×N（HPA 2-8）；
> - PostgreSQL RLS 行级隔离 + 应用层 `tenant_id` 双保险；
> - 核心服务可用率 ≥ 99.5%；
> - 底座能力层与基础设施层独立伸缩，互不影响。
>
> 计划交付内容：
> ```
> deploy/k8s/
> ├── base/            # Namespace、ConfigMap、Secret、ServiceAccount
> ├── adapter/         # Deployment + Service + HPA + PDB + NetworkPolicy
> ├── web/             # Deployment + Service + HPA + Ingress
> ├── dify/            # 底座编排（仅内部 Service，不对外暴露）
> ├── infra/           # 有状态组件声明（外部托管优先）
> └── overlays/
>     ├── dev/
>     └── prod/
> ```

## 关键设计约束

1. **适配层无状态**：Pod 不挂载持久卷，状态全部外置 Redis/PostgreSQL，
   因此可任意水平扩缩（架构文档 1.2）。
2. **底座隔离**：`dify-api`/`dify-worker` 仅在内部 Service 暴露，
   通过 NetworkPolicy 只允许 `adapter` 命名空间访问（架构文档 1.4）。
3. **密钥管理**：所有 OA_* 敏感变量走 Kubernetes Secret，
   生产环境建议接入外部密钥管理服务（Vault/KMS）。
4. **探针契约**：`/healthz` 为存活探针，`/readyz` 为就绪探针，
   依赖配置与注册表可用性（架构文档 10.2 D-07）。
5. **弹性基线**：
   - web：CPU 60% 触发扩容，上限 10 副本；
   - adapter：CPU 65% 触发扩容，上限 8 副本；SSE 长连接场景需放宽
     `terminationGracePeriodSeconds`。

## 阻塞项

- 需主理人与运维确认 K8s 版本、集群规格与云厂商选型（架构文档 10.2 D-07）。
- 需确认镜像仓库地址与拉取凭据管理方式。
