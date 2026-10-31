# 依赖升级观察清单（Dependency Upgrade Watchlist）

- **建立日期**：2026-10-29
- **来源**：`docs/archive/audit-2026-10-28-r2-consolidated.md` R2-ARC-17 的附带发现
- **为什么需要**：仓库只有**漏洞扫描**（`dependency-scan.yml`，回答"现在有没有已知 CVE"），
  没有**升级巡检**（回答"什么时候必须升、升到什么、不升的代价是什么"）。
  `deploy/images.lock` 引用的「架构文档 9.6 月度巡检」条款实际不存在，仓库也没有
  dependabot / renovate。结果：所有"上游已弃用/不修"的债务没有任何到期判据，
  只能靠某次审计偶然撞见。

## 使用方式

1. 每季度（或每次大版本规划前）过一遍本表，更新「当前状态」列；
2. **触发条件成立时必须处理**——不是"看一下"，是"排进当前 Sprint"；
3. 新增条目要写清「为什么现在不升」和「什么条件下必须升」，否则不录入。

---

## W-1 · starlette → httpx2（测试客户端）

| 项 | 内容 |
|---|---|
| **现状** | `starlette 1.10.0`（约束 `>=1.3.1,<2.0`）；`fastapi.testclient` 抛 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`；`uv.lock` 内**无 httpx2**（`grep -c httpx2` → 0） |
| **为什么不现在升** | fastapi 尚未声明支持 httpx2；此时迁移等于追一个未稳定的 release。上界 `<2.0` 天然挡住破坏性大版本，不会被动引入 |
| **触发条件（任一成立即必须处理）** | ① starlette 2.0 发布；② fastapi 官方声明支持/要求 httpx2；③ 引入 dependabot/renovate（机器人会开出破坏性 PR，届时本条升级为**高优先级**） |
| **影响面** | **57 个测试文件**使用 `TestClient`（直接 import 41 个 + 仅用 fixture 的并集口径）。升级失败会同时报 import 错误——**fail-loud，不会带病上线**，这是本项可暂缓的核心理由 |
| **到期判据** | 无固定日期，按触发条件 |
| **备注** | 2026-10-29 已删除 `pyproject.toml` 里那条无效的 `ignore::DeprecationWarning:httpx.*`（实测为 no-op 空转配置）。删除后若该警告出现，属**正常信号**，不要再加回过滤器 |

---

## W-2 · python-jose（存在上游不修的 CVE）——✅ 已消除

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已闭环**（2026-10-30）：JWT 库整体迁移 **PyJWT 2.15.1**（MIT，活跃维护），`uv.lock` 重生成后 python-jose / ecdsa / rsa / pyasn1 / six 全部退出依赖树；`pyproject.toml` 改为 `pyjwt>=2.8,<3.0` |
| **原现状** | `python-jose 3.5.0`；`docs/architecture-v1.0.md` 记录 **PYSEC-2026-4298 且上游无修复版本** |
| **迁移要点** | `core/security.py` 显式 `algorithms=[conf.jwt_algorithm]` 白名单**保持不变**；另发现并修复 PyJWT 语义差异——`verify_signature=False`（stub 模式）时 PyJWT 默认连 `exp` 一并放行，已显式 `verify_exp: True` 保住「过期令牌必拒」红线 |
| **验证** | 后端全量回归 2211 passed / 0 failed，覆盖率 85.87%；安全用例 99 过；`pip-audit` 中该 CVE 随包移除 |
| **到期判据** | 无（已闭环） |

---

## W-3 · ecdsa（Minerva 侧信道）——✅ 已消除

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已闭环**（2026-10-30）：ecdsa 作为 python-jose 的传递依赖，随 W-2 迁移一并移除（PyJWT 不依赖 ecdsa） |
| **原风险评估** | 本项目仅用验签、不生成签名、不持有私钥 → 侧信道不可利用 |
| **验证** | `uv.lock` 中已无 `ecdsa` 条目；`pip-audit` 不再报 CVE-2024-23342 |
| **到期判据** | 无（已闭环） |

---

## W-4 · 前端大版本落后

| 项 | 内容 |
|---|---|
| **现状** | `tailwindcss@3.4` / `zustand@4.5` / `date-fns@3.6`，各落后 1 个大版本 |
| **为什么不现在升** | 前端稳定性取舍：Tailwind 4 是配置模型重写（CSS-first），迁移成本与收益不成比例；zustand 5 / date-fns 4 有 breaking change 但不带来业务价值 |
| **触发条件** | ① 出现安全公告；② 与 Next 16 的兼容性冲突；③ 需要其新特性时 |
| **到期判据** | 每次 Next 大版本升级时**顺带评估**（跟着框架走，不单独行动） |

---

## W-5 · squid 镜像底座（关联 R2-ARC-15）

| 项 | 内容 |
|---|---|
| **现状** | `deploy/docker-compose.yml:415` 仍为 `ubuntu/squid:6.13-25.04_beta`——**beta 标签 + Ubuntu 25.04（interim，标准支持约 9 个月）**；`deploy/Dockerfile.squid` 未建（2026-10-29 复核） |
| **状态** | ❌ **已逾期，未闭环**（2026-10-29 复核）：原硬日期 2026-01-31 已过（Ubuntu 25.04 标准支持约 2026-01 结束），至今未切换 |
| **为什么不现在处理** | 需先决策走「自建 LTS 镜像」还是「换上游 stable tag」，属部署形态改动，不是一行修改 |
| **触发条件** | **Ubuntu 25.04 标准支持结束（按 Canonical 惯例约 2026-01）前必须切换**——**该触发条件已成立**（2026-10-29 复核） |
| **到期判据** | 原日期硬约束 **2026-01-31 已逾期**。**新处置口径（2026-10-29）**：① **重定硬日期**（新截止日待定，须在下次季度巡检前闭环）；② 二选一落地：确认 GPL-2.0 灰名单豁免后换上游 stable tag，或建 `deploy/Dockerfile.squid` 自建 LTS 镜像 |
| **备注** | 该条早在 2026-10-22 审计 `P2-7` 就已登记，至今未闭环——本表把它变成有日期的条目，避免第三次"再次发现"。2026-10-29 复核：日期已过仍未闭环，升级为逾期条目 |

---

## W-6 · `OA_BATCH` 批次时钟（关联 R2-QA-16）

| 项 | 内容 |
|---|---|
| **现状** | `ci-adapter.yml:93` 硬编码 `OA_BATCH: B21`；缺陷台账已有 3 条 `due_batch: B30` |
| **状态** | ✅ **已闭环**（2026-10-29 核实）：`deploy/validate_batch_clock.py` 已落地，`ci-adapter.yml:212` 已挂「批次时钟校验（OA_BATCH vs 台账 due_batch）」门禁 |
| **风险** | 「缺失即红」只防忘设、不防"设成旧值"。批次推进到 B30 而 CI 仍写 B21 → 3 条到期缺陷**逃检**，门禁照常绿 |
| **修复方向** | 加 `deploy/validate_batch_clock.py`：`OA_BATCH` 必须 ≥ 台账 `status=="open"` 条目的最大 `due_batch`（**已落地**，见「状态」行） |
| **到期判据** | 在批次推进到 B22 之前必须落地（否则时钟开始失真） |

---

## 维护记录

| 日期 | 动作 |
|---|---|
| 2026-10-29 | 建档，录入 W-1 ~ W-6（来源：第二轮审计 R2-ARC-17 附带发现 + R2-ARC-15 + R2-QA-16） |
| 2026-10-29 | 全仓审计校正：W-2 行号两处（`architecture-v1.0.md:1421→1419`、`core/security.py:157→158`）；W-5 标记「已逾期，未闭环」并补新处置口径；W-6 标记「已闭环」（`validate_batch_clock.py` 已挂门禁），`ci-adapter.yml:88→93` |
| 2026-10-30 | **W-2 / W-3 闭环**：JWT 库迁 PyJWT 2.15.1，python-jose + ecdsa 链退出依赖树（详见两表「状态」行） |
| 2026-10-30 | 前端补丁级升级：`next 16.4.2→16.4.3`、`@tanstack/react-query 5.104.5→5.105.0`、`autoprefixer 10.6.1→10.6.2`、`eslint 10.13.0→10.14.0`（均在 caret 范围内）。`eslint-config-next` 停留 16.4.2：npm 锁条目未随 ^16.4.3 重解析（仅开发期 lint 预设，不影响运行时），下次锁重建时自然上移 |
| 2026-10-30 | SBOM 时效复核：`sbom.yml` 已有**每周一 03:00 UTC 定时重算**（cron `0 3 * * 1`），审计初稿「无自动重生成」表述已过期 |
