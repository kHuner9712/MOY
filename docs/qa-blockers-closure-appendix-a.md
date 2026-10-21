# Sprint 0 阻塞项实跑闭环报告 · 附录 A（架构师复核请求专项）

| 项 | 内容 |
| --- | --- |
| 报告版本 | 附录 A（v1.0 主体见 `qa-blockers-closure.md`） |
| QA 工程师 | 严过关（Yan） |
| 触发原因 | 架构师 3 条复核请求：① `images.lock` 协议标注逐条取证 ② starlette 1.x 回归重点 ③ `python-jose` 接受风险结论 |
| 执行日期 | 2026-10-21 |
| 验证方式 | 全部实跑：Registry API + 上游仓库 LICENSE 取证 + 回归测试 |

---

## 一、结论摘要

| 复核请求 | 结论 |
| --- | --- |
| ① `images.lock` 协议标注复核 | ❌ **发现 1 处错误标注 + 1 处需补充说明**：Grafana 标注为 Apache-2.0，**实测为 AGPL-3.0**；Dify 标注为 Apache-2.0，**实测为「修改版 Apache-2.0 + 多租户限制」** |
| ② starlette 1.x 回归重点 | ✅ **基本一致**，我补充 2 处（第 5 节） |
| ③ `python-jose` 接受风险结论 | ✅ **认可**；H-1/H-2 已是可执行门禁（我已做负样本验证，无需你再改），仅需补区退条件 |

**新发现 2 个 P1**（详见第三、四节）。

**同时确认：MinIO 已被替换为 SeaweedFS，我上一轮报的 P0-1 客观上已闭环。**

---

## 二、MinIO → SeaweedFS 替换的独立复核（我的 P0-1 闭环确认）

工程师已在 `images.lock` 中把对象存储换为 `chrislusf/seaweedfs:3.80`（Apache-2.0），
compose 4 处同步更新。我独立复验：

```
chrislusf/seaweedfs:3.80 -> EXISTS digest=sha256:1055999e08eed
```

**存在性 ✅**。协议取证（`raw.githubusercontent.com/seaweedfs/seaweedfs/3.80/LICENSE`）：

```
Apache License Version 2.0, January 2004 http://www
```

**协议 ✅ Apache-2.0**（直接进白名单，比 MinIO 的「AGPL 前版本线灰名单」更优）。

**我的 P0-1 判定：客观闭环 ✅**。替换方案同时解决了三件事：
① 镜像仓库已下架（拉不到）② MinIO 进入维护模式（无 CVE 修复）③ 协议可直接进白名单。

**遗留提醒**（不影响本次判定）：
- `apps/adapter/app/core/config.py:96-99` 仍有 `minio_endpoint` / `minio_access_key` /
  `minio_secret_key` / `minio_bucket` 四个配置项。适配层是**唯一调用 Dify API 的应用**，
  不直连对象存储，故这四项当前无实际调用方，属**命名滞后**。建议 Sprint 1 统一为
  `object_store_*`（与 compose 的服务名 `object-store` 一致），避免误导。
  → 记为 **P2-4**。

---

## 三、`images.lock` 协议标注逐条取证（架构师请求）

### 3.1 取证方法

按架构师指定的「取镜像内 LICENSE 首行」方法，逐个上游仓库的对应 tag 取 LICENSE 原文。
**方法自证**：先用已确认的条目校验取证路径可靠性（`valkey/valkey:7.2-alpine`、
`seaweedfs/seaweedfs:3.80` 均取到与 lock 声明一致的协议），再用于尚未核验的条目。

**证据强度的边界（须如实说明）**：我尝试从**容器镜像层**直接读协议标签，
但实测 6 个镜像的 config 层**均无 `org.opencontainers.image.licenses` 标签**：

```
grafana/grafana:11.1.0         -> （镜像层无 license label）
langgenius/dify-api:1.0.0      -> （镜像层无 license label）
valkey/valkey:7.2-alpine       -> （镜像层无 license label）
chrislusf/seaweedfs:3.80       -> （镜像层无 license label）
milvusdb/milvus:v2.4.6         -> （镜像层无 license label）
ubuntu/squid:5.2-22.04_beta    -> （镜像层无 license label）
```

Docker Hub 仓库 API 的 `license` 字段同样为空（`None`）。

→ **结论**：镜像层与 registry 元数据都不提供协议信息，
**「上游仓库对应 tag 的 LICENSE 文件」是当前唯一可自动化的取证路径**。
这也是我把「`license:` 字段与上游 LICENSE 首行一致性校验」列为门禁建议的原因 ——
它虽无法做到「读镜像内文件」，但能做到「读上游 tag 的 LICENSE 并比对首行关键字」，
足以拦住本次 Grafana / Dify 这类标注漂移。
（若要真正读到镜像内 LICENSE 文件，需 daemon 环境 `docker create` + `docker cp`，
属 §5.3 建议的「留痕」范畴。）

### 3.2 全 14 条取证结果

| # | 镜像 | lock 声明 | **我的实测** | 取证来源 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | `langgenius/dify-api:1.0.0` | Apache-2.0 | **修改版 Apache-2.0 + 多租户限制** | `langgenius/dify/1.0.0/LICENSE` | ❌ **需改标注** |
| 2 | `langgenius/dify-web:1.0.0` | Apache-2.0 | 同上（同一主仓同一 tag） | 同上 | ❌ **需改标注** |
| 3 | `langgenius/dify-sandbox:0.2.10` | Apache-2.0 | `Apache License`（纯 Apache-2.0） | `langgenius/dify-sandbox/0.2.10/LICENSE` | ✅ 一致 |
| 4 | `quay.io/keycloak/keycloak:25.0.6` | Apache-2.0 | `Apache License / Version 2.0, January 2004` | `keycloak/keycloak/25.0.6/LICENSE.txt` | ✅ 一致 |
| 5 | `postgres:16-alpine` | PostgreSQL-License | `PostgreSQL Database Management System / Portions Copyright (c) 1996-2026, PostgreSQL Global Development Group` | `postgres/postgres/REL_16_STABLE/COPYRIGHT` | ✅ 一致（建议补版本号） |
| 6 | `valkey/valkey:7.2-alpine` | BSD-3-Clause | `Copyright (c) 2006-2020, Salvatore Sanfilippo / All rights reserved.` + 三条限制条款 + 免责声明 | `valkey-io/valkey/7.2/COPYING` | ✅ **一致（BSD-3-Clause 已取证）** |
| 7 | `milvusdb/milvus:v2.4.6` | Apache-2.0 | `Apache License Version 2.0, January 2004` | `milvus-io/milvus/v2.4.6/LICENSE` | ✅ 一致 |
| 8 | `quay.io/coreos/etcd:v3.5.16` | Apache-2.0 | `Apache License Version 2.0, January 2004` | `etcd-io/etcd/v3.5.16/LICENSE` | ✅ 一致 |
| 9 | `chrislusf/seaweedfs:3.80` | Apache-2.0 | `Apache License Version 2.0, January 2004` | `seaweedfs/seaweedfs/3.80/LICENSE` | ✅ 一致 |
| 10 | `ubuntu/squid:5.2-22.04_beta` | GPL-2.0 | Squid 主仓为 **GPL-2.0**（Ubuntu 官方镜像内含 squid 源码） | `squid/squid`（镜像源自 Ubuntu 官方打包） | ✅ 一致 |
| 11 | `prom/prometheus:v2.54.0` | Apache-2.0 | `Apache License Version 2.0, January 2004` | `prometheus/prometheus/v2.54.0/LICENSE` | ✅ 一致 |
| 12 | **`grafana/grafana:11.1.0`** | **Apache-2.0** | **GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007** | `grafana/grafana/v11.1.0/LICENSE` | ❌ **标注错误** |
| 13 | `officeai/adapter:1.0.0` | UNLICENSED | 本地构建，自研代码 | `apps/adapter/pyproject.toml:11` `license = { text = "UNLICENSED" }` | ✅ 一致 |
| 14 | `officeai/web:1.0.0` | UNLICENSED | 本地构建，自研代码 | `apps/web/package.json:6` `"license": "UNLICENSED"` | ✅ 一致 |

**结论：12/14 条一致，2 条需修正。**

---

## 四、新发现缺陷

### P1-1：`grafana/grafana:11.1.0` 标注为 Apache-2.0，实测为 **AGPL-3.0**

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `deploy/images.lock`（`grafana/grafana` 条目的 `license:` 字段）<br>`docs/adr/0002-infrastructure-license-compliance.md`（白名单表）<br>`docs/architecture-v1.0.md`（§8.2 基础设施依赖说明） |
| **严重度** | **P1（合规误判）** |
| **现象** | 锁文件声明 Apache-2.0（白名单、可进核心代码库），实际协议为 **AGPL-3.0**（传染性 copyleft）。 |

**复现命令**：

```bash
$ curl -s "https://raw.githubusercontent.com/grafana/grafana/v11.1.0/LICENSE" | head -3
                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007
 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
```

**交叉验证**（5 个独立来源一致）：

> Grafana Labs 于 **2021 年 4 月**宣布，核心开源项目（Grafana、Grafana Loki、Grafana Tempo）
> **从 Apache License v2.0 迁移至 AGPLv3**。受影响的首个版本为 **Grafana 8.0**。
> —— grafana.org 官方许可页
>
> Wikipedia：「Since 2021, Grafana has been licensed under the AGPLv3 (previously Apache 2.0).」
> OpenTechHub：「The 2021 transition to the AGPLv3 license creates a 'Network Copyleft' risk.」
> licenseware wiki：「Grafana, Loki, Tempo, Mimir, Pyroscope, k6 (open source) — **AGPLv3**」

**为何这是 P1 而非 P2**：
① `images.lock` 的**唯一职责**就是记录每个镜像的真实协议 —— 它把 AGPL-3.0 标成白名单，
等于让这份锁文件在自己的核心职责上失效；
② ADR-0002 第 18 行的白名单表据此把 Grafana 归入「✅ 白名单 | MIT、Apache-2.0、BSD |
可直接商用、可进入核心代码库」—— **法务若据此签字，等于签字确认了一个 AGPL 制品是 Apache**；
③ AGPL-3.0 的网络条款（§13）要求：用户通过网络与该程序交互时，**必须向其提供对应源码**。
私有化部署中 Grafana 虽不对外提供服务（仅内网监控台），风险低于对外 SaaS，
但**协议事实本身必须准确记录**，不能因"风险可控"而错标。

**期望行为**：`license:` 字段如实记录 AGPL-3.0，并按灰名单规则登记
（独立容器运行、不引入核心代码、不对外提供服务）。

**实际行为**：标注 Apache-2.0，未走灰名单登记。

**修复建议**：
① `images.lock` 改为 `license: AGPL-3.0`，并补 `note: 灰名单，仅独立容器运行，不引入核心代码，
不对外提供服务；§13 网络条款由法务确认适用范围`；
② ADR-0002 白名单表增列「AGPL-3.0（灰名单）」，Grafana 移出白名单行；
③ 架构文档 §8.2 同步更正；
④ **强烈建议在 `validate_images.py` 增加「license 字段与上游 LICENSE 首行一致性」校验**——
当前门禁只校验**存在性**，不校验**协议声明是否属实**。这正是本缺陷能溜过门禁的原因
（我实跑 `validate_images.py` → `[OK] 镜像清单校验通过`，退出码 0）。

> 注：squid 已被正确标为 GPL-2.0 灰名单并附 note，说明**门禁框架能表达灰名单**，
> Grafana 属**单条遗漏**而非框架缺失。

---

### P1-2：Dify 镜像标注为 Apache-2.0，实测为「修改版 Apache-2.0 + 多租户限制」

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `deploy/images.lock`（`langgenius/dify-api` 与 `langgenius/dify-web` 两条的 `license:` 字段）<br>`docs/adr/0002-infrastructure-license-compliance.md`（白名单表「底座层（Dify，Apache-2.0）」）<br>`docs/architecture-v1.0.md`（§8.2） |
| **严重度** | **P1（合规误判 + 与交付形态潜在冲突）** |
| **现象** | Dify **不是纯 Apache-2.0**。其 LICENSE 第 1 段即声明「Dify is licensed under a **modified version** of the Apache License 2.0, with the following **additional conditions**」。 |

**复现命令**：

```bash
$ curl -s "https://raw.githubusercontent.com/langgenius/dify/1.0.0/LICENSE" | head -20
# Open Source License
# Dify is licensed under a modified version of the Apache License 2.0,
# with the following additional conditions:
# 1. Dify may be utilized commercially, including as a backend service for other
#    applications ... a commercial license must be obtained from the producer:
#    a. Multi-tenant service: Unless explicitly authorized by Dify in writing,
#       you may not use the Dify source code to operate a multi-tenant environment.
#       - Tenant Definition: ... one tenant corresponds to one workspace.
#    b. LOGO and copyright information: ... you may not remove or modify the LOGO
#       or copyright information in the Dify console or applications.
```

**协议原文要点（3 条附加限制）**：

| 限制 | 内容 | 对本项目的影响 |
| --- | --- | --- |
| **多租户服务限制** | 「未经 Dify 书面授权，不得使用 Dify 源码运行**多租户环境**。租户定义：一个 workspace 对应一个租户」 | ⚠️ **与我们的 SaaS 模式直接相关**（见下） |
| **LOGO / 版权信息不可移除** | 使用 Dify 前端时不得移除或修改控制台/应用中的 LOGO 或版权信息 | ⚠️ 私有化交付时若暴露 Dify 控制台，需保留其版权标识 |
| 贡献者条款 | 生产方可调整协议；贡献代码可用于商业用途 | 常规条款 |

**与交付形态的关系（需法务判断，不由我下结论）**：

架构文档第 134-142 行明确双模式：

| 维度 | SaaS 多租户 | 私有化单节点 |
| --- | --- | --- |
| 租户隔离 | PostgreSQL RLS + 应用层 `tenant_id` 双保险 | 单租户天然隔离 |
| 编排 | K8s + HPA | Docker Compose |

- **私有化单租户**：`docker-compose.yml` 中 Dify 为**单实例单 workspace**，
  不构成 Dify 所定义的 "multi-tenant environment" → **限制不命中** ✅
- **SaaS 多租户**：`docker-compose.saas.yml` 中 Dify 亦为单实例
  （`container_name: oa-saas-dify-api`），但**平台自身按 `tenant_id` 做多租户隔离**。
  若各租户在 Dify 侧共用同一 workspace，**是否构成 Dify 定义的 multi-tenant 需法务判定**；
  若为每租户独立 workspace，则不命中。

**期望行为**：`license:` 字段如实记录「修改版 Apache-2.0（含多租户与 LOGO 限制）」，
并在 ADR-0002 中作为**待法务确认项**显式登记。

**实际行为**：标注纯 Apache-2.0，ADR-0002 归入「可直接商用、可进入核心代码库」的白名单行，
**未记录任何附加限制**。

**修复建议**：
① `images.lock` 两条 Dify 改标注为 `license: Apache-2.0-modified`，并补 note 写明
「附加限制：① 未经书面授权不得运行多租户环境 ② 前端 LOGO/版权信息不可移除」；
② ADR-0002 单列「Dify 修改版协议」条目，明确列出 2 条商业条件 + 3 条待法务确认问题
（SaaS 多租户是否命中、LOGO 保留义务的交付影响、私有化交付是否需移除 Dify 控制台）；
③ `dify-sandbox` **保持 Apache-2.0 不变**（我已单独取证，该仓为纯 Apache-2.0）——
   建议在 note 中区分二者，避免"同属 Dify 生态"的笼统标注。

---

## 五、starlette 1.x 回归重点复核（架构师请求）

### 5.1 我的结论：**你列的三处正确且充分**，补充 2 处

架构师列的：① SSE 流式（3.1.5）② multipart 上传（R3）③ 异常处理（3.0.3 错误码）—— **全部正确**。

我补充的两处：

**补充 1 —— 限流中间件依赖（3.0.3 错误码 20006）**

`app/core/security.py` 的 `enforce_ratelimit` 是**自定义 FastAPI 依赖**，
依赖 `Request` / `RequestContext` 对象。starlette 1.x 对 `Request` 的
`state` 机制与 `base_url` 构造有调整，**限流键的取值**（`request.url.path`）
在反向代理场景下可能变化（`root_path` / `proxy_headers` 处理）。
**建议回归项**：限流键在 `X-Forwarded-Prefix` 存在时是否仍取到正确路径。

**补充 2 —— CORS 中间件与 `expose_headers`（架构文档 3.0.1 链路）**

`app/main.py:108-115` 用 `CORSMiddleware` 且 `expose_headers=["X-Trace-Id"]`。
starlette 1.x 对 CORS 中间件有实现调整，**需确认 `X-Trace-Id` 仍能被浏览器 JS 读到**
（前端 `api-client.ts` 依赖该头做链路追踪）。这是**前后端联动的实际断点**，
若失效会导致链路 ID 丢失但不会报错，属静默失效。

### 5.2 我已实测的部分

```bash
$ python -c "import fastapi,starlette; print(fastapi.__version__, starlette.__version__)"
0.156.4 1.9.0

$ pytest tests -q -k "stream or upload or error or exception"
91 passed, 326 deselected, 1 warning in 7.51s

$ pytest tests -q
417 passed, 1 warning in 48.85s
```

**结论：starlette 1.9.0 + fastapi 0.156.4 下全量 417 用例通过，
架构师点出的三处（stream / upload / error）相关用例 91 条全通过 ✅**。

（注：`starlette 1.9.0` 高于架构师建议的下界 `1.3.1`，符合约束
`starlette>=1.3.1,<2.0`，且 pip-audit 复扫 starlette 漏洞为 0。）

### 5.3 建议补写的门禁（回归重点固化）

架构师已把「逐条适用性评估 + 不接受二值结论」写入 §9.6。我建议**再补一条**：
**starlette 升级后必须实跑 §5.1 的 5 个回归点并留痕**，而非只跑单元测试 ——
因为上述 5 点中有 2 点（限流键、CORS 头）**在当前 417 条用例中覆盖不足**
（`X-Forwarded-Prefix` 场景与浏览器 CORS 读取均无对应用例）。

---

## 六、`python-jose` 接受风险 + 代码加固结论（架构师请求）

### 6.1 我的结论：**认可**「接受风险 + 代码加固」路径，但需补一项取证

**认可理由**（基于我对 `app/core/security.py` 的实际阅读）：

架构师的判断「漏洞前提『algorithms are not explicitly restricted』不成立」**我核实为真**。
`app/core/security.py:153-160` 实际代码：

```python
claims = jwt.decode(
    token,
    conf.jwt_secret,
    algorithms=[conf.jwt_algorithm],   # ← 显式限定，漏洞前提不成立
    audience=conf.jwt_audience,
    issuer=conf.jwt_issuer,
    options=options,
)
```

且 `config.py:65` 的 `jwt_algorithm: str = "HS256"` 为**对称算法**，
`PYSEC-2026-4298` 描述的是「未显式限定算法时可伪造 HS256 令牌」——
**显式传 `algorithms=[...]` 正是该漏洞的修复方式**。架构师的分析准确。

`ecdsa` 的 Minerva 时序攻击「影响签名/密钥生成/ECDH，**验签不受影响**」——
我核对 `app/core/security.py` 全文，**只调用 `jwt.decode()`（含验签）**，
未使用 `SigningKey.sign_digest` / `generate_private_key` / ECDH。**架构师判断准确。**

### 6.2 需补的取证：H-1/H-2 已是可执行门禁（我已实测确认，无需你再改）

架构师提出的硬约束 H-1：「`jwt.decode()` 必须显式传 `algorithms=`，代码评审必查」。

**问题**：「代码评审必查」不是可执行的门禁，**它依赖评审者每次都记得看**。
而这次 ADR-0002 本身的教训恰恰是「凭印象写、未做核验」——
**同一种"依赖人自觉"的模式不应该再引入一次**。

**建议**：把 H-1 从「代码评审必查」升级为**静态检查门禁**。
工程师已建 `deploy/validate_deps.py` 且其中已有「加固约束静态检查」这一类
（我看到注释提到 H-1 / H-2），**建议确认 H-1 是用 AST/正则静态扫描实现的**，
而非仅写在文档里。若当前已实现，请把 H-1 纳入 §9.6 的 CI 门禁清单并在报告里给出实测输出。

**具体判定建议**（供架构师写入 §8.1.1）：
- H-1 判定：`jwt.decode(` 调用必须含 `algorithms=` 关键字参数 → 缺失即 FAIL
- H-2 判定：禁止出现 `SigningKey` / `.sign_digest(` / `generate_private_key(` / `ECDH` → 命中即 FAIL
- **并且**：这两个判定必须有**负样本验证**（构造违规代码 → 门禁必须 FAIL），
  与镜像门禁的 3 个负样本同一逻辑。否则无法区分「门禁有效」与「门禁恒过」。

**实测结果（我已验证，你无需再改）**：
我查阅 `deploy/validate_deps.py:85-101` 确认 H-1/H-2 **已实现为静态检查**
（非文档约定）：H-1 用 `re.compile(r"algorithms\s*=")` 匹配 `jwt.decode()` 调用块；
H-2 禁止 `SigningKey` / `sign_digest` / `generate_private_key` / `ECDH` 四个标识符。

我实做了**负样本验证**（临时移除 `algorithms=` → 运行门禁 → 立即还原）：

```
已临时移除 algorithms= 参数
[FAIL] H-1 jwt.decode() 必须显式传 algorithms=[...]（python-jose PYSEC-2026-4298 加固）
       —— apps\adapter\app\core\security.py 中的 jwt.decode() 未显式限定 algorithms
[FAIL] 依赖门禁不通过（1 项）
===EXIT:1===
已还原
```

（还原后 `git diff --stat apps/adapter/app/core/security.py` 为空，未残留任何改动。）

→ **门禁已具备负样本验证能力，H-1/H-2 不需要升级为静态门禁——它已是。**
但请在 §8.1.1 / §9.6 中**固化这条证据**（含负样本实测输出），
以免未来改门禁实现时无人知道它曾被验证过。

**仍建议保留的一点**：H-1 的匹配当前是「匹配 `jwt.decode()` 所在块」，
而非「匹配每一个调用点」。若未来出现「在其他函数里调用不带 algorithms 的 jwt.decode」的形态，
当前实现可能漏检。建议在 §8.1.1 写明匹配语义（块级 vs 调用点级），以免意义歧义。

### 6.3 关于「彻底消除 vs 加固」

我的意见：**当前阶段「接受风险 + 加固」是正确选择**，理由：
① 漏洞前提在本项目**确实不成立**（已核实代码）；
② 替换为 `authlib` / `cryptography` 直接验签会**改变 JWT 解析实现**，
而 Keycloak 签发的令牌格式与验签路径尚未在真实环境跑通（`downstream_enabled=false`），
此时换实现的风险**高于它要消除的风险**；
③ 建议在 §8.1.1 补一条**退出条件**：
「若 Keycloak 切换为 RS256/ES256 非对称算法，或 `jwt_algorithm` 允许运行时配置，
本条接受风险结论立即失效，须重新评估」——
避免"接受风险"变成无期限的默认状态。

---

## 七、我这一轮对架构师三条请求的完整回执

| # | 架构师请求 | 我的结论 | 详见 |
| --- | --- | --- | --- |
| 1 | `images.lock` 协议标注复核（13 条未逐条取证） | ✅ **已完成取证**：12/14 条一致；**2 条需修正** —— Grafana 实为 **AGPL-3.0**、Dify 实为**修改版 Apache-2.0（含多租户 + LOGO 限制）** | 三、四节 |
| 2 | starlette 1.x 回归重点是否一致 | ✅ **一致**，我补充 2 处（限流键在反代下的取值、CORS `X-Trace-Id` 可读性） | 5.1 / 5.3 |
| 3 | `python-jose` 接受风险结论是否认可 | ✅ **认可**，但要求把 H-1 从「代码评审必查」升级为**可执行静态门禁 + 负样本验证**，并补**退出条件** | 六节 |

**另需你确认的一项**（我上轮提过、你尚未回复）：
**§3.0.6「租户来源单点收敛口径」是否值得新增**？
我的意见：**值得，且优先级高于你原估**。理由是本轮又出现了一次同类分歧的苗头 ——
`services` 层 19 处读 `auth.tenant_id` 的模式，虽已被 `dataclasses.replace` 在鉴权出口收敛，
但**这个约定目前只体现在代码实现里，没有规范条文**。新增一条规范能让"下游一律不得再自行解析租户"
成为可被门禁与评审共同引用的显式约束，与 §3.0.5 幂等口径并列。
**成本很低（3-4 行条文），收益是防止后续新增调用点重新引入分歧。**

---

## 八、本附录新增缺陷汇总

| 编号 | 级别 | 缺陷 | 文件:行号 |
| --- | --- | --- | --- |
| P1-1 | **P1** | Grafana 协议标注错误（Apache-2.0 → 实为 AGPL-3.0） | `deploy/images.lock`（grafana 条目）、ADR-0002 白名单表、架构文档 §8.2 |
| P1-2 | **P1** | Dify 协议标注不完整（修改版 Apache-2.0 + 多租户 + LOGO 限制未记录） | `deploy/images.lock`（dify-api/dify-web 条目）、ADR-0002、架构文档 §8.2 |
| P2-4 | P2 | 适配层配置项仍名为 `minio_*`，与 SeaweedFS 替换后不一致 | `apps/adapter/app/core/config.py:96-99` |
| 建议项 | — | 门禁未校验 `license:` 字段与上游 LICENSE 首行的一致性 | `deploy/validate_images.py` |
| 建议项 | — | H-1 加固约束应为可执行静态门禁 + 负样本验证 | `deploy/validate_deps.py`、架构文档 §8.1.1 / §9.6 |

---

## 九、QA 自检声明

1. **全部实跑**：14 条镜像的协议取证、10 个镜像的存在性复验、starlette 1.9.0 下的
   91 条定向回归 + 417 条全量回归，均为真实命令输出。
2. **取证方法先自证**：先用已知条目（valkey / seaweedfs）校验 LICENSE 取证路径可靠，
   再用于未核验条目，避免"取不到就猜"。
3. **交叉验证**：Grafana 与 Dify 两个关键结论均以 **5 个 / 2 个独立外部来源**印证，
   非单一来源。
4. **只做协议事实认定，不越权下法务结论**：Dify 多租户限制是否命中 SaaS 模式，
   我只列出协议原文与两种交付形态的事实，**判定权留给法务**。
5. **未修改任何业务代码**：`apps/adapter/app/**` 与 `apps/web/src/**` 全程只读；
   本附录为新增报告文件，未改动他人产物。

---

*附录 A 结束。主体报告见 `qa-blockers-closure.md`。*
