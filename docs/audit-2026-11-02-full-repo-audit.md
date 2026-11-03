# Moy（麦子）全仓审计报告 · 2026-11-02（第四轮 · 整改后复审）

> **审计范围**：架构与目录结构、代码质量与规范一致性、安全漏洞、性能隐患、依赖版本与许可、测试覆盖。
> **审计对象**：`main` 分支 HEAD `5e8ff2b`（2026-11-02 22:46），工作区仅含本报告归档/新增两个文档改动。
> **基线**：`docs/audit-2026-10-31-full-repo-audit.md`（10-31 全仓审计）+ `docs/archive/audit-2026-11-02-full-repo-audit-round3.md`（11-02 第三轮 46 条，原路径本报告接管、原文已归档，git `880df56` 可回溯）+ `docs/remediation-ledger-2026-11-02.md`（整改台账）+ `docs/verify-2026-11-02-fix-batch1.md`（批次 1 独立验证）。
> **方法**：全部实测命令本轮重跑（不复读旧结论）；差分口径为「已修复（对应提交）/ 仍存在 / 新增」三态，已修复项不重复计为问题；密钥类发现只记位置与类型，不复述值。
> **本报告只审计、不含任何代码修复改动。**

---

## 一、总体评价（TL;DR）

**一句话**：两轮整改是**真实落地**的——第三轮的 7 条 P0 高危里 6 条已闭环（含令牌窃取链 P0-5 整体拆除），测试全绿、16 个门禁全 exit 0、依赖双零漏洞；**唯一没动的是部署形态（P0-4）**，它现在是全仓仅剩的 1 条高危，也是唯一的上线阻塞项。

> **⚡ 2026-11-02 夜追记**：G1/M1/M2/M3 四项已按本报告建议清单连夜整改并提交（见 **§九 整改执行记录**），P0-4（G1）代码面已收口、M1 刷新竞态已闭环、M2 阈值已执行、M3 完成 pin SHA 与 **CI 历史首次真实运行取证**（连环抓出 5 处 workflow 存量 bug 并修复）。本报告的 §二/§三 问题清单保留当时判定原文，闭环状态以 §九 为准。

三个必须知道的结论：

1. **验证链恢复可信**。第三轮「12 条红灯 + 两处门禁假绿」已全部清零（`8af426e` 等）：本轮实跑后端 **2527 passed / 235 skipped / 0 failed**（收集 2762），`validate_deps` / `validate_import_directions` 裸跑均 EXIT=0，16 个 `validate_*.py` 全绿。第三轮判定的 P0-1/P0-2/P0-3 逐条复核为真闭环。
2. **令牌链路（第三轮 P0-5）已整体拆除**（`16858d4`→`c1db4f9` 等 9 个提交，+13k 行）：令牌彻底离开 JavaScript（httpOnly Cookie + 服务端 PKCE/令牌交换 + CSRF 三级判定），专项安全审查确认 localStorage/sessionStorage **零令牌残留**、Cookie 属性（HttpOnly/SameSite=Lax/host-only/Secure 只信 `OA_PUBLIC_BASE_URL`）与登出/失效链路均正确。**新增面也带来了新问题**：1 条中危（并发刷新竞态误踢，多标签页现实条件可触发）+ 2 条低危（见 §三）。
3. **部署形态仍是「没成型」**（第三轮 P0-4 原样遗留）：两套主栈 compose 的 `adapter:8000` / `web:3000` 仍裸绑全网卡明文（`docker-compose.yml:688-689`、`docker-compose.saas.yml:492,523`），`edge` 只映射 80 无 TLS（K4-min 口径），`deploy/k8s/` 仍只有一份 README。**上公网前必须先做这一条。**

放行判断：**代码与验证链可放行；SaaS 公网部署不可放行**——阻塞面收敛为 P0-4（TLS/端口/资源/k8s）一条 + 2 条上线前应修的中危（刷新竞态、CI 从未实跑）。

---

## 二、闭环核实（基线问题差分，不再计为新问题）

### 2.1 10-31 基线（H/M/L 共 20 项）

| 基线编号 | 本轮结论 | 复核证据（2026-11-02 实测） |
|---|---|---|
| H1 前端覆盖极薄 | ✅ **已彻底闭环** | `vitest run --coverage` 实测 **87.17 / 81.23 / 82.05 / 88.39**（Stmts/Branch/Funcs/Lines），70 个测试文件全过，较第三轮 86.75% 再升 |
| H2 成员列表 N+1/内存分页 | ✅ **已修复** | `list_members_page` + `links_of_users` 单批 IN（`member_service.py`）；`identity_service.py:253` 已委托，内存 `paginate` 消失 |
| H3 SaaS 基础设施端口暴露 | ✅ **已修复** | milvus 19530 / 对象存储 8333 / admin 23646 全绑 `127.0.0.1`（`docker-compose.saas.yml:157,177,178`）；遗留的 adapter/web 端口另计入 P0-4 |
| M1 Redis 限流窗口 | ✅ **已修复** | `ratelimit.py:160-165` `SET NX EX` + 锚点只写一次 + 真实 TTL |
| M2 排队查询 | ⚠️ **部分** | `_queue_rank` 已 COUNT(*)、`next_queued` 已 LIMIT 1 ✅；`list_queued` 仍无 LIMIT → 并入 M6（P1-03） |
| M3 工作区 55 文件未提交 | ✅ **已闭环** | `git status` 干净（仅本报告两个文档） |
| M4 PG 覆盖盲区 | ⚠️ **部分** | org/role/usage PG 用例已补（`cb9670e`，426 行）；CI 5b 下限 50→88 **已拍板仍未执行** → 并入 M2/口径 |
| M5/M6 口径漂移/门禁差 | ✅ **已修复** | `pyproject.toml` 已含 `fail_under = 85`；`ci-web.yml` 注释与实际阈值一致 |
| M7 令牌链路纵深 | ✅ **已修复（本轮）** | 令牌离 JS（httpOnly Cookie）+ CSP/出站整改经决策豁免 R1/R3（`docs/design-2026-11-02-security-deploy.md` §4.4），详见 §三 新增专项 |
| M8 许可成分 | ✅ **已对齐** | 本轮实测无 GPL/AGPL/SSPL；灰名单 MPL-2.0（certifi/pathspec）、PSF-2.0、MIT-0 |
| L1 超大文件 | ❌ **未修且略增** | 非测试源文件 >800 行 **24 个**（第三轮 20 个口径含空行差异）；`api-client.ts` 1887、`model_service.py` 1734 → 计入 P2-01 |
| L2 循环依赖 3 组 | ⏸ **未到期** | `validate_import_directions` 裸跑 EXIT=0「基线内 23 / 外 0，豁免未到期（B23-B27）」 |
| L3 `require_permission` 空放行 | ✅ **已修复** | `security.py:566` 空/空白权限抛 30004；`policy_service.py:604-611` `has_point("")` fail-closed（批次 1 跟进项 N1 也已闭环） |
| L4 密钥扫描扩面 | ✅ **已修复** | `validate_secrets` 扫 `.yml/.yaml/.sh/.example`，本轮 EXIT=0 |
| L5 镜像 digest | ❌ **未修** | compose `@sha256` 引用计数 **0**（`images.lock` 有 20 条 digest 但运行时不校验）→ P1-05 |
| L6 三处小性能 | ✅ **全部修复** | `rag.py` to_thread / `segment_service` 强引用 / SSE refetch 节流（`8626a72`） |
| L7 依赖大版本落后 | ❌ **未修且+2** | `npm outdated` **14 项**（8 个大版本）→ P1-21 |
| L8 node_modules 幽灵包 | ✅ **已清理** | `npm ls --all` extraneous/invalid **0 条** |
| L9 裸跑 1 红 | ✅ **已修复** | 16 个门禁本轮裸跑全 EXIT=0（含 `validate_import_directions`、`validate_batch_clock` 带 OA_BATCH） |

### 2.2 第三轮 46 条（P0/P1/P2）+ 阻断项

| 第三轮编号 | 本轮结论 | 复核证据 |
|---|---|---|
| **P0-1** 12 条测试红灯/时间炸弹 | ✅ **已修**（`8af426e`） | `test_job_runner.py` **15 passed**；本轮全量 **0 failed**；批次 1 QA 独立验证 2472/0 |
| **P0-2** `validate_deps` 实红 | ✅ **已修**（`8af426e`） | 本轮裸跑 EXIT=0「依赖门禁通过」 |
| **P0-3** `validate_import_directions` 裸跑恒红 | ✅ **已修**（`8af426e`） | 本轮裸跑 EXIT=0（基线内 23 / 外 0 / stale 0） |
| **P0-4** 无 TLS + 端口全网卡 + k8s 空壳 | ❌ **仍存在（唯一高危遗留）** | 见 §三 G1 |
| **P0-5** 令牌窃取链三件套 | ✅ **已闭环**（`16858d4`/`3640e30`/`c1db4f9` 等） | 令牌离 JS、Cookie 属性正确、CSRF 三级判定；专项审查 8 个安全点逐条核实（§三 注） |
| **P0-6** 产物下载 404 | ✅ **已修**（`e074372`） | `deploy/nginx/*.conf` `/files/artifacts` 路由 **8 处** |
| **P0-7** 覆盖率口径擦线 + CI 5b 阈值 | ⚠️ **仍存在，部分缓解** | 见 §三 M2 |
| P1-01 租户导出内存/并发闸门 | ❌ 仍存在 | `tenant_data_service.py` BytesIO 整包 + 无信号量；saas compose 零资源限制 |
| P1-02 运营租户列表全量+每行 3 查 | ❌ 仍存在 | `ops_service.py:232` 仍全量拉取后内存切页 |
| P1-03 `list_queued` 无 LIMIT | ❌ 仍存在 | `task/postgres.py:455-463` 仍 `SELECT *` 无 LIMIT |
| P1-04 `add_artifacts` 逐条事务 | ❌ 仍存在 | `artifact_repo.py:441` 循环内 `tenant_scope` |
| P1-05 镜像浮动 + 无 digest | ❌ 仍存在 | `@sha256` 计数 0 |
| P1-06 `use client` 87%/虚拟化/轮询 | ❌ 仍存在 | 实测 `'use client'` **81 / 93** 非测试 tsx |
| P1-07 about 页技术披露 | ❌ 仍存在 | 产品口径决定项，代码未动 |
| P1-08 产物下载 XSS 面 | ✅ **已修**（`e074372`） | `artifact_files.py:91-97` 自带 `nosniff`+`sandbox`、`:136-139` 改 `attachment`、MIME 白名单 |
| P1-09 `pay_channel=fake` 守卫 | ✅ **已修** | `security.py:1285` fake → 30004 |
| P1-10 JWT 占位值校验 | ✅ **已修** | `security.py:1192-1215,1307-1314` 占位标记 + 启动拦截；`validate_secrets` 21 项占位值均有生成路径 |
| P1-11 空权限静默放行 | ✅ **已修** | `security.py:566` 抛错 + 写操作记录 |
| P1-12 幂等键被架空 | ✅ **已修**（`bd3eb3a`） | 幂等键按用户动作绑定 |
| P1-13 workbench 子组件 0 覆盖 | ❌ 仍存在 | 本轮覆盖率实测 `confirm-bar/action-form/file-picker/help-panel/submit-bar/fact-card` 均 **0%** |
| P1-14 e2e 不执行 | ⚠️ **口径更新** | `ci-web.yml:346-406` 已有 playwright 门禁 5 接线，但 **CI 从未实跑**；本地默认命令仍不含 e2e |
| P1-15 棘轮贴实测 | ❌ 仍存在 | 阈值 86.4/80.4/81.1/87.6 vs 实测 87.17/81.23/82.05/88.39（~0.8pp 余量），策略未改 |
| P1-16 PG 用例默认 skip | ❌ 仍存在（合理保留） | 本轮 235 skip（收集 2762 的 8.5%） |
| P1-17 弱默认用户名 | ❌ 仍存在 | `.env.example:119,147,224`（officeai/officeai/admin） |
| P1-18 迁移回滚链不全 | ❌ 仍存在 | `deploy/migrations/rollback/` 仍 **7** 个（缺 `0002`-`0005`） |
| P1-19 备份恢复无演练证据 | ❌ 仍存在 | `docs/drills/` 不存在 |
| P1-20 三处小 N+1 | ❌ 未核全部（1 处已核仍在） | `knowledge_base_repo.py` 内存后端逐条 count 等 |
| P1-21 依赖大版本落后 | ❌ 仍存在 | `npm outdated` 14 项 → §五 量化表 |
| P2-01 超大文件 | ❌ 略增 | 24 个非测试文件 >800 行 |
| P2-02 `redact()` 死代码 | ✅ **已修** | `logging.py:9-11` `_redact_event_dict` 已挂 processor 链 |
| P2-03 mypy 非 strict | ❌ 仍存在（披露类） | `strict=false` + 全局 `ignore_missing_imports` |
| P2-04 前端契约可选化 | ❌ 仍存在 | `types.ts` vs `schemas/rag.py` 必填口径差 |
| P2-05 `ruff format` 红灯 | ✅ **已修** | 本轮 `--check` EXIT=0，399 files formatted |
| P2-06 main.py 数字矛盾 | ✅ **已修** | 「96 个端点 / 99 操作」口径一致 |
| P2-07 非 BusinessError 透传 | ❌ 仍存在 | `business-errors.ts:226` 仍 `return error.message` |
| P2-08 上传仅扩展名校验 | ❌ 仍存在 | 无魔数/文件名净化（已核无路径穿越） |
| P2-09 下载限流共享桶 | ❌ 仍存在 | 按 `client.host` 计；轮 1 加边缘后会实际发生 |
| P2-10 `jwt_algorithm` 无 Literal | ❌ 仍存在 | `config.py:71` 仍 `str` |
| P2-11 pg_dump DSN 入参 | ❌ 仍存在 | 已核无命令注入，仅同机信息暴露 |
| P2-12 `deploy_mode` 默认口径 | ❌ 仍存在 | 与交付预设 `private` 不一致（`saas` 更严，不构成放宽） |
| P2-13 骨架屏 0 测试 | ❌ 仍存在 | `SkeletonLoader` 测试引用 **0** |
| P2-14 `.git` 3.15GiB 垃圾 | ✅ **基本闭环（新进展）** | 本轮 `git count-objects -vH`：**size-garbage 0 bytes**、size-pack 6.44 MiB；历史未改写（`e3734a0` 仍在）；**残留 3 条 stash** 待清理 |
| P2-15 分支策略不符 | ❌ 仍存在 | 远端仅 `main` |
| P2-16 actions 未 pin SHA | ❌ 仍存在 | `uses:` **0/38** SHA-pinned |
| P2-17 RLS 新表门禁缺失 | ❌ 仍存在 | 无「新增含 tenant_id 表必须启用 RLS」校验 |
| P2-18 `now_iso` 7 份副本 | ❌ 仍存在 | `grep '^def now_iso'` 仍 **7** |
| R1/R3（CSP unsafe-inline / img-src） | ⊘ 已豁免（决策留档） | 不计问题；R4/R5/R8（Keycloak 可达性/realm/JWKS）已随轮 1a 落地，R4 仍需现场真登录一次 |
| 批次 1 QA 三条跟进（N1/N3/F7） | ✅ **全部闭环** | N1 `policy_service.py:604-611` fail-closed；N3 `validate_secrets` 模板占位一致性门禁；F7 `_prefix`/`_tail` 已排除出脱敏白名单（`logging.py:60-64`） |

**闭环统计（两轮基线合计 66 项，含豁免/未到期）**：✅ 完全闭环 **26** 项 / ⚠️ 部分闭环 **5** 项 / ❌ 仍存在 **31** 项 / ⊘ 豁免或 ⏸ 未到期 **4** 项。

---

## 三、问题清单（本轮口径：高 1 / 中 16 / 低 13）

> 来源标注：【遗留】= 基线已登记且仍在；【新增】= 本轮（令牌链路改造后）新发现。已修复项见 §二，不在此表。

### 3.1 高（1 项）— 上线阻塞

| 编号 | 来源 | 问题 | 证据 | 影响面 |
|---|---|---|---|---|
| **G1** | 【遗留 P0-4】 | **部署形态没成型：无 TLS 终止 + 应用端口裸绑全网卡 + k8s 空壳**。两套主栈 `adapter`/`web` 仍 `- "8000:8000"` / `- "3000:3000"` 省略 host IP（=绑全部网卡、明文）；`edge`（full:595-604 / saas:412-421）与 lite `proxy` 均只映射 **80**，无 TLS；saas compose 资源限制全缺（full 8 处/lite 10 处 `mem_limit`，saas 0）；`deploy/k8s/` 仅 README。`next.config.js` 的 HSTS 在明文 HTTP 下不被浏览器采纳 | `docker-compose.yml:688-689`；`docker-compose.saas.yml:492,523`；`deploy/k8s/README.md` | 公网部署下全部会话 Cookie（含 `Secure` 降级场景）、业务数据明文过网；429/限流/安全头全部绕过面（直连 8000/3000） |

### 3.2 中（16 项）

| 编号 | 来源 | 问题 | 证据 | 影响面 |
|---|---|---|---|---|
| **M1** | 【新增】 | **并发刷新竞态仍有误踢窗口**：realm `revokeRefreshToken: true`（刷新令牌一次性），`session-gate` 定时 `scheduleRefresh` 直连刷新、不经过 `api-client` 的 `recoveryInFlight` 单飞（单飞亦仅单标签页有效）；多标签页/定时刷新×401 恢复并发时，后到者 `invalid_grant`→20002→前端判 `dead` 强制登出，且 `sso_routes.py:816` 的 `Set-Cookie oa_refresh=;Max-Age=0` 可能覆盖先到者刚轮换的新令牌，会话彻底损毁需重登。已做 0-30s 抖动但 8 小时多标签会话内几乎必然命中一次 | `session-gate.tsx:299-314`；`api-client.ts:553-583`；`auth-refresh.ts:325`；`sso_routes.py:800-817` | 自伤型竞态（外部不可主动触发，CSRF 已拦），但多标签是常态；长会话用户被随机踢出 |
| **M2** | 【遗留 P0-7】 | **覆盖率口径与门禁阈值未按拍板执行**：CI 5b `--fail-under=50` 仍在（拍板 50→88，实测 `postgres.py` 90.66%）；默认无 PG 口径与含 PG 口径差 ~6pp（第三轮 85.72 vs 92.31），「新增 PG 代码不写用例即破 85%」的结构性诱导未消除 | `ci-adapter.yml:599`；`pyproject.toml` fail_under=85 | 门禁阈值失真；PG 主路径默认不在本地防线 |
| **M3** | 【遗留/新视角】 | **CI 全链路从未实跑**：本地 16 门禁 + 测试全绿只代表本机；`ci-adapter.yml`/`ci-web.yml` 的 38 处 action 未 pin SHA（P2-16），且无一次真实 workflow 成功记录可引用 | `git log` 无 CI 结果留档；workflow 文件 | 「本地绿 ≠ CI 绿」；发布判据缺自动化证据 |
| **M4** | 【遗留 P1-01】 | 租户全量导出整包常驻内存（200k 行上限 ≈200MB 峰值）+ 无并发闸门 + saas 容器零 `mem_limit` | `tenant_data_service.py:84,688,705`；saas compose | 并发导出可拖垮宿主 |
| **M5** | 【遗留 P1-02】 | 运营端租户列表全量拉取 + 内存切页 + 每行 3 查（与已修成员列表同型缺陷换入口复发） | `ops_service.py:216,232-247` | 页大小 200 时单请求 1+600 查询 |
| **M6** | 【遗留 P1-03】 | `list_queued` 无 LIMIT 且 `SELECT *`（仓库层 `SELECT *` 23 处/6 文件） | `task/postgres.py:455-463` | 队列积压时线性放大 |
| **M7** | 【遗留 P1-04】 | `add_artifacts` 逐条事务（N 条 = N 次 commit+RTT）；`create_steps` 同型属短事务硬约定有意保留不计 | `artifact_repo.py:441` | 批量写放大 |
| **M8** | 【遗留 P1-05】 | 基础设施镜像补丁级浮动（含 squid `_beta` 通道）+ compose 运行时不校验 digest（`@sha256` 计数 0） | `docker-compose.yml:42,93,415`；`images.lock` | tag 重推供应链风险 |
| **M9** | 【遗留 P1-06】 | 前端 `'use client'` 81/93（87%）、零列表虚拟化、三页 3-5s 轮询多标签叠加 | 实测计数 | SSR/RSC 收益放弃；大列表渲染与稳态请求压力 |
| **M10** | 【遗留 P1-07】 | about 页向终端用户披露技术选型（「Dify 工作流底座」「pgvector 数据库扩展」），超出合规署名必要范围；禁词表不含底座品牌名 | `about/page.tsx:31-48` | 产品红线（技术黑盒化） |
| **M11** | 【遗留 P1-13】 | `components/workbench` 5 个子组件 + `fact-card.tsx` 覆盖 **0%**，页面↔子组件接缝（props/回调签名）单测不可见，tsc 拦不住替身改名 | 本轮覆盖率实测 | 子组件重构无保护网 |
| **M12** | 【遗留 P1-14+M3】 | e2e（12 个 spec）与浏览器级门禁仅有 CI 接线、从未实跑；本地默认命令不含 | `ci-web.yml:346-406` | 前端接缝缺口无兜底 |
| **M13** | 【遗留 P1-17 + 新增】 | 配置默认值口径弱：`.env.example` 弱默认用户名（:119,147,224）；`config.py:51,69` `env/auth_mode` 默认 `development`/`stub`——漏配 `OA_ENV` 的生产部署会以 stub 跑通鉴权（`production`+stub 有启动拦截，`development`+stub 无） | `.env.example:119,147,224`；`config.py:51,69` | 「手抄模板」路径留下弱默认/空鉴权 |
| **M14** | 【遗留 P1-18/19】 | 回滚与灾备「有实现、无证据」：11 条迁移仅 7 条有 down SQL；备份恢复无演练记录（`docs/drills/` 不存在）；`rollback.sh` 按 tag 回退无 digest 校验（与 M8 耦合） | `deploy/migrations/rollback/`（7） | 故障场景下无法整链回退/无法证明可恢复 |
| **M15** | 【遗留 P1-21】 | 依赖大版本落后 8 项（typescript 5.9→7、tailwind 3.4→4.3、zustand 4.5→5、date-fns 3.6→4.4、lucide-react 0.454→1.66、sonner 1.7→2、tailwind-merge 2.6→3.7、@types/node 20→26）+ 补丁级 6 项；后端 11 项可升级（最大 fastapi 0.156.8→0.162.0） | `npm outdated` / `uv pip list --outdated` 实测 | 安全补丁通道被大版本阻塞 |
| **M16** | 【遗留 P1-15/16】 | 覆盖率棘轮阈值贴实测（~0.8pp），只能防倒退；9.5%→8.5%（235/2762）PG 用例默认不执行，生产主路径默认不在本地防线 | `vitest.config.mts:302-307`；pytest skip 明细 | 质量信号与覆盖率数字难归因 |

### 3.3 低（13 项）

| 编号 | 来源 | 问题 | 证据 |
|---|---|---|---|
| **L1** | 【新增】 | 同名 Cookie「首现值优先」+ 无 `__Host-` 前缀 → 子域 cookie tossing 会话固定面：攻击者先控子域写入 `oa_session=<攻击者令牌>; Domain=.example.com`，受害者登录后新旧两条同名 Cookie 并存、浏览器旧者在前，`parse_cookie_header` 取首现值 → 受害者以攻击者身份操作。前置强（需子域控制权）故定低；https 现场应启用 `__Host-` | `session_cookie.py:283-290` |
| **L2** | 【遗留 P2-09】 | 产物下载限流按 `client.host` 共享令牌桶（60/min），加边缘后全租户打满即全员 429 | `artifact_files.py:65-77` |
| **L3** | 【遗留 P2-10】 | `jwt_algorithm: str` 未 `Literal` 约束 | `config.py:71` |
| **L4** | 【遗留 P2-11】 | `pg_dump` 含密码 DSN 作命令行参数（同机 `/proc/*/cmdline` 可读；已核无命令注入） | `db/migrate.py:830-836` |
| **L5** | 【遗留 P2-13】 | `SkeletonLoader` 零测试；三态门禁只查「有没有」不查「对不对」 | 实测引用 0 |
| **L6** | 【遗留 P2-16】 | 38 处 `uses:` 全部 tag 引用、0 处 pin SHA | workflow 实测 |
| **L7** | 【遗留 P2-17】 | 无门禁校验「新增含 tenant_id 的表必须启用 RLS」（人工维护 `strict_tables` 数组） | `init-schema-phase2.sql:631-641` |
| **L8** | 【遗留 P2-18】 | `now_iso()` 7 份逐字相同副本，破坏基于函数名的影响面统计 | `grep '^def now_iso'` → 7 |
| **L9** | 【遗留 P2-01】 | 非测试源文件 >800 行 **24 个**（`api-client.ts` 1887、`types.ts` 1751、`model_service.py` 1734、`security.py` 1435…），较第三轮略增 | 本轮 `git ls-files` 逐文件计行 |
| **L10** | 【遗留 P2-03/04】 | mypy `strict=false` + `ignore_missing_imports` 下的 0 error（披露）；前端 `KnowledgeBase` 3 个后端必填字段声明为可选 | `pyproject.toml:159,168`；`types.ts:164-171` |
| **L11** | 【遗留 P2-07/08】 | `toBusinessMessage` 对非 BusinessError 透传英文技术文案；上传仅扩展名校验（无魔数/文件名净化） | `business-errors.ts:226`；`rag_service.py:594-601` |
| **L12** | 【遗留 P2-15 + P2-14 残留】 | 分支策略与 README 声明的 Git Flow 不符（远端仅 `main`）；`.git` 垃圾已清但 **3 条 stash** 残留（含自标「勿 apply」的废弃半成品） | `git stash list` → 3 |
| **L13** | 【遗留】 | README 质量数字仍手抄快照且已失真：「2211 passed / 212 skipped / 85.87% / mypy 205 文件」vs 本轮实测 2527 passed / 235 skipped / 87.96%（行口径）/ 209 文件 | `README.md:24,235-237` |

---

## 四、六维度结论速览

| 维度 | 判定 | 关键实测（2026-11-02） |
|---|---|---|
| 1 架构与目录 | **通过** | 五层红线成立（`dify_client.py`/`api-client.ts` 唯一出口）；16 个门禁全绿含 import 方向（基线 23 条/外 0）；新增 `api/sso_routes.py`(854)、`core/oidc_server.py`(583)、`core/session_cookie.py`(363)、`core/csrf.py`(171) 与前端 `session-client.ts`/`auth-refresh.ts` 层次清晰、测试同批；扣分项=超大文件 24 个 + `sso_routes.py` 落在 `app/api/`（非 `v1/`）与既有路由目录惯例不一致 |
| 2 代码质量 | **通过（附披露）** | ruff check 0 / ruff format 399 files / **mypy 0（209 文件）** / eslint 0 / tsc 0 全部实跑全绿；TODO 真实计数 0；披露：mypy 非 strict、`now_iso` 7 副本、README 数字失真 |
| 3 安全 | **有条件通过** | 令牌链路整体闭环（专项审查 8 安全点全过：Cookie 属性、CSRF 三级判定、登出/失效、令牌零暴露、PKCE/常量时间比较、无开放重定向、stub 生产拦截）；存量安全控制（注入 0、租户三重隔离、AES-GCM 凭据、验签顺序）保持扎实；**待修**：M1 刷新竞态、L1 cookie tossing、M13 配置默认值 |
| 4 性能 | **通过（附整改项）** | 无崩溃级缺陷；M4-M7 四项同型债务（全量拉取/无 LIMIT/逐条事务/内存整包）未清，属规则化治理对象 |
| 5 依赖与许可 | **通过** | `pip-audit`（--path venv）除自身包外 **0 已知漏洞**；`npm audit --omit=dev` **0**（16 moderate 全在 dev 链 minimatch）；无 GPL/AGPL/SSPL；后端 53 包 / 前端 510 包，灰名单仅 MPL-2.0×2、PSF-2.0、MIT-0、LGPL 组合×1；`uv lock --check` 无漂移、npm 无幽灵包；缺口=大版本 8 项（M15）+ 镜像 digest（M8） |
| 6 测试覆盖 | **通过（附口径风险）** | 后端 **2527 passed / 235 skipped / 0 failed**（14:51），行覆盖 **87.96%**（20311 语句；无 PG 口径）；前端 **70 文件全过**，87.17/81.23/82.05/88.39 四指标全过阈；缺口=workbench 子组件 0%（M11）、e2e 未实跑（M12）、PG 默认 skip（M16）、CI 5b 阈值未执行（M2） |

---

## 五、实测数据汇总（可复核）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `OA_BATCH=B21 pytest apps/adapter/tests --cov=app` | **2527 passed / 235 skipped / 0 failed**，891.19s；收集 **2762** |
| 后端覆盖率 | `coverage report`（无 PG 口径） | TOTAL **20311 语句 / 2445 未覆盖 / 87.96%**（行口径；第三轮含分支口径 85.72% 无 PG / 92.31% 含 PG）；`fail_under=85` 通过 |
| 前端全量 | `npm run test --workspace apps/web -- --coverage` | **70 文件全过**，All files **87.17 / 81.23 / 82.05 / 88.39**（Stmts/Branch/Funcs/Lines），阈值 86.4/80.4/81.1/87.6 全达 |
| 静态门禁 | `ruff check` / `ruff format --check` / `mypy app` / `eslint --max-warnings=0` / `tsc --noEmit` | 全 0（mypy **209 文件**；format 399 files） |
| deploy 门禁 | `validate_*.py` × 16（含裸跑 import_directions / batch_clock 带 OA_BATCH） | **全 EXIT=0** |
| 后端漏洞 | `uvx pip-audit --path .venv/Lib/site-packages` | 除 `officeai-adapter`（自身、非 PyPI）外 **0 已知漏洞** |
| 前端漏洞 | `npm audit --omit=dev` / `npm audit` | **0** / 16 moderate（全 dev 链 minimatch DoS） |
| 许可 | importlib.metadata / license-checker | 后端 53 包、前端 510 包；**无 GPL/AGPL/SSPL**；MPL-2.0（certifi、pathspec）、PSF-2.0（typing_extensions 等）、MIT-0、前端 LGPL-3.0-or-later 组合×1（sharp 系） |
| 版本落后 | `npm outdated` / `uv pip list --outdated` | 前端 **14 项**（8 大版本）、后端 11 项（最大 fastapi 0.156.8→0.162.0，余补丁级） |
| 锁一致性 | `uv lock --check` / `npm ls --workspaces` / `npm ls --all` | 全部一致；extraneous/invalid **0** |
| 前端包体 | `next build` + `.next/static` 实测 | 24 路由（19 静态 / 5 动态）；static 总量 **1363 KB**，最大 chunk 230 KB（第三轮盲区 #7 已补量化） |
| git 卫生 | `git count-objects -vH` / `git stash list` | size-garbage **0 bytes**（3.15GiB 垃圾已清）、size-pack 6.44 MiB、stash **3** 条；历史未改写 |

---

## 六、未验证盲区（不下结论）

1. **含 PG 口径未复跑**（本机无 PG）：4 条 Windows PG 夹具 `Event loop is closed` 残留（第三轮已知）、92.31% 含 PG 覆盖率均沿用基线数字。
2. **CI 从未实跑**：38 处 action、两套 workflow 的真实执行结果无任何记录（M3）。
3. **现场真登录**（R4-①~⑤）：Keycloak 浏览器可达性、realm 渲染产物域名需真实环境核对。
4. **压测类**：导出内存峰值、`job_repo` OR 条件执行计划、`plugin_task` 队列索引、HNSW 检索、SSE 长连接上限、多副本内存限流语义。
5. **`nginx -t` / 真机 TLS 握手**：需 Linux 现场。
6. **Playwright e2e**（12 spec）未运行（M12）。

---

## 七、风险排序与改进建议清单

### P0 — 上线前必须（阻塞公网部署）

| # | 动作 | 对应 | 验收口径 |
|---|---|---|---|
| 1 | **部署形态收口（轮 2 既定范围）**：两套 compose 加 nginx（复用 `lite-tls.conf`），`adapter`/`web` 改 `127.0.0.1:` 或 `expose:`；`edge`/`proxy` 补 TLS；saas 补 `mem_limit`+healthcheck；`deploy/k8s/` 补齐或明确声明 V1.0 不交付 | G1 | `grep -c '"8000:8000\|"3000:3000'` = 0；TLS 握手可达；HSTS 在 https 下生效 |
| 2 | **刷新竞态收口**：`scheduleRefresh` 并入跨标签单飞（或 Web Locks/`BroadcastChannel` 互斥），`sso_routes` 20002 清 Cookie 前校验「失败的 refresh 令牌仍是当前令牌」再清 | M1 | 新增「双标签并发刷新」用例：最多一个 dead、赢家 Cookie 不被覆盖；把系统时钟拨后一年重跑仍 0 failed |
| 3 | **CI 首跑拿基线**：恢复额度后跑一次全绿留档（顺手完成 M2 的 50→88 阈值提交——已拍板，注释写明「实测 90.66% − 2.66pp」来源） | M2、M3 | workflow 成功截图/链接入 README「验证链」节；CI 5b fail-under=88 |

### P1 — 近期（两周内）

| # | 动作 | 对应 | 验收口径 |
|---|---|---|---|
| 4 | 仓储层「分页下推 + 批查询 + 批量写单事务」沉淀为编码规则 + review checklist，逐点消化：`ops_service` 租户列表、`list_queued` LIMIT、`add_artifacts` 批量 | M5/M6/M7 | 逐点 PR + 每点一条「查询次数不随行数增长」断言用例 |
| 5 | 导出链路改 spooled tempfile + `StreamingResponse` + `asyncio.Semaphore`；saas 容器补资源上限 | M4 | 200k 行导出 RSS 实测入档 |
| 6 | 前端测试债：`components/workbench/*` 6 个子组件级用例（别再加页面用例）；骨架屏三态「对不对」断言 | M11、L5 | workbench 组件行覆盖 >60%，阈值棘轮同步只升不降 |
| 7 | e2e 接入本地一键命令并在 README 声明边界；Playwright 12 spec 至少跑通一次 | M12 | `npm run test:e2e` 本地可复跑 |
| 8 | 配置默认值收口：`.env.example` 弱默认改随机生成路径提示；`env`/`auth_mode` 默认值与「production+stub 拦截」同款补 `development` 场景显式告警或启动判据 | M13 | 负样本：漏配 `OA_ENV` 的模板启动必须有显式告警/拦截 |
| 9 | 回滚与灾备：补 `0002`-`0005` down SQL 或声明最低可回退点；跑一次 clean-room 备份恢复演练落 `docs/drills/` | M14 | 演练记录含耗时与校验输出 |
| 10 | 供应链：镜像收到补丁位 + compose digest 引用评估；actions pin SHA（38 处） | M8、L6 | `grep -c '@sha256'` > 0；`uses:` SHA 覆盖率逐步 100% |
| 11 | about 页技术披露改业务语言（产品决定）；禁词表补品牌名+白名单 | M10 | G-14/G-15 门禁能抓新披露 |
| 12 | 依赖：补丁级批处理（6+11 项）；大版本 8 项单独立项不与交付同批 | M15 | `npm outdated` 无补丁级滞后 |

### P2 — 持续（技术债批次）

| # | 动作 | 对应 |
|---|---|---|
| 13 | `__Host-` Cookie 前缀（https 现场启用）+ 文档化 http 现场限制 | L1 |
| 14 | 产物下载限流改按鉴权上下文 + `OA_TRUSTED_PROXY_CIDRS`（轮 2 既定） | L2 |
| 15 | `require_permission` 链路收尾三件：`jwt_algorithm` Literal、`toBusinessMessage` 技术文案兜底、上传魔数+文件名净化 | L3、L11 |
| 16 | 「含 tenant_id 新表必须启用 RLS」门禁 + `now_iso` 下沉 `base.py` + 同名函数禁止新增门禁 | L7、L8 |
| 17 | 拆 `model_service.py`/`plugin_service.py`/`api-client.ts`（>1500 行优先） | L9 |
| 18 | mypy 逐步收紧（先 `disallow_untyped_calls`）；前端契约必填对齐 | L10 |
| 19 | git 卫生：3 条 stash 经确认后清理；README 数字改链路引用（禁手抄快照） | L12、L13 |
| 20 | 分支策略与 README 对齐（Git Flow 或改声明） | L12 |

---

## 八、放行判断

> **⚡ 2026-11-02 夜追记**：下表为审计当时判定。G1/M1/M2/M3 整改后，「部署（SaaS/公网）」的代码面已收口（TLS/端口/k8s 清单，见 §九），剩余上线条件是**现场证书**与 CI 全绿确认；「验证链（CI）」已从「从未实跑」变为「已实跑并修复 5 处存量 bug」，最终结论见 §九。

| 交付面 | 判定 | 一句话理由 |
|---|---|---|
| 代码质量 | **放行** | 四项静态门禁实测全绿，错误码/黑盒化/技术债登记纪律保持 |
| 功能回归 | **放行** | 后端 2527/0 failed + 前端 70 文件全绿，第三轮 12 条红灯真清零 |
| 安全 | **有条件放行** | 令牌链路闭环后核心面扎实；M1/L1/M13 三处收口后可无保留 |
| 部署（私有化 lite） | **有条件放行** | lite 形态有 nginx/TLS 模板，待现场 `nginx -t` 与真登录验证 |
| **部署（SaaS/公网）** | **不放行** | G1：无 TLS + 端口裸绑 + k8s 空壳，唯一高危 |
| 验证链（CI） | **不放行** | CI 从未实跑，本地绿不能替代 |

**总判定**：`main @ 5e8ff2b` **代码面可交付、公网面不可交付**。与第三轮「QA 阻塞 + 上线阻塞」相比，QA 阻塞已解除（红灯清零、门禁全绿），阻塞面收敛为部署形态一条主线 + CI 实跑取证。

**建议的 48 小时行动顺序**：M2 的 50→88 阈值提交（一行）→ CI 首跑取证（M3）→ M1 刷新竞态（唯一新增中危）→ G1 部署收口立项（轮 2 既定范围，工作量主要在 compose/nginx/k8s）。

---

*本报告为 2026-11-02 晚间对 HEAD `5e8ff2b` 的独立复审；所有数字均来自本轮实跑命令（§五 可复核），修复状态均给出提交号或文件行号；第三轮报告原文归档于 `docs/archive/audit-2026-11-02-full-repo-audit-round3.md`（git `880df56`）。密钥类发现只记位置与类型。*
