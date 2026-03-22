# MOY Commercial Readiness Checklist v1

更新时间：2026-03-22  
适用范围：所有会进入主分支并影响产品行为的任务（功能、接口、权限、规则、配置、公共入口）。

## Purpose

本清单用于把“商业化就绪”定义为系统级门禁，而不是“功能做完即可上线”。  
任务完成时，必须按 gate 逐项给出证据（代码、测试、接口行为、文档）。

## Gate Scorecard

| Gate ID | Gate Key | Gate Name | Blocking Level | Required Evidence |
| --- | --- | --- | --- | --- |
| CR-01 | domain_clarity | Domain Clarity | Blocker | 域模型/状态定义与实现一致 |
| CR-02 | permission_consistency | Permission Consistency | Blocker | UI 可见能力与 API 鉴权一致 |
| CR-03 | source_traceability | Source Traceability | Blocker | 关键对象可追踪 source/type/ref |
| CR-04 | event_action_linkage | Event and Action Linkage | Blocker | 事件 -> 动作 -> 结果链路可追踪 |
| CR-05 | ai_fallback_behavior | AI Fallback Behavior | Blocker | provider 失败时有明确 fallback |
| CR-06 | governance_audit_expectations | Governance and Audit Expectations | Blocker | 高风险变更有治理、审计、可回滚 |
| CR-07 | public_commercial_entry_integrity | Public Commercial Entry Integrity | Blocker | request-demo/start-trial 闭环完整 |
| CR-08 | test_coverage_expectations | Test Coverage Expectations | Blocker | 有聚焦自动化测试覆盖关键改动 |
| CR-09 | build_release_quality_gates | Build and Release Quality Gates | Blocker | lint/test/build 全部通过 |

## CR-01 Domain Clarity

- Gate Key: `domain_clarity`
- Release Rule: 业务对象、状态流转、字段语义必须明确且与现有架构一致。
- Pass Criteria:
  - 新增/修改的核心对象有明确类型定义（`types/*`）。
  - 状态流转可在 service 层定位，避免 page 内隐式状态机。
  - 命名保持 MOY 系统语义，不使用模糊“临时字段”。
- Repo Evidence:
  - 类型：`types/*.ts`
  - 业务服务：`services/*-service.ts`
  - 对象映射：`services/mappers.ts`
- Fail Action: 阻断发布，先补域模型定义与状态说明。

## CR-02 Permission Consistency

- Gate Key: `permission_consistency`
- Release Rule: UI 展示能力、API 实际授权、角色模型必须一致。
- Pass Criteria:
  - UI 不展示后端一定拒绝的动作按钮。
  - API 使用统一 capability/helper 判定，不在页面内“猜权限”。
  - owner/admin/manager/sales/viewer 边界有测试覆盖。
- Repo Evidence:
  - 权限模型：`lib/role-capability.ts`、`lib/auth.ts`
  - 关键页面：`app/(app)/*`
  - API：`app/api/*`
  - 测试：`tests/role-permission-model.test.ts`
- Fail Action: 阻断发布，先修权限对齐和文案误导问题。

## CR-03 Source Traceability

- Gate Key: `source_traceability`
- Release Rule: 关键记录和动作必须可追溯来源、触发链路和责任人。
- Pass Criteria:
  - 关键对象保留 `source_type/source_ref_type/source_ref_id` 或等价链路字段。
  - 业务快照中能定位 `entry_trace`、`request_fingerprint`、触发实体信息。
  - manager/executive 视角可追到“为什么有这个动作/事件”。
- Repo Evidence:
  - 入口追踪：`lib/commercial-entry.ts`
  - lead/action：`services/inbound-lead-service.ts`、`services/work-item-service.ts`
  - 事件：`services/business-event-service.ts`、`conversion_events`
- Fail Action: 阻断发布，先补来源链路字段和映射逻辑。

## CR-04 Event and Action Linkage

- Gate Key: `event_action_linkage`
- Release Rule: 事件与动作必须形成闭环，不允许“有告警无处理”。
- Pass Criteria:
  - 事件可触发工作项、干预请求或明确下一步动作。
  - 动作对象可回链到源事件/源记录。
  - 结果对象可用于归因（至少能定位 action -> outcome）。
- Repo Evidence:
  - 事件：`services/business-event-service.ts`
  - 动作：`services/work-item-service.ts`
  - 归因：`app/api/attribution/route.ts`、`app/api/value-metrics/route.ts`
- Fail Action: 阻断发布，先补事件到动作的系统连接。

## CR-05 AI Fallback Behavior

- Gate Key: `ai_fallback_behavior`
- Release Rule: AI 不是单点依赖，provider 异常时系统必须可降级继续运行。
- Pass Criteria:
  - 场景服务明确 fallback 条件和 fallback 结果结构。
  - API 响应/内部记录能区分 provider vs fallback 来源。
  - fallback 有测试覆盖，避免 silent failure。
- Repo Evidence:
  - provider 与 fallback：`lib/ai/*`、`lib/*-fallback.ts`
  - 场景服务：`services/ai-analysis-service.ts`、`services/trial-conversion-service.ts`
  - 测试入口：`tests/run-tests.ts`
- Fail Action: 阻断发布，先补 fallback 行为和可观测字段。

## CR-06 Governance and Audit Expectations

- Gate Key: `governance_audit_expectations`
- Release Rule: 高风险配置与控制面写入必须可诊断、可审计、可回滚。
- Pass Criteria:
  - 写路径有 schema 校验、冲突检测（expectedVersion/compareToken）。
  - 关键变更进入审计日志并保留版本上下文。
  - 回滚路径至少具备 preview + execute 的受控流程。
- Repo Evidence:
  - 治理服务：`services/org-config-governance-service.ts`
  - 并发保护：`lib/override-concurrency-guard.ts`
  - 相关 API：`app/api/settings/*`
- Fail Action: 阻断发布，先修治理链路后再合并。

## CR-07 Public Commercial Entry Integrity

- Gate Key: `public_commercial_entry_integrity`
- Release Rule: 公共入口必须是商业化系统入口，不是孤立表单。
- Pass Criteria:
  - `/request-demo`、`/start-trial` 提交进入 lead -> assignment -> downstream request。
  - 入口 trace、资格快照、handoff 状态可在系统中追踪。
  - trial 场景具备 onboarding intent，不丢失下游执行意图。
- Repo Evidence:
  - 页面：`app/request-demo/page.tsx`、`app/start-trial/page.tsx`
  - API：`app/api/public/request-demo/route.ts`、`app/api/public/start-trial/route.ts`
  - 服务：`services/inbound-lead-service.ts`、`services/demo-request-service.ts`、`services/trial-request-service.ts`
  - 文档：`docs/commercial-entry-system-v1.md`
- Fail Action: 阻断发布，先补系统闭环与追踪元数据。

## CR-08 Test Coverage Expectations

- Gate Key: `test_coverage_expectations`
- Release Rule: 每个有业务影响的改动必须有聚焦自动化测试。
- Pass Criteria:
  - 新逻辑至少新增或更新 1 组针对性测试。
  - 测试断言覆盖“成功路径 + 关键边界/失败降级路径”。
  - 测试已接入 `tests/run-tests.ts` 主入口。
- Repo Evidence:
  - 测试文件：`tests/*.test.ts`
  - 主入口：`tests/run-tests.ts`
- Fail Action: 阻断发布，先补测试再进入发布流程。

## CR-09 Build and Release Quality Gates

- Gate Key: `build_release_quality_gates`
- Release Rule: 发布前质量门禁必须全部通过。
- Pass Criteria:
  - `npm run lint` 通过
  - `npm run test` 通过
  - `npm run build` 通过
  - 失败项必须修复，不允许“带红发布”
- Repo Evidence:
  - CI/本地执行日志
  - `package.json` scripts
- Fail Action: 任一失败即阻断发布。

## Release Decision Record

每次任务交付时，建议附上以下记录（可直接复制）：

```markdown
### Commercial Readiness Decision

- Scope:
- Date:
- Owner:

- CR-01 domain_clarity: PASS/FAIL
- CR-02 permission_consistency: PASS/FAIL
- CR-03 source_traceability: PASS/FAIL
- CR-04 event_action_linkage: PASS/FAIL
- CR-05 ai_fallback_behavior: PASS/FAIL
- CR-06 governance_audit_expectations: PASS/FAIL
- CR-07 public_commercial_entry_integrity: PASS/FAIL
- CR-08 test_coverage_expectations: PASS/FAIL
- CR-09 build_release_quality_gates: PASS/FAIL

- Overall Decision: GO / NO-GO
- Remaining Risks:
- Follow-up Task:
```

## Validation Commands

```bash
npm run lint
npm run test
npm run build
```

若任一命令失败，本次交付不得标记为 commercial-ready。
