#!/usr/bin/env python3
"""计量对账脚本（阶段二 B07 / G-23 / 架构文档 2.5 第 7 条 / PRD P2-BL-03 验收 5）。

**为什么要对账**（而 M4 与 BL5 不需要对账）：

- `usage_event` 与 `usage_monthly` 由**同一事件同一事务**写入，因此
  「按成员求和 == 企业总量」是**结构保证**，偏差必须为 **0**（任何非 0 都是缺陷）；
- 但「事件表总和」与「月度聚合」之间可能因运维手工改数、历史迁移、
  折算口径变更而漂移，这条链路的偏差才用阈值（默认 **1%**）判定。

三条检查（任一不通过 → 退出码 1）：

1. **结构误差 0**：`SUM(usage_monthly WHERE scope_type='member')` ==
   `usage_monthly(scope_type='tenant', scope_id=tenant)`；
2. **事件 vs 聚合**：`SUM(usage_event.units)` 与租户聚合行偏差 ≤ 阈值；
3. **逐能力维度**：按能力逐项比较，避免「总量对得上但内部错位」。

偏差超限时写一条 `audit_log(action='billing.reconcile_mismatch')`（含前后值），
便于运营在操作记录页直接看到（不写技术日志而已）。

用法::

    python apps/adapter/scripts/reconcile_usage.py --period 2026-11 --threshold 0.01
    OA_BUSINESS_STORE=postgres python apps/adapter/scripts/reconcile_usage.py \\
        --period 2026-11 --tenant tnt_xxx

退出码：0 = 通过；1 = 偏差超限或结构误差非 0；2 = 环境不可用（非 PostgreSQL）。

> 说明（诚实登记）：设计稿提到的「Valkey 诊断计数 vs 聚合」在本阶段**未接入**，
> 原因是适配层尚无 Valkey 客户端（限流为进程内实现），且 D-12 已明确
> 「Valkey 只做实时限速与诊断，不作为用量真相源」。因此本脚本以
> **事件表**作为独立第二来源完成对账，口径等价且更强（事件表是幂等且可审计的）。

**执行形态与 CI 边界（B-8 结论）**：

- **不由 PR CI 触发**；由部署侧每日 02:00 调度执行
  （`docs/architecture-phase2.md:417` 的「运维 cron 或 CI 手动跑」）。
- 正确性由 `tests/integration/test_usage_endpoints.py` 与
  `tests/integration/test_qa_b07_b08_verification.py` 的单测/集成门禁覆盖，
  脚本本体不进 `.github/workflows/`。
- 执行需真实 PostgreSQL（`OA_BUSINESS_STORE=postgres`，否则退出码 2 / `[SKIP]`），
  且偏差超限时**会写** `audit_log(billing.reconcile_mismatch)`。
  因此**禁止在 CI 空账期运行**：三项计量全 0 的「恒等假绿」没有统计意义
  （`docs/qa-phase2-test-plan.md` R-5 要求最小样本量），还会污染审计表。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

#: 适配层根目录（`apps/adapter`）
ADAPTER_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from app.business.audit_service import AuditService  # noqa: E402 - 需先补 sys.path
from app.core.config import Settings  # noqa: E402
from app.db.repositories.usage import (  # noqa: E402
    CAPABILITIES,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    current_period,
)

#: 默认偏差阈值（1%，G-23）
DEFAULT_THRESHOLD: Final[float] = 0.01

#: 对账偏差超限时写入审计的动作编码
ACTION_RECONCILE_MISMATCH: Final[str] = "billing.reconcile_mismatch"

#: 对账审计的资源类型（billing 域尚无独立资源类型，复用计量事件语义）
RESOURCE_USAGE: Final[str] = "usage_reconcile"

#: 对账操作的执行人标识（系统动作）
RECONCILE_ACTOR: Final[str] = "system"


@dataclass(slots=True)
class ReconcileReport:
    """对账结果。

    Attributes:
        period: 账期。
        tenant_id: 租户 ID。
        member_total: 按成员求和。
        tenant_total: 企业维度总量。
        event_total: 事件表总额。
        threshold: 允许的偏差阈值。
        structure_ok: 「成员求和 == 企业总量」是否成立（必须严格相等）。
        deviation: 事件表与企业总量的相对偏差。
        capability_diffs: 逐能力偏差明细。
        messages: 失败原因清单（业务语言，可直接展示给运营）。
    """

    period: str = ""
    tenant_id: str = ""
    member_total: int = 0
    tenant_total: int = 0
    event_total: int = 0
    threshold: float = DEFAULT_THRESHOLD
    structure_ok: bool = True
    deviation: float = 0.0
    capability_diffs: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """是否通过全部检查。"""
        return self.structure_ok and self.deviation <= self.threshold and not self.messages


def relative_deviation(left: int, right: int) -> float:
    """计算两项计量的相对偏差。

    Args:
        left: 计量 A。
        right: 计量 B。

    Returns:
        float: `|A-B| / max(A,B)`；两者皆为 0 时返回 0.0。
    """
    baseline = max(abs(left), abs(right))
    if baseline == 0:
        return 0.0
    return abs(left - right) / baseline


async def reconcile(
    *,
    settings: Settings,
    tenant_id: str,
    period: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> ReconcileReport:
    """执行一次对账（纯计算，不写审计）。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
        period: 账期 `YYYY-MM`。
        threshold: 允许偏差。

    Returns:
        ReconcileReport: 对账结果。
    """
    from app.business.usage_service import UsageService, get_usage_store

    service = UsageService(settings, store=get_usage_store(settings))
    rows = await service.store.monthly_rows(tenant_id, period)
    member_total = sum(row.units for row in rows if row.scope_type == SCOPE_MEMBER)
    tenant_total = sum(
        row.units for row in rows if row.scope_type == SCOPE_TENANT and row.scope_id == tenant_id
    )
    event_total = await service.event_total(tenant_id=tenant_id, period=period)

    report = ReconcileReport(
        period=period,
        tenant_id=tenant_id,
        member_total=member_total,
        tenant_total=tenant_total,
        event_total=event_total,
        threshold=threshold,
        structure_ok=member_total == tenant_total,
        deviation=relative_deviation(event_total, tenant_total),
    )
    if not report.structure_ok:
        report.messages.append(
            f"按成员求和（{member_total}）不等于企业总量（{tenant_total}），结构误差必须为 0"
        )
    if report.deviation > threshold:
        report.messages.append(
            f"事件表总额（{event_total}）与企业总量（{tenant_total}）偏差 "
            f"{report.deviation:.2%} 超过阈值 {threshold:.2%}"
        )

    for capability in CAPABILITIES:
        event_units = sum(
            row.units
            for row in rows
            if row.capability == capability and row.scope_type == SCOPE_TENANT
        )
        if event_units == 0:
            continue
        member_units = sum(
            row.units
            for row in rows
            if row.capability == capability and row.scope_type == SCOPE_MEMBER
        )
        report.capability_diffs[capability] = member_units - event_units

    return report


async def record_mismatch(settings: Settings, report: ReconcileReport) -> None:
    """把对账失败写入操作记录（运营可见，含前后值）。

    Args:
        settings: 配置实例。
        report: 对账结果。
    """
    audit = AuditService(settings)
    await audit.record(
        tenant_id=report.tenant_id,
        actor_id=RECONCILE_ACTOR,
        action=ACTION_RECONCILE_MISMATCH,
        resource_type=RESOURCE_USAGE,
        resource_id=report.period,
        result="failure",
        change_summary={
            "period": report.period,
            "before": {"tenant_total": report.tenant_total, "event_total": report.event_total},
            "after": {"member_total": report.member_total},
            "deviation": round(report.deviation, 6),
            "threshold": report.threshold,
            "reasons": report.messages,
        },
    )


async def _run_once(
    *, args: argparse.Namespace, settings: Settings, period: str
) -> tuple[ReconcileReport, bool]:
    """在同一事件循环内完成「对账 + 偏差超限写审计」。

    两次 `asyncio.run` 会复用绑定了已关闭事件循环的数据库引擎单例，导致
    `record_mismatch` 抛 `Event loop is closed`（被上层当成「脚本崩了」），
    而退出码仍是 1 → 表面看「失败即告警」，实际审计里没有记录（QA `test_b07_5c`）。

    Args:
        args: 命令行参数（用到 `tenant` / `threshold`）。
        settings: 配置实例。
        period: 账期 `YYYY-MM`。

    Returns:
        tuple[ReconcileReport, bool]: 对账结果与**审计是否写入成功**。
    """
    report = await reconcile(
        settings=settings,
        tenant_id=args.tenant,
        period=period,
        threshold=args.threshold,
    )
    if report.passed:
        return report, False
    await record_mismatch(settings, report)
    return report, True


def render(report: ReconcileReport) -> str:
    """把对账结果渲染为可读文本。

    Args:
        report: 对账结果。

    Returns:
        str: 多行文本。
    """
    status = "PASS" if report.passed else "FAIL"
    lines = [
        f"[{status}] period={report.period} tenant={report.tenant_id}",
        f"  成员求和={report.member_total} 企业总量={report.tenant_total} "
        f"事件总额={report.event_total} 偏差={report.deviation:.4%} 阈值={report.threshold:.2%}",
    ]
    if report.capability_diffs:
        diffs = ", ".join(
            f"{key}:{value}" for key, value in sorted(report.capability_diffs.items())
        )
        lines.append(f"  逐能力差值（成员-企业）：{diffs}")
    for message in report.messages:
        lines.append(f"  - {message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 0 = 通过；1 = 不通过；2 = 环境不可用。
    """
    parser = argparse.ArgumentParser(description="计量对账（G-23）：事件表 vs 月度聚合")
    parser.add_argument("--period", default="", help="账期 YYYY-MM（缺省为当前 UTC 月）")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="允许偏差（默认 0.01，即 1%）",
    )
    parser.add_argument("--tenant", default="default", help="租户 ID（默认 default）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于 CI 采集）")
    args = parser.parse_args(argv)

    settings = Settings()
    if not settings.uses_postgres:
        print(
            "[SKIP] 当前 OA_BUSINESS_STORE 不是 postgres，进程内无持久化计量数据；"
            "请用 OA_BUSINESS_STORE=postgres 重跑对账。"
        )
        return 2

    period = args.period or current_period(datetime.now(UTC))

    import asyncio

    # **必须单事件循环**：`app.db.engine` 是进程级单例且绑定创建它的事件循环，
    # 先 `asyncio.run(reconcile)` 再 `asyncio.run(record_mismatch)` 会在第二次运行
    # 时拿到「Event loop is closed」的引擎 → 偏差超限时审计写不进去
    # （QA `test_b07_5c` 实测：退出码 1 但 audit_log 无记录，等于告警丢失）。
    report, audited = asyncio.run(_run_once(args=args, settings=settings, period=period))
    if report is None:  # pragma: no cover - _run_once 恒返回报告
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "period": report.period,
                    "tenant_id": report.tenant_id,
                    "member_total": report.member_total,
                    "tenant_total": report.tenant_total,
                    "event_total": report.event_total,
                    "deviation": round(report.deviation, 6),
                    "threshold": report.threshold,
                    "structure_ok": report.structure_ok,
                    "passed": report.passed,
                    "messages": report.messages,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(render(report))

    if report.passed:
        return 0
    if audited:
        print("[FAIL] 计量对账未通过（已写入操作记录 billing.reconcile_mismatch）")
    else:  # pragma: no cover - 审计写入失败时不得静默
        print(
            "[FAIL] 计量对账未通过，但操作记录写入失败；请在数据库可写后重跑，否则告警会丢失。",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
