# Keycloak realm 配置（K5 / R5）

本目录是 **realm `officeai` 的唯一权威定义**。在它存在之前，realm 完全由现场人工创建，
令牌有效期不在仓库控制范围内——默认 access token 300 s 且无刷新，用户每 5 分钟被踢一次。

```
deploy/keycloak/
├── realm-officeai.json          # 模板（入库；含 ${OA_PUBLIC_BASE_URL} / ${KC_SSL_REQUIRED} 占位）
├── render-realm.sh              # 渲染器（现场值 → rendered/）
├── .gitignore                   # 忽略 rendered/
└── README.md                    # 本文件
```

**渲染产物 `rendered/realm-officeai.json` 不进版本库**：它含现场域名，
而模板必须能服务于任意客户域名。

## 一、为什么不用 `--import-realm`

Keycloak 的 `--import-realm` 语义是「**服务每次启动都尝试导入**」，而我们要的是
「**安装期一次性动作**」。更关键的一点：

> 已存在同名 realm 时 `--import-realm` 是否覆盖，取决于导入策略/镜像版本的默认行为。
> **客户既有 realm 被覆盖是不可挽回的事故**（用户、客户端、mapper、会话策略全丢），
> 不能把安全性押在工具的默认值上。

所以本方案用**显式探测**代替默认策略：先 `kcadm get realms/officeai`，
存在就打印 `[skip]` 并退出 0，不存在才 `create`。安全性来自「我显式检查过」。

## 二、导入步骤（三种现场）

### 现场 A：全新安装

```bash
# 1) 渲染（install.sh 会自动调用；手工部署时自己跑）
OA_PUBLIC_BASE_URL=https://moy.example.com \
KC_SSL_REQUIRED=external \
  sh deploy/keycloak/render-realm.sh

# 2) 起身份底座，等 healthy
docker compose -f deploy/docker-compose.yml up -d keycloak

# 3) 一次性导入（profile=install，默认不起）
docker compose -f deploy/docker-compose.yml --profile install up -d keycloak-import
docker compose -f deploy/docker-compose.yml logs keycloak-import   # 期望 [ok] realm officeai 已创建
```

### 现场 B：realm 已存在（升级 / 客户自带）

导入器打印 `[skip] realm officeai 已存在，不覆盖` 并退出 0，**不做任何修改**。

随后应做一次「差异体检」（手工，只读）：用 admin API 读
`accessTokenLifespan` / `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` / `revokeRefreshToken`
与本文基线比对。**不一致只告警、不自动修改**——客户可能是有意为之（例如合规要求 4 小时绝对上限），
把差异写进安装日志即可。

```bash
docker compose -f deploy/docker-compose.yml exec keycloak \
  /opt/keycloak/bin/kcadm.sh get realms/officeai \
  --fields accessTokenLifespan,ssoSessionIdleTimeout,ssoSessionMaxLifespan,revokeRefreshToken
```

### 现场 C：客户用自己的 IdP（不是本仓 Keycloak）

整体走 compose profile 关掉 `keycloak` 服务，然后：

| 变量 | 取值 |
| --- | --- |
| `OA_PUBLIC_BASE_URL` | 客户 IdP 对外域名（决定 `KC_HOSTNAME` 与 Cookie `Secure`） |
| `OA_JWT_ISSUER` | 客户 IdP 的 realm issuer（**必须与令牌里的 `iss` 逐字符相等**） |
| `OA_JWT_AUDIENCE` | 客户 IdP 签发令牌的 `aud`（不是 `officeai-adapter` 时要改） |
| `OA_JWT_ALGORITHM` | 与客户 IdP 签名算法一致；`RS*` 走 JWKS，`HS*` 走共享密钥 |

适配层的算法分流见 `apps/adapter/app/core/security.py::uses_asymmetric_algorithm`——
这也是「必须按算法分流、不能只支持 RS256」的原因之一：客户自带 IdP 的算法不可控。

## 三、关键字段（改动前请先读）

| 字段 | 取值 | 为什么 |
| --- | --- | --- |
| `accessTokenLifespan` | `300` | **保持 Keycloak 默认，不要调大**。会话时长由平台服务端会话决定，access token 短寿命把「令牌泄露后的可用窗口」压到 5 分钟 |
| `ssoSessionIdleTimeout` | `1800` | 与平台会话空闲超时（30 min）对齐，两处同时过期不会出现「一边以为还活着」 |
| `ssoSessionMaxLifespan` | `28800` | 绝对上限 8 h（一个工作日），超时强制重新认证 |
| `revokeRefreshToken` / `refreshTokenMaxReuse` | `true` / `0` | 刷新即轮换、严格一次性，配合轮换可检测 refresh token 重放 |
| `sslRequired` | `external` / `none` | 见下节三态 |
| `registrationAllowed` | `false` | **安全项**：设 `true` 等于把用户创建开给互联网 |
| `clients[0].redirectUris` | `${OA_PUBLIC_BASE_URL}/auth/callback` | **必须精确**。Keycloak 对 redirect_uri 做前缀/通配匹配，写 `/*` 会把任意路径变成合法回调 → 授权码可被导向他处 |
| `clients[0].webOrigins` | `${OA_PUBLIC_BASE_URL}` | 同源部署下必须与 redirectUris 同源；写 `"*"` 等于放开 CORS |
| `attributes.pkce.code.challenge.method` | `S256` | 留空时 Keycloak 接受 `plain`，PKCE 退化为「明文校验」，保护力归零 |
| `protocolMappers[audience-officeai-adapter]` | `included.custom.audience=officeai-adapter` | **不配就必失败**：Keycloak access token 的 `aud` 默认是 `account`，`jwt.decode(audience="officeai-adapter")` 直接抛错——症状与「算法不匹配」完全同款（都是 401），排障极易误判。这里用 mapper 去适配既有 `OA_JWT_AUDIENCE`，而不是反过来改配置 |
| `protocolMappers[tenant-id]` | 用户属性 `tenant_id` → claim | SaaS **必须**（`resolve_tenant_id` 依赖该 claim）；私有化走 `_default_tenant`，用户属性缺失时 Keycloak 不签发该 claim，无害 |

`realm_access.roles` 不需要 mapper：它是 Keycloak 默认签发内容，`_extract_roles` 读的就是它。

## 四、`sslRequired` 三态

| 取值 | 适用现场 | 处置 |
| --- | --- | --- |
| `external` | SaaS（始终 TLS）；私有化 + 边缘 TLS | 非私有网段必须 HTTPS |
| `none` | 私有化纯 HTTP（客户不提供证书，走 `edge.conf`） | 必须设 `none`，否则登录页被 Keycloak 以 `HTTPS required` 拒绝。渲染时按 `OA_EDGE_TLS` 决定并在安装日志打 **WARN** |
| `all` | **不使用** | 连内网回环也强制 HTTPS，与「适配层用 `http://keycloak:8080` 直连」冲突 |

口径与 Cookie `Secure` 一致：**允许降级，但必须显式、必须告警**。

> ⚠️ 已知不确定项：`sslRequired` 与纯 HTTP 的实际拦截行为**尚未在 `start` 模式（非 `start-dev`）
> 上实测**。`start-dev` 会放宽 HTTPS 强制，用它测出来的「不拦」不能当结论。
> 首次部署须按设计文档 §4.2 的 R5-③ 复验。

## 五、渲染器

```bash
sh deploy/keycloak/render-realm.sh            # 渲染（缺省 OA_PUBLIC_BASE_URL=http://localhost）
sh deploy/keycloak/render-realm.sh --check    # 只校验占位可被完全替换，不落盘
```

- 尾斜杠会被去掉：`https://host/` → `https://host`，否则 `redirectUris` 变成
  `https://host//auth/callback`，Keycloak 精确匹配失败 → 现场报 `Invalid parameter: redirect_uri`，极难定位；
- 渲染后若仍残留 `${...}`，脚本**直接失败**（把字面量 `${OA_XXX}` 当域名提交给 Keycloak
  会导致登录必然失败，且报错看不懂）；
- 模板里**不含任何密钥**（公开客户端无 client secret），因此可以入库，
  不与 `deploy/validate_secrets.py` 的「密钥不入库」检查冲突。
