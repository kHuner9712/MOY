# Keycloak issuer 可达性与 realm 配置设计（R4 / R5 / R8）

| 项 | 内容 |
| --- | --- |
| 文档 | `docs/design-2026-11-02-keycloak-realm.md` |
| 版本 | v1.0（2026-11-02） |
| 作者 | 高见远（架构师） |
| 上游 | `docs/design-2026-11-02-security-deploy.md`（主文档，§4.1 R4/R5 由此展开） |
| 基线 | `main @ e074372` |
| 性质 | **阻断项核查 + 配置设计**。只读，不改代码/配置 |
| 一句话结论 | **三套编排的 Keycloak 全部不可达（浏览器侧）；且即便打通可达性，令牌校验算法也不匹配（HS256 vs RS256）——两者都是上线阻断项，必须与 P0-5 同批。** |

---

## 0. 结论摘要

### 能力边界（V1.0 定稿，可直接对外引用）

> **V1.0 仅支持 Keycloak 形态的身份提供方（含客户自建 Keycloak）。**
> **Auth0 / Okta / Azure AD / 其他非 Keycloak IdP 不在 V1.0 范围内**，计划在 V1.1 提供。

这条不是"没来得及做"，而是**范围声明**：V1.0 的身份链路在四处绑定了 Keycloak 形态——
`keycloak_base_url` + `keycloak_realm`（`config.py:146-147`）、自省端点
`/realms/{realm}/protocol/openid-connect/token/introspect`（`keycloak_client.py:26`）、
以及依赖 `realm_access` / `attributes` 等原生字段的用户资料与角色映射（`identity_service.py`）。
换 IdP 要改的是**整条链路**，不是某个配置项。

**现场话术（供销售/交付直接引用）**：客户说"我们用的是 Okta"时，
答"V1.0 以 Keycloak 作为身份底座，支持客户自建 Keycloak 实例并接入既有 AD/LDAP；
非 Keycloak 的 IdP 适配在 V1.1 规划中"——不要答"我们支持标准 OIDC"，
那会让客户合理地期待 Okta 能直接接。

### R4（issuer 可达性）：**不可达，三套编排全部不可达**

浏览器拿不到 issuer，不是因为地址配错了，而是**根本没有暴露路径**：三套 compose 里 `keycloak` 都没有任何 `ports` 映射（只在内部网络里），而边缘 nginx（只有 lite 有）的 location 清单里**没有 `/realms/` 与 `/resources/`**，请求会落到 `location /` 交给 Next.js 变成 404。叠加 `OA_WEB_OIDC_ISSUER` 在两个 `.env` 示例里都是**空值**，`install.sh` 也从不写它——登录页直接显示"统一登录尚未配置"，连跳转都不会发生。

**最小改法（推荐）**：新增单一变量 `OA_PUBLIC_BASE_URL` → 注入 `KC_HOSTNAME` + `KC_PROXY_HEADERS=xforwarded` + 边缘两条 location + 把三个 `OA_WEB_OIDC_ISSUER` 类变量填上。**full 栈还需给 keycloak 加一张 `officeai-frontend` 网**（它现在只在 backend 网，边缘够不到它）。

### R8（并列阻断项，核查中新发现）：**令牌校验算法不匹配**

适配层用 **HS256 + 共享密钥** 验签（`security.py:162-173` + `.env.example:198-200`），而 Keycloak 默认 realm 签 **RS256**。全仓 `jwks` / `PyJWK` / `RS256` **0 命中**。所以：**R4 打通之后，登录能到回跳页，但每一个后续 API 调用都会 401**。必须实现 JWKS 验证（`cryptography` 已是直接依赖，`PyJWKClient` 可直接用）。

### R5（realm 与有效期）：**V1.0 必须做服务端惰性刷新，不做"拉长 access TTL"的替代方案**

最终取值：Keycloak `accessTokenLifespan=300s`（保持默认）、`ssoSessionIdleTimeout=1800s`、`ssoSessionMaxLifespan=28800s`、`revokeRefreshToken=true`、`refreshTokenMaxReuse=0`；平台会话（Redis）空闲 30 min 滑动 / 绝对上限 8 h / 惰性刷新节流 15 min。
理由：若不做刷新，access TTL 必须拉到 8 h 才能"一个工作日不被打断"，那等于把令牌泄露可用窗口从 5 分钟放大到 8 小时——**与 P0-5 刚做的收口自相矛盾**。而 BFF 形态下 refresh token 本来就在服务端，刷新是既有能力的一部分，前端零改动。

---

## 1. R4：Keycloak issuer 可达性核查

> **状态（2026-11-04 审计更正）**：本节为 **2026-11-02 的快照**；其中「三套都没有 `KC_HOSTNAME`」
> 与「均未设置 `KC_PROXY_HEADERS`」已被后续改动推翻（`docker-compose.yml:498,501`、
> `docker-compose.saas.yml:349,352`、`docker-compose.lite.yml:176,179` 均已设置）。
> 当前拓扑与端口口径以 `docs/deployment-guide.md` §12.5 为准。

### 1.1 配置现状（逐项实测）

| 配置项 | 实际取值 | 位置 |
| --- | --- | --- |
| `KC_HOSTNAME` | **未设置**（三套都没有） | 全仓 `grep` 0 命中 |
| `KC_HOSTNAME_STRICT` | `"false"` | `docker-compose.yml:492`、`docker-compose.saas.yml:343`、`docker-compose.lite.yml:169` |
| `KC_HTTP_ENABLED`（环境变量） | `"true"` | `docker-compose.yml:493`、`docker-compose.lite.yml:170`；**saas.yml 无此环境变量** |
| `KC_PROXY` / `KC_PROXY_HEADERS` | **均未设置** | 全仓 `grep` 0 命中 |
| keycloak 启动命令 | `["start","--http-enabled=true","--http-port=8080"]` | `docker-compose.yml:486`、`docker-compose.saas.yml:337` |
| keycloak 启动命令（lite） | 上述 + `--http-management-port=9000 --health-enabled=true` | `docker-compose.lite.yml:157-161` |
| keycloak 网络 | full `[officeai-backend]` / saas `[officeai]` / lite `[officeai-lite]` | `:505` / `:350` / lite 同网 |
| keycloak 宿主端口映射 | **三套均无** | YAML 解析：full 只有 milvus/object-store/adapter/web/prometheus 有 `ports`；lite 只有 `proxy`(80) 与 `dify-web`(127.0.0.1:3001) |
| `OA_WEB_OIDC_ISSUER` | **空** | `deploy/.env.example:374`、`deploy/.env.lite.example:66` |
| `OA_WEB_OIDC_CLIENT_ID` | **空** | `deploy/.env.example:335` |
| `OA_JWT_ISSUER` | `officeai` | `deploy/.env.example:200` |
| `OA_JWT_ALGORITHM` | `HS256` | `deploy/.env.example:198` |
| `install.sh` 是否写 OIDC 变量 | **否**（`grep OIDC_ISSUER\|OIDC_CLIENT\|realms` 于 `deploy/install.sh` → 0 命中） | — |
| 仓库是否有 realm 导入文件 | **没有**（`find` 无 `*realm*`；`grep rsa-generated\|realm.*officeai` 于全仓 json/yaml → 0 命中） | — |

> `saas.yml` 未设 `KC_HTTP_ENABLED` 环境变量不构成问题：它的启动命令里已有 `--http-enabled=true`（`:337`）。这是"环境变量当契约、命令行当入口"的双写惯例，lite 的注释（`docker-compose.lite.yml:171-173`）说明了这一点。

### 1.2 判定依据

两条独立证据，任一条成立即"浏览器不可达"：

1. **Docker 网络语义**：容器未声明 `ports` 时，端口只在所属 bridge 网络内可达；宿主机与外网无路由。三套编排的 `keycloak` 都没有 `ports`，因此只有同网容器（`adapter`、`postgres` 等）能访问 `http://keycloak:8080`。
2. **边缘 location 清单**：`deploy/nginx/lite.conf` 的全部 location 是 `= /healthz`、`/api/`、`= /readyz`、`= /metrics`、`^~ /files/artifacts/`、`/`。`/realms/...` 与 `/resources/...` 都不匹配前五条，必然落到 `location /` → `web:3000`（Next.js）→ 404。`full` 与 `saas` 更是**连边缘服务都没有**。

### 1.3 三套编排分别结论

| 编排 | keycloak 网络 | 宿主端口 | 边缘是否有 `/realms/` | 边缘能否解析 keycloak | **浏览器可达 issuer** |
| --- | --- | --- | --- | --- | --- |
| `docker-compose.yml`（全量私有化） | `officeai-backend` | 无 | 无边缘服务 | **不能**（边缘在 `officeai-frontend`，keycloak 不在该网） | **✗** |
| `docker-compose.saas.yml` | `officeai`（单网） | 无 | 无边缘服务 | 能（同网） | **✗** |
| `docker-compose.lite.yml` | `officeai-lite`（单网） | 无 | **无**（`lite.conf` 无该 location） | 能（同网） | **✗** |

**附加阻断**：`OA_WEB_OIDC_ISSUER` 与 `OA_WEB_OIDC_CLIENT_ID` 在示例里都是空值，`login/page.tsx:42,48` 判定 `IS_UNIFIED_LOGIN_READY=false`，点击登录只弹 `统一登录尚未配置，请联系管理员`（`:103`）。**即使打通了网络与本页可达性，登录入口依然是灰的**——这两件事必须一起改。

### 1.4 issuer 会被 Host 头污染（实测，这是改法的关键依据）

`KC_HOSTNAME_STRICT=false` 且不设 `KC_HOSTNAME` 时，Keycloak **用请求的 `Host` 头动态生成 issuer**。我用真实 `quay.io/keycloak/keycloak:26.8.1` 起了一个实例实测：

```
默认 Host            → issuer = http://localhost:18080/realms/officeai
Host: platform.example.com → issuer = http://platform.example.com/realms/officeai
Host: evil.attacker.com    → issuer = http://evil.attacker.com/realms/officeai   ← 跟着变了
```

设 `KC_HOSTNAME=https://platform.example.com` 之后：

```
Host: evil.attacker.com    → issuer = https://platform.example.com/realms/officeai  ← 固定，不再跟着变
带 X-Forwarded-Proto: https → authorization_endpoint / jwks_uri 全部为 https://platform.example.com/...
```

> **实测口径说明**：上述测试跑在 `start-dev` 模式下。issuer 由 `Host` 派生 / 由 `KC_HOSTNAME` 固定这一机制在 `start` 下同源（由 `hostname-strict=false` 决定，我们的生产配置显式设了该值），结论可用；但**生产形态应在首次部署时复验一次**（判据见 §4）。

由此得出两条设计约束：

1. **必须设 `KC_HOSTNAME`**，用固定值而不是"让 Host 决定"。否则 issuer 是一个攻击者可控字符串，而 `OA_JWT_ISSUER` 是固定值 → 两者对不上时表现为"所有令牌 401"，且不同入口访问会得到不同 issuer（多入口/多域名现场必然踩）。
2. **边缘必须把真实 `Host` 传给 Keycloak**，且 `X-Forwarded-Proto` 要正确（TLS 终止在边缘时 Keycloak 必须知道对外是 https，否则生成的端点全是 `http://`，浏览器直接拦混合内容）。

### 1.5 最小改法（推荐方案：边缘同源挂载 + `KC_HOSTNAME` 固定）

选"同源子路径挂载"而不是"独立 SSO 域名"，理由：① 单 origin 让 P0-5 的 Cookie 与 CSP 都最简（无跨域、`connect-src 'self'` 即可）；② 只多一个 TLS 证书；③ 客户现场少配一个 DNS。代价是 app 的 Cookie 会随请求带到 `/realms/`（`Path=/`），考虑到 Keycloak 是我们自己的组件，接受。

#### 改动 1：单一真源变量

`deploy/.env.example`（+ `.env.lite.example`）新增：

```bash
# 平台对外规范地址（含协议，不带尾斜杠）。三处用途：KC_HOSTNAME / OA_WEB_OIDC_ISSUER / OA_JWT_ISSUER 的派生基准
OA_PUBLIC_BASE_URL=https://officeai.example.com
# 派生（不要手写，由 install.sh 填）
#   OA_WEB_OIDC_ISSUER = ${OA_PUBLIC_BASE_URL}/realms/${OA_KEYCLOAK_REALM}
#   OA_JWT_ISSUER      = 同上
```

**为什么派生而不是让人手填三个**：`OA_JWT_ISSUER` 与 `OA_WEB_OIDC_ISSUER` 只要差一个字符（多/少尾斜杠）就是"全部 401"，而这个错误在联调时表现极难归因。派生是唯一能保证一致的做法。

##### ⚠ 与 lite 既有 `OA_LITE_PUBLIC_URL` 的冲突必须显式解决（v1.2 补充）

`OA_LITE_PUBLIC_URL` **已经存在**（`.env.lite.example:54`，默认 `http://localhost`），而且有两条现役机制在维护它：

- `install.sh:396-405` 的 `apply_proxy_port_on_new_file()` 按 `--proxy-port` 推导并 `env_set`（`:403`,`:405`）；
- `validate_lite_up.sh:190-194` 校验"入口端口与对外基址一致"，不一致就 `warn`。

若 `Secure` 派生改读 `OA_PUBLIC_BASE_URL` 而 lite 现场只维护老变量，结果是**两个对外基址、新的那个恒为空** → `https://` 判定恒为 false → 反向代理后 `Secure` **永远缺席**。这是最坏的形态：配了、看起来也生效了、其实没生效。

**收敛方案（采用"插值"，不改 `install.sh`）**：

| 编排 | 写法 | 理由 |
| --- | --- | --- |
| `lite` | `OA_PUBLIC_BASE_URL: ${OA_LITE_PUBLIC_URL}`（在 adapter/web/keycloak 服务里插值） | 单一事实来源仍是 `OA_LITE_PUBLIC_URL`；`install.sh` 一个字不改；`validate_lite_up.sh` 的既有校验继续有效 |
| `full` / `saas` | `${OA_PUBLIC_BASE_URL:?必须设置平台对外规范地址}`（**fail loud**） | 标准栈原本没有对外基址变量，属于新增 |

**标准栈不给默认值（2026-11-02 已拍板，与上面 `:?` 是同一套机制）**：

> 不给默认值是有意的：它是 issuer 与 Cookie `Secure` 的**双重派生源**，一个错值会同时污染这两条链路，且错误要等到用户点登录才暴露。空值让它在 `compose up` 阶段就失败——**判据必须简单到不需要维护**。

三条支撑理由，按力度排序：

1. **失败点更早，信息量差几十倍**。模板留空 → `compose up` **插值阶段**就拒绝（"required variable missing"），现场只可能是"你没填"；而"给默认值 + 启动校验拦"是**容器起来之后 adapter 启动时**才拦，同样 fail-loud，但人第一反应会去怀疑镜像、数据卷、依赖顺序。**同样是失败，早一层就是几十倍的信息量。**
2. **判据必须简单到不需要维护**。"什么算配置错误"没有确定答案：`127.0.0.1` / `[::1]` 好判（`ipaddress.is_loopback` 语义判定即可，不会漏），但客户的 `192.168.1.50` 直连、`0.0.0.0`、`host.docker.internal` 该不该拦，**每一个都要一次判断，每次判断都可能误伤真实现场**。问题不在于枚举是否完整，而在于**这个判据本身在回答一个没有确定答案的问题**。
3. **能复用就不要新增平行的第二套机制**。`:?` 已经存在且在别处生效，再加一条 loopback 校验就是第二个判据——**两个判据将来会分叉**，这比"少写几行代码"重要得多。

> 被否的选项（③ 保留默认值 + 启动校验拦 loopback）**不删除记录**，理由见 §2.4.3 —— 下一轮若有人再提 loopback 校验，那行能省一次讨论。

**当前代码状态与目标差一步**：`.env.example:64` 现在仍是 `OA_PUBLIC_BASE_URL=http://localhost`（字面量），**需按本决策改为空**，并同步 `qa/phase4-verify.sh`。改的时候有一条硬约束（主理人 2026-11-02 明确）：**不许把 QA 脚本改成"忽略该变量"来换绿灯**——那等于拔掉门禁的牙齿。正确改法是让脚本断言"该键存在但为空/未设置时，`compose config` 必须失败"。

**`OA_PUBLIC_BASE_URL` 的"生成路径"**：标准栈由 `install.sh` 按与 lite 同款的逻辑（`scheme` + `host` + 可选 `port`）写入；`.env.example` 里留空并注明"必填，由 install.sh 生成"。
（这不触发 `validate_secrets.py` 的生成路径门禁——该门禁只针对含 `CHANGE_ME` 的密钥类变量；但按同样纪律给出生成路径，现场才不需要手工拼 URL。）

#### 改动 2：keycloak 服务环境变量（三套都改）

```yaml
      KC_HOSTNAME: ${OA_PUBLIC_BASE_URL:?必须设置平台对外地址}
      KC_HOSTNAME_STRICT: "false"          # 保留现值，让 KC 接受边缘转发的 Host
      KC_PROXY_HEADERS: xforwarded         # Keycloak 25+ 取代 KC_PROXY=passthrough
```

`KC_PROXY_HEADERS` 的作用：让 Keycloak 采信边缘的 `X-Forwarded-*`，从而在 TLS 由边缘终止时生成 `https://` 端点。**不设它的话，TLS 边缘后面 Keycloak 会坚持生成 `http://` 的 `authorization_endpoint`**，表现为"登录页跳到 http，被浏览器/边缘拦掉"。

#### 改动 3：网络（只有 full 栈需要）

`docker-compose.yml` 的 `keycloak.networks` 由 `[officeai-backend]` 改为 `[officeai-backend, officeai-frontend]`，与 `adapter`（`:605`）同款双网。
`saas` / `lite` 都是单网且边缘已在该网，**不需要改**。

> **记录 ARC-01 的修订理由**：原不变量是"adapter 是唯一双挂网的服务"。这里必须破例，因为**身份底座要对外提供登录页，其可达性天然与 web 同级**；替代方案"把边缘加入 backend 网"会让最外层组件直连 PG/Milvus，是更差的选择。同时明确约束：**边缘不暴露 `/admin/`**（见改动 4），keycloak 的管理面仍然只能从 backend 网访问。

#### 改动 4：边缘新增 `/realms/` 与 `/resources/` 两条 location（`/sso/` 与完整六条清单见 §4.1.1）

```nginx
    # 身份底座：只放行登录所需的两个前缀，**不放行 /admin/**
    location ^~ /realms/ {
        proxy_pass http://keycloak:8080;
        proxy_http_version 1.1;
        proxy_set_header Connection        "";
        proxy_set_header Host              $host;          # KC 用它生成 issuer，必须传真实值
        proxy_set_header X-Forwarded-Proto $edge_proto;    # 见下方 map
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_read_timeout 60s;
        # 安全头就地重复（add_header 不继承，见 lite.conf:38-40）
    }

    location ^~ /resources/ {          # 登录页的静态资源（主题 CSS/JS/图片）
        proxy_pass http://keycloak:8080;
        proxy_http_version 1.1;
        proxy_set_header Connection        "";
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $edge_proto;
        proxy_read_timeout 60s;
    }
```

`$edge_proto` 用 map 定义在 `nginx.conf`（**只接受"升级到 https"，永不接受降级**）：

```nginx
    # 上游 LB 终止 TLS 时会带 X-Forwarded-Proto: https。
    # 只采信 https（升级方向安全：最坏结果是我们自己以为在 TLS 后面），
    # 绝不采信客户端声称的 http（那会让 KC 生成 http 端点，可用于降级攻击）。
    map $http_x_forwarded_proto $edge_proto {
        default $scheme;
        https   https;
    }
```

**为什么不暴露 `/admin/`**：Keycloak 管理台是"能改一切"的界面，暴露在公网等于把身份底座的完全控制权挂到互联网上。运维需要时走 `docker compose exec` 或 SSH 隧道到 `officeai-backend` 网访问。这一条要写进运维手册。

> ⚠️ 若 keycloak 服务名在边缘所在网不可解析（full 栈改网络前），`proxy_pass http://keycloak:8080` 会在启动时解析失败导致 nginx 起不来。因此**改动 3 必须先于改动 4**；且按既有惯例应改为变量 + `resolver`（`lite.conf:18-23` 记录的坑：静态 upstream 换 IP 后 502 直到手工 reload）：
> `set $keycloak_backend "keycloak:8080";` 然后 `proxy_pass http://$keycloak_backend;`

#### 改动 5：OIDC 变量填值

```bash
OA_WEB_OIDC_ISSUER=${OA_PUBLIC_BASE_URL}/realms/officeai
OA_WEB_OIDC_CLIENT_ID=officeai-adapter
OA_JWT_ISSUER=${OA_PUBLIC_BASE_URL}/realms/officeai     # 注意：不是 officeai
OA_JWT_ALGORITHM=RS256                                  # 见 §2
```

`OA_JWT_ISSUER` 从 `officeai` 改为完整 realm URL 是**必须的**：`_decode_token` 把它传给 `jwt.decode(issuer=...)`（`security.py:171`），PyJWT 会校验 `iss` 声明等于该值；而 Keycloak 的 `iss` 是完整 URL。

#### 与其它任务的同批关系

| 依赖 | 关系 |
| --- | --- |
| **P0-5 `/sso/*`** | **必须同批**。`/sso/complete` 的服务端兑换要调 Keycloak token endpoint（服务端可达，本就没问题）；但**浏览器必须先能打开登录页**，否则 `/sso/start` 生成的 authorize_url 打不开 |
| **P0-4 边缘模板** | **必须同批**。`/realms/`、`/resources/`、`/sso/` 三条 location 一次性加进 `edge.conf`，否则又是"漏一条 location → 恒定 404"（与 `e074372` 修的 S-03 同款） |
| **P0-4 网络改动** | 前置：full 栈的 keycloak 双网改动必须先合，否则边缘起不来 |
| **R8 JWKS** | **必须同批**。可达性打通后，令牌仍会因算法不匹配全部 401 |

---

## 2. R8：令牌校验算法不匹配（核查中新发现的并列阻断项）

### 2.1 证据

| 事实 | 位置 |
| --- | --- |
| 验签用 `conf.jwt_secret` 作密钥、`algorithms=[conf.jwt_algorithm]` | `apps/adapter/app/core/security.py:166-173` |
| `jwt_algorithm` 默认 `HS256` | `config.py:71`；`.env.example:198 OA_JWT_ALGORITHM=HS256` |
| `jwt_secret` 默认空、示例为 `CHANGE_ME_generate_with_openssl_rand_hex_32` | `config.py:70`；`.env.example:197` |
| 生产启动**强制要求** `auth_mode=oidc` 且 `jwt_secret` 非空、非占位、够长 | `security.py:932-981`（`validate_auth_startup`） |
| 全仓 `jwks` / `PyJWK` / `RS256` / `openid-configuration` **0 命中** | `grep` 于 `apps/adapter/app/**.py` |
| 有 `KeycloakClient.introspect()`（令牌自省）但**从未被鉴权链路调用** | `app/clients/keycloak_client.py:26,146`；调用方只有 `identity_service.py`（用 user/group/role 相关方法） |
| 鉴权唯一入口 `get_auth_context` → `AuthContext.resolve(extract_bearer_token(...))` → `_decode_token` | `security.py:454-477` |

**结论**：生产鉴权路径的隐含模型是"**与签发方共享 HS256 密钥**"，而 Keycloak 默认 realm 的签名算法是 **RS256 + JWKS 公钥分发**。两者不兼容 → 真实 Keycloak 令牌在 `_decode_token` 处必然抛异常 → 20002。所以 R4 打通后仍然"登录不了"。

### 2.2 两条出路，推荐 A

| 方案 | 做法 | 代价 | 判定 |
| --- | --- | --- | --- |
| **A. 适配层实现 JWKS/RS256 验证** | 按 `jwt_algorithm` 分流：RS256 走 `jwt.PyJWKClient(issuer + '/protocol/openid-connect/certs')`（带缓存与 kid 轮换），HS256 保留（兼容现场对称签发） | 约 60 行 + 单测。`cryptography==50.0.2` **已是直接依赖**（`apps/adapter/pyproject.toml:52`），`PyJWKClient` 开箱可用 | **推荐** |
| B. 把 realm 改成 HS256 签名（`hmac-generated`），把该 key 抄进 `OA_JWT_SECRET` | 零代码 | ① 该密钥可**为任意用户伪造令牌**（RS256 下没有私钥则不可能）；② 密钥要从 admin API 导出并按"共享密钥"管理，轮换无流程；③ 与 PKCE 公开客户端的模型冲突 | **否决** |

**方案 A 的一个额外好处**：`OA_JWT_SECRET` 继续保留是**必需的**——产物下载令牌用它对 payload 做 HMAC（`app/core/artifact_token.py:66-87,128,149`）。因此 `validate_auth_startup` 的"jwt_secret 必须非空/非占位/够长"这一组校验**完全不用放宽**，RS256 与产物签名能共存。这是选 A 而不是"删掉共享密钥"的关键理由。

### 2.3 方案 A 的实现要点

1. `_decode_token` 分流：
   - `jwt_algorithm` 以 `RS` 开头 → `PyJWKClient`（按 `kid` 取键，**内存缓存 + 未命中时刷新一次**，避免 JWKS 轮换后永久 401 与每请求打 Keycloak 两种极端）；
   - 其他（HS256）→ 走现有 `jwt_secret` 路径（私有化对称签发现场）。
2. **JWKS 地址必须走内网，不能用公网 issuer 派生**（本节 v1.2 修正，见下）：默认取
   `{OA_KEYCLOAK_BASE_URL}/realms/{OA_KEYCLOAK_REALM}/protocol/openid-connect/certs`，
   **不是** `{OA_JWT_ISSUER}/protocol/openid-connect/certs`。
   这两件事用的是**不同的基准**，不能合并：
   - `iss` **校验**用 `OA_JWT_ISSUER`（公网值，必须与 Keycloak 实际发出的 `iss` 逐字符相等）；
   - **JWKS 拉取**用 `OA_KEYCLOAK_BASE_URL`（内网值，`config.py:146` 默认 `http://keycloak:8080`）。

   **为什么不能都走公网 issuer**（这是 v1.0 草稿的错误，落地会变成隐性故障）：
   - 私有化现场的内网 DNS **通常没有**公网域名的记录——公网名靠外部 DNS/边缘对外，容器内解析会直接失败；
   - 即使能解析，也要让容器回环绕边缘一圈，且必须信任边缘证书（自签现场**必然失败**）；
   - 多一跳就多一个失败面，而 JWKS 是每个请求都要用的热路径。

   **这样拆不会导致 issuer 不一致**：实测证明，设了 `KC_HOSTNAME` 后，从**内网地址**拉 discovery 得到的 `issuer` 仍是公网值（见 §1.4 的实测输出）。所以"内网取 JWKS + 公网值校验 `iss`"完全自洽。

   **这不是新增约定，而是沿用编排早已确立的内外分离**：三套 compose **无条件**注入内网地址
   （`docker-compose.yml:571` / `docker-compose.saas.yml:385` / `docker-compose.lite.yml:435`
   均为 `OA_KEYCLOAK_BASE_URL: http://keycloak:8080`），且 `docker-compose.yml:567-570` 的既有注释
   已经把"字段名 ↔ 环境变量名"的映射规则写清楚了。`KeycloakClient` 也一直用它做令牌自省
   （`INTROSPECT_PATH`，`keycloak_client.py:26`）——**JWKS 只是第三个消费者**。

   **不做运行期回落（重要）**：不存在"回落到从 issuer 派生"的分支。
   理由见本节末的"定稿理由"，一句话版本：**V1.0 的 IdP 固定为 Keycloak 形态，这是范围声明。**
   JWKS 地址**恒为** `{OA_KEYCLOAK_BASE_URL}/realms/{OA_KEYCLOAK_REALM}/protocol/openid-connect/certs`，纯配置态推导，确定性、可预测。

   > 关于 `OA_KEYCLOAK_BASE_URL` 的语义（保留，它是判据的前提）：它指的是
   > "**适配层视角的 IdP 地址**"而不是"我们自己的 keycloak"——正因为它的语义这么宽，
   > 才必须额外声明"V1.0 只接受 Keycloak 形态的 IdP"（见 §0 能力边界）。
   > **不能**从"语义宽"推出"不需要分支"，那个推论有漏洞，见下方论证纠错。

   **为什么必须禁止"拉取失败就换数据源"**：那会把
   「网络抖动 → 悄悄切到公网地址 → 自签现场证书不受信 → 401」变成一个新的隐性故障源，
   **症状与本项要修的问题完全同款**（都是 401），排障时无法区分。
   同理，"`keycloak_base_url == 默认值 才走内网"这种判定也不采用——客户把 IdP 部署在
   `http://idp.internal:8080` 这类非默认地址时会静默走错分支。
   **若将来出现非标准 realm 路径的 IdP**，再加显式 `OA_JWKS_URL` 覆盖项（显式优先、不做探测）；
   V1.0 不加，因为它会多一个可配错的项，而当前没有这个现场。

   **"外部 IdP 回落"这个备选方案曾被提出，最终未采纳（2026-11-02 定稿）。** 但请注意：
   **"未采纳"的理由是范围声明，不是技术妥协**；本文件 v1.2 曾给出的那个理由**有漏洞，已作废**。

   **① 先记一次论证纠错（避免后人重走弯路）**

   v1.2 的论证是：「`keycloak_base_url` 的语义是"适配层视角的 IdP 地址"，外部 IdP 也走这条路径，
   所以不需要分支」。**这个论证只防住了一半**：

   - 它防住的是"**网络不可达**"（内网 DNS 无记录、绕回边缘需信任自签证书）；
   - 它**没防住"URL 拼错"**：路径模板 `/realms/{realm}/...` 是 **Keycloak 专有**的。
     把 `keycloak_base_url` 指向 Auth0 / Okta / Azure AD 后，拼出来是
     `https://login.example.com/realms/officeai/protocol/openid-connect/certs` → **必然 404**；
     而 OIDC 标准公钥位置是 `{issuer}/protocol/openid-connect/certs`，**没有 `/realms/{realm}` 这一段**。

   一句话：**非空 ≠ 路径形态正确。** 这是两种不同的失效模式，不能用一个论证互相顶替。

   **② 真正的理由（定稿）：整副骨架假设 IdP 就是 Keycloak，这是范围声明**

   不是"不需要分支"，而是"V1.0 不支持非 Keycloak IdP"。证据是三件套把这个假设绑死了：

   | 绑定物 | 位置 | 假设 |
   | --- | --- | --- |
   | `keycloak_base_url` + `keycloak_realm` | `config.py:146-147` | realm 是 Keycloak 的概念 |
   | `INTROSPECT_PATH = /realms/{realm}/protocol/openid-connect/token/introspect` | `keycloak_client.py:26` | 自省端点也是 Keycloak 专有路径 |
   | 用户资料 / 组织成员 / 角色管理 | `identity_service.py` | 依赖 `realm_access` / `attributes` 等 Keycloak 原生字段 |

   要让非 Keycloak IdP 真正可用，**要改的不是 JWKS 一处，而是"自省 + 用户资料 + 角色映射"一整条链路**。
   只给 JWKS 加分流，会产生"能验签但拿不到角色"的**半可用状态**——比明确不支持更糟，
   因为它会在登录成功那一刻给出"看起来能用"的错觉。

   **②b 补一条更硬的事实：那个回落分支即使加上，本身也不成立**

   （此段修正一处过强表述：本文件曾写"该分支永远走不到、是死分支"——**不准确，已改**。
   对非 Keycloak IdP，分支**会被走到**。但走到也没用，原因如下。）

   备选分支的产物是 `{issuer}/protocol/openid-connect/certs`。
   **这个路径同样是 Keycloak 专有约定，不是 OIDC 标准**（RFC 8414 / OIDC Discovery 规定的是
   从 `{issuer}/.well-known/openid-configuration` 读 `jwks_uri` 字段，而不是任何固定路径）。

   **实测证据（任何人可复现，2026-11-02）**：

   ```bash
   $ curl -s https://accounts.google.com/.well-known/openid-configuration \
       | python -c "import json,sys; d=json.load(sys.stdin); print(d['issuer']); print(d['jwks_uri'])"
   https://accounts.google.com
   https://www.googleapis.com/oauth2/v3/certs
   ```

   两者**连域都不同**（`accounts.google.com` vs `www.googleapis.com`）。再直接验反证：

   ```bash
   $ curl -s -o /dev/null -w '%{http_code}\n' https://accounts.google.com/protocol/openid-connect/certs
   404
   ```

   即：把 Keycloak 的惯用路径套到 Google 上，返回的是 **404**，不是公钥。
   这条实证证明的是**一般命题**——各家 `jwks_uri` 位置不同，不存在可拼的固定模板。

   主流非 Keycloak IdP 的公钥位置对照（google 行即上述实测）：

   | IdP | 实际 JWKS 位置 | `{issuer}/protocol/openid-connect/certs` 的结果 |
   | --- | --- | --- |
   | Keycloak | `{issuer}/protocol/openid-connect/certs` | ✅ 正确 |
   | **Google** | **`https://www.googleapis.com/oauth2/v3/certs`（实测）** | **❌ 404（实测）** |
   | Auth0 | `https://{tenant}/.well-known/jwks.json` | ❌ 404 |
   | Microsoft Entra ID | `https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys` | ❌ 404 |
   | Okta（Workforce） | `https://{domain}/oauth2/{authServerId}/v1/keys` | ❌ 404 |

   > 表中 Keycloak / Google 两行为本次实测（命令与输出见上）；Auth0 / Entra / Okta 三行取自各家公开文档，
   > **我未逐一实拉**——但它们只是"各家不同"的补充例证，**结论不依赖它们**：
   > Google 一行已足够证伪"存在可拼的固定模板"这个命题。

   所以两种情形下该分支都不产生额外价值：
   **IdP 是 Keycloak 形态**时，内网派生本来就对，分支走不到（或走到了结果相同）；
   **IdP 不是 Keycloak 形态**时，分支会走到，但拼出的仍是 Keycloak 专有路径 → 照样 404。
   换句话说：**这条分支只对 Keycloak 形态有效，而 Keycloak 形态本来就不需要它。**

   推论也落到 V1.1 的后继动作上：**不要用"路径模板分流"支持外部 IdP**，
   唯一标准做法是读 discovery 的 `jwks_uri`（或显式配置 `OA_JWKS_URL`）。

   **③ 为什么也不采用"按 issuer 形态嗅探"的判据**

   曾有一个版本定为「`iss` 路径含 `/realms/` → 内网派生；不含 → 从 issuer 派生」。
   **该判据作废**：它只解决 JWKS 一个点，却让"外部 IdP 可用"变成一个局部成立的假象
   （自省仍然 404）。而且形态嗅探天然脆弱（旧版 Keycloak 是 `/auth/realms/`，
   用"等于"会误判，用"包含"则任何路径里带 `/realms/` 的外部 IdP 都会被误判）。
   **判据越聪明，误判越难排查**——这类"按字符串形态猜供应商"的逻辑不进入安全路径。

   **④ 后继动作（V1.1，若确有非 Keycloak 现场）**

   **首选：读 discovery 的 `jwks_uri`**（`{issuer}/.well-known/openid-configuration` → `jwks_uri`），
   这是 OIDC 标准做法，对任何合规 IdP 都成立；或提供显式 `OA_JWKS_URL` 覆盖项（显式优先、不做探测）。
   **不要**用"按 issuer 路径形态拼模板"的方式支持外部 IdP——见 ②b 的对照表，那样对 Auth0/Entra/Okta 全是 404。
   无论走哪条，**必须连带改自省 + 用户资料 + 角色映射**，禁止只做 JWKS 一侧。

   **⑤ 现状以代码为准**：`app/core/security.py::resolve_jwks_url` **不读 `jwt_issuer`**，
   且有测试正面锁定 ——
   `tests/unit/test_jwt_jwks.py::test_rs256_external_idp_still_hits_configured_idp_base`
   断言"外部 IdP 仍打配置的 IdP base"。
   ⚠ **下一轮实现者注意**：不要凭"应该有回落"的直觉把它加回来；先读 §0 的能力边界。
3. **JWKS 不可达时的 fail-closed**：与既有 fail-closed 纪律一致（`security.py:242-249` 的延迟判权白名单是同一思路），返回 20002 而不是放行。
4. **`keycloak_base_url` 为空时必须返回空串，不能拼相对路径**（落地期补记，比"会不会 404"更值钱）：

   若空 base 被拼成 `/realms/{realm}/protocol/openid-connect/certs`（一个**相对 URL**），
   它会被当成同源路径打到自己身上 → 得到一个与鉴权**无关**的 404，
   而错误码仍是 20002「令牌非法」。**排障方向被引向令牌，真因是配置**——这类"错误码指向错误方向"的故障，
   比 404 本身难查一个量级。

   因此实现为返回**空串**，由上层按"拉不到公钥"fail-closed（见 `test_rs256_empty_base_url_fails_closed`
   ——断言 20002 且**零网络请求**）。并在启动期前置拦截：
   `validate_auth_startup` 第 7 条，**`RS*` 算法 + 空 `keycloak_base_url` → 30004 拒绝启动**。
   **只拦 `RS*`**：HS256 对称签发的私有化现场不依赖它，不能把那些人挡在门外。

5. **"无分支"已由测试锁死**（防止后人凭直觉加回来）：
   `test_jwks_url_is_independent_of_jwt_issuer` 用 **4 种 `jwt_issuer` 形态**
   （realm URL / 外部 IdP URL / 非 URL 的旧占位 `officeai` / 空串）断言推导结果**逐字相同**。
   一旦有人加回回落分支，这条立刻红——**用"锁定不变式"比"删掉一个用例"强**：
   删用例只是去掉证据，锁不变式是让错误实现无法通过。

6. **日志只记来源、不记地址细节**：选中 JWKS 来源时打 `jwks_source_selected`，
   内容为 `{scheme}://{host}`（`security.py:265`）。够定位"打的是内网还是外部"，又不把完整路径与参数写进日志。
7. `OA_JWT_AUDIENCE` 要与令牌 `aud` 对齐：Keycloak access token 的 `aud` 默认可能是 `account`。**在 realm.json 里给 client 加 audience mapper**，把 `aud` 固定为 `officeai-adapter`，与现有 `OA_JWT_AUDIENCE=officeai-adapter`（`.env.example:199`）一致——**用 realm 适配已有配置，而不是反过来改配置**，可减少改动面。

### 2.4 结论：全链路只需新增 2 个环境变量（原设计 4 个，已收敛）

复核既有 `Settings` 后发现两个"本来就有、不必新加"的配置（`config.py:146-148`），据此收敛：

| 原计划新增 | 处置 | 理由 |
| --- | --- | --- |
| `OA_OIDC_TOKEN_ENDPOINT` | **删除**，改为派生 | `keycloak_base_url` + `keycloak_realm` 已存在；token endpoint 恒为 `{base}/realms/{realm}/protocol/openid-connect/token`。加一个可手填的同义变量，只会制造"两个基准不一致"的漂移面 |
| `OA_OIDC_CLIENT_ID` | **删除**，复用 `OA_KEYCLOAK_CLIENT_ID` | `keycloak_client_id` 已存在且默认值正确（`officeai-adapter`）。同一语义两个变量是典型的配置漂移源 |
| `OA_AUTH_COOKIE_SECURE` | **删除**，改为派生 | 由 `OA_PUBLIC_BASE_URL` 是否以 `https://` 开头决定。仍满足"`Secure` 只由配置决定、绝不读请求头"的约束（§2.4.6），且少一个可配错的项 |
| `OA_AUTH_COOKIE_ENABLED` | **保留** | 这是 §2.4.8 的**回滚开关**，必须可独立于其余配置切换 |
| `OA_PUBLIC_BASE_URL` | **保留（新增）** | 单一真源，派生 `KC_HOSTNAME` / 两个 issuer / Cookie `Secure` |
| `OA_AUTH_COOKIE_NAME` | **删除** | Cookie 名是代码常量，不需要现场配置；可配只会让"运维改了个名 → 前端读不到" |

**净新增 2 个键：`OA_PUBLIC_BASE_URL`、`OA_AUTH_COOKIE_ENABLED`。两者都不是密钥类、值里不含 `CHANGE_ME`**，
因此**不触发** `deploy/validate_secrets.py` 的门禁——按工程侧核实，该门禁的判据是
「**占位值必须有生成路径**」而**不是**「名字像密钥就拦」；且 URL 类取值走良性前缀白名单（`http://` / `https://`）。
所以这里不需要 `# 生成：...` 注释（但 `OA_PUBLIC_BASE_URL` 仍建议给出生成方式，见 §1.5——不是为了过门禁，是为了现场不必手拼 URL）。

> 说明：`OA_OIDC_TOKEN_ENDPOINT` 被裁掉的首要理由仍然是 §2.4 表里的"同一语义两个变量会漂移"，
> **不是**门禁（名字含 `TOKEN` 并不构成拦截条件）。此处不夸大裁剪的附带收益。

#### 2.4.1 落地状态（2026-11-02 轮 1a 实测核对）

| 项 | 状态 | 实测依据 |
| --- | --- | --- |
| `Settings.public_base_url` | ✅ **已加** | `config.py` 已有字段；`security.py:1230` `if not conf.public_base_url.strip():` → 30004 |
| `Settings.auth_cookie_enabled` | ⬜ **未加（有意）** | 与 `/sso/*`（轮 1b）同批提，避免出现"配了但没人读"的字段 |
| `OA_PUBLIC_BASE_URL` 注入 compose | ✅ **已加** | 三套 compose 均以 `${OA_PUBLIC_BASE_URL:?...}` 取（标准栈） |
| `validate_auth_startup` 第 6 条 | ✅ **已加** | `security.py:1131`（判据说明）/ `:1239`（`public_base_url_missing_in_production`）；**生产/预发为空即拒绝启动** |
| `validate_auth_startup` 第 7 条 | ✅ **已加** | `security.py:1208` `if uses_asymmetric_algorithm(conf.jwt_algorithm) and not conf.keycloak_base_url.strip():` → 30004 / `jwks_base_url_missing_in_production`（`:1216`）。**只拦 `RS*`**，HS256 对称签发现场不受影响 |
| JWKS 空 base 处置 | ✅ **已加** | `resolve_jwks_url` 空 base 返回**空串**（不拼相对路径）；`test_rs256_empty_base_url_fails_closed` 断言 20002 + **零网络请求** |
| "无分支"测试锁 | ✅ **已加** | `test_jwks_url_is_independent_of_jwt_issuer`：4 种 `jwt_issuer` 形态推导结果**逐字相同**，加回分支即红 |
| JWKS 来源日志 | ✅ **已加** | `security.py:265` `jwks_source_selected`，内容仅 `{scheme}://{host}` |
| `OA_JWT_SECRET` 启动校验 | ✅ **未放宽一个字** | 产物下载 HMAC 仍依赖它（`artifact_token.py`），故 `_jwt_secret_placeholder_reason` 路径原样保留 |

> 实跑核对：`pytest tests/unit/test_jwt_jwks.py -q` → **17 passed**（本次自跑确认，非转述）。
> 小瑕疵（不影响功能，改不改随轮次）：`test_jwks_url_is_independent_of_jwt_issuer` 的 docstring 写"三种 issuer 形态"，
> 而循环实际覆盖 **4 种**（多一个空串）。描述与断言不符时，以断言为准。

#### 2.4.2 两个基址变量必须单一真源（**落地约束，勿手改单个**）

`OA_PUBLIC_BASE_URL`（新）与 lite 既有的 `OA_LITE_PUBLIC_URL`（老）指向同一件事。**两者若各自被维护，`Secure` 派生会静默失效**（新变量恒空 → 判定恒 false → 反代后 Cookie 永远不带 `Secure`）。已落地的处置是**分形态、单一来源**：

| 形态 | 谁是源 | 谁被派生 | 实测依据 |
| --- | --- | --- | --- |
| **标准栈**（full / saas） | `base_url`（`install.sh` 计算出）；**模板留空，不在 `.env.example` 给字面量** | `env_set OA_PUBLIC_BASE_URL "${base_url}"` + 派生 `OA_JWT_ISSUER` / `OA_WEB_OIDC_ISSUER` / `OA_WEB_OIDC_CLIENT_ID` | `install.sh:413,448`（`OA_PUBLIC_BASE_URL="${base_url}" ...`）；**缺口**：`.env.example:64` 仍是字面量，待改为空（见 §2.4.3） |
| **轻量版**（lite） | `OA_LITE_PUBLIC_URL` | **不写** `OA_PUBLIC_BASE_URL`——编排里用 `OA_PUBLIC_BASE_URL: ${OA_LITE_PUBLIC_URL}` 插值过去 | `install.sh:422-426`（注释明确"不写 `OA_PUBLIC_BASE_URL`"）、`:467-469`（`backfill_derived_env` 只读不写） |

**纪律**：改端口/域名时**只改源**（标准栈改 `base_url` 来源、lite 改 `OA_LITE_PUBLIC_URL` 或 `install.sh --proxy-port`），
**不要单独去改 `OA_PUBLIC_BASE_URL` / `OA_JWT_ISSUER` / `OA_WEB_OIDC_ISSUER` 三者中任一个**——
它们差一个字符（多/少尾斜杠）就是"所有接口 401"，而这个错误在联调时表现极难归因。

**实测印证（轮 1a，lite 现场）**：`.env.lite` 里**没有** `OA_PUBLIC_BASE_URL` 这一行，但 `compose config` 解出：

```
OA_PUBLIC_BASE_URL: http://localhost:65200     ← 由 ${OA_LITE_PUBLIC_URL} 插值而来
KC_HOSTNAME:        http://localhost:65200     ← 同源
```

**这印证了插值确实生效，也印证了它的反向风险**：`compose config` 的输出取决于**替换顺序**——
`.env` 里的字面量与编排插值同时存在时，谁赢不是由直觉决定的。因此 `.env.lite.example:56-57`
已加**禁令注释**（"不要在轻量版写 `OA_PUBLIC_BASE_URL`，编排里是插值"），把"两个来源"降到"一个来源"。
这是本设计里唯一一处**靠注释而非代码**保证的约束，因此更需要在评审时检查。

#### 2.4.3 ✅ 已拍板（2026-11-02）：模板**不给**字面量默认值（选 ②）

**结论**：`deploy/.env.example` 的 `OA_PUBLIC_BASE_URL` **改为空值**，由 `install.sh` 生成；
缺失/为空时在 `compose up` **插值阶段**直接失败。同步修改 `qa/phase4-verify.sh`。

**两个层面要分清**（这是之前两次讨论混在一起的原因）：

| 层 | 结论 | 含义 |
| --- | --- | --- |
| compose 层 | ✅ 用 `:?` fail-loud，**不给 `:-` 兜底** | 变量缺失/为空 → `up` 直接失败 |
| 模板层 | ✅ **留空**（已拍板） | `.env.example` 不放字面量 |

**当前代码状态**：`.env.example:64` 仍是 `OA_PUBLIC_BASE_URL=http://localhost`（**与决策不符，待改**）；
`qa/phase4-verify.sh` 待同步。

**QA 脚本改动的硬约束**（主理人 2026-11-02 明确）：**不许把它改成"忽略该变量"来换绿灯**——那等于拔掉门禁的牙齿。
正确改法是断言"该键为空/未设置时 `compose config` 必须失败"，即**门禁断言失败行为，而不是绕过失败行为**。

**决策理由（按力度排序）**：

1. **失败点更早，信息量差几十倍**。留空 → `compose up` 插值阶段拒绝，现场只可能是"你没填"；
   "给默认值 + 启动校验拦"是容器起来后 adapter 启动时才拦，同样 fail-loud，但人会先去怀疑镜像、数据卷、依赖顺序。
2. **判据必须简单到不需要维护**。"什么算配置错误"没有确定答案：`127.0.0.1`/`[::1]` 可用
   `ipaddress.is_loopback` 语义判定（不会漏），但客户的 `192.168.1.50` 直连、`0.0.0.0`、
   `host.docker.internal` 每一个都要一次判断，**每次判断都可能误伤真实现场**。
3. **能复用就不要新增平行的第二套机制**。`:?` 已存在且生效，再加 loopback 校验就是第二个判据，**两个判据将来会分叉**。

**被否的选项（保留记录，供下一轮省一次讨论）**：

| 选项 | 结论 | 理由 |
| --- | --- | --- |
| ① 保留字面量 | ❌ 否 | 生产忘改则**起来了但 issuer=localhost**，失败推迟到用户点登录那一刻——把配置错误推给最终用户 |
| **② 改空 + 同步改 QA** | ✅ **采纳** | 失败发生在部署那一刻，判据最简单（空就是空） |
| ③ 保留字面量 + 启动校验拦 loopback | ❌ 否 | 问题不在枚举是否完整（`is_loopback` 能覆盖 127.0.0.0/8 与 ::1），而在**这个判据本身在回答一个没有确定答案的问题**（见理由 2）；且引入与 `:?` 平行的第二套判据 |

> **下一轮若有人再提"启动校验拦 loopback"**：请先读上表 ③ 那行。
> 这不是"当时没考虑到"，是**评估后否决**——定 `0.0.0.0`/私网 IP/`host.docker.internal` 该不该算错的讨论成本，会持续高于它挡住的收益。

### 2.5 落库顺序：`Settings` 字段必须与编排注入**同一提交**

`deploy/validate_config_names.py:19` 的 **R1** 判据是「**编排注入**的每个 `OA_*` 必须是 `Settings` 的字段（`OA_` + 字段名大写）或白名单」，而 `:28` 明确「**不校验 `.env` 模板与编排的对齐关系**」。由此得出两条落地纪律：

1. **只往 `.env.example` 加键不会红**（模板与编排对齐不在该门禁范围内）；
2. **一旦往 compose 注入，`Settings` 就必须有同名字段**，否则 R1 直接红并报"未登记的配置名"。

因此下列三项**必须同一个提交**：

| 项 | 内容 | 落地状态 |
| --- | --- | --- |
| `apps/adapter/app/core/config.py` | `public_base_url: str = ""`、`auth_cookie_enabled: bool = True` | `public_base_url` ✅ 已加（轮 1a）；`auth_cookie_enabled` ⬜ 与 `/sso/*` 同批（轮 1b），**不要提前加**——单加字段而无人读，会让人误以为开关已生效 |
| `deploy/docker-compose*.yml` | 向 `adapter` 注入 `OA_PUBLIC_BASE_URL` / `OA_AUTH_COOKIE_ENABLED` | `OA_PUBLIC_BASE_URL` ✅ 已注入（`${...:?}`）；`OA_AUTH_COOKIE_ENABLED` ⬜ 轮 1b |
| `deploy/.env.example` / `.env.lite.example` | 同名键（模板侧；不参与 R1 判定，但属于同一变更的完整性） | ⬜ 随轮 1b |

**分两次提交的后果**：先注入编排 → CI 红（看起来像"注入写错了"）；先加 `Settings` → 不红但容器读不到（看起来像"配置没生效"）。两种都浪费一轮排障。

> 附注：因 `Settings` 采用 `extra="ignore"`（`docker-compose.yml:567-570` 的注释记录过这个坑：`OA_KEYCLOAK_URL` 被静默丢弃），**拼错变量名不会报错、只会静默回落到默认值**。这也是为什么 `OA_` + 字段名大写的规则只能靠机械门禁守，不能靠人记。

### 2.6 升级路径的坑：老现场会"起不来"且报错不指向原因（已修，但必须写进运维认知）

这是加 `OA_PUBLIC_BASE_URL` 时必然踩到的一次性坑，**下一轮任何人再加 `${VAR:?}` 必填变量都会重演**：

1. `install.sh` 遇到**已存在**的环境文件会「保持不动」直接 return（不覆盖人工配置，这个行为是对的）；
2. 而新变量在编排里是 `${OA_PUBLIC_BASE_URL:?}` → **老现场升级后容器直接起不来**；
3. 报错只说"变量缺失"，**不会提示"先去跑一次 install.sh"** —— 现场很容易误判成"编排写错了"。

已落地的修法：`install.sh:464` 的 **`backfill_derived_env()`**（在 `:336` 调用）——只补**缺失或仍是旧占位值**（如 `officeai`）的派生键，**人工填过的一律不动**，并提示需要 `docker compose build web`（`NEXT_PUBLIC_*` 是构建期内联，`restart` 不生效）。

**对 K5 同样适用**：realm 渲染依赖基址，升级现场必须先能拿到 `OA_PUBLIC_BASE_URL`，否则 realm 里的 `redirectUris` 会渲染成错误的域名。

**给下一轮的纪律**：任何新增的"必填派生变量"都要同时回答两个问题——
（a）老现场升级时它从哪来？（b）缺失时的报错能不能让人直接定位到"跑 `install.sh`"？
只做（a）不做（b），等于把一个可自愈的问题变成一次现场排障。

---

## 3. R5：realm 配置与令牌有效期

### 3.1 仓库现状核实

```bash
$ grep -rn "realm" deploy/ --include=*.yml --include=*.json --include=*.sh
deploy/docker-compose.lite.yml:197:      # JVM 起来 + 建表 + 初始化 realm 实测 81 s（二次启动 48 s）
deploy/docker-compose.yml:521:      # JVM 起来 + 建表 + 初始化 realm 实测 81 s（二次启动 48 s）
```

**确认：只有两条注释提到 realm，没有任何 realm 导入配置**。`find` 全仓无 `*realm*` 文件；`OA_KEYCLOAK_REALM=officeai` 只是个变量（`.env.example:140`），没有任何机制用它创建 realm。`install.sh` 里也**没有** realm 创建步骤（`grep keycloak` 只有读管理口令打印、`env_set` 写密码等）。

**推论**：realm 完全由现场人工创建 → **令牌有效期不在仓库控制范围内**。这与 `docs/deployment-guide.md` 里也查不到任何 issuer/realm/令牌有效期说明（`grep` 0 命中）互相印证：OIDC 登录这条路是**未交付状态**。

### 3.2 最终取值表（V1.0 定稿）

#### Keycloak realm 侧

| 参数 | 取值 | 依据 |
| --- | --- | --- |
| `accessTokenLifespan` | **300 s**（保持默认，不调大） | 会话时长由**平台服务端会话**决定，不由 access TTL 决定（见 §3.3）；短 TTL 让"令牌泄露后的可用窗口"最小 |
| `ssoSessionIdleTimeout` | **1800 s**（30 min） | 与平台会话空闲超时对齐，两者同时生效不会打架 |
| `ssoSessionMaxLifespan` | **28800 s**（8 h） | 一个工作日；超时强制重新认证，是"绝对上限"而非"闲置" |
| `revokeRefreshToken` | **true** | 刷新即轮换，旧 refresh token 立即作废 |
| `refreshTokenMaxReuse` | **0** | 严格一次性，配合轮换可检测重放 |
| `sslRequired` | SaaS/HTTPS 现场 `"external"`；私有化纯 HTTP 现场 `"none"` | 见下方"不确定项" |
| `bruteForceProtected` | **true** | 登录页对外暴露后的基本防护（`failureFactor: 30`, `waitIncrementSeconds: 60`） |

#### 平台服务端会话（Redis，P0-5 的 A 方案）

| 参数 | 取值 | 依据 |
| --- | --- | --- |
| 空闲超时（滑动） | **30 min** | 与 KC `ssoSessionIdleTimeout` 对齐；两处同时过期不会出现"一边以为活着" |
| 绝对上限 | **8 h** | 与 KC `ssoSessionMaxLifespan` 对齐 |
| 惰性刷新节流 | **15 min** | 兼顾"及时感知用户被禁用"（最坏延迟 15 min）与"不打爆 Keycloak"（≤4 次/小时/会话） |
| 会话存储 | **Redis/Valkey db0**（`redis_url` 已存在，`config.py:156`；`core/cache_backend.py` 可复用） | 单副本部署下不需要跨实例共享，但用 Redis 让 V1.1 加副本时零改造 |

> 关键设计点：**浏览器只持 opaque session id，永远不感知令牌过期**。因此"静默续期"不需要前端任何代码——这是把会话端点放适配层（而不是让浏览器持有 access token）带来的第二个好处（第一个是 XSS 免疫）。

### 3.3 refresh 流程归属：**V1.0 做，不做替代方案**

结论：**做服务端惰性刷新，`/sso/refresh` 由"可选 A2"提升为 V1.0 必做项**，但它不是一个前端可见的端点（前端不需要调用），而是适配层在鉴权依赖里按节流策略内部完成的动作。

三条理由（按力度排序）：

1. **不做的话，`accessTokenLifespan` 必须设到 8 h** 才能满足"一个工作日不被打断"。那会把令牌泄露的可用窗口从 5 分钟放大到 8 小时——**P0-5 刚把令牌从 JS 里挪走，转头又给它 8 小时寿命，自相矛盾**。这是"两害相权"里明显更差的一侧。
2. **不做的话，"被禁用/离职用户仍可使用最长 8 小时"**。统一登录场景下这是最容易被客户合规投诉的一条（"我司已在 AD/Keycloak 停用账号，为什么还能登进来"）。惰性刷新把该窗口压到 15 min。
3. **成本极低**：BFF 形态下 refresh token 本就在服务端会话记录里，刷新是"调一次 token endpoint + 更新会话记录"，约 30 行；**前端零改动**（它只看到 opaque session id 一直有效）。

**若因工期极端压缩必须裁掉刷新**，则必须同时满足：`accessTokenLifespan=28800`、把"停用账号最长 8 小时仍可用 + 无静默续期"写进已知限制、并在验收单由主理人签字。**这是降级方案，不是等价方案。**

### 3.4 realm.json 交付方式

#### 文件位置

```
deploy/keycloak/
├── realm-officeai.json     # realm 定义（客户端 / 有效期 / mapper / 暴力破解防护）
└── README.md               # 导入步骤、不覆盖策略、客户已有 realm 时的处理
```

#### 完整结构规格（可照抄的骨架，K5 以此为准）

> 图例：`[M]` = V1.0 必须；`[D]` = 可后置（V1.1 或按现场决定）。
> `${OA_PUBLIC_BASE_URL}` / `${KC_SSL_REQUIRED}` 两个占位由 `install.sh` 渲染后写入 `deploy/keycloak/rendered/realm-officeai.json`，**渲染产物不进库**。

```json
{
  "realm": "officeai",
  "displayName": "OfficeAI",
  "enabled": true,
  "sslRequired": "${KC_SSL_REQUIRED}",

  "accessTokenLifespan": 300,
  "accessTokenLifespanForImplicitFlow": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 28800,
  "revokeRefreshToken": true,
  "refreshTokenMaxReuse": 0,
  "offlineSessionIdleTimeout": 2592000,
  "offlineSessionMaxLifespanEnabled": false,

  "bruteForceProtected": true,
  "permanentLockout": false,
  "failureFactor": 30,
  "waitIncrementSeconds": 60,
  "maxFailureWaitSeconds": 900,
  "minimumQuickLoginWaitSeconds": 60,
  "quickLoginCheckMilliSeconds": 1000,
  "maxDeltaTimeSeconds": 43200,
  "maxTemporaryLockouts": 0,

  "registrationAllowed": false,
  "resetPasswordAllowed": false,
  "rememberMe": false,
  "verifyEmail": false,
  "loginWithEmailAllowed": true,
  "duplicateEmailsAllowed": false,
  "editUsernameAllowed": false,
  "internationalizationEnabled": false,

  "clients": [
    {
      "clientId": "officeai-adapter",
      "name": "OfficeAI Platform (public client, PKCE)",
      "description": "平台自身前端使用的公开客户端；走 authorization code + PKCE(S256)，不持有客户端密钥",
      "enabled": true,

      "protocol": "openid-connect",
      "publicClient": true,
      "bearerOnly": false,
      "consentRequired": false,
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,

      "rootUrl": "${OA_PUBLIC_BASE_URL}",
      "baseUrl": "/",
      "redirectUris": ["${OA_PUBLIC_BASE_URL}/auth/callback"],
      "webOrigins": ["${OA_PUBLIC_BASE_URL}"],

      "attributes": {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "${OA_PUBLIC_BASE_URL}/*"
      },

      "protocolMappers": [
        {
          "name": "audience-officeai-adapter",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": {
            "included.custom.audience": "officeai-adapter",
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true"
          }
        },
        {
          "name": "tenant-id",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-usermodel-attribute-mapper",
          "consentRequired": false,
          "config": {
            "user.attribute": "tenant_id",
            "claim.name": "tenant_id",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true"
          }
        }
      ]
    }
  ]
}
```

#### 字段逐项说明（含 MUST / DEFER 判定）

| 字段 | 取值 | 级别 | 依据 / 注意 |
| --- | --- | --- | --- |
| `realm` | `officeai` | **[M]** | 必须与 `OA_KEYCLOAK_REALM`（`.env.example:140`）、`OA_PUBLIC_BASE_URL` 派生出的 issuer 路径段一致 |
| `enabled` | `true` | **[M]** | — |
| `sslRequired` | 三态，见下 | **[M]** | 见 §3.4.1 |
| `accessTokenLifespan` | `300` | **[M]** | 会话时长由平台服务端会话决定（§3.3），此值保持 Keycloak 默认、**不要调大** |
| `accessTokenLifespanForImplicitFlow` | `300` | [D] | 隐式流已关闭（`implicitFlowEnabled: false`），此值只防误开 |
| `ssoSessionIdleTimeout` | `1800` | **[M]** | 与平台会话空闲超时对齐，两处同时过期不打架 |
| `ssoSessionMaxLifespan` | `28800` | **[M]** | 绝对上限 8 h |
| `revokeRefreshToken` | `true` | **[M]** | 刷新即轮换 |
| `refreshTokenMaxReuse` | `0` | **[M]** | 严格一次性，配合轮换可检测重放 |
| `offlineSessionIdleTimeout` / `offlineSessionMaxLifespanEnabled` | 见骨架 | [D] | V1.0 不用 offline token（`scope=offline_access` 未申请）。**保持默认且不申请该 scope**，否则会绕开 8 h 上限 |
| `bruteForceProtected` 及五个 `failureFactor`/`waitIncrement*`/`maxFailureWait*`/`minimumQuickLogin*`/`quickLoginCheck*`/`maxDeltaTime*`/`maxTemporaryLockouts` 字段 | 见骨架 | **[M]**（开关）/ [D]（阈值微调） | 登录页对外暴露后的基本防护。骨架取值是 Keycloak 默认量级，先照抄再按现场调 |
| `registrationAllowed` | `false` | **[M]** | 企业工具不开放自注册。**这条是安全项**：设 `true` 等于把用户创建开给互联网 |
| `resetPasswordAllowed` | `false` | **[M]** | 改密走客户 IdP/AD 流程，平台不代管 |
| `loginWithEmailAllowed` / `duplicateEmailsAllowed` | `true` / `false` | **[M]** | 邮箱重复会让"按邮箱找人"产生歧义 |
| `verifyEmail` / `rememberMe` / `editUsernameAllowed` / `internationalizationEnabled` | `false` | [D] | 平台不依赖信箱验证；用户名可编辑会让审计主体漂移（若现场确有需求再开，并同步审计口径） |
| `clients[0].clientId` | `officeai-adapter` | **[M]** | 必须等于 `OA_WEB_OIDC_CLIENT_ID`（`.env.example:335`）与 `OA_KEYCLOAK_CLIENT_ID`（`config.py:148`） |
| `publicClient` | `true` | **[M]** | 现有前端是 PKCE 公开客户端：兑换不带 client secret（`oidc.ts:398-404`），authorize URL 不带 secret（`oidc.ts:312`） |
| `standardFlowEnabled` | `true` | **[M]** | 只用 authorization code 流 |
| `implicitFlowEnabled` / `directAccessGrantsEnabled` / `serviceAccountsEnabled` | 全 `false` | **[M]** | 隐式流已被 OAuth 2.1 弃用；密码模式（ROPC）会让口令经过平台代码；平台不需要服务账号。**三条都是"关掉即减少一个攻击面"** |
| `bearerOnly` | `false` | **[M]** | 它要发起登录，不是纯资源服务器 |
| `rootUrl` / `baseUrl` | `${OA_PUBLIC_BASE_URL}` / `/` | **[M]** | 供 Keycloak 生成相对链接；与实际访问域名不一致会导致登录后跳回错误主机 |
| `redirectUris` | `${OA_PUBLIC_BASE_URL}/auth/callback` | **[M]** | **必须精确**（Keycloak 对 redirect_uri 做前缀/通配匹配，写 `/*` 会把任意路径变成合法回调 → 授权码可被导向他处）。回调页在 `apps/web/src/app/(auth)/auth/callback/`；**P0-5 不改变该 URI**（BFF 仍走同一回调页，只是把 code 交给 `/sso/complete`），因此客户 realm 已注册的回调地址**不需要改** |
| `webOrigins` | `${OA_PUBLIC_BASE_URL}` | **[M]** | 同源部署下必须与 `redirectUris` 同源；写成 `"+"` 或 `"*"` 会放开 CORS |
| `attributes.pkce.code.challenge.method` | `S256` | **[M]** | **强制 S256**。留空时 Keycloak 接受 `plain`，PKCE 的保护力退化为"明文校验"，等于把 verifier 暴露在 URL 里 |
| `attributes.post.logout.redirect.uris` | `${OA_PUBLIC_BASE_URL}/*` | [D] | 只有在实现 RP-initiated logout（`end_session_endpoint`）时才需要。V1.0 的登出是**本地清会话**（`/sso/session` DELETE），不调 Keycloak，故可后置 |
| `protocolMappers[0]` audience mapper | `included.custom.audience = officeai-adapter` | **[M]** | **这是让 `OA_JWT_AUDIENCE=officeai-adapter`（`.env.example:199`）真正生效的前提**。Keycloak access token 默认 `aud` 常为 `account`，不配这个 mapper 则 `jwt.decode(audience=...)` 必然失败——表现与 R8 的"算法不匹配"**完全同款**（都是 401），排障时极易误判。**用 mapper 去适配既有配置，而不是反过来改 `OA_JWT_AUDIENCE`**，可减少改动面 |
| `protocolMappers[1]` tenant_id mapper | 用户属性 `tenant_id` → claim `tenant_id` | **[M]（仅 SaaS）/ [D]（私有化）** | 私有化单租户走 `_default_tenant`（`security.py:226-227`），不需要该声明；SaaS 下 `resolve_tenant_id` 依赖它（`security.py:232-238`），且 `.env.example:27` 已把它写成"SaaS + OIDC 的前置条件"。**SaaS 必须配，否则首个 `profile` 直接 400/403** |

> **不需要 mapper 的一项**：`realm_access.roles` 是 Keycloak 默认签发内容，`_extract_roles`（`security.py:120-125`）读的就是它，无需额外配置。

#### 3.4.1 `sslRequired` 三态处理

| 取值 | 适用现场 | 处置 |
| --- | --- | --- |
| `"external"` | **SaaS**（始终 TLS）；私有化 + 边缘 TLS | 默认值。非私有网段必须 HTTPS |
| `"none"` | **私有化纯 HTTP 现场**（客户不提供证书、走 `edge.conf`） | 必须设 `none`，否则登录页被 Keycloak 以 `HTTPS required` 拒绝。渲染时由 `install.sh` 按 `OA_EDGE_TLS` 决定，并在安装日志打 **WARN** |
| `"all"` | **不使用** | 连内网回环也强制 HTTPS，与"适配层用 `http://keycloak:8080` 直连"冲突 |

渲染规则（`install.sh`）：

```bash
# OA_EDGE_TLS=on（挂 docker-compose.tls.yml）→ external；否则 none + WARN
if [ "${OA_EDGE_TLS:-off}" = "on" ]; then KC_SSL_REQUIRED="external";
else KC_SSL_REQUIRED="none"; warn "纯 HTTP 现场：realm sslRequired=none（登录页不加密，仅限内网）"; fi
```

> 口径与 `docs/design-2026-11-02-security-deploy.md` §2.4.6 对 Cookie `Secure` 的处置**一致**：允许降级，但必须显式、必须告警。
> ⚠ `sslRequired` 的实际拦截行为**尚未得到可信实测**（§3.5 已说明 `start-dev` 会掩盖），首次部署须按 §4.2 的 R5-③ 复验。

- `publicClient: true` + `S256`：与现有前端 PKCE 公开客户端口径一致（`oidc.ts:398-404` 的兑换不带 client secret；`oidc.ts:312` 拼 authorize URL）。
- `${...}` 占位由 `install.sh` 渲染（项目内已有 python 可用的渲染/校验链）。**渲染后的文件与模板都不含任何密钥**（公开客户端无 secret），因此可以入库；这一点使它不会与 `validate_secrets.py` 的"密钥不入库"检查冲突。

#### 挂载与导入（**关键：不在 keycloak 服务上加 `--import-realm`**）

不要给生产 keycloak 加 `--import-realm`。它在每次启动都会尝试导入，语义是"服务启动行为"，而我们要的是"**安装期一次性动作**"。改为**一次性 installer 服务**：

```yaml
  keycloak-import:
    image: quay.io/keycloak/keycloak:26.8.1
    profiles: ["install"]                 # 默认不起，只有安装流程显式拉起
    depends_on:
      keycloak: { condition: service_healthy }
    volumes:
      - ./keycloak/realm-officeai.json:/tmp/realm.json:ro
    entrypoint: ["/bin/bash", "-c"]
    command:
      - |
        set -e
        /opt/keycloak/bin/kcadm.sh config credentials \
          --server http://keycloak:8080 --realm master \
          --user "$$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$$KC_BOOTSTRAP_ADMIN_PASSWORD"
        if /opt/keycloak/bin/kcadm.sh get realms/officeai >/dev/null 2>&1; then
          echo "[skip] realm officeai 已存在，不覆盖"
        else
          /opt/keycloak/bin/kcadm.sh create realms -f /tmp/realm.json
          echo "[ok] realm officeai 已创建"
        fi
```

**"客户已有 realm 不被覆盖"由这段流程保证，而不是依赖导入策略的默认值**——这是刻意的：把安全性放在"我显式检查过"，而不是"我相信工具在已存在时会跳过"。

#### 客户已有 realm 时的处理（三种现场）

| 现场 | 处理 |
| --- | --- |
| 全新安装 | `docker compose --profile install up -d keycloak-import` → 创建 realm |
| 已存在同名 realm（升级/客户自带） | installer 打印 `[skip]` 并退出 0。**随后跑一次"差异体检"**：用 admin API 读 `accessTokenLifespan`/`ssoSessionIdleTimeout`/`ssoSessionMaxLifespan`/`revokeRefreshToken` 与基线比对，不一致**只 WARN 不修改**（客户可能有意如此），并把差异写进安装日志 |
| 客户使用自己的 IdP（非本仓 Keycloak） | 只需 `OA_PUBLIC_BASE_URL` 指向客户 IdP 域、`OA_JWT_ISSUER` 指向其 realm、`OA_JWT_ALGORITHM` 与其签名算法一致（RS256 走 JWKS）。**keycloak 服务可用 compose profile 整体关闭** |

> 第三种现场是 R8 为什么必须做成"按算法分流"而不是"只支持 RS256"的原因之一：客户自带 IdP 的算法不可控。

### 3.5 一个**不确定项**（必须现场复验，不要当结论用）

`sslRequired` 与纯 HTTP 部署的关系：我用 `start-dev` 实测时，即使把 realm 改成 `sslRequired=external`，HTTP 的 auth 端点仍返回 302（正常跳登录页）。**这条不能作为结论**——`start-dev` 模式本身会放宽 HTTPS 强制，掩盖了差异。

**正确做法**：在 `start` 模式的实例上复验一次（判据见 §4 的 R5-③）。在此之前，按 Keycloak 文档行为处理：`external` 要求非私有网段走 HTTPS，因此**纯 HTTP 私有化现场必须设 `sslRequired: "none"`**，并在安装日志打 WARN（`docs/design-2026-11-02-security-deploy.md` §2.4.6 对 Cookie 的 `Secure` 也是同一处置口径：允许但在启动时告警）。

---

## 4. 实施顺序与验收判据

### 4.1 顺序（依赖驱动）

| 步 | 内容 | 依赖 | 轮次 | 所属任务 |
| --- | --- | --- | --- | --- |
| K1 | `.env` 新增 `OA_PUBLIC_BASE_URL` 并派生三个 issuer 变量；`install.sh` 加渲染 | 无 | **轮 1**（属 K4-min） | R4 |
| K2 | `keycloak` 加 `KC_HOSTNAME` + `KC_PROXY_HEADERS`（三套） | K1 | **轮 1**（属 K4-min） | R4 |
| K3 | full 栈 `keycloak.networks` 加 `officeai-frontend` | K2 | **轮 1**（属 K4-min） | R4 |
| **K4-min** | `edge.conf` 落地 + 六条 location + `full`/`saas` 新增 `edge` 服务 + `NEXT_PUBLIC_ADAPTER_BASE_URL` 置空 | K1-K3、P0-5 的 `/sso/*` | **轮 1（必须，见 §4.1.1）** | R4 + P0-5 |
| **K4-rest** | `edge-tls.conf` + `docker-compose.tls.yml` + `ssl_ciphers` + HSTS 单点化 + 端口收口 + 资源限制 + k8s | K4-min | **轮 2（P0-4）** | P0-4 |
| K5 | `deploy/keycloak/realm-officeai.json` + README + `keycloak-import` installer 服务 | K1 | **轮 1** | R5 |
| K6 | 适配层 JWKS/RS256 分流（`_decode_token`） | 无（可与 K1-K5 并行） | **轮 1（必须与 P0-5 同批）** | R8 |
| K7 | 适配层服务端会话 + 惰性刷新 | P0-5 的 `/sso/*` 落地 | **轮 1** | R5 |
| K8 | 全链路联调（登录 → 刷新 → 登出 → 产物下载） | K4-min + K5 + K6 + K7 | 轮 1 末 | 全部 |

**K6 必须与 P0-5 同批**，否则 P0-5 验收时"登录成功但所有接口 401"，会被误判为 P0-5 自身的缺陷而反复返工。

### 4.1.1 K4 的归属边界：轮 1 必须包含「K4-min」，不能等 P0-4

主理人原计划「轮 1 = K6 + P0-5 + K5 + K7；轮 2 = K1-K4 + P0-4」。**按此切分，轮 1 的验收不成立**——原因是一条硬约束：

> **Keycloak 没有任何宿主端口映射，浏览器要打开登录页，就必须有一个对外入口把它转出去。**
> 而"给 keycloak 加一个 `8080:8080` 宿主端口"等于把身份底座明文端口裸绑到 `0.0.0.0`，
> 比加边缘明显更糟（且与 P0-4 的收口方向相反）。所以**访问 `/realms/` 这件事本身就需要边缘存在**。

同理，P0-5 的 `/sso/*` 是**同源相对路径**设计，没有边缘就无处落地；`full` 与 `saas` 当前**连 `/api/` 的反代都没有**（两套编排里根本没有 nginx 服务），只能靠 `http://host:8000` 跨源直连——而那条路上 Cookie 虽可用（Cookie 不区分端口），却要改生产 CORS 白名单（`main.py:491-493` 现为 `[]`），等于为一个临时形态开一个跨源口子，轮 2 收口时还要撤掉。

**结论：轮 1 必须包含「K4-min」——K4 的功能子集，只做"让登录打通"所必需的部分；P0-4 的形态与加固整体留在轮 2。**

#### K4-min（轮 1，必须）

| # | 内容 | 为什么是"必须" |
| --- | --- | --- |
| 1 | 新增 `deploy/nginx/edge.conf`（由 `lite.conf` 提升） | 唯一入口载体；后续轮次只加，不重写 |
| 2 | `lite.yml` 的 `proxy` 换挂 `edge.conf` | lite 已有边缘，改挂即可 |
| 3 | `full` / `saas` 新增 `edge` 服务（HTTP，`${OA_EDGE_HTTP_PORT:-80}:80`） | 这两套**当前无任何反代**，否则 `/sso/*`、`/realms/` 无处落地 |
| 4 | `full` 栈 `keycloak.networks` 加 `officeai-frontend` | 否则边缘解析不到 keycloak（`saas`/`lite` 单网，无需改） |
| 5 | `KC_HOSTNAME` + `KC_PROXY_HEADERS=xforwarded` + `OA_PUBLIC_BASE_URL` 及三个派生 issuer 变量 | R4 的安全与正确性前提（见 §1.4、主文档 §4.2 注） |
| 6 | `NEXT_PUBLIC_ADAPTER_BASE_URL` 置空（三套构建参数） | 边缘化后必须走同源相对路径，否则浏览器绕过边缘直连 8000 |

#### K4-min 要求的边缘 location（六条，按匹配优先级）

```nginx
    location = /healthz            { ... return 200 "ok\n"; }        # 边缘自身探活，compose healthcheck 依赖
    location ^~ /files/artifacts/  { proxy_pass http://$adapter_backend; }   # ⚠ 见下方"回归陷阱"
    location ^~ /sso/              { proxy_pass http://$adapter_backend; }   # P0-5 会话端点（新增）
    location ^~ /realms/           { proxy_pass http://$keycloak_backend; }  # R4 登录页（新增）
    location ^~ /resources/        { proxy_pass http://$keycloak_backend; }  # R4 登录页静态资源（新增）
    location /api/                 { proxy_pass http://$adapter_backend; }   # 既有（lite 有，full/saas 需新增）
    location /                     { proxy_pass http://$web_backend$request_uri; }
```

> ⚠ **回归陷阱（务必别踩）**：`full` / `saas` 现在**没有** `/files/artifacts/` 的 location；把它从 `lite.conf` 提升到 `edge.conf` 时若漏掉这一条，会**重新引入 2026-11-02 审计 S-03 的产物下载恒定 404**（该问题已在 `e074372` 修复，回归等于白修）。这是把 lite 配置复制到另外两套时最容易漏的一行。

#### 可后置（轮 2，属 P0-4）

| 内容 | 为什么可以晚 |
| --- | --- |
| TLS 终止（`edge-tls.conf` + `docker-compose.tls.yml` + 证书约定） | 轮 1 用 HTTP 边缘即可验通登录；TLS 是传输加固，不阻塞功能 |
| `ssl_ciphers` / `ssl_session_tickets` / HSTS 单点化 | 纯加固，与登录能否打通无关 |
| 端口收口（`adapter`/`web` 改 `expose:`） | 轮 1 保留宿主端口不影响登录；**且收口后才需要** `OA_TRUSTED_PROXY_CIDRS`（产物下载限流，见主文档 §3.3.3） |
| 三套 compose 资源限制 / `memswap_limit` | 与登录无关 |
| k8s manifests | V1.1（主文档 §4.1 R6） |

#### 对轮 2 的影响（不是白做）

K4-min 交付的 `edge.conf` 就是轮 2 的**同一个文件**：轮 2 只需在其上追加 TLS 变体与端口收口，不需要重写。所以把 K4 拆成 `K4-min`（轮 1）+ `K4-rest`（轮 2）**不会造成返工**，只是把"让登录通"与"把形态收干净"分开排期。

#### 轮 1 验收的边界（明确写下，避免"验收不成立"）

轮 1 结束时可以并且只能验到：

- ✅ 登录页能打开（`/realms/...` 返回 200）
- ✅ 登录成功、Cookie 下发、业务接口 200（同源 `/api/`）
- ✅ 令牌不在 JS 可达位置
- ❌ **不能**验 TLS、安全头、端口收口、资源限制（这些属轮 2）

若主理人希望轮 1 就带 TLS（例如交付演示需要 `https://`），则 K4-min 需追加"`edge-tls.conf` + 证书 + `docker-compose.tls.yml`"三项，并额外需要客户/自签证书——**这是唯一值得从轮 2 提前的部分**。


### 4.2 验收判据（可执行）

```bash
PUB=https://<对外域名>
REALM=officeai

# ── R4-① issuer 与配置逐字符一致（尾斜杠也算差异）
curl -s "$PUB/realms/$REALM/.well-known/openid-configuration" | jq -r .issuer
# 期望：完全等于 OA_WEB_OIDC_ISSUER 与 OA_JWT_ISSUER 的取值

# ── R4-② 伪造 Host 不能改 issuer（实测已验证此判据能区分修复前后）
curl -s -H 'Host: evil.example.com' "$PUB/realms/$REALM/.well-known/openid-configuration" | jq -r .issuer
# 期望：仍为 $PUB/realms/$REALM（修复前会变成 http://evil.example.com/realms/officeai）

# ── R4-③ TLS 边缘后端点协议必须是 https（KC_PROXY_HEADERS 是否生效）
curl -s "$PUB/realms/$REALM/.well-known/openid-configuration" | jq -r '.authorization_endpoint, .token_endpoint, .jwks_uri'
# 期望：三条全部 https:// 开头

# ── R4-④ 管理台不对外
curl -s -o /dev/null -w '%{http_code}\n' "$PUB/admin/master/console/"
# 期望：404（出现 200/302 即为暴露）

# ── R4-⑤ 登录页真的能打开（不是"变量为空"的假修）
curl -s -o /dev/null -w '%{http_code}\n' \
  "$PUB/realms/$REALM/protocol/openid-connect/auth?client_id=officeai-adapter&response_type=code&scope=openid&redirect_uri=$PUB/auth/callback&code_challenge=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG&code_challenge_method=S256"
# 期望：200（登录表单），非 400/404

# ── R8-① 真实令牌的 alg 是 RS256（确认 realm 没被改成 HS256 走偏门）
TOKEN=<从浏览器/兑换接口拿到的真实 access_token>
python -c "import base64,json,sys; h=sys.argv[1].split('.')[0]; \
print(json.loads(base64.urlsafe_b64decode(h+'='*(-len(h)%4)))['alg'])" "$TOKEN"
# 期望：RS256

# ── R8-② JWKS 可拉取且 kid 能对上令牌头
curl -s "$PUB/realms/$REALM/protocol/openid-connect/certs" | jq -r '.keys[].kid'
# 期望：非空，且包含令牌头部 kid

# ── R8-③ 带真实令牌打业务接口（这是"登录真的通了"的唯一判据）
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" "$PUB/api/v1/identity/profile"
# 期望：200（当前实现下恒为 401）

# ── R5-① realm 有效期落地正确
#   （admin token 拿法同 installer）
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$PUB/admin/realms/$REALM" -H 'Host: 127.0.0.1' \
  | jq '{accessTokenLifespan, ssoSessionIdleTimeout, ssoSessionMaxLifespan, revokeRefreshToken, refreshTokenMaxReuse, sslRequired}'
# 期望：300 / 1800 / 28800 / true / 0 / external(或 none)

# ── R5-② 不覆盖已有 realm
docker compose --profile install run --rm keycloak-import        # 已存在时应打印 [skip]
# 在客户已自定义 accessTokenLifespan=600 的实例上重跑上面命令，再查 R5-① → 期望仍为 600

# ── R5-③ sslRequired 行为复验（§3.5 的不确定项，必须在 start 模式实例上做）
docker compose -f docker-compose.yml exec keycloak sh -c \
  'curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8080/realms/officeai/protocol/openid-connect/auth?client_id=officeai-adapter&response_type=code&scope=openid&redirect_uri=http%3A%2F%2Flocalhost%2Fauth%2Fcallback&code_challenge=abc&code_challenge_method=S256"'
# sslRequired=external 时期望 403（"HTTPS required"）；=none 时期望 302
```

---

## 5. 与主文档的关系

- 本文展开主文档 `docs/design-2026-11-02-security-deploy.md` §4.1 的 **R4、R5**，并新增 **R8**（该文 §4.1 没有，属本次核查发现）。
- **R8 应回写进主文档**作为与 R4 并列的阻断项，否则主文档的"P0-5 可验收"判断是错的（登录通了但业务接口全 401）。
- 本文 §1.5 的改动 4（三条 location）与主文档 §3.3.1 的 `edge.conf`/`edge-tls.conf` 是**同一份文件的同一批改动**，不得分两次改。
- 本文 §3.4 的 `keycloak-import` installer 服务与主文档 §3.3.2 的 `edge` 服务属同一批 compose 改动。

---

*作者：高见远（架构师）· 2026-11-02 · 供主理人决策、姜实现（工程师）落地消费*
