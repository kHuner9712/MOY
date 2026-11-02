# K8s 集群编排（V1.1 准备，**当前为交付缺口**）

> **状态：manifests 全部未创建 —— 这是一个明确的交付缺口，不是"已完成"或"进行中"。**
>
> 本目录当前只有这份说明文件。任何把它当作"k8s 已支持"的依据都是误读。

---

## 1. 与 V1.0 的关系（2026-11-02 拍板）

**V1.0 不交付 k8s 形态，V1.0 SaaS 与私有化均以 Docker Compose + nginx 边缘上线。**

依据（主理人 2026-11-02 拍板，见 `docs/design-2026-11-02-security-deploy.md` §4.1 R6）：

1. **V1.0 是单副本部署**（`deploy/k8s/` 无 HPA，且不具备引入多副本的前提条件），
   k8s 相对 compose 的收益在单副本下**主要是"未来弹性"**，不构成 V1.0 的上线阻塞。
2. 周期紧张。V1.0 强上 k8s 需额外投入集群、镜像仓库、Ingress 控制器、Secret 管理，
   与"能上线"直接冲突。
3. 部署面的真实阻塞项是 **P0-4**（无 TLS 终止 / 应用端口绑全网卡 / 三套 compose 资源限制不全），
   这些在 compose 上修完即可上线；k8s 化不是阻塞项。

**因此：本目录的定位是 V1.1 的准备物。** 其内容不得被引用为 V1.0 的验收依据。

### 1.1 本文档旧口径的更正（重要）

本文件此前版本写过"HPA 2-10 副本 / adapter 2-8 副本 / 可用率 ≥ 99.5%"。
**这些是 V1.1 及以后的目标值，在 V1.0 不成立，不得作为验收口径引用。** 具体：

| 旧口径 | V1.0 实际 | 说明 |
| --- | --- | --- |
| `web` HPA 2-10 | **单副本，无 HPA** | 弹性能力 V1.1 再引入 |
| `adapter` HPA 2-8 | **单副本，无 HPA** | 同上 |
| 可用率 ≥ 99.5% | **不作承诺** | 单副本无冗余，承诺可用率与事实不符 |
| PDB | **不做** | 单副本下 PDB 无语义 |

---

## 2. 目标清单（V1.1 落地物）

字段级要求、取值依据、验收判据见
**`docs/design-2026-11-02-security-deploy.md` §3.3.5「k8s 最小三件套」与 §3.5 验收判据**，此处不复述，只列结构：

```
deploy/k8s/
├── README.md                     # 本文件
└── base/
    ├── kustomization.yaml        # 把下列资源串起来
    ├── namespace.yaml            # Namespace officeai
    ├── serviceaccount.yaml       # SA + automountServiceAccountToken: false
    ├── secret.yaml               # **只写引用方式**，密钥值由 deploy/.env 生成，禁止入库
    ├── configmap.yaml            # 非敏感配置
    ├── app.env.keys              # env 键清单（供 validate_deploy.py 机械校验，防两处漂移）
    ├── ingress.yaml              # 或文档声明"由外部 LB 提供"，二选一
    ├── adapter/
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   └── networkpolicy.yaml
    └── web/
        ├── deployment.yaml
        ├── service.yaml
        └── networkpolicy.yaml
```

`dify/` 与 `infra/`（底座与有状态组件）**V1.1 不落地**：底座优先外部托管，或继续用 compose 承载。

### 2.1 必须 vs 可后置

| 分类 | 项目 |
| --- | --- |
| **V1.1 必须** | `securityContext`（`runAsNonRoot` / `allowPrivilegeEscalation: false` / `capabilities.drop: [ALL]` / `seccompProfile: RuntimeDefault`）、`resources.requests` 与 `limits`、`readinessProbe`（adapter 打 `/readyz`）、`livenessProbe`（打 `/healthz`，**不要**用 `/readyz`）、`NetworkPolicy`（含 **DNS egress 53**）、`Secret` 引用、`terminationGracePeriodSeconds`（adapter 的 SSE 长连接要排空） |
| **可后置** | `readOnlyRootFilesystem`（**须先验证运行期无写盘路径**，验证方法见主文档 §3.3.5）、HPA、PDB、多副本、Vault/KMS 外部密钥、`overlays/{dev,prod}` 多环境 |

---

## 3. 仍然有效的设计约束

以下四条在 V1.1 落地时依然成立，`docs/architecture-v1.0.md` 的对应结论不变：

1. **适配层无状态**：Pod 不挂载持久卷，状态全部外置 Redis/PostgreSQL，因此可任意水平扩缩。
2. **底座隔离**：`dify-api` / `dify-worker` 仅在内部 Service 暴露，通过 `NetworkPolicy` 只允许 `adapter` 访问。
3. **探针契约**：`/healthz` 为存活探针，`/readyz` 为就绪探针（含 DB 连通性与权限自检）。
   **两者不可互换**：把 `livenessProbe` 指向 `/readyz` 会让依赖故障触发连环重启。
4. **密钥管理**：所有 `OA_*` 敏感变量走 Kubernetes `Secret`，生产建议接外部密钥管理（Vault/KMS）。
   **单一真源**：Secret 由 `kubectl create secret --from-env-file=deploy/.env` 生成，
   键集合以 `deploy/.env.example` 为唯一真源，受 `deploy/validate_config_names.py` 管辖。

---

## 4. 前置条件（V1.1 开工前需确认）

- K8s 版本、集群规格与云厂商选型（架构文档 10.2 D-07）。
- 镜像仓库地址与拉取凭据管理方式。
- **V1.0 的 P0-4 已闭环**（边缘模板、端口口径、资源限制取值确定）——
  k8s 的 resources 取值与端口口径应复用 P0-4 的结论，而不是另起一套。

---

*最后更新：2026-11-02（架构师 高见远）· 依据 `docs/design-2026-11-02-security-deploy.md` §3.3.5 / §4.1 R6*
