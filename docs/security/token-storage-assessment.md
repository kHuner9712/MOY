# Token 存储与 CSP 收敛评估（审计 2026-10-30 M9，P1-6 交付物）

## 现状（实测）

- 访问令牌存 **localStorage**（`apps/web/src/lib/session-store.ts`，键 `moy.session.v1`），
  代码注释自认：同源 XSS 可直接取走令牌。运行期令牌另在 `api-client.ts` 模块内存副本中
  注入 `Authorization: Bearer` 头。
- CSP（`apps/web/next.config.js`）含 `'unsafe-inline'`，注释说明为 Next.js App Router
  RSC 内联 script 所需。XSS 面一旦成立 = 直接失会话。
- OIDC 登录为 PKCE-S256、兑换不带客户端密钥（scan:oidc 8/8 看护）；会话失效/登出清理完整。

## 威胁模型

| 场景 | localStorage + unsafe-inline | httpOnly Cookie |
| --- | --- | --- |
| 同源 XSS | **令牌直接失守**（缓解：CSP 收窄 + 输出编码 + 禁词/占位词门禁） | 取不到令牌（能借会话发请求，但读不走凭据） |
| 跨站请求伪造 | 不受影响（头带令牌） | 需 CSRF 防护（SameSite=Lax/Strict + CSRF token） |
| 多端/第三方客户端 | 简单（Bearer 头） | 需 BFF 或双轨 |
| 私有化单租户内网 | 风险面小 | 价值有限 |

## 结论与建议（分档）

1. **短期（本季度，推荐先做）**：保持 localStorage 架构不动，收敛 XSS 面——
   - CSP nonce 化逐步替 `'unsafe-inline'`（Next.js 需在 middleware/server 层逐请求生成
     nonce；RSC 内联块先走 nonce 白名单，客户端样式可暂缓 `'unsafe-inline'` 样式位）；
   - 前端输出编码现状良好（0 处 dangerouslySetInnerHTML/innerHTML，审计实测），保持门禁；
   - 评估缩短 access token TTL（当前签发侧可配），把泄露窗口压到分钟级。
2. **中期（SaaS 公网上线前，必须决策）**：迁 **httpOnly + Secure + SameSite cookie**——
   - 仅对浏览器会话启用 cookie 轨；API 面（第三方/集成）保留 Bearer 轨双轨并存；
   - 配套 CSRF token（双提交 cookie）与 `Vary: Cookie` 缓存口径；
   - 影响面：api-client 注入逻辑、OIDC 回跳写会话、登出清除、e2e 全链路，需专项排期。
3. **私有化默认部署**：内网 + TLS（lite-tls.conf）+ 现有 CSP 下风险可接受，不强制迁移。

## 判定口径

- 本评估是「决策依据」，不改代码；选定档位后另立改造项（含 e2e 与门禁改造）。
- 触发强制升级到第 2 档的条件：SaaS 公网上线 / 出现实际 XSS 事件 / 桌面端分发。
