# 轻量版最小容器集实测基线（P4-35）

| 项 | 内容 |
| --- | --- |
| 文档 | `docs/lite-container-baseline.md` |
| 版本 | v1.0（2026-10-24） |
| 作者 | 高见远（架构师） |
| 性质 | **实测报告**。本文件是 PRD v1.1 §5.1 / P4-15 判据 / 对外口径三处的**唯一数字来源** |
| 基线 | `main @ 2bdfa19` + 工作区在途整改 |
| 适用 | 私有化**轻量版**（主推形态，D4 首个付费客户） |
| 一句话结论 | **常驻 9 个容器、空载内存峰值 1.93 GiB（Docker 工作集口径）/ 2.30 GiB（cgroup 上界）、首次起栈 95.7s → 8G 单机够用，余量 ≥5.6 GiB** |

> **口径纪律**：本文件所有数字都来自本机实测（2026-10-24 17:00-17:20 UTC），采样方法写在第 2 节。
> 任何一个数字被引用到 PRD / 对外材料时，必须连同"口径"一起引用（是 Docker 工作集还是 cgroup 上界、
> 是空载还是链路后）。**禁止只抄数字不抄口径**——阶段三就栽在"覆盖率 90.78% 没写含不含 PG"上。

---

## 1. 容器清单

### 1.1 常驻 9 个（实测 docker compose up 后 `docker ps` 计数 = 9）

| # | 服务 | 镜像 : tag | RepoDigest（sha256） | 镜像大小 | 启动顺序依赖 | 存在理由（不可删的原因） |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `proxy` | `nginx:1.28-alpine` | `a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236` | 93.5 MB | adapter(healthy), web(healthy) | 统一入口 + TLS + **SSE 必须 `proxy_buffering off`**；`/metrics` 与 `/api` 分流。直连 adapter 会把 8000 暴露给客户网络 |
| 2 | `adapter` | `officeai/adapter:1.0.0` | `969f696da3869d28f70622eff0a7e88eb6b440c4f944026f1e04d42d98a983aa` | 399 MB | postgres, valkey, dify-api(healthy) | 业务主体（96 端点 + 会话/记忆/计量/鉴权校验） |
| 3 | `web` | `officeai/web:1.0.0` | `f638b96573608c97142b78230f304e9f37195da87793e0bb4647b17b945aa130` | 313 MB | adapter(healthy) | 自研前端（Next.js standalone，**不是静态站，必须有服务端进程**） |
| 4 | `postgres` | **`pgvector/pgvector:0.8.7-pg18-bookworm`** | `2358fcba361ed2233a5ed81b5fe4ca779ccb304120ce531a3bf51c0ed7e2bc11` | 641 MB | —（根依赖） | 业务库 + **`vector` 扩展**（记忆向量 + 轻量版知识库向量）。实测 `PostgreSQL 18.6`，镜像内 `/usr/share/postgresql/18/extension/` 含 43 个 vector 相关文件 |
| 5 | `valkey` | `valkey/valkey:9.2-alpine` | `cdcb7b1c7b6f1c9cb9442c71ac6275e78184657448407b2c1fdde7ee8024f4fb` | 70.9 MB | —（根依赖） | 幂等键、限流计数、缓存；Dify 的 celery broker（db1）也复用同实例 |
| 6 | `keycloak` | `quay.io/keycloak/keycloak:26.8.1` | `79c623a22abacf3fe6c00a5fb30f5b7843d717b234033b80995891c06116d244` | 752 MB | postgres(healthy) | **令牌签发方**。适配层「只做令牌校验与请求转发，不做登录与签发」（`security.py` 模块 docstring / 架构 3.0.2）。**没有它就没有人能登录**——这不是可选项 |
| 7 | `dify-api` | `langgenius/dify-api:1.17.1` | `ceede5b903afaa20348f7ad80ebf847379dc56d886a99b9ec6a08913dacd7732` | 4.08 GB | postgres, valkey, plugin_daemon | 4 个能力档位应用（Service API `/v1/chat-messages`） |
| 8 | `dify-worker` | 同 `dify-api:1.17.1`（`MODE=worker`） | 同上（同镜像） | 0（复用镜像层） | postgres, valkey | 知识库索引异步任务；**去掉它 = 文档上传后永远停在"处理中"** |
| 9 | `plugin_daemon` | `langgenius/dify-plugin-daemon:0.6.10-local` | `34412a22d1e1d1a6c73727fef88cffbcdd9be7a21bf9ad3076b4bf5c1b88fd9b` | 2.28 GB | postgres(healthy) | **Dify 1.x 模型 provider 的管理者**：缺它 provider 列表 500（`PluginDaemonInnerError`，compose 注释已记录 2026-10-21 实测）。模型 provider（DeepSeek/通义等）以插件形态装在这里 |

**镜像磁盘占用（解压后合计）≈ 8.4 GB**（去重 dify-api 与 worker 共用镜像：93.5+399+313+641+70.9+752+4080+2280 = 8629 MB）。离线包必须按此规划介质。

### 1.2 可选 1 个（不进常驻）

| 服务 | 镜像 | 何时需要 | 为什么不做常驻 |
| --- | --- | --- | --- |
| `dify-web` | `langgenius/dify-web:1.17.1`（digest `sha256:6353fe8e…`，见 `deploy/images.lock:69-71`；本轮未拉取整镜像） | ① 首次配置模型 provider（填模型 API Key）② 导入/升级 4 个档位应用 ③ 客户要自己改提示词 | 日常对话零依赖。**实测证据**：本机标准栈连续运行 29 小时**从未启动 diffy-web**，同期 `POST /api/v1/model/generate` 正常返回（`code=0`，usage 47/16/63） |

建议编排里放 `profiles: [admin]`，运维手册写：`docker compose --profile admin up -d dify-web` → 配完 → `docker compose --profile admin down`。

### 1.3 相对标准栈删掉的 7 项（省 ≈1.6 GiB 内存 + 3.1 GB 镜像）

| 删掉 | 省内存（实测/推算） | 省镜像 | 依据 |
| --- | --- | --- | --- |
| `milvus` + `milvus-etcd` + `milvus-object-store` | 279.4 + 43.8 + 147.8 = **471 MiB** | 2.2 GB + 724 MB | 轻量版 `VECTOR_STORE=pgvector`；Dify 1.17 官方镜像**已内置** `dify-vdb-pgvector` 后端（容器内 `entry_points(group='dify.vector_backends')` 含 `pgvector`，`dify-vdb-pgvector 0.0.1` + python `pgvector 0.5.0` 已装）→ **不需要装任何插件，只改环境变量** |
| `etcd`（顶层） | 12.8 MiB | 86.4 MB | 仅 Milvus 需要 |
| `object-store`（SeaweedFS） | 158.4 MiB | 724 MB | Dify 1.17 支持 `STORAGE_TYPE=local`（容器内 `ext_storage.py` 实测有 `case StorageType.LOCAL → OpenDALStorage(scheme="fs")`） |
| `dify-sandbox` | 126.3 MiB | 848 MB | 4 个档位应用不含代码执行节点 |
| `dify-worker-beat` | 375.8 MiB | 0（复用镜像） | 仅定时调度；代价：Dify 侧历史清理不自动跑 → 运维手册"季度手工清理" |
| `ssrf_proxy` | 162.9 MiB | 257 MB | 无外网 URL 抓取场景。**若客户用需要抓 URL 的知识库同步，加 `--profile with-ssrf` 起回** |
| `prometheus` | 79.1 MiB | 381 MB | 观测非常驻组件；P4-13 的 4 job 全 UP 在标准版验收 |

> ⚠️ **两个"删了会起不来"的坑（实测踩到，写进编排注释）**：
> 1. **`CODE_EXECUTION_ENDPOINT` / `SSRF_PROXY_HTTP_URL` / `SSRF_PROXY_HTTPS_URL` 只能"整条不设"，绝不能设成空字符串**。置空会撞 DifyConfig 的 URL 校验 → `ValidationError: CODE_EXECUTION_ENDPOINT Input should be a valid URL, input is empty` → **dify-api 进程直接退出**。
> 2. **`dify-api` 的 `MIGRATION_ENABLED` 必须为 `true`**，否则 `dify` 库没有表，第一个请求就 500。

### 1.4 Dify 最小通道的向量库环境变量（逐字取自镜像内源码，非文档推测）

`dify_vdb_pgvector/pgvector.py:39-53,295-302` 实际读取这 7 个键，缺一个就抛
`ValueError: config PGVECTOR_XXX is required`：

```yaml
VECTOR_STORE: pgvector
PGVECTOR_HOST: postgres          # 与业务库同实例
PGVECTOR_PORT: "5432"
PGVECTOR_USER: <OA_POSTGRES_USER>
PGVECTOR_PASSWORD: <OA_POSTGRES_PASSWORD>
PGVECTOR_DATABASE: dify          # 独立库：向量表不落业务库，避免与 RLS 策略混库
PGVECTOR_MIN_CONNECTION: "2"
PGVECTOR_MAX_CONNECTION: "10"
```

> 三者**共享同一个 PG 与同一个 Valkey**（Q-1）：PG 上是 `officeai` / `dify` / `dify_plugin` / `keycloak`
> 四个库，Valkey 上是 db0（适配层）/ db1（Dify celery）/ db2（plugin_daemon）。

---

## 2. 每容器内存峰值

### 2.1 采样方法（可复现，必须随数字一起引用）

| 项 | 口径 |
| --- | --- |
| 主口径 | `docker stats --no-stream` 的 `MemUsage`（**Docker 官方工作集**，已扣除可回收页缓存；客户运维看到的也是这个数） |
| 上界校验 | cgroup v2 `memory.current`（**含 page cache**），每阶段末对每个容器取 1 次快照 |
| 实测单次采样耗时 | **2.9 s**（`docker stats` 自身开销）→ **有效采样间隔 3.8-3.9 s** |
| 冷启动阶段 | 25 次采样（覆盖 0-95.7 s 全过程，与 `up -d` **并行**执行；`up` 被放后台，否则它的阻塞会让时序失真） |
| 空载阶段 | 40 s 窗口 / **18 次**采样，取窗口内峰值（keycloak 首尾差 <1%，判定已稳态） |
| 完整链路 | 5 次 M1 请求 + 3 s 静置，**每请求后采样 1 次**，另加静置后 1 次，共 6 次 |
| 宿主机 | Windows + Docker Desktop，Linux VM 内存 **15.53 GiB**，Docker 29.9.1，Compose v2 |
| 隔离性 | 独立 compose 项目 `oalite`、独立卷、独立端口（65000 段），全生命周期与生产栈 `oa-*` 互不可见 |

### 2.2 冷启动峰值（JVM 已加上限）

| 服务 | docker stats（MiB，**主口径**） | cgroup memory.current（MiB，上界） |
| --- | --- | --- |
| keycloak | **828.0** | 908.8 |
| dify-worker | 427.7 | 503.8 |
| dify-api | 422.9 | 491.5 |
| adapter | 97.3 | 92.4 |
| postgres | 94.3 | 190.9 |
| web | 42.5 | 47.6 |
| proxy | 29.4 | 33.4 |
| plugin_daemon | 18.9 | 22.9 |
| valkey | 8.4 | 12.3 |
| **合计** | **1969.4** | **2303.6** |

### 2.3 空载稳态（起栈后静置 40 s）

| 服务 | docker stats（MiB） | cgroup（MiB） |
| --- | --- | --- |
| keycloak | **797.6** | 909.1 |
| dify-worker | 425.3 | 503.1 |
| dify-api | 420.6 | 490.9 |
| postgres | 96.3 | 191.1 |
| adapter | 87.5 | 92.2 |
| web | 41.0 | 42.4 |
| proxy | 28.5 | 33.3 |
| plugin_daemon | 19.0 | 20.1 |
| valkey | 11.4 | 11.3 |
| **合计** | **1927.2** | **2293.5** |

### 2.4 一次完整链路后的增量（关键结论）

**轻量栈本身跑不通完整链路的证据与处理**：轻量栈缺「模型 provider 插件」的安装步骤
（`POST /v1/chat-messages` → `{"code":"invalid_param","message":"Provider langgenius/deepseek/deepseek does not exist."}`）。
这属**交付流程**（离线装插件 + 填 Key），不属容器集本身，故改为**在标准栈上测链路增量**——
两者跑的是**同一镜像、同一代码**，增量可平移到轻量栈。

标准栈 5 次 M1 实测（全部成功）：延迟 **1.20 / 1.79 / 1.37 / 1.78 / 2.09 s**，`total_units` 161-218。

| 服务 | 链路前基线 | 链路峰值 | **增量** |
| --- | --- | --- | --- |
| adapter | 91.7 | 93.1 | +1.4 |
| dify-api | 474.7 | 476.6 | +1.9 |
| plugin_daemon | 85.3 | 87.8 | +2.5 |
| postgres | 74.3 | 77.2 | +2.9 |
| valkey | 8.2 | 9.2 | +1.0 |
| dify-worker | 415.9 | 416.2 | +0.3 |
| **合计** | 1150.1 | 1160.1 | **+10.0 MiB** |

> **结论**：内存由「进程基线」决定，**不由请求量决定**（本量级）。一次完整链路的额外占用 < 10 MiB，
> 可忽略。所以容量规划只看 §2.3 的空载稳态即可 → 轻量版**链路后峰值 ≈ 1.93 GiB**。

### 2.5 反例：不设 JVM 上限会发生什么（必须写进编排）

同一套编排、只去掉 `JAVA_OPTS_APPEND`，Keycloak 空载就吃 **2166.8 MiB**（cgroup 2278.8），
整机合计 **3304.5 MiB / 3667.9 MiB**——**比加了上限高 72%**。JVM 默认按宿主内存取堆，
在 8G 机器上会按 2G 量级堆去预留，直接把余量吃掉。

```yaml
# keycloak 服务必须带这一行（本次实测值：384m 足够令牌签发与用户管理）
JAVA_OPTS_APPEND: "-Xms64m -Xmx384m"
```

---

## 3. 启动顺序与就绪依赖

### 3.1 实测时序（首次启动，空卷）

`t=0` = `docker compose up -d` 发起时刻；数值为 3.9 s 轮询的**观测值**（误差 ≤1 个采样周期），
容器 `StartedAt` 为精确值（两者已交叉核对）。

| 顺序 | 服务 | 就绪时刻（观测） | 精确 StartedAt（相对 valkey） | 就绪判据 | 主要耗时原因 |
| --- | --- | --- | --- | --- | --- |
| 1 | valkey | 7.1 s | +0.00 s | `valkey-cli ping` | 镜像启动 |
| 2 | plugin_daemon | 13.2 s | +0.35 s（postgres 先起） | Running（无就绪探针） | 连 PG + 跑自身迁移 |
| 3 | postgres | 13.3 s | +0.35 s | `pg_isready` **且** `schema_migration` 表存在 | 首次跑 4 个 initdb 脚本（建 4 库 + 34 表 + RLS + 应用角色） |
| 4 | dify-worker | 17.0 s | — | Running（无就绪语义） | celery worker 起 |
| 5 | **dify-api** | **78.1 s** | +13.7 s | `GET /health` 200 | **长尾项**：Dify 自身 boot（Flask + 插件客户端 + provider 初始化），首次跑全套 alembic 迁移 |
| 6 | keycloak | 81.6 s | +14.1 s | `GET :9000/health/ready` 200 | JVM + 建表 + realm 初始化 |
| 7 | adapter | 86.1 s | +79.6 s | `GET /readyz` 200（5 项检查全绿） | 等 dify-api healthy 后启动 |
| 8 | web | 89.4 s | — | `GET /` < 500 | Next.js 起 |
| 9 | proxy | 95.7 s | — | `GET /nginx-health` 200 | 等 adapter + web healthy |
| — | **全部就绪** | **95.7 s** | | | **目标 ≤180 s，达标（余量 84 s）** |

### 3.2 二次启动（保留卷，模拟重启/升级后）

**100.9 s**（keycloak 从 81.6 s 缩到 48.1 s；**dify-api 仍是 84.4 s**）。

> **结论（重要）**：`dify-api` 的 ~78-85 s **不是迁移造成的**——首次 78.1 s、二次 84.4 s，无差异。
> 它是 Dify 1.17.1 的固有启动开销。**所以"预先跑迁移"省不下启动时间**，
> 3 分钟目标要保的是"不要在这条链上再叠串行依赖"，而不是优化迁移。

### 3.3 两条就绪判据的硬要求（都是本轮踩出来的）

| 判据 | 错误写法 | 后果 | 正确写法 |
| --- | --- | --- | --- |
| postgres 就绪 | 只判 `pg_isready` | **实测**：docker-entrypoint 会先起临时实例跑 `initdb.d`（此时 `pg_isready` 为真），随后停掉再起正式实例；`plugin_daemon` 在窗口内连上又被断开 → **`panic: failed to init dify plugin db`，容器退出**（无 restart 策略时永久不起来） | `pg_isready && psql -tAc "SELECT to_regclass('public.schema_migration') IS NOT NULL" \| grep -q t` |
| keycloak 就绪 | 探 `:8080/health/ready` | **实测 404**。Keycloak 25+ 的健康端点迁到**管理端口 9000** | `--health-enabled=true --http-management-port=9000`，探 `:9000/health/ready` |

其他：
- 9 个服务**全部**加 `restart: unless-stopped`（`plugin_daemon` 那个 panic 在标准栈是靠 restart 自愈的，轻量版不能省）。
- 有状态服务 100% 配 healthcheck；`plugin_daemon` / `dify-worker` 无就绪语义，只做进程存在判定（已在表中标注）。

---

## 4. 容器总数与整机内存峰值 → 8G 够不够

### 4.1 总数

| 口径 | 数字 |
| --- | --- |
| 常驻容器 | **9** |
| 可选容器（`dify-web`，profile `admin`） | 1（不常驻） |
| 标准栈对照 | 21（编排定义）/ 常驻 20（`keycloak-import` 为一次性 `install` profile） |

**对外统一写法（Q-1 拍板）**：

> 平台 4 个容器（应用 / 数据库 / 缓存 / 反向代理）**+ 内置 AI 能力通道**，共 **9 个容器**。

禁止写"4 容器"。原因见 §1.1 第 6 行：**身份提供方也是容器**，且它是单机第一内存消费者。

### 4.2 整机内存峰值

| 场景 | docker stats（主口径） | cgroup（上界） | 说明 |
| --- | --- | --- | --- |
| 冷启动峰值 | **1969.4 MiB (1.92 GiB)** | 2303.6 MiB (2.25 GiB) | 含 Dify/PG 初始化的一次性开销 |
| 空载稳态 | **1927.2 MiB (1.88 GiB)** | 2293.5 MiB (2.24 GiB) | 容量规划用这个 |
| 链路后（+5 次 M1） | 1937.2 MiB（= 稳态 + 10.0） | ≈2303.5 MiB | 增量可忽略 |
| **建议按此规划** | **≤ 2.5 GiB** | ≤ 3.0 GiB | 留 20% 抖动余量 |

### 4.3 "8G 够不够" —— 结论

| 整机内存 | 结论 | 余量（按 2.5 GiB 规划值） |
| --- | --- | --- |
| **8 GB** | **够用，推荐为最低承诺配置** | ≈5.5 GiB（69%） |
| 4 GB | **技术上够**（2.5 GiB 留 1.5 GiB），但**不建议承诺**：知识库文档量上去后 PG 与 Dify 会增长，且宿主要给 OS/Docker 留空间 | ≈1.5 GiB（38%） |
| 2 GB | **不够** | 负 |

**增长项（必须写进交付说明，避免"承诺 8G 却 OOM"）**：
1. `dify-api` / `dify-worker` 随**知识库文档量与分段数**线性增长（本次测的是 0 文档空库）；
2. `postgres` 的 `shared_buffers`（本测 128 MB）与连接数（本测 `max_connections=200`）按客户规模调；
3. 并发：本次只测了串行 5 次请求；**并发 100 以上的峰值未测**（Q-3：轻量版不承诺并发指标，只披露实测）；
4. `keycloak` 的 `-Xmx384m` 撑 ≤500 用户；再大会 GC 抖动，需上调并按 §2.5 重测。

---

## 5. 所测版本与结论失效条件

### 5.1 被测版本矩阵

| 组件 | 版本 | 关键属性 |
| --- | --- | --- |
| Dify | **1.17.1**（`langgenius/dify-api:1.17.1`，digest `ceede5b9…`） | 向量后端以 **workspace package + entry point** 形式内置（`api/packages/dify-vdb-*`）；`plugin_daemon 0.6.10-local` |
| PostgreSQL | **18.6**（`pgvector/pgvector:pg18`，deb 包 `18.6-1.pgdg12+2`） | 镜像内 pgvector **0.8.x**（extension 目录 43 个 vector 文件） |
| Keycloak | 26.8.1 | `start`（非 `--optimized`，首次必须不带该参数，否则 `--optimized was used for first ever server start` 直接退出） |
| Valkey | 9.2-alpine | db0/db1/db2 三库复用 |
| 适配层 / 前端 | `officeai/adapter:1.0.0` / `officeai/web:1.0.0` | 基线 `2bdfa19` + 在途整改 |
| 编排 | Docker Compose v2 / Docker 29.9.1 | 独立项目 `oalite` |
| 宿主 | Windows + Docker Desktop，VM 15.53 GiB | 非 Linux 裸机；**cgroup/内存统计口径在 Linux 裸机上应有 ±5% 差异，需复测确认** |

### 5.2 结论失效条件（任一命中即须重测，不得沿用本文件数字）

| # | 条件 | 影响 |
| --- | --- | --- |
| L-1 | Dify 升级（1.17.1 → 其他） | 向量后端形态、`plugin_daemon` 依赖、启动时长（78-85 s）全部可能变化 |
| L-2 | `pgvector/pgvector` 换 tag（如 `pg18` → `0.8.7-pg18-bookworm`） | PG 补丁版本与扩展版本变化；**镜像 digest 必须同步进 `images.lock`** |
| L-3 | Keycloak 去掉 `JAVA_OPTS_APPEND` 或改 `-Xmx` | 空载内存 797.6 → 2166.8 MiB（+172%） |
| L-4 | 知识库有文档（>0 份）或 Dify 侧有历史会话堆积 | `dify-api` / `dify-worker` / `postgres` 内存上浮；启动时长变长 |
| L-5 | 并发 >1（本测全串行） | 未测；**Q-3 规定轻量版不承诺并发**，只披露实测值 |
| L-6 | 宿主机内存 ≠ 15.53 GiB | JVM 默认堆、PG 自动参数都会漂移；**这就是必须显式设 `-Xmx` 的原因** |
| L-7 | 编排增加了 `ssrf_proxy` / `dify-web` / `prometheus` | 容器数变 10-12，内存 +110-250 MiB |
| L-8 | 启用 `pgvector` 以外的向量后端（切回 Milvus） | 容器数 +3、内存 +471 MiB、镜像 +2.9 GB |

### 5.3 与架构文档的一致性

`docs/architecture-phase4.md` §9.1 的容器清单与内存表**以本文件为准**（该节已标注引用本文件）。
若两者冲突，本文件优先——它是实测，架构文档是设计推算。

---

## 附录 G：测量工件（可丢弃，不进交付物）

| 文件 | 用途 |
| --- | --- |
| `scripts/bench/lite-probe/docker-compose.probe.yml` | 9 服务探针编排（独立项目 `oalite`、65000 段端口、独立卷）。**不是交付编排**：允许硬编码探针端口，禁止被 `install.sh` 引用 |
| `scripts/bench/lite-probe/nginx-probe.conf` | 最小反代配置（含 SSE `proxy_buffering off`） |
| `scripts/bench/lite-probe/probe.py` | 采样与计时器：`cold` / `idle` / `chain` 三个动作；口径与 §2.1 一一对应 |
| `scripts/bench/lite-probe/out/*.json` | 原始采样结果（`cold_firstboot.json` 首次、`cold.json` 二次、`idle.json` 空载、`chain.json` 链路） |
| `scripts/bench/lite-probe/out/probe.env` | 合并后的探针环境（`deploy/.env` + 本地生成的两个内部密钥）。**含明文口令**：本轮测完已**删除**，复跑时由 `probe.py` 现场重建。`scripts/bench/lite-probe/out/` 必须进 `.gitignore`（交给 T4-05.4 一并处理） |

复现命令：

```bash
cd scripts/bench/lite-probe
python probe.py cold                     # 冷启动（自动生成 out/probe.env）
python probe.py idle --sec 40            # 空载
docker compose --env-file out/probe.env -p oalite -f docker-compose.probe.yml down -v
```

> 清理要求：测完必须 `down -v`，否则 9 个容器会长期占用宿主内存并污染后续测试。
> 本轮测完已清理（`docker ps | grep oalite` 应为空）。

---

*作者：高见远（架构师）· 2026-10-24 · 供许清楚（PRD §5.1 / P4-15 判据）、齐活林（交付口径）、严过关（复测）消费*
