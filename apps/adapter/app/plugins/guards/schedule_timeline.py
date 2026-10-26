"""发布排期时间校验（架构 §7.2 第 5 步 / P1-10 声明化）。

判据两条：

1. 计划时间不得早于当前（按**北京时间**判定，见 `SCHEDULE_TZ`）；
2. 同平台同刻不得重复（发布会撞车）。

**语义硬约束（不进 params）**：识别不了的时间写法**不判失败**——排期表的
时间列允许用户自由填写，把"没认出来"当成违规会让守规矩的用户反复被拦
（比漏判更伤）。params 只允许收紧列名 / 时间写法 / 文案，
`validate_params` 拒绝把 `forbid_past` / `forbid_duplicate_platform_time`
关掉的布尔项。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Final

from app.core.logging import get_logger
from app.plugins.guards.registry import register
from app.plugins.guards.table_parse import parse_csv_rows, pick_column
from app.plugins.guards.types import GuardViolation

logger = get_logger(__name__)

#: 产物键（声明侧由 `GuardSpec.output_key` 声明；这里是规则的强制覆盖范围）
SCHEDULE_OUTPUT_KEY: Final[str] = "schedule"

#: 排期表里「平台」列的候选键名（声明侧不固定表头，这里给一组常见写法）
SCHEDULE_PLATFORM_KEYS: Final[tuple[str, ...]] = ("平台", "platform", "channel")

#: 排期表里「计划时间」列的候选键名
SCHEDULE_TIME_KEYS: Final[tuple[str, ...]] = (
    "计划时间",
    "planned_at",
    "计划发布时间",
    "时间",
    "time",
)

#: 排期时间的**业务时区**（Asia/Shanghai，固定 UTC+8）。
#:
#: 为什么需要它：`strptime` 解析出的是 **naive** datetime（无时区），而
#: 无参 `datetime.now` 也给 naive 本地时间。两者相减/相比只在「进程本地时区恰好
#: 就是业务时区」时才对——容器默认 `TZ=UTC` 时，用户写的「2026-11-02 10:00」
#: 会被当成 UTC 10:00，也就是北京时间 18:00，**过去 8 小时的排期会被判成未来**，
#: 反之亦然；更糟的是把 tz-aware 的 `now`（如 `datetime.now(UTC)`）传进来会直接
#: `TypeError: can't compare offset-naive and offset-aware datetimes`。
#:
#: 口径：排期表是给人看的**北京时间**，因此解析结果一律按 `SCHEDULE_TZ` 解释，
#: 参照时间统一折算到 aware 后再比较。中国全境单一时区、无夏令时，固定偏移
#: 是安全且免依赖 `zoneinfo`（tzdata 在精简镜像里未必存在）的选择。
SCHEDULE_TZ: Final[timezone] = timezone(timedelta(hours=8))

#: 业务时区偏移（小时；manifest params 的 `timezone_offset_hours` 基线）
SCHEDULE_TZ_OFFSET_HOURS: Final[int] = 8

#: 可识别的时间写法（识别不了的**不判失败**——宁可放过也不误伤用户的自由格式）
SCHEDULE_TIME_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

#: 排期违规时给用户的建议动作
SCHEDULE_OPERATOR_ACTION: Final[str] = "修改排期表里的计划时间后重新导出"

#: 违规是否可重试（时间不补好，重跑还是同样的表）
SCHEDULE_RETRYABLE: Final[bool] = False


@dataclass(frozen=True, slots=True)
class ScheduleScan:
    """发布排期表的校验结果。

    Attributes:
        problems: 业务语言的问题清单（空表示通过）。
        checked_rows: 实际解析出计划时间的行数（0 表示没有可校验的时间）。
    """

    problems: tuple[str, ...] = ()
    checked_rows: int = 0

    @property
    def clean(self) -> bool:
        """是否通过校验。

        Returns:
            bool: 无问题返回 True。
        """
        return not self.problems


def _parse_schedule_time(
    value: str, time_formats: tuple[str, ...], tz: timezone
) -> datetime | None:
    """按声明支持的几种写法解析时间（都不匹配返回 None）。

    Args:
        value: 原始时间文本。
        time_formats: `strptime` 格式列表。
        tz: 解释解析结果的业务时区。

    Returns:
        datetime | None: 解析结果；无法识别返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in time_formats:
        try:
            # 解析结果按**业务时区**解释（naive + tzinfo），否则与 aware 的参照
            # 时间比较会 TypeError，与 naive 本地时间比较会错 8 小时。
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def scan_schedule(
    rows: list[dict[str, str]],
    *,
    now: datetime | None = None,
    time_keys: tuple[str, ...] = SCHEDULE_TIME_KEYS,
    platform_keys: tuple[str, ...] = SCHEDULE_PLATFORM_KEYS,
    time_formats: tuple[str, ...] = SCHEDULE_TIME_FORMATS,
    timezone_offset_hours: int = SCHEDULE_TZ_OFFSET_HOURS,
    forbid_past: bool = True,
    forbid_duplicate_platform_time: bool = True,
) -> ScheduleScan:
    """校验排期表：计划时间不得早于当前，同平台同刻不得重复。

    识别不了的时间写法**不判失败**——排期表的时间列允许用户自由填写，
    把"没认出来"当成违规会让守规矩的用户反复被拦（比漏判更伤）。

    时区口径（审计 P2-4）：排期时间是**北京时间**（`SCHEDULE_TZ` = UTC+8）。
    解析结果与参照时间都折算到 aware 后比较；naive 的 `now` 按 `SCHEDULE_TZ`
    解释，缺省参照取 `datetime.now(UTC)`。这样容器 `TZ=UTC` 时也不会错 8 小时，
    传 aware `now` 也不会 `TypeError`。

    Args:
        rows: 排期表行。
        now: 参照时间（缺省取当前时刻；测试注入固定值以保证可复现）。
            naive 值按 `SCHEDULE_TZ` 解释，aware 值按绝对时间参与比较。
        time_keys: 「计划时间」列候选键名（收紧列名用）。
        platform_keys: 「平台」列候选键名。
        time_formats: 可识别的时间写法（收紧写法用）。
        timezone_offset_hours: 业务时区偏移（小时）。
        forbid_past: 是否拦截过去时间（**必须为 True**——关掉等于拆掉主判据）。
        forbid_duplicate_platform_time: 是否拦截同平台同刻重复（**必须为 True**）。

    Returns:
        ScheduleScan: 校验结果。
    """
    tz = timezone(timedelta(hours=int(timezone_offset_hours)))
    # 参照时间必须与解析结果**同时区**（见 SCHEDULE_TZ 说明）：naive 入参按
    # 业务时区解释，缺省取 UTC 当前时刻（aware），比较的是绝对时间。
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=tz)
    problems: list[str] = []
    seen: dict[tuple[str, datetime], int] = {}
    checked = 0
    for index, row in enumerate(rows, start=1):
        raw_time = pick_column(row, time_keys)
        platform = pick_column(row, platform_keys)
        parsed = _parse_schedule_time(raw_time, time_formats, tz)
        if parsed is None:
            continue
        checked += 1
        if forbid_past and parsed < reference:
            problems.append(f"第 {index} 行计划时间（{raw_time}）已经过去了")
            continue
        key = (platform.strip(), parsed)
        if forbid_duplicate_platform_time and key in seen:
            problems.append(f"第 {seen[key]} 行与第 {index} 行是同一个平台同一个时间，发布会重复")
            continue
        seen[key] = index
    return ScheduleScan(problems=tuple(problems), checked_rows=checked)


def _as_str_tuple(params: Mapping[str, object], key: str) -> tuple[str, ...]:
    """把声明参数里的非空字符串列表读成元组（缺失返回空元组 = 用缺省）。

    Args:
        params: 声明参数。
        key: 参数键。

    Returns:
        tuple[str, ...]: 字符串元组。

    Raises:
        ValueError: 类型不合法或空列表（空列表会关掉判据）。
    """
    raw = params.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple) or not raw:
        raise ValueError(f"参数 {key} 必须是非空列表（空列表会关掉判据）")
    if not all(isinstance(item, str) and item for item in raw):
        raise ValueError(f"参数 {key} 必须是非空字符串列表")
    return tuple(raw)


#: 声明参数允许的键
_ALLOWED_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "time_keys",
        "platform_keys",
        "time_formats",
        "timezone_offset_hours",
        "forbid_past",
        "forbid_duplicate_platform_time",
        "retryable",
        "operator_action",
    }
)

#: **必须保持为 True** 的判据开关（False = 把判据关掉，一律拒绝）
_LOCKED_TRUE_KEYS: Final[tuple[str, ...]] = ("forbid_past", "forbid_duplicate_platform_time")


class _ScheduleTimelineRule:
    """发布排期时间规则的声明式实现（`GuardRule` 协议）。"""

    name: Final[str] = "schedule_timeline"
    required_output_keys: Final[tuple[str, ...]] = (SCHEDULE_OUTPUT_KEY,)

    def validate_params(self, params: Mapping[str, object]) -> None:
        """校验声明参数：只允许收紧列名/写法/文案，拒绝关闭判据的布尔项。

        Args:
            params: 声明里的 `guards[].params`。

        Raises:
            ValueError: 未知键、类型不合法、或试图关掉判据。
        """
        unknown = sorted(set(params) - _ALLOWED_PARAM_KEYS)
        if unknown:
            raise ValueError(f"schedule_timeline 不认识这些参数：{unknown}")
        for key in ("time_keys", "platform_keys", "time_formats"):
            if key in params:
                _as_str_tuple(params, key)
        if "timezone_offset_hours" in params:
            offset = params["timezone_offset_hours"]
            if not isinstance(offset, int) or isinstance(offset, bool) or not -12 <= offset <= 14:
                raise ValueError("参数 timezone_offset_hours 必须是 -12..14 的整数")
        for key in _LOCKED_TRUE_KEYS:
            if key in params and params[key] is not True:
                raise ValueError(f"参数 {key} 不能关闭（合规判据不可被声明关掉）")
        if "retryable" in params and not isinstance(params["retryable"], bool):
            raise ValueError("参数 retryable 必须是布尔值")
        if "operator_action" in params:
            action = params["operator_action"]
            if not isinstance(action, str) or not action.strip():
                raise ValueError("参数 operator_action 必须是非空文案")

    def check(
        self, *, text: str, content: bytes | None, params: Mapping[str, object]
    ) -> GuardViolation | None:
        """校验排期表时间（过去时间 / 同平台同刻重复）。

        Args:
            text: 产物内联文本（`content` 缺失时按 CSV 解析）。
            content: 排期表 CSV 字节。
            params: 声明参数（缺省 = 内置基线）。

        Returns:
            GuardViolation | None: 违规返回违规记录；合规返回 None。
        """
        rows = parse_csv_rows(content) if content else parse_csv_rows((text or "").encode("utf-8"))
        time_keys = _as_str_tuple(params, "time_keys") or SCHEDULE_TIME_KEYS
        platform_keys = _as_str_tuple(params, "platform_keys") or SCHEDULE_PLATFORM_KEYS
        time_formats = _as_str_tuple(params, "time_formats") or SCHEDULE_TIME_FORMATS
        raw_offset = params.get("timezone_offset_hours")
        offset = raw_offset if isinstance(raw_offset, int) else SCHEDULE_TZ_OFFSET_HOURS
        scan = scan_schedule(
            rows,
            time_keys=time_keys,
            platform_keys=platform_keys,
            time_formats=time_formats,
            timezone_offset_hours=offset,
            forbid_past=bool(params.get("forbid_past", True)),
            forbid_duplicate_platform_time=bool(params.get("forbid_duplicate_platform_time", True)),
        )
        if scan.clean:
            return None
        logger.warning("plugin_schedule_blocked", problems=list(scan.problems))
        return GuardViolation(
            reason_text="；".join(scan.problems),
            operator_action=str(params.get("operator_action") or SCHEDULE_OPERATOR_ACTION),
            retryable=bool(params.get("retryable", SCHEDULE_RETRYABLE)),
        )


register(_ScheduleTimelineRule())


__all__: list[str] = [
    "SCHEDULE_OPERATOR_ACTION",
    "SCHEDULE_OUTPUT_KEY",
    "SCHEDULE_PLATFORM_KEYS",
    "SCHEDULE_RETRYABLE",
    "SCHEDULE_TIME_FORMATS",
    "SCHEDULE_TIME_KEYS",
    "SCHEDULE_TZ",
    "SCHEDULE_TZ_OFFSET_HOURS",
    "ScheduleScan",
    "scan_schedule",
]
