# K8s 集群编排（V1.1 准备物，**清单已落仓库**）

> **状态：manifests 已落仓库（G1，2026-11-02）。定位仍是 V1.1 准备物，不作 V1.0 验收依据。**
>
> 本目录此前是"只有说明文件"的交付缺口；G1 已把 `base/` 下的最小清单全部落齐
> （可 `kubectl apply -k deploy/k8s/base`，YAML 解析与键集合校验通过）。
> 但拍板口径不变：**任何把它当作"V1.0 已支持 k8s"的引用都是误读**——
> 清单未在真实集群做过端到端验收，V1.0 上线形态仍是 Docker Compose + nginx 边缘。

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

## 2. 清单现状（G1，2026-11-02 落地）

字段级要求、取值依据、验收判据见
**`docs/design-2026-11-02-security-deploy.md` §3.3.5「k8s 最小三件套」与 §3.5 验收判据**，
此处不复述规格，只登记现状：

```
deploy/k8s/
├── README.md                     # 本文件
└── base/                         # ✅ 已落仓库（G1）
    ├── kustomization.yaml        # ✅ resources 全列；app.env.keys 是校验清单、不是资源
    ├── namespace.yaml            # ✅ Namespace officeai（隔离域锚点）
    ├── serviceaccount.yaml       # ✅ SA officeai + automountServiceAccountToken: false
    ├── secret.yaml               # ✅ 只写形状与生成命令，**零密钥值**（见 §5.2）
    ├── configmap.yaml            # ✅ 68 个非敏感键，值逐字取自 deploy/.env.example
    ├── app.env.keys              # ✅ 102 个键名（.env.example 全量），供机械校验防漂移
    ├── ingress.yaml              # ✅ 落 Ingress（二选一里的这一项，可替换为外部 LB）
    ├── adapter/
    │   ├── deployment.yaml       # ✅ 单副本 / Recreate / 探针 / 资源 / 安全上下文
    │   ├── service.yaml          # ✅ ClusterIP 8000
    │   └── networkpolicy.yaml    # ✅ 入向=边缘+web，出向=底座 Service 名+DNS
    └── web/
        ├── deployment.yaml       # ✅ 同上口径，端口 3000
        ├── service.yaml          # ✅ ClusterIP 3000
        └── networkpolicy.yaml    # ✅ default-deny + web（入向=边缘，出向=adapter+DNS）
```

`dify/` 与 `infra/`（底座与有状态组件）**V1.1 不落地**：底座优先外部托管，
或继续用 compose 承载；NetworkPolicy 的 egress 按「底座 Service 名」预留（见 §5.5）。

### 2.1 必须 vs 可后置

| 分类 | 项目 |
| --- | --- |
| **V1.1 必须（✅ 已写入清单）** | `securityContext`（`runAsNonRoot` / `allowPrivilegeEscalation: false` / `capabilities.drop: [ALL]` / `seccompProfile: RuntimeDefault`）、`resources.requests` 与 `limits`、`readinessProbe`（adapter 打 `/readyz`）、`livenessProbe`（打 `/healthz`，**不要**用 `/readyz`）、`NetworkPolicy`（含 **DNS egress 53**）、`Secret` 引用、`terminationGracePeriodSeconds`（adapter 的 SSE 长连接要排空） |
| **可后置（已登记）** | `readOnlyRootFilesystem`（**当前为 `false`**：运行期写盘路径未验证，验证方法见主文档 §3.3.5，通过后才改 `true`，两个 Deployment 内均有⚠注释）、HPA、PDB、多副本、Vault/KMS 外部密钥、`overlays/{dev,prod}` 多环境 |

---

## 3. 仍然有效的设计约束

以下四条在 V1.1 落地时依然成立，`docs/architecture-v1.0.md` 的对应结论不变：

1. **适配层无状态**：Pod 不挂载持久卷（清单里只有 `tmp` emptyDir），状态全部外置
   Redis/PostgreSQL，因此可任意水平扩缩。
2. **底座隔离**：`dify-api` / `dify-worker` 仅在内部 Service 暴露，通过 `NetworkPolicy`
   只允许 `adapter` 访问。
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
  k8s 的 resources 取值与端口口径**已复用 P0-4 的结论**（见 §5.4），不再另起一套。
- 集群内有 ingress-nginx（`ingressClassName: nginx`）；改用其它控制器时同步改
  `ingress.yaml` 的 class 与注解前缀。

---

## 5. 如何用（G1 起清单可直接 apply）

### 5.1 部署

```bash
# 1) 先生成 Secret（见 5.2，密钥值只在本地 deploy/.env，不入库）
kubectl -n officeai create secret generic officeai-secrets \
  --from-env-file=deploy/.env --dry-run=client -o yaml | kubectl apply -f -

# 2) 再 apply 清单（Namespace 也在其中，一起创建）
kubectl apply -k deploy/k8s/base
```

注意：`secret.yaml` 本身不含 `data`，第 1 步是**必做步骤**而不是可选项——
只跑第 2 步，Pod 会因 `officeai-secrets` 为空/不存在而起不来（这是刻意设计：
失败要发生在部署那一刻）。

### 5.2 Secret 生成（零密钥入库）

```bash
kubectl -n officeai create secret generic officeai-secrets \
  --from-env-file=deploy/.env --dry-run=client -o yaml | kubectl apply -f -
```

键集合的唯一真源是 `deploy/.env.example`（`base/app.env.keys` 是它的全量镜像，
102 个键，供 `deploy/validate_deploy.py` 机械校验防 k8s 与 compose 键漂移）；
值的唯一真源是本地 `deploy/.env`。**禁止**把生成结果存回仓库。

敏感键判定（与主文档 §3.3.6 门禁同一条判据，宁多勿少）：命名含
`PASSWORD`/`SECRET`/`_KEY`/`TOKEN` 的一律只走 Secret，不进 ConfigMap——
包括名字命中但语义非密钥的 `OA_KEYCLOAK_REALM`/`OA_KEYCLOAK_CLIENT_ID`/
`OA_KEYCLOAK_DB`/`OA_KEYCLOAK_ADMIN`，以及值里嵌口令的 `OA_DATABASE_URL`。

⚠️ envFrom 覆盖顺序：`configmapRef` 排在 `secretRef` 之后，同名键以 ConfigMap
为准（68 个非敏感键）。**改非敏感配置要改 `configmap.yaml`（或按站点出 overlay），
只改 `deploy/.env` 不生效**；改完 `kubectl -n officeai rollout restart deploy/adapter deploy/web`。

### 5.3 镜像 tag / digest 注入方式

两个 Deployment 的 `image:` 写成 `name:tag@sha256:<digest>`，digest **逐字取自
`deploy/images.lock`**（`officeai/adapter` 1.0.0 / `officeai/web` 1.0.0 条目），
与 compose 侧同一真源（§3.3.6：k8s 用 digest 而非浮动 tag，后续
`validate_deploy.py` 断言「Deployment 的 digest ∈ images.lock」）。

- **升级镜像** = 构建/推送后先更新 `deploy/images.lock`，再同步改
  `base/{adapter,web}/deployment.yaml` 的 `image:`（两处，机械断言守着）。
- **换镜像仓库** = 改 `image:` 的仓库前缀 + 换成你自己的 digest；
  不要用 `latest` 或裸 tag（重建即变，无法复现）。
- 自研镜像 `source: local`（images.lock 说明）：digest 是构建产物的 RepoDigest，
  离线分发必须用**同一份镜像 tar**，不能指望"重新构建得到同一 digest"。

### 5.4 与 compose 的口径一致性（防两套部署形态漂移）

| 口径 | compose（P0-4 收口后） | k8s（本清单） |
| --- | --- | --- |
| **端口不裸暴露** | adapter/web 只 `expose`，宿主只开边缘 80/443 | Service 全部 ClusterIP，外部只进 Ingress；`/readyz` `/metrics` 用 `whitelist-source-range` 复刻 edge.conf 的「限内网」 |
| **密钥走 Secret** | 值在 `deploy/.env`（不入库） | `officeai-secrets` 由 `--from-env-file=deploy/.env` 生成；非敏感键走 ConfigMap，键集合同源 `.env.example` |
| **资源限制同源** | adapter 512M 内存限额（lite 实测口径） | adapter requests 200m/128Mi → limits 1/512Mi；web 同款量级 |
| **探针契约** | healthcheck 打 `/readyz`（adapter）/ `/`（web） | readiness 同口径；liveness 单独打 `/healthz`（不可互换） |
| **路由口径** | `deploy/nginx/edge.conf` 八条路径 | `ingress.yaml` 逐条对齐（`/`、`/api/`、`/sso/`、`/files/artifacts/`、`/realms/`、`/resources/`、`/healthz`、`/readyz`、`/metrics`） |

TLS：**落 Ingress**（规格「二选一」取这一项，可替换）——`tls.secretName:
officeai-edge-tls` 只引用不生成，证书由外部产出（cert-manager 或部署侧
`kubectl create secret tls`）。若改由外部 LB 终止 TLS/分发，删 `ingress.yaml`
的 `tls` 段或整个文件即可，路由口径仍以 edge.conf 为准。

### 5.5 runAsUser 取值与来源、NetworkPolicy 口径

- **runAsUser / fsGroup = 1001**（adapter 与 web 同值），来源是镜像构建指令：
  - `apps/adapter/Dockerfile:72-73`：`groupadd --system --gid 1001 adapter && useradd --system --uid 1001 ...`（`USER adapter`）；
  - `apps/web/Dockerfile:82-83`：`addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs`（`USER nextjs`）。
  Pod 的 `securityContext` 用它把「镜像里的 USER」变成集群侧硬门禁
  （`runAsNonRoot: true`），改镜像用户时两处同步。
- **NetworkPolicy egress 按「底座 Service 名」给**：`postgres:5432` /
  `valkey:6379` / `keycloak:8080` / `dify-api:5001`（与 compose 服务名逐字一致，
  适配层只直连 dify-api）。**V1.0 底座可外部托管**：届时把对应条目的 `to`
  换成 `ipBlock: { cidr: <外部地址>/32 }`、必要时改端口，规则注释里已写明，
  其余不动。
- **DNS 53（UDP+TCP）必须显式放行**：漏掉它的典型故障是 Service 名解析失败，
  表现为「偶发 502 / 连接超时」，看起来像应用故障，排障成本极高——两份
  networkpolicy 的注释里都登记了这条。

### 5.6 已知口径差异登记（对接前必须处置）

1. **compose 内联键不在清单**：`OA_DIFY_BASE_URL` / `OA_KEYCLOAK_BASE_URL` /
   `OA_REDIS_URL` / `OA_OBJECT_STORE_ENDPOINT` 在 `docker-compose.yml` 的
   `environment:` 内联注入，**不在** `.env.example`，因此不在 `app.env.keys` /
   `configmap.yaml`；k8s 侧当前只能依赖 `apps/adapter/app/core/config.py` 的
   代码默认值（注意 `redis_url` 默认 `redis://redis:6379/0` 无口令，与栈内
   valkey 口径不符）。对接前决定「补进 .env.example」或「另行注入」。
2. **`readOnlyRootFilesystem: false`**：未验证运行期写盘（见 §2.1），验证通过才开。
3. **Ingress `/healthz` 与 edge.conf 有语义差**：edge.conf 是边缘自答，Ingress
   无自答机制，改指 adapter `/healthz`（`ingress.yaml` 内有注释登记）。
4. **`/realms/` `/resources/` 后端 `keycloak` 是 Service 名预留**：base 不含底座，
   外部托管时删这两条或建同名 ExternalName Service。

### 5.7 前端配置是**构建期**的（审计 2026-11-03 QA-02）

web 只读 Next.js 构建期内联的 `NEXT_PUBLIC_*`（`apps/web/Dockerfile:58-65` 的
`ARG` + `ENV`），**运行期注入同名环境变量没有任何消费方**。因此
`configmap.yaml` 不再包含 `OA_WEB_ADAPTER_BASE_URL` / `OA_WEB_OIDC_ISSUER` /
`OA_WEB_OIDC_CLIENT_ID`（以及固定端口的 `OA_WEB_PORT`），这些值改为构建镜像时传入：

```bash
docker build \
  --build-arg NEXT_PUBLIC_ADAPTER_BASE_URL="" \
  --build-arg NEXT_PUBLIC_OIDC_ISSUER="https://<对外域名>/realms/officeai" \
  --build-arg NEXT_PUBLIC_OIDC_CLIENT_ID="officeai-adapter" \
  -t officeai/web:1.0.0 apps/web
```

- `NEXT_PUBLIC_ADAPTER_BASE_URL` 在边缘收口形态下应**置空**（浏览器走同源
  `/api`、`/sso`，由 Ingress/edge 转发），置成 `http://localhost:8000` 会绕过边缘；
- 改这三个值必须**重新构建镜像并更新 `deploy/images.lock`**，`rollout restart`
  不会生效——这正是旧 ConfigMap 三个键「配了等于没配」的原因。

---

*最后更新：2026-11-03（QA-02：前端键从运行期 ConfigMap 移出，改为构建期 build-arg）· 依据 `docs/audit-2026-11-03-full-repo-audit.md`*
