# 整改台账（2026-11-02 第三轮审计 46 条 + 阻断项 R1-R8）

| 项 | 内容 |
| --- | --- |
| 文档 | `docs/remediation-ledger-2026-11-02.md` |
| 版本 | v1.0（2026-11-02） |
| 作者 | 高见远（架构师） |
| 基线 | `main @ ad4cba4` |
| 用途 | **轮 2 开工前的对账工具**。防止重复劳动（把已修的再做一遍）与漏项（以为做了其实没做） |
| 上游 | `docs/audit-2026-11-02-full-repo-audit.md`（46 条）、`docs/design-2026-11-02-security-deploy.md`、`docs/design-2026-11-02-keycloak-realm.md`（R1-R8） |

---

## 0. 状态定义与核验口径

| 标记 | 含义 | 判定依据 |
| --- | --- | --- |
| ✅ **已修** | 代码已改并有提交 | 本文件核验时**实际读码/实跑确认**，并给 commit 或文件行号 |
| ⬜ **待做** | 未修，已排期 | 标出轮次或"未排期" |
| ⊘ **已豁免** | 决定不做，附理由 | 由主理人拍板或有书面论证 |
| ⚠️ **已知残留** | 已评估并有意接受 | 见主设计文档 §4.4 |
| ❓ **待复核** | 本文件**无法静态判定**，需实跑或需产品决定 | 明确写出"需要什么才能判定"，不猜 |

> **口径纪律**：本台账**只写核验过的东西**。凡标 ❓ 的，都是"审计有结论但我这次没能独立确认"的项，
> **不是**"已修"也**不是**"未修"。轮 2 对账时请先跑 §6 的复核命令再决定是否要动。
>
> **计数说明**：下表汇总数只作**量级参考**，行级标记才是准的。台账是滚动更新的活文件，
> 每轮落地后会有若干条从 ⬜ 变 ✅，不必为了"总数对得上"去凑数字。

---

## 1. 汇总

| 分类 | 条数 | 说明 |
| --- | --- | --- |
| ✅ 已修 | 12 | 含 P0×4、P1×5、P2×3 |
| ⬜ 待做（有轮次） | 3 | **轮 1b**：P0-5；**轮 2**：P0-4、P2-09。（R4/R5/R8 已于轮 1a 落地，见 §2） |
| ⬜ 待做（未排期） | 15 | 性能债、测试债、供应链、工程债 |
| ⊘ 已豁免 | 3 | R1、R3、R6（+ P1-16 口径） |
| ⚠️ 已知残留 | 5 | 见设计文档 §4.4 |
| ❓ 待复核 | 13 | 需实跑门禁/压测/产品决定 |
| **合计** | **46（审计）+ 8（阻断项）** | — |

---

## 2. 阻断项 R1-R8

| # | 主题 | 状态 | 依据 / 落点 |
| --- | --- | --- | --- |
| **R1** | CSP 仍含 `unsafe-inline` | ⊘ **已豁免**（主理人 2026-11-02 拍板） | 论证见主文档 §4.4 残留 1。**下一轮若报为"新问题"，请先对照该节** |
| **R2** | 是否存在已交付客户实例 | ✅ **已定**：按无客户实例处理 | 一次性硬登出，不做迁移端点（主文档 §2.4.7） |
| **R3** | `img-src` 允许任意 `https:` | ⊘ **已豁免** | 主文档 §4.4 残留 2。令牌已读不到，仅非凭据 DOM 内容可带出 |
| **R4** | Keycloak issuer 浏览器不可达 | ✅ **已落地（轮 1a，K4-min）** | 实测核对：三套编排均新增 `edge`（lite 沿用 `proxy`）只映射 80；`edge.conf` 含 `^~ /realms/` + `^~ /resources/`；`KC_HOSTNAME` 三套均注入（lite 取 `OA_LITE_PUBLIC_URL`、标准栈取 `OA_PUBLIC_BASE_URL`）+ `KC_PROXY_HEADERS: xforwarded`；full 栈 `keycloak` 已加 `officeai-frontend` 网（现为双网）；`OA_WEB_OIDC_ISSUER` 已由基址派生。⚠ **仍需现场验一次真实登录**（判据见专题文档 §4.2 R4-①~⑤），静态核对不能替代"浏览器真能打开登录页" |
| **R5** | realm 配置缺失 / 令牌有效期无主 | ✅ **已落地（轮 1a，K5）** | `deploy/keycloak/` 现有 `realm-officeai.json` + `render-realm.sh` + `rendered/` + README；三套编排均有 `keycloak-import` 服务（profile `install`，用 `kcadm get` 探测后创建，未使用 `--import-realm`）。⚠ 渲染产物的域名需现场用真实基址复核 |
| **R8** | JWT 校验 HS256 vs Keycloak RS256 | ✅ **已落地（轮 1a，K6）** | `security.py::resolve_jwks_url` + `get_jwks_client` 已实现；JWKS 走内网 `keycloak_base_url`（不读 `jwt_issuer`，**无回落分支**）；空 base 返回**空串**（不拼相对路径，避免 20002 把排障引向令牌）；启动校验第 6 条（`public_base_url` 必填）+ **第 7 条**（`RS*` + 空 base → 30004，只拦 RS*）；`OA_JWT_SECRET` 校验未放宽。实跑 `pytest tests/unit/test_jwt_jwks.py` → **17 passed**（本轮自跑）。⚠ 见下方"已裁决"行 |
| **R6** | V1.0 是否上 k8s | ⊘ **已定**：V1.0 不上，清单作 V1.1 准备 | `deploy/k8s/README.md` 已改写（`ad4cba4`），旧 HPA 口径已更正 |
| **R7** | 证书来源的交付责任边界 | ✅ **已修（本轮，文档）** | `docs/deployment-guide.md` §12.5 已改写为四小节：12.5.1 两种形态并存（A 前置网关终止=默认推荐 / B 平台自建 TLS=轮 2 交付）、12.5.2 B 形态证书约定（含自签仅限验证 + `chmod 600` + `reload` 不重启）、12.5.3 **`OA_PUBLIC_BASE_URL` 的 scheme 决定 Cookie `Secure`（唯一判定处，收拢原先散在三处的口径）**、12.5.4 按现象处置。§2.3 端口表与 §8 对比表的证书口径同步更新，三处已一致 |

---

## 3. P0（7 项，阻断交付）

| # | 主题 | 状态 | 依据 |
| --- | --- | --- | --- |
| **P0-1** | 12 条测试红灯（8 条时间炸弹） | ✅ **已修** | `8af426e`（"清零8条时间炸弹红灯"）。⚠ 另 4 条为 Windows 下 PG 夹具 `Event loop is closed`，审计自己注明"不随本次修复消失"，**属已知残留而非回归** |
| **P0-2** | `validate_deps.py` 实红（pyjwt 未登记） | ✅ **已修** | `8af426e`；工程侧报告 `validate_deploy` / `validate_secrets` EXIT=0 |
| **P0-3** | `validate_import_directions.py` 裸跑恒红（`DEFAULT_BASELINE` 未使用） | ✅ **已修** | `8af426e`（两处门禁假红） |
| **P0-4** | 无 TLS 终止 + 应用端口全网卡 + k8s 空壳 | ⬜ **待做 · 轮 2**（K4-rest） | 现状复核与设计见主文档 §3。轮 1 只做 K4-min（登录打通所必需）。⚠ 审计"三套 compose 零内存限制"表述**不准确**，实际 saas=0 / full=8 / lite=10，缺口是 saas 全量 + full 缺 11（含 `web`）+ 三套零 CPU——见主文档 §3.1.2 |
| **P0-5** | 令牌窃取链三件套 | ⬜ **待做 · 轮 1b** | 设计已完成（主文档 §2），方案 = httpOnly Cookie / BFF 形态 / 会话端点放适配层。轮 1a 已铺好前置：`edge.conf` 含 `^~ /sso/` 占位、`OA_PUBLIC_BASE_URL` 与派生 issuer 已注入、`Settings.public_base_url` 已加。**1b 待做**：`/sso/*` 端点 + `auth_cookie_enabled` 字段/注入 + 前端 6 文件改造 |
| **P0-6** | 产物下载路径未路由，生产 404 | ✅ **已修** | `e074372`：`lite.conf` / `lite-tls.conf` 增 `location ^~ /files/artifacts/`。⚠ **轮 2 把 lite 配置提升为 `edge.conf` 时不得漏这一条**，否则回归 |
| | | | |
| **P0-7** | 覆盖率默认口径擦线过（+0.72pp 余量） | ⬜ **已定，待执行（单独提交）** | 主理人 2026-11-02 拍板：`ci-adapter.yml` 的 CI 5b（`--include="*/db/repositories/*/postgres.py" --fail-under=50`）阈值 **50 → 88**（实测 90.66%，余量 2.66pp）。**必须带注释写明来源**（"实测 90.66% − 2.66pp"），否则后人会以为 88 是拍的。⚠ **等轮 1a 落地后单独提交**，不要混进 K6 的 diff（保持改动可归因） |
| **已拍板** | **`.env.example` 的 `OA_PUBLIC_BASE_URL` 是否保留字面量** | ✅ **选 ②（2026-11-02）：模板留空，`compose up` 插值阶段即失败** | 同步改 `qa/phase4-verify.sh`。**QA 脚本硬约束（主理人明确）：不许改成"忽略该变量"换绿灯**——正确改法是断言"该键为空时 `compose config` 必须失败"，即门禁断言失败行为而非绕过它。**待落地**：`.env.example:64` 现仍是 `http://localhost`。**被否的 ③（保留 + 启动校验拦 loopback）理由保留在专题文档 §2.4.3**：不是"没考虑到"，而是评估后否决——判据本身在回答一个没有确定答案的问题（`0.0.0.0`/私网 IP/`host.docker.internal` 算不算错？），且会与 `:?` 形成两套平行判据 |
| **已裁决** | **JWKS 是否要"外部 IdP 回落"分支** | ✅ **已裁决（2026-11-02）：不做回落分支** | 理由 = **V1.0 仅支持 Keycloak 形态 IdP（范围声明）**：`keycloak_base_url`/`keycloak_realm`、`INTROSPECT_PATH`、`realm_access` 角色映射四处绑死该假设，换 IdP 要改整条链路。**附加事实**：该分支即使加上也不成立——其产物 `{issuer}/protocol/openid-connect/certs` 仍是 Keycloak 专有路径。**实测可复现**：`curl -s https://accounts.google.com/.well-known/openid-configuration` 的 `issuer` 是 `https://accounts.google.com`、`jwks_uri` 是 `https://www.googleapis.com/oauth2/v3/certs`（**连域都不同**），且直接请求 `https://accounts.google.com/protocol/openid-connect/certs` 返回 **404**。故不存在可拼的固定模板，只能读 discovery 的 `jwks_uri`。 **若要支持非 Keycloak IdP，需 V1.1 完整设计（读 discovery 的 `jwks_uri` 或显式 `OA_JWKS_URL`，并连带改自省/用户资料/角色映射），不是加一个 `if`。** |

---

## 4. P1（21 项）

### 4.1 已修（5 项）

| # | 主题 | 依据 |
| --- | --- | --- |
| **P1-08** | 产物下载 `inline` + 任意 MIME + 无 nosniff（同源 XSS 面） | ✅ `e074372`：`Content-Disposition` 改 `attachment`、`SAFE_DECLARED_CONTENT_TYPES` 白名单、响应自带 `nosniff` + `CSP: sandbox`、文件名清 CR/LF。**核验点：与 P0-6 同批修，未打开新面** |
| **P1-09** | `OA_PAY_CHANNEL=fake` 无生产守卫 | ✅ `security.py:1143` `if conf.pay_channel == "fake":` → 30004（已读码确认） |
| **P1-10** | `OA_JWT_SECRET` 模板占位值可通过启动校验 | ✅ `security.py:1062` `JWT_SECRET_PLACEHOLDER_MARKERS` + `:1165` `_jwt_secret_placeholder_reason()` 接入启动校验（已读码确认）；另 `0916c99` 补模板生成路径 |
| **P1-11** | `require_permission` 空 permission 静默 return | ✅ `security.py:450` 起改为 `if not permission.strip():` 抛错并写操作记录（已读码确认） |
| **P1-12** | 前端每次写请求现场生成新幂等键（去重被架空） | ✅ `bd3eb3a`（幂等键生命周期改为按用户动作绑定） |

### 4.2 待做（有轮次，1 项）

| # | 主题 | 轮次 | 说明 |
| --- | --- | --- | --- |
| **P1-01** | 租户全量导出整包常驻内存 + 无并发闸门 | 轮 2（仅容器侧）/ 应用层未排期 | ⚠ 主文档 §3.3.4 只给容器加内存上限（让爆炸范围止于本容器），**应用层的 `BytesIO` 放大与并发闸门未解决**，需独立设计 |

### 4.3 待做（未排期，11 项）

| # | 主题 | 备注 |
| --- | --- | --- |
| **P1-02** | 运营端租户列表全量拉取 + 每行 3 查 | 与已修成员列表同型缺陷换入口复发 |
| **P1-03** | `list_queued` 无 `LIMIT`；仓库层 `SELECT *` 23 处 | 性能债 |
| **P1-04** | `add_artifacts` 逐条事务（N 条 = N 次 commit） | 性能债；`create_steps` 属有意保留、不计 |
| **P1-05** | 基础设施镜像补丁级浮动 + compose 不校验 digest | ❗**已核验仍未修**：`grep -c "@sha256" deploy/docker-compose.yml` → **0**；`images.lock` 有 20 条 digest 但运行时按 tag 引用 |
| **P1-06** | `'use client'` 覆盖 87%、零列表虚拟化、三页轮询 | 前端架构债 |
| **P1-13** | `components/workbench` 整目录 0 覆盖（接缝不可见） | 测试债 |
| **P1-14** | `tests/e2e` 默认命令不执行 | 测试有效性债 |
| **P1-17** | `.env.example` 弱默认用户名 | ❗**已核验仍未修**：`.env.example:66` `OA_POSTGRES_USER=officeai`、`:94` `OA_OBJECT_STORE_ACCESS_KEY=officeai`、`:171` `OA_KEYCLOAK_ADMIN=admin` |
| **P1-18** | 11 条迁移仅 7 条有 down SQL（`0002`-`0005` 缺） | ❗**已核验仍未修**：`ls deploy/migrations/rollback/ \| wc -l` → 7 |
| **P1-20** | 三处小 N+1 | 性能小项 |
| **P1-21** | 前端 12 项依赖落后（8 个大版本跨越） | 依赖治理；后端 10 项仅补丁级 |

### 4.4 待复核（4 项）

| # | 主题 | 需要什么才能判定 |
| --- | --- | --- |
| **P1-15** | 覆盖率棘轮阈值 = 实测 −0.3pp（只能防倒退） | 产品决定是否改棘轮策略 |
| **P1-16** | 9.5%（235/2465）PG 用例默认不执行 | 审计自述"单一原因 skip 合理"→ 倾向 ⊘ 接受；但需确认"生产主路径是否在本地防线内" |
| **P1-19** | 灾备"有实现、无证据"（无演练记录；`rollback.sh` 按 tag 回退） | 需一次真实演练留档；与 P1-05（digest）耦合 |
| **P1-07** | about 页向终端用户披露技术选型（产品红线） | ❗**已核验仍未修**：`about/page.tsx:35` `pgvector`「记忆检索所需的数据库扩展」、`:39` `Dify`「工作流底座」。属**产品口径决定**，非单纯代码问题 |

---

## 5. P2（18 项）

### 5.1 已修（3 项）

| # | 主题 | 依据 |
| --- | --- | --- |
| **P2-02** | `redact()` 死代码，未接入 structlog processors | ✅ `logging.py:117` `_redact_event_dict` 已作为 processor 挂入（`:177`），注释显式标注 P2-02（已读码确认） |
| **P2-05** | `ruff format --check` 4 文件红灯而 README 称"全绿" | ✅ `e074372`（"ruff format 388"）。⚠ 本次核验期间**观测到一次瞬时红（1 文件）随即复绿**，期间有并行改动，**不判定为回归**；以 CI 结果为准 |
| **P2-06** | `main.py` 前后写"30 个端点"与"99 个操作"自相矛盾 | ✅ `main.py:13` 已改为"96 个端点：OpenAPI 99 个操作"，与 `:28` 一致（已读码确认） |

### 5.2 已知残留（与设计文档 §4.4 对应）

| # | 主题 | 归属 |
| --- | --- | --- |
| **P2-09** | 产物下载限流按 `client.host`，反代后共享一个令牌桶 | ⚠ 轮 2 处置：`OA_TRUSTED_PROXY_CIDRS` + `client_ip()`（主文档 §3.3.3）。**轮 1 加边缘后此问题会实际发生**（租户全站 60/min），但影响有界、不阻塞登录，故随轮 2 |

### 5.3 待做（未排期，9 项）

| # | 主题 | 核验状态 |
| --- | --- | --- |
| **P2-01** | 超大文件 >800 行 20 个 | 未核（结构性债） |
| **P2-03** | mypy `strict=false` 下达成 0 error；`Any` 451 处 | 未核（披露类） |
| **P2-04** | 前端 `KnowledgeBase` 3 个必填字段声明为可选 | 未核 |
| **P2-07** | `toBusinessMessage` 对非 `BusinessError` 透传 `error.message` | ❗**疑似未修**：`business-errors.ts:226` 仍有 `return error.message`；需确认是否新增了分支 |
| **P2-08** | 上传只校验扩展名（无魔数/净化） | 未核；审计已确认无路径穿越、无 RCE，属间接风险 |
| **P2-10** | `jwt_algorithm: str` 未用 `Literal` | ❗**已核验仍未修**：`config.py:71` 仍是 `str` |
| **P2-11** | `pg_dump` 把含密码的 DSN 作命令行参数 | 未核；审计已确认无命令注入 |
| **P2-12** | `deploy_mode` 默认 `saas` 与交付预设 `private` 不一致 | 未核 |
| **P2-13** | `SkeletonLoader` 从未被测；三态门禁只查"有没有" | 未核 |

### 5.4 待做（工程/供应链，4 项）

| # | 主题 | 核验状态 |
| --- | --- | --- |
| **P2-14** | `.git` 3.15 GiB 垃圾（41 个 `tmp_obj_*` + 悬空对象） | ❗**已核验仍未修，已完成量化（附录 A）**：`size-pack 68.21 MiB` / `size-garbage 3.15 GiB`；不可达 2009 blob/34 tree/18 commit；stash 3 条。**关键结论：3.15 GiB 全部来自 41 个 `tmp_obj_*`，删它们不碰历史、不可逆面为零；而 `git gc --prune=now` 收益上限仅几十 MiB 却是唯一不可逆操作。建议只删 `tmp_obj_*`。** 等用户批准 |
| **P2-15** | 远端仅 `main`，与 README 声明的 Git Flow 不符 | 未核 |
| **P2-16** | 33 处 `uses:` 全用 tag 未 pin SHA | ❗**已核验仍未修**：实测 27 处 `@v4` + 11 处 `@v5`，**0 处 SHA** |
| **P2-17** | 无门禁校验"新增含 `tenant_id` 的表必须启用 RLS" | 未核；`validate_deploy.py` 有 `rls` 相关检查但需确认是否覆盖"新增表" |
| **P2-18** | `now_iso()` 被复制 7 份，破坏影响面分析 | ❗**已核验仍未修**：`grep -rn "^def now_iso"` → **7** |

---

## 6. 轮次视图（轮 2 开工前按此对账）

### 轮 1（登录打通 + 令牌收口）

| 项 | 内容 |
| --- | --- |
| 阻断项 | R4（K4-min）、R5（K5）、R8（K6） |
| 审计项 | P0-5 |
| 伴随 | K7 服务端会话 + 惰性刷新 |
| **验收边界** | 只能验：登录页可开、Cookie 下发、业务接口 200、令牌不在 JS。**不能验** TLS / 安全头 / 端口 / 资源 |

### 轮 2（形态收口 + 加固）

| 项 | 内容 |
| --- | --- |
| 阻断项 | P0-4（K4-rest：TLS、端口收口、资源限制、k8s） |
| 审计项 | P2-09（trusted proxy）、P1-01 容器侧 |
| 文档项 | **R7**（改写 `deployment-guide.md` §12.5 的证书口径） |

### 未排期（建议按批次）

| 批次 | 项 |
| --- | --- |
| 性能债 | P1-02、P1-03、P1-04、P1-20 |
| 测试债 | P1-13、P1-14 |
| 供应链 | P1-05、P2-16 |
| 工程债 | P2-14（需批准）、P2-15、P2-18、P2-01 |
| 安全纵深 | P2-07、P2-08、P2-10、P2-11、P2-17 |
| 产品决定 | P1-07、P1-15、P1-16 |
| **已定待执行** | P0-7（CI 5b 阈值 50→88，**轮 1a 后单独提交**）、R7（`deployment-guide.md` §12.5 已于本轮改写完成） |
| **等用户批准（不可逆）** | P2-14（`.git` 清理，量化见附录 A；**建议只删 `tmp_obj_*`，不跑 `git gc --prune=now`**） |

---

## 7. 需要实跑才能复核的命令（轮 2 对账前先跑）

```bash
# 门禁（决定 P0-2/P0-3/P2-05 是否真绿）
python deploy/validate_deps.py
python deploy/validate_import_directions.py            # 裸跑，必须 EXIT=0
python deploy/validate_import_directions.py --self-test
python deploy/validate_config_names.py
python deploy/validate_deploy.py
python deploy/validate_secrets.py
cd apps/adapter && python -m ruff check . && python -m ruff format --check . && mypy app

# 测试（决定 P0-1 是否真绿；含 PG 版本才知道 4 条 Windows 残留是否仍在）
cd apps/adapter && pytest -q
cd apps/web && npm run test && npm run typecheck && npm run lint

# 覆盖率口径（决定 P0-7/P1-15/P1-16）
cd apps/adapter && pytest -q --cov=app --cov-report=term-missing
```

---

## 8. 核验中发现与审计不一致之处（勘误）

| 审计原文 | 实测 | 影响 |
| --- | --- | --- |
| "三套 compose `grep mem_limit\|resources:` → **0 命中**" | `docker-compose.yml` 8 处、`lite.yml` 10 处、`saas.yml` 0 处 | 工作量从"三套全补"降为"saas 全量 + full 补 11 个（含 `web`）+ 三套补 CPU" |
| P0-4 隐含"容器以 root 运行" | `adapter/Dockerfile:81 USER adapter`、`web/Dockerfile:90 USER nextjs` | 现状不是 root 运行；缺的是**强制声明**（k8s `runAsNonRoot`）与 `cap_drop` |
| P1-01 把"容器零内存限制"列为放大因素 | 同第一行 | 该放大因素在 full/lite 上不成立，仅 saas 成立 |
| P2-14 "2001 个悬空 blob + 9 个 stash 残留提交" | 实测 `git fsck --unreachable` → **2009 blob / 34 tree / 18 commit**；stash 实为 **3 条** | 数量级一致，明细不同。以本节实测为准 |

---

## 附录 A：P2-14 `.git` 垃圾量化（**非破坏性准备，未执行清理**）

> 本附录只量化"会删掉什么"，供主理人/用户判断。**未执行任何清理操作。**

### A.1 现状（`main @ ad4cba4` 实测）

| 项 | 数值 |
| --- | --- |
| `size-pack`（真实历史） | **68.21 MiB** / 4 packs / 8916 in-pack 对象 |
| 有效松散对象 | 65 个 / 346.95 KiB |
| **`size-garbage`** | **3.15 GiB** / 41 个文件 |
| 不可达对象 | **2009 blob / 34 tree / 18 commit** |
| stash 条目 | **3 条** |
| `.git` 实际体积 | ≈ **3.2 GiB** |

41 个 `tmp_obj_*` 单文件 **65–91 MiB**（`ls` 实测前三个：75.9 / 80.8 / 65.4 MiB），是**中断的传输临时文件**，不是对象。

### A.2 关键区别：3.15 GiB 里有 99% 不需要动历史就能删

这是最值得先讲清的一点——**两种"清理"的风险完全不同**：

| 选项 | 动作 | 回收 | 可逆性 | 风险 |
| --- | --- | --- | --- | --- |
| **① 低风险（推荐先做）** | 只删 `.git/objects/*/tmp_obj_*`（41 个文件） | **3.15 GiB** | 无影响——这些文件**不被任何 ref/对象引用**，删掉对仓库零影响 | 极低。建议先 `git count-objects -vH` 前后对比确认 `size-garbage` 归 0 |
| **② 彻底（需用户明确批准）** | 再跑 `git gc --prune=now` | 额外回收 2009 blob / 34 tree / 18 commit 占用的空间（**上限受 68 MiB pack 约束**，实际省不了多少） | **不可逆**：18 个悬空 commit 永久消失，之后无法从任何方式找回 | 中。若有人指望从这些悬空 commit 找回已删分支，就没了 |

**结论**：体积问题（3.15 GiB）几乎全部由 ① 解决，而 ① **不碰历史、不可逆面为零**。选项 ② 的收益上限只有几十 MiB，却带来唯一的不可逆面。**建议只做 ①**；② 除非用户明确要求，不必做。

### A.3 三条 stash 需要单独决定（`gc` 不会删它们）

stash 被 `refs/stash` 引用，所以 `git gc` **不会**清理它们；要清必须显式 `git stash clear`。三条内容：

| 条目 | 描述 |
| --- | --- |
| `stash@{0}` | On `refactor/wave3-deploy-lint`：WIP B22-01 b批① 软限制改造，**2026-10-27 中断半成品**，自带说明"SyntaxError: 505 ruff errors，**勿直接 apply**" |
| `stash@{1}` | On `develop`：`s2-wip` |
| `stash@{2}` | On `develop`：`pre-b20-dirty` |

**建议**：`stash@{0}` 自己标注了"勿直接 apply"，属废弃半成品，可与其他两条一并由用户确认后清理；但**这是用户决定项**，本附录不建议动作。

### A.4 附带发现

`git count-objects` 在仓库内会产生 41 条 `warning: garbage found` 输出，会污染任何"解析 git 输出"的门禁脚本。若将来有门禁读取 `count-objects`，需先过滤 `warning` 行（本文件 §A.1 的取值已做过滤）。

---

*作者：高见远（架构师）· 2026-11-02 · 供主理人轮 2 对账使用*
