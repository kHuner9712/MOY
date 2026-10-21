# 第一阶段验收报告（底座与适配层攻坚 · 第 1-8 周）

| 项 | 内容 |
| --- | --- |
| 报告版本 | v1.0 |
| 验收日期 | 2026-10-21 |
| 验收方式 | 全栈实跑（Docker daemon 29.9.1 实测）+ 适配层真实联调（下游非桩模式）+ 独立复跑门禁 |
| 依据 | `docs/plan-v1.0.md` 第一阶段排期；`docs/architecture-v1.0.md` 第 3/6/8/9 章 |
| 环境 | Windows + Docker Desktop；私有化编排 `deploy/docker-compose.yml`；15 个容器全部 Up |

---

## 一、结论：**第一阶段验收通过**（附 3 项有条件项，见第六节）

第一阶段三大硬验收点全部实跑达成：
1. **底座核心功能跑通** —— Dify 1.0.0 全家桶 + Milvus + Keycloak + SeaweedFS 15 容器实跑，Dify API `/health` 返回 `{"status":"ok"}`，工作流真实执行 `succeeded`；
2. **所有原子能力均可通过适配层标准接口调用** —— 适配层以 `OA_DOWNSTREAM_ENABLED=true` 真实联调，A1→Dify `/v1/workflows/run` 真实执行、A3 终态正确映射、U1 鉴权校验、M3 档位目录、幂等重放，**6/6 集成用例通过**；
3. **全链路基础流程打通** —— 前端（web 容器，3 页面 200/307 符合预期）→ 适配层（30 端点）→ 底座（Dify/Milvus/Keycloak/SeaweedFS/PostgreSQL/Valkey）全栈实跑。

---

## 二、逐项对照（总方案第一阶段排期）

### 2.1 第 1-2 周：底座部署与接口规范

| 交付物 | 状态 | 证据 |
| --- | --- | --- |
| 可运行的底座环境 | ✅ | 15 容器 Up（5 healthy）；Dify `/health` ok v1.0.0；Milvus healthy；工作流 run `succeeded` |
| 接口规范文档 v1.0 | ✅ | `docs/architecture-v1.0.md` 第 3 章：30 端点、38 错误码、`{code,message,data}` 统一响应；QA 双轮逐字核验一致 |
| 项目脚手架与代码规范 | ✅ | ruff（0.17.1）/ ruff format / mypy / pytest 全绿；`git log` 规范提交 |

### 2.2 第 3-5 周：适配层开发与联调

| 交付物 | 状态 | 证据 |
| --- | --- | --- |
| 适配层核心服务 | ✅ | 30 端点（M1-M4/R1-R7/A1-A6/T1-T7/U1-U6）+ 2 探针；**417 单元/集成用例通过**；覆盖率 91.01%（门禁 80%） |
| 适配层与底座能力联调 | ✅ | **本轮首次达成**：真实模式 6/6 联调用例（`qa_probe/integration_real.py`）；错误映射路径实测（Dify 400 → 10001 业务文案，内部细节零泄露） |
| 前端基础框架与组件库 | ✅ | Next.js 14 构建通过；web 容器 Up healthy；`/knowledge` `/login` 200、`/` 307（未登录跳转，符合预期） |
| 联调测试报告 | ✅ | 本报告第三节 |

### 2.3 第 6-8 周：基础权限与全链路

| 交付物 | 状态 | 证据 |
| --- | --- | --- |
| 基础用户权限系统 | ✅ | U1-U6 端点 + Keycloak 25.0.6 实跑；OIDC 验签 5 场景回归用例（Sprint 0 QA 要求项） |
| 可演示的基础对话 + 知识库 Demo | ⚠️ 部分 | 前端对话/知识库页面实跑可访问；对话主链路受模型提供方限制（见第六节 C-1） |
| 第一阶段验收报告 | ✅ | 本报告 |

---

## 三、适配层-底座真实联调证据（6/6）

执行：`apps/adapter/qa_probe/integration_real.py`（适配层容器 `downstream_enabled=true`，Dify 已发布含 Answer 节点的工作流应用）

| # | 用例 | 结果 |
| --- | --- | --- |
| 1 | healthz（确认真实非桩模式） | ✅ `downstream_enabled=true` |
| 2 | U1 令牌与权限点校验 | ✅ `allowed=true`，用户/租户/角色正确 |
| 3 | M3 模型目录 | ✅ 4 个能力档位登记 |
| 4 | A1 创建任务（真实调用 Dify `/v1/workflows/run`） | ✅ `task_id` 生成，下游 status 200 |
| 5 | A3 任务终态（Dify succeeded → 自研 completed） | ✅ `status=completed` |
| 6 | 幂等重放（同一 Idempotency-Key） | ✅ 20007 重复请求已忽略，返回原任务 |

**错误映射路径实测**：A1 早期因载荷缺 `user` 字段被 Dify 拒（400），适配层统一映射为 `{"code":10001,"message":"AI 暂时没有给出回答，请重试"}`——内部路径、状态码、底座报文零泄露，符合总方案 1.2 技术黑盒化。

---

## 四、本轮实跑发现并修复的缺陷（全部为真实环境才暴露的问题）

### 4.1 适配层缺陷（3 个，T02 真实协议转换层）

| # | 缺陷 | 根因 | 修复 |
| --- | --- | --- | --- |
| INT-01 | A1/M1 调用 Dify 均返回 400 | 载荷缺 Dify 必填 `user` 终端用户标识（Dify ≥ 0.6 强制） | `agent_service.create_task` 补 `auth.user_id`；`model_service` 签名透传 `user_id`（M1/M2/载荷构造） |
| INT-02 | A3 查询任务 404 | Dify 端点为 `/v1/workflows/run/{id}`（单数），适配层调 `/runs/`（复数） | 两处调用路径修正 |
| INT-03 | blocking 模式任务永停 `running` | Dify blocking 同步返回终态未映射 | `create_task` 按 `data.status` 映射自研状态与进度 |

### 4.2 部署编排缺陷（8 个）

| # | 缺陷 | 根因 | 修复 |
| --- | --- | --- | --- |
| INF-01 | dify-api/worker 启动失败 | OpenDAL S3 缺 `region` | 两处 compose 补 `OPENDAL_S3_REGION`（默认 us-east-1） |
| INF-02 | Dify 建号失败（回落云元数据 169.254.169.254） | 凭证环境变量名错误：Dify 的 `_get_opendal_kwargs` 把 `OPENDAL_S3_ACCESS_KEY` 转成 `access_key`，而 OpenDAL S3 驱动要 `access_key_id` | 改名为 `OPENDAL_S3_ACCESS_KEY_ID` / `OPENDAL_S3_SECRET_ACCESS_KEY`（两份 compose 共 4 处） |
| INF-03 | dify/keycloak 库不存在 | 统一 postgres 只建了 officeai 库 | 新增 `deploy/init-databases.sql`（dify/keycloak/dify_plugin），两份 compose 挂载 initdb |
| INF-04 | Keycloak 崩溃循环 | `start --optimized` 跳过运行时增强，postgres JDBC 驱动未注册 | 去掉 `--optimized`（两份 compose） |
| INF-05 | squid 崩溃循环（No forward-proxy ports） | squid.conf 缺 `http_port` | 补 `http_port 3128` + 三段式访问规则（先挡 SSRF 目标、再放行容器网络、最后拒绝外网） |
| INF-06 | SeaweedFS 容器恒 unhealthy | 健康检查用 `curl`，BusyBox 镜像无 curl（S3 网关实际跨容器可达，netstat 显示具误导性） | 改 `wget` + 显式 `127.0.0.1`（busybox wget 解析 localhost 会走 IPv6 误报） |
| INF-07 | 模型提供方 API 500 | Dify 1.0 提供方管理由插件守护进程代理，栈中缺失 | 新增 `plugin_daemon` 服务（官方 1.0.0 同款 0.0.3-local，独立库 dify_plugin，端口 5002，`PLUGIN_WORKING_PATH` 必填） |
| INF-08 | web 镜像构建失败 | `next.config.js` 未开 `output: 'standalone'` | 开启 standalone 输出 |

### 4.3 合规修复（承接 QA 附录 A，随 D-06 拍板落地）

- P1-1 Grafana：AGPL-3.0 且 AGPL 前版本线（7.5.x）在 Docker Hub 已不存在（实测 404）→ **V1.0 移除**，监控留 Prometheus；
- P1-2 Dify：协议标注更正为 Apache-2.0 修改版 + 三条规避约束（SaaS 不用多租户/不改代码/不展示品牌）；
- P2-4：适配层 `minio_*` 配置项改名 `object_store_*`（config/compose/.env.example/validate_images/架构文档五处同步）。

---

## 五、门禁独立复跑记录（2026-10-21）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .`（0.17.1） | ✅ All checks passed（62 文件） |
| `ruff format --check .` | ✅ 62 files already formatted |
| `mypy app` | ✅ Success: no issues found in 43 source files |
| `pytest` | ✅ **417 passed**（覆盖率 91.01%，门禁 80%） |
| `docker compose config`（双编排） | ✅ 零告警 |
| `validate_images.py` | ✅ 13 引用 = 13 登记 |
| `next lint` / `tsc --noEmit` / `next build` | ✅ 全绿（web 容器 healthy） |
| 真实联调 | ✅ 6/6（`qa_probe/integration_real.py`） |

---

## 六、有条件项与已知限制（不阻塞阶段门关闭，列入 Sprint 1 首批处理）

| # | 项 | 说明 | 处置 |
| --- | --- | --- | --- |
| C-1 | **M1/M2 对话主链路 happy path 未闭合** | Dify 1.0 模型提供方为插件化，市场 API（marketplace.dify.ai）本环境不可达；已用「工作流 Answer 节点」验证 A 链路全通，M 链路的请求/响应/SSE 协议转换代码路径就绪但缺真实模型提供方 | 用户提供任一模型 API 密钥（通义千问/豆包/DeepSeek）后安装提供方插件复测；或私有化场景随离线包预置提供方（Sprint 3/4 范围） |
| C-2 | **RAG 链路（R1-R7）真实联调未覆盖** | Dify 数据集 API 需 dataset 级密钥（非 app key），适配层单默认密钥设计需 T02 明确 per-kb 密钥机制 | Sprint 1 与 D-02（业务库归属）一并设计 |
| C-3 | **前端深层交互未做浏览器级验证** | 页面可达性已验证（200/307），组件级交互走查待组件库深化（T03，D-08 已拍板） | T03/S2 |

---

## 七、结论与放行

第一阶段（第 1-8 周）交付物**全部达成或达成附条件项**，三大硬验收点实跑通过，质量门禁全绿，实跑修复 11 个缺陷（3 适配层 + 8 部署编排）。**准予进入第二阶段（第 9-16 周：基础功能全量对齐）**，C-1/C-2/C-3 纳入 Sprint 1 任务池。

**放行条件**：C-1 在模型密钥到位或离线包方案确定后闭环；C-2/C-3 按既有排期自然消化。

---

*报告结束。联调脚本：`apps/adapter/qa_probe/integration_real.py`；Mock LLM：`apps/adapter/qa_probe/mock_llm.py`；决策记录：`docs/decisions/0004-phase1-open-decisions-ratification.md`。*
