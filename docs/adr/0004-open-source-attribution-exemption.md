# ADR-0004：开源声明页的技术黑盒化豁免及其边界

- **状态**：✅ **已接受（Accepted）—— 2026-11-07 主理人齐活林裁决**
- **日期**：2026-11-07
- **决策者**：齐活林（主理人／交付总监）裁决；高见远（架构师）记录
- **决策来源**：`docs/engineering-standards.md` 附录 B-4（原「待确认」，本 ADR 承载裁决）
- **相关文档**：`docs/adr/0001-adapter-layer-uniqueness.md`（技术黑盒化为其红线之一）、
  `docs/adr/0002-infrastructure-license-compliance.md`（第 9.2 节 Dify 修改版附加约束）、
  `docs/adr/0003-design-token-baseline.md`（体验一致性）、
  `docs/architecture-v1.0.md:148-163`（§1.4 架构红线）、
  `docs/engineering-standards.md` S3.1-e / 附录 B-4
- **关联证据**：`deploy/images.lock:42-56`；`apps/web/src/app/(main)/about/page.tsx:27-42`；
  `.github/workflows/dependency-scan.yml:80-84`；`apps/web/scripts/check-banned-terms.mjs:49-110`
- **编号说明**：`docs/adr/` 原有 `0001` / `0002` / `0003` 三份（`git ls-files docs/adr/` 实测），
  **0004 未被占用**，故取本号，无需顺延。

---

## 一、背景

`ADR-0001` 把「技术黑盒化」定为一票否决级架构约束：终端用户全程感知不到 AI 技术概念
与底层组件，前端用户可见文案不得出现模型名 / 向量库 / RAG / Agent 等术语
（`docs/engineering-standards.md` S3.1-e）。

但合规要求与之存在张力：

1. `deploy/images.lock:42-56` 记录 Dify 采用 **Apache-2.0 修改版**，附带「多租户特性限制」
   与「商标 / LOGO 限制」，三条规避约束中第 3 条为
   「**不展示 Dify 品牌标识（前端黑盒化 + 开源声明如实列明）**」——即「不展示品牌标识」
   与「开源声明如实列明」是**同时成立**的两项义务，署名 / 版权信息不可移除。
2. `apps/web/src/app/(main)/about/page.tsx:27-42` 的 `OPEN_SOURCE_COMPONENTS` 常量
   是渲染给用户的开源组件清单，逐项列出 `name` / `license` / `role`，其中
   `:35` `pgvector`、`:36` `Valkey`、`:39` `Dify`（`role: '工作流底座'`）确属
   「底座 / 向量库组件名」。
3. 该文件 `:21-23` 注释自称「只写它在我们产品里负责什么，不写它的技术类别」，
   而 `role` 实际写的是技术类别（「记忆检索所需的数据库扩展」「工作流底座」「缓存」），
   口径未自洽。

2026-11-06 全仓审计曾把此页记为 **ARCH-11「豁免边界未定义」**。本轮复核更正该结论：
**豁免其实已在 CI 显式登记**，真正的问题是**两套门禁口径不一致**（见 §二）。

---

## 二、事实基础（本轮复核，逐条给证据）

| # | 事实 | 证据 |
| --- | --- | --- |
| 1 | **源码级底座直连扫描存在**：`base-layer-isolation` job 有三步——扫 `apps/web`/未来 `plugins` 的底座字样、查前端依赖无底座 SDK、查 `apps/adapter/app` 中 `dify-api｜dify_base_url` 只许出现在 `core/config.py` 与 `clients/dify_client.py` | `.github/workflows/dependency-scan.yml:40-131`（step 分别在 `:49`、`:95`、`:108`） |
| 2 | **开源声明页已有显式白名单**：「合规署名表行显式白名单」，只放行 `about/page.tsx` 中**含 `license:` 字段的署名表行**，直连 URL 与其他字样照红 | `.github/workflows/dependency-scan.yml:80-84`（注释 `:80-82`，规则 `:84`） |
| 3 | **前端术语门禁的词表不覆盖组件名**：`TERM_GROUPS` 由 `docs/prd-phase2-increment.md` §7 生成，含 `milvus` / `rag` / `agent` / `向量` / `模型` 等，**不含** `dify` / `pgvector` / `valkey` / `seaweedfs` | `apps/web/scripts/check-banned-terms.mjs:49-110`；CI `ci-web.yml:121`（G-14 源码）、`:294`（G-16 构建产物） |
| 4 | 因此两条门禁对同一页面给出**不同判据**：直连门禁按「行是否含 `license:`」放行；术语门禁因词表不含组件名而**根本不参与判定** | 上述 2 + 3 |
| 5 | `qa_probe` 的运维引导脚本豁免另有逐条登记，与本 ADR 的开源声明豁免是**两件事** | `docs/architecture-v1.0.md:157`（§1.4 第 6 条）、`:1598`（§9.6 范围说明，2026-11-07 修订后行号） |

**更正结论（取代 2026-11-06 ARCH-11 的表述）**：
「about 页无豁免登记」**不成立**——豁免已在 `dependency-scan.yml:80-84` 登记；
「**不存在源码级门禁**」这一判断同样**不成立**（源码级门禁实为
`dependency-scan.yml:40-131` 的 `base-layer-isolation` job 三步，
见 `docs/engineering-standards.md` S3.1-a/b/c 对本轮 ARCH-15 的更正）。
**待解决的问题是两套门禁口径不一致、判据收窄，以及豁免边界未成文。**

---

## 三、裁决

### 3.1 开源声明页属合规必需豁免

`apps/web/src/app/(main)/about/page.tsx` 的开源组件署名表，作为 Dify 修改版
Apache-2.0 及其他第三方许可所要求的署名 / 版权信息载体，**允许**出现第三方组件名称、
版本与许可信息。该豁免是**页面级、字段级**的，不是对技术黑盒化的整体放宽。

### 3.2 边界必须收窄（一票否决）

| 允许（仅此三类） | 禁止（越界即失效） |
| --- | --- |
| 第三方组件**名称** | 把 `模型名` / `向量库` / `RAG` / `Agent` 等术语**作为产品能力描述** |
| 组件**版本** | 用组件名暗示产品功能卖点、能力档位或技术实现路线 |
| **许可信息**（协议名、版权、署名、许可全文链接） | 在署名表之外（导航、营销文案、错误提示、按钮、帮助）出现任何底座 / 向量库 / 模型术语 |
| — | 把 `role` 字段写成技术类别描述（现有 `about/page.tsx:35-39` 的「工作流底座」「记忆检索所需的数据库扩展」「缓存」须由 PM / 工程复核收窄为许可信息或产品化中性表述） |

> **执行边界**：现有 `role` 文案的复核与收窄由 PM / 工程执行；本 ADR **不改动任何代码**
> （第 3 轮冻结：不得改 `deploy/`、`apps/`、`.github/`）。在收窄完成前，
> `dependency-scan.yml:84` 的「含 `license:` 字段」放行规则继续有效，但**不得据此扩写**
> 署名表内容。

### 3.3 两条门禁必须共用同一张路径清单

`apps/web/scripts/check-banned-terms.mjs` 与 `.github/workflows/dependency-scan.yml`
的白名单**必须指向同一份豁免路径清单**，不得各自维护字符串。

- 现状：前者靠「词表不含组件名」隐式放行（无路径白名单），后者靠
  `dependency-scan.yml:84` 的**内联正则**硬编码 `about/page.tsx` 路径 —— 两套口径、两处维护。
- 要求：抽出单一真源（建议 `apps/web/scripts/attribution-allowlist.json` 或由
  `check-banned-terms.mjs` 导出常量供两处引用），清单内容 = 本 ADR §3.1 的页面路径 +
  允许字段（`name` / `version` / `license`）；任何一处新增豁免必须改这份清单并经评审。
- **【规范待固化】**：该单一真源与两处引用改造**尚未实现**，
  登记于 `docs/engineering-standards.md` 附录 A.4（建议脚本名
  `apps/web/scripts/check-attribution-allowlist.mjs` 或并入 `check-banned-terms.mjs`）。
  在落地前，禁止把「共用清单」写成已生效门禁。

### 3.4 与相邻豁免的隔离

本 ADR 只裁决**开源声明页**。`docs/architecture-v1.0.md:157`（§1.4 第 6 条）的
`apps/adapter/qa_probe/bootstrap_dify.py` 运维引导脚本豁免是**独立且唯一**的底座直连
豁免，边界与失效条件不变；出现第二条底座直连豁免需求时，按该条回到评审流程重新裁决，
**不得**引用本 ADR 作为先例。

---

## 四、后果

| 正面 | 代价 / 待办 |
| --- | --- |
| 合规署名义务与黑盒化红线的冲突有了成文边界，不再是「靠 CI 正则隐式放行」 | `about/page.tsx:35-39` 的 `role` 字段需复核收窄（PM / 工程执行，本轮冻结未改） |
| 两条门禁的口径差异被显式登记，避免后人误判为「无门禁」 | 单一路径清单落地前，§3.3 属【规范待固化】，须在 `docs/engineering-standards.md` 附录 A.4 跟踪 |
| ARCH-11 / ARCH-15 两处历史结论得到更正并留痕 | 新增组件到署名表时，须同时核对 §3.2 边界与 `deploy/sbom/generate.py` 生成的 SBOM 一致性（`about/page.tsx:14-19` 的维护约定） |

---

## 五、复核触发条件

1. 署名表新增 / 删除任一组件；
2. `check-banned-terms.mjs` 词表或 `dependency-scan.yml` 的 `FORBIDDEN` / 白名单正则变更；
3. Dify 或其他第三方组件的许可条款变化（`deploy/images.lock` 协议登记变更）；
4. §3.3 的单一路径清单落地时，须回来把「【规范待固化】」改为「机械保证」并注明门禁路径。

---

*记录人：高见远（架构师）。本 ADR 只承载裁决与边界，不改动代码 / 配置 / CI；
执行动作按 `docs/engineering-standards.md` 附录 B-4 的「裁决与执行归属」列分派。*
