"""合规规则包（P1-10）：声明式护栏的规则实现与注册表。

**引入本包的目的**：护栏判据此前写死在 `engine` 的 `if output_key == ...`
分支里——插件要换判据就得改引擎代码，而"改引擎"意味着全平台回归。
现在「哪个产物键过哪条规则」进 `manifest.guards[]`，判据实现在本包的
规则模块里，引擎只负责声明式分派与「违规 → `StepOutcome`」的翻译。

分层：`registry` 只认协议与注册表；规则模块认 `(text, content, params)`；
`types.GuardViolation` 是规则与引擎之间的唯一结果类型。本包不 import
`engine` / `actions` / `app.services.*`。

导入本包即完成三规则的注册（`__init__` 拉起各规则模块）。
"""

from __future__ import annotations

from app.plugins.guards.offer_amount import (
    OFFER_AMOUNT_ALLOWED_VALUES,
    OFFER_AMOUNT_CONTEXT_KEYWORDS,
    OFFER_AMOUNT_CONTEXT_WINDOW,
    OFFER_AMOUNT_FIELDS,
    OFFER_AMOUNT_FORBIDDEN_PHRASES,
    OFFER_AMOUNT_OPERATOR_ACTION,
    OFFER_AMOUNT_RETRYABLE,
    OFFER_OUTPUT_KEY,
    AmountScan,
    scan_offer_amount,
)
from app.plugins.guards.registry import (
    GuardRule,
    all_rule_names,
    register,
    required_rules_for,
    resolve_guard,
)
from app.plugins.guards.schedule_timeline import (
    SCHEDULE_OPERATOR_ACTION,
    SCHEDULE_OUTPUT_KEY,
    SCHEDULE_PLATFORM_KEYS,
    SCHEDULE_RETRYABLE,
    SCHEDULE_TIME_FORMATS,
    SCHEDULE_TIME_KEYS,
    SCHEDULE_TZ,
    ScheduleScan,
    scan_schedule,
)
from app.plugins.guards.screening_risk import (
    SCREENING_OPERATOR_ACTION,
    SCREENING_OUTPUT_KEY,
    SCREENING_RETRYABLE,
    SCREENING_RISK_HEADERS,
    SCREENING_ROW_ID_KEYS,
    scan_screening,
)
from app.plugins.guards.table_parse import (
    column_count,
    is_markdown_separator,
    markdown_table_rows,
    parse_csv_rows,
    pick_column,
    split_markdown_row,
)
from app.plugins.guards.types import GuardViolation

__all__: list[str] = [
    "OFFER_AMOUNT_ALLOWED_VALUES",
    "OFFER_AMOUNT_CONTEXT_KEYWORDS",
    "OFFER_AMOUNT_CONTEXT_WINDOW",
    "OFFER_AMOUNT_FIELDS",
    "OFFER_AMOUNT_FORBIDDEN_PHRASES",
    "OFFER_AMOUNT_OPERATOR_ACTION",
    "OFFER_AMOUNT_RETRYABLE",
    "OFFER_OUTPUT_KEY",
    "SCHEDULE_OPERATOR_ACTION",
    "SCHEDULE_OUTPUT_KEY",
    "SCHEDULE_PLATFORM_KEYS",
    "SCHEDULE_RETRYABLE",
    "SCHEDULE_TIME_FORMATS",
    "SCHEDULE_TIME_KEYS",
    "SCHEDULE_TZ",
    "SCREENING_OPERATOR_ACTION",
    "SCREENING_OUTPUT_KEY",
    "SCREENING_RETRYABLE",
    "SCREENING_RISK_HEADERS",
    "SCREENING_ROW_ID_KEYS",
    "AmountScan",
    "GuardRule",
    "GuardViolation",
    "ScheduleScan",
    "all_rule_names",
    "column_count",
    "is_markdown_separator",
    "markdown_table_rows",
    "parse_csv_rows",
    "pick_column",
    "register",
    "required_rules_for",
    "resolve_guard",
    "scan_offer_amount",
    "scan_schedule",
    "scan_screening",
    "split_markdown_row",
]
