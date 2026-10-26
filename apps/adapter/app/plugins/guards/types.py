"""合规规则的公共结果类型（P1-10）。

规则模块只产出 `GuardViolation`，**不**认识 `StepOutcome` / `ArtifactDraft`：
把违规翻译成步骤状态是引擎的职责，规则只回答「哪里不合规、用户该怎么办」。
这条分界让规则可以被加载器、门禁与单测直接调用，不必牵动整个引擎。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardViolation:
    """一条合规违规（业务语言，直接可展示给用户）。

    Attributes:
        reason_text: 违规原因（业务语言，禁止正则 / 字段名 / 错误码）。
        operator_action: 建议动作（业务语言，告诉用户下一步怎么补）。
        retryable: 用户能否靠重跑补救（评分表风险为空 = True，金额/排期 = False）。
    """

    reason_text: str
    operator_action: str
    retryable: bool = False


__all__: list[str] = ["GuardViolation"]
