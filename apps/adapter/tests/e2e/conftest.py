"""阶段三 B26：三插件端到端（e2e）共享装配。

**这个目录验的是什么**（写清楚，否则 e2e 最容易变成"跑通了不报错"）：

1. **真实的一层**：真 PostgreSQL（`plugin_task` / `plugin_task_step` / `plugin_artifact` /
   `plugin_installation` / `plugin_grant` / `plugin_capability_policy` 全部真读写）、
   真 HTTP 端点（A1/A3/A4/WF1/PL1-PL14）、真步骤引擎与护栏（`PluginEngine.guard`）、
   真渲染器（`FileRenderer` 出 csv/docx 字节）、真产物下载代理
   （`/files/artifacts/{token}` 的 HMAC 令牌）、**真 git 对象**
   （`git init --bare` + `hash-object`/`mktree`/`commit-tree`，不用 mock）。
2. **被桩住的一层**：外部世界只有两处——「生成服务」与「办公工具」。
   它们由 `CorpusActionGateway` 以**固定语料**应答（`architecture-phase3.md` B26
   第 2 项「6 插件声明 + 桩能力 + 登录态夹具」，以及 QA 计划 §5.5 的
   `local_bare_repo` / `github_stub` / `wechat_mp_stub` / `fail_injector` 夹具）。
   桩的注入点就是生产用的那一个：`app/api/v1/plugin_deps.build_action_gateway()`。

**为什么必须这样桩**：平台侧产物是"下游产出 → 引擎脱敏 → 落库 → 渲染/下载"，
判定点全在**平台这一段**（CSV 是否逐行一致、docx 是否可打开、提交内容是否与产物
逐字相同、失败后前序步骤是否被重跑）。把下游换成固定语料，才能让这些判定点
**可复现地**被断言；反之若真去调模型，"风险非空""≥5 问"这类判据每次都在变，
只能退化成"看起来差不多"。

**每个用例的 docstring 都要写清"验的是哪个用户旅程的哪个判定点"**。
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.business.plugin_grant_service import reset_plugin_grant_service
from app.business.plugin_review_service import reset_plugin_review_service
from app.business.plugin_service import reset_plugin_service
from app.business.subscription_service import SubscriptionService
from app.business.task_service import reset_task_service
from app.core.config import Settings, get_settings, reset_settings_cache
from app.db.engine import dispose_engine
from app.db.repositories.artifact_repo import get_artifact_store, reset_artifact_store
from app.db.repositories.billing import get_billing_store
from app.db.repositories.task import get_task_store, reset_task_store
from app.db.session import platform_scope
from app.main import create_app
from app.plugins.actions import (
    ACTION_WHITELIST,
    ActionResult,
    ArtifactDraft,
    RunContext,
)
from app.plugins.sanitizer import ArtifactSanitizer
from app.schemas.billing import ChangeSubscriptionRequest

#: e2e 用例统一使用「按用例唯一」的租户，跨用例互不污染（也顺带验了 RLS 不串行）
TENANT_PREFIX: str = "tnt_e2e"

#: 三个标杆插件
DEV_PLUGIN: str = "plg_dev_workbench"
CONTENT_PLUGIN: str = "plg_content_ops"
HR_PLUGIN: str = "plg_hr_admin"

#: 内置 6 个插件（勘误 #1：市场永远返回这 6 条）
EXPECTED_PLUGIN_IDS: frozenset[str] = frozenset(
    {
        DEV_PLUGIN,
        CONTENT_PLUGIN,
        HR_PLUGIN,
        "plg_weekly_report",
        "plg_meeting_minutes",
        "plg_mail_draft",
    }
)

#: 企业能力（`org.*` 三项）
ORG_CAPABILITIES: tuple[str, ...] = (
    "org.directory.read",
    "org.repo.write",
    "org.external.publish",
)

#: 生成服务 / 工具的桩返回里不出现任何凭据字样（引擎会做 `assert_no_secret`）
STUB_RECEIPT_ID: str = "wx_rcpt_e2e_0001"
STUB_EVENT_ID: str = "evt_e2e_0001"


def pytest_configure(config: pytest.Config) -> None:
    """注册 `e2e` 标记（`--strict-markers` 下未注册即红）。

    Args:
        config: pytest 配置对象。
    """
    config.addinivalue_line(
        "markers", "e2e: 三插件端到端结果物用例（需 PostgreSQL，--run-pg 启用）"
    )


# ===========================================================================
# 固定语料（下游"生成服务"的应答）
# ===========================================================================
#: 研发工作台：步骤 3 的代码文件内容（提交后要与 `git show` 逐字比对）
DEV_CODE: str = (
    "export function ExportButton({ onClick }: { onClick: () => void }) {\n"
    "  return <button onClick={onClick}>导出</button>;\n"
    "}\n"
)

#: 研发工作台：步骤 3 提交到仓库时使用的文件路径
DEV_CODE_PATH: str = "src/components/export-button.tsx"

#: 研发工作台：步骤 4 的用例文件内容
DEV_TEST_CODE: str = (
    "import { render } from '@testing-library/react';\n"
    "import { ExportButton } from '../src/components/export-button';\n"
    "\n"
    "test('点击导出会触发回调', () => {\n"
    "  const onClick = jest.fn();\n"
    "  render(<ExportButton onClick={onClick} />);\n"
    "});\n"
)

#: 内容运营：排期表列顺序（逐字，QA 计划 TC-P3-CNT20-01 的四列）
SCHEDULE_COLUMNS: list[str] = ["平台", "标题", "计划时间", "状态"]

#: 行政人事：评分表列顺序
SCREENING_COLUMNS: list[str] = ["姓名", "匹配分", "亮点", "风险", "结论", "理由"]


def sha256_of(data: str | bytes) -> str:
    """计算 sha256（与 `ArtifactSanitizer.digest` 同口径）。

    Args:
        data: 文本或字节。

    Returns:
        str: 64 位十六进制摘要。
    """
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


# ===========================================================================
# 本地 bare 仓库（真实 git 对象，"离开界面也成立"的结果物）
# ===========================================================================
class BareRepo:
    """本地裸仓库（用 git CLI 造真对象，不用 mock）。

    Attributes:
        path: 裸仓库目录。
    """

    def __init__(self, path: Path) -> None:
        """初始化并造一个初始提交（1 个 commit + 1 个分支）。

        Args:
            path: 裸仓库目录。
        """
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._git("init", "--bare", "--initial-branch=main", ".")
        self._git("config", "user.email", "e2e@officeai.local")
        self._git("config", "user.name", "E2E")
        # 初始提交：走 plumbing，避免依赖工作区与 checkout
        blob = self._git_stdin("hash-object", "-w", "--stdin", stdin="演示仓库\n").strip()
        tree = self._git_stdin("mktree", stdin=f"100644 blob {blob}\tREADME.md\n").strip()
        commit = self._git_stdin("commit-tree", tree, "-m", "初始提交", stdin=None).strip()
        self._git("update-ref", "refs/heads/main", commit)

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        """执行 git 命令（返回 stdout）。

        Args:
            *args: 子命令与参数。

        Returns:
            str: 标准输出。

        Raises:
            RuntimeError: git 返回非 0。
        """
        completed = subprocess.run(  # noqa: S603 - 参数全部来自本文件常量
            ["git", "-C", str(self.path), *args],  # noqa: S607 - 依赖 PATH 中的 git
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失败：{completed.stderr.strip()}")
        return completed.stdout

    def _git_stdin(self, *args: str, stdin: str | None) -> str:
        """执行需要标准输入的 git 命令。

        Args:
            *args: 子命令与参数。
            stdin: 标准输入内容（None 表示空输入）。

        Returns:
            str: 标准输出。

        Raises:
            RuntimeError: git 返回非 0。
        """
        # 刻意用**字节模式**：文本模式会把 `\n` 翻译成 `\r\n`，
        # 于是 `mktree` 收到 `README.md\r`（对象名里带回车）、blob 内容也多出 `\r`，
        # 结果"提交内容与产物逐字相同"这条判据会以假红的方式暴露出来（Windows 上实测踩过）。
        completed = subprocess.run(  # noqa: S603 - 参数全部来自本文件常量
            ["git", "-C", str(self.path), *args],  # noqa: S607 - 依赖 PATH 中的 git
            capture_output=True,
            input=(stdin or "").encode("utf-8"),
            check=False,
        )
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        if completed.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失败：{stderr}")
        return completed.stdout.decode("utf-8", "replace")

    # ------------------------------------------------------------------
    # 断言用读数
    # ------------------------------------------------------------------
    def commit_count(self) -> int:
        """当前分支的提交数（`git rev-list --count`）。

        Returns:
            int: 提交数。
        """
        return int(self._git("rev-list", "--count", "HEAD").strip())

    def head(self) -> str:
        """当前分支顶端提交。

        Returns:
            str: commit sha。
        """
        return self._git("rev-parse", "HEAD").strip()

    def has_object(self, sha: str) -> bool:
        """判断对象是否存在（`git cat-file -e`）。

        Args:
            sha: 对象 ID。

        Returns:
            bool: 存在返回 True。
        """
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), "cat-file", "-e", sha],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0

    def file_text(self, sha: str, path: str) -> str:
        """读取某个提交里某文件的内容（`git show <sha>:<path>`）。

        Args:
            sha: 提交 ID。
            path: 仓库内路径。

        Returns:
            str: 文件内容。
        """
        return self._git("show", f"{sha}:{path}")

    def commit_file(self, path: str, content: str, message: str) -> str:
        """把一个文件写进**新提交**（父提交为当前 HEAD），并移动分支。

        `mktree` 不接受带斜杠的路径，因此按目录层级自底向上建树
        （`src/components/x.tsx` → 先建 `components` 树，再建 `src` 树）。
        提交里保留原有 `README.md`，与本用例"只新增 1 个文件"的判定一致。

        Args:
            path: 仓库内路径。
            content: 文件内容。
            message: 提交说明。

        Returns:
            str: 新提交 sha。
        """
        base = self.head()
        blob = self._git_stdin("hash-object", "-w", "--stdin", stdin=content).strip()
        parts = [item for item in path.split("/") if item]
        tree = self._git_stdin("mktree", stdin=f"100644 blob {blob}\t{parts[-1]}\n").strip()
        for directory in reversed(parts[1:-1]):
            tree = self._git_stdin("mktree", stdin=f"040000 tree {tree}\t{directory}\n").strip()
        readme = self._git("rev-parse", "HEAD:README.md").strip()
        entries = f"100644 blob {readme}\tREADME.md\n"
        entries += (
            f"100644 blob {blob}\t{parts[0]}\n"
            if len(parts) == 1
            else f"040000 tree {tree}\t{parts[0]}\n"
        )
        root = self._git_stdin("mktree", stdin=entries).strip()
        commit = self._git_stdin("commit-tree", root, "-p", base, "-m", message, stdin=None).strip()
        self._git("update-ref", "refs/heads/main", commit)
        return commit

    def changed_file_count(self, sha: str) -> int:
        """取某次提交改动的文件数（`git show --stat` 的权威口径）。

        Args:
            sha: 提交 ID。

        Returns:
            int: 文件数。
        """
        output = self._git("show", "--pretty=format:", "--name-only", sha)
        return len([line for line in output.splitlines() if line.strip()])


# ===========================================================================
# 桩门面：把「下游生成服务 + 办公工具」换成固定语料
# ===========================================================================
#: 单个步骤的应答实现
StepHandler = Callable[[RunContext, dict[str, Any], "GatewayScript"], ActionResult]


@dataclass
class GatewayScript:
    """一次用例内的桩脚本与调用记录。

    Attributes:
        plugin_id: 目标插件。
        handlers: `step_seq → 应答实现`（未登记的步骤走默认文本应答）。
        failures: `step_seq → 固定失败结果`（`fail_injector` 夹具）。
        calls: 调用流水（`task_id` / `step_seq` / `action` / `tool` / `operation`）。
        counts: `step_seq → 被调次数`（"前序步骤不重跑"的判据）。
        repo: 本地裸仓库（研发工作台的提交落点）。
        published: 发布回执记录（内容运营）。
        schedule_offset_days: 排期表计划时间相对今天的天数（负数 = 过去）。
        screening_blank_risk: 是否让评分表第 2 行风险留空（负样本注入）。
    """

    plugin_id: str = ""
    handlers: dict[int, StepHandler] = field(default_factory=dict)
    failures: dict[int, ActionResult] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    counts: Counter[int] = field(default_factory=Counter)
    repo: BareRepo | None = None
    published: list[dict[str, Any]] = field(default_factory=list)
    schedule_offset_days: int = 3
    screening_blank_risk: bool = False
    offer_amount_negotiable: bool = False

    def fail_at(
        self,
        step_seq: int,
        *,
        error_code: int,
        reason_text: str,
        operator_action: str = "可以重试这一步",
        retryable: bool = False,
    ) -> None:
        """登记一条步骤级失败（只影响该步）。

        Args:
            step_seq: 步骤序号。
            error_code: 业务码（只进内部，不进响应体）。
            reason_text: 失败原因（业务语言）。
            operator_action: 建议动作。
            retryable: 是否可重试。
        """
        self.failures[step_seq] = ActionResult(
            status="failed",
            error_code=error_code,
            reason_text=reason_text,
            operator_action=operator_action,
            retryable=retryable,
        )

    def heal(self) -> None:
        """清掉全部注入失败（模拟"用户按建议动作处理完之后再试一次"）。"""
        self.failures.clear()

    def calls_of(self, task_id: str) -> list[dict[str, Any]]:
        """取某个任务的调用流水。

        Args:
            task_id: 任务 ID。

        Returns:
            list[dict[str, Any]]: 调用记录。
        """
        return [item for item in self.calls if item["task_id"] == task_id]

    def steps_of(self, task_id: str) -> list[int]:
        """取某个任务实际执行过的步骤序号（按发生顺序）。

        Args:
            task_id: 任务 ID。

        Returns:
            list[int]: 步骤序号列表。
        """
        return [int(item["step_seq"]) for item in self.calls_of(task_id)]


class CorpusActionGateway:
    """`ActionGateway` 的 e2e 实现：固定语料 + 真 git 对象 + 调用流水。

    Attributes:
        _script: 桩脚本与调用记录。
    """

    def __init__(self, script: GatewayScript) -> None:
        """初始化。

        Args:
            script: 桩脚本与调用记录。
        """
        self._script = script

    def resolve(self, action: str) -> str:
        """解析原语（与生产实现同一条白名单判据）。

        Args:
            action: 原语名。

        Returns:
            str: 落点标识。

        Raises:
            ValueError: 原语不在白名单内。
        """
        if action not in ACTION_WHITELIST:
            raise ValueError(f"action={action} not in whitelist")
        return f"corpus.{action}"

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """执行一个原语（先记流水，再按脚本应答）。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 执行结果。
        """
        step_seq = int(params.get("step_seq") or ctx.step_seq or 0)
        self._script.calls.append(
            {
                "task_id": ctx.task_id,
                "step_seq": step_seq,
                "action": action,
                "tool": str(params.get("tool_type") or ""),
                "operation": str(params.get("operation") or ""),
            }
        )
        self._script.counts[step_seq] += 1
        injected = self._script.failures.get(step_seq)
        if injected is not None:
            return injected
        handler = self._script.handlers.get(step_seq)
        if handler is not None:
            return handler(ctx, params, self._script)
        return _default_text_result(params)


def _default_text_result(params: dict[str, Any]) -> ActionResult:
    """未登记步骤的默认应答（每个 `produces` 一份文本产物）。

    Args:
        params: 原语参数。

    Returns:
        ActionResult: 成功结果。
    """
    title = str(params.get("title") or "结果")
    text = f"{title}：已按本次输入生成内容。"
    drafts = tuple(
        ArtifactDraft(
            output_key=str(key),
            type="text",
            name=title,
            inline_text=text,
            preview_text=text,
            size_bytes=len(text.encode("utf-8")),
        )
        for key in params.get("produces") or []
    )
    return ActionResult(
        status="ok",
        artifacts=drafts,
        data={draft.output_key: text for draft in drafts},
    )


# ===========================================================================
# 研发工作台（plg_dev_workbench）语料
# ===========================================================================
def _dev_step1(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 1「理解需求」：三点要点表。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    text = (
        "要点,说明\n"
        "导出入口,列表页需要一个导出按钮\n"
        "导出范围,导出当前筛选结果\n"
        "兼容性,不改变现有列表行为"
    )
    return _text_artifacts(params, text, artifact_type="table")


def _dev_step2(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 2「生成技术方案」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    text = (
        "方案：在列表页工具栏新增导出按钮，点击后按当前筛选条件导出；"
        "新增 src/components/export-button.tsx 承载按钮与导出调用，"
        "并补一个用例文件覆盖点击回调。"
    )
    return _text_artifacts(params, text)


def _dev_step3(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 3「生成代码文件」（内容与提交后的仓库文件逐字相同）。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    return _file_artifacts(params, DEV_CODE, name=DEV_CODE_PATH)


def _dev_step4(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 4「生成单元测试」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    return _file_artifacts(params, DEV_TEST_CODE, name="export-button.test.tsx")


def _dev_step5(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 5「提交到指定仓库」：真的写进本地裸仓库，并记一次合并请求。

    Args:
        ctx: 执行上下文。
        params: 原语参数。
        script: 桩脚本。

    Returns:
        ActionResult: 成功结果（`commit` / `pull_request` 两个外链产物）。

    Raises:
        RuntimeError: 未装配本地仓库（用例装配错误，不是业务失败）。
    """
    repo = script.repo
    if repo is None:  # pragma: no cover - 用例装配错误
        raise RuntimeError("e2e 未装配本地裸仓库")
    code = str((params.get("prior") or {}).get("code_files") or "")
    commit = repo.commit_file(DEV_CODE_PATH, code, f"feat: {params.get('title') or '导出按钮'}")
    inputs = params.get("inputs") or {}
    repository = str(inputs.get("repo") or "acme/demo")
    commit_url = f"https://git.example.com/{repository}/commit/{commit}"
    pr_url = f"https://git.example.com/{repository}/pull/1?head={commit}"
    script.published.append({"tool": "github", "head": commit, "base": "main"})
    drafts = (
        ArtifactDraft(
            output_key="commit",
            type="link",
            name="commit",
            inline_text=commit,
            external_url=commit_url,
            preview_text=commit,
            size_bytes=len(commit.encode("utf-8")),
        ),
        ArtifactDraft(
            output_key="pull_request",
            type="link",
            name="pull_request",
            inline_text=pr_url,
            external_url=pr_url,
            preview_text=pr_url,
            size_bytes=len(pr_url.encode("utf-8")),
        ),
    )
    return ActionResult(
        status="ok",
        artifacts=drafts,
        data={
            "tool_execution_id": "tex_e2e_dev_step5",
            "tool_type": "github",
            "operation": "create_pull_request",
            "commit": commit,
            "pull_request": pr_url,
            "result": {"url": commit_url, "head": commit, "base": "main"},
        },
    )


def build_dev_script(repo: BareRepo) -> GatewayScript:
    """装配研发工作台的桩脚本。

    Args:
        repo: 本地裸仓库。

    Returns:
        GatewayScript: 桩脚本。
    """
    return GatewayScript(
        plugin_id=DEV_PLUGIN,
        handlers={
            1: _dev_step1,
            2: _dev_step2,
            3: _dev_step3,
            4: _dev_step4,
            5: _dev_step5,
        },
        repo=repo,
    )


# ===========================================================================
# 内容运营（plg_content_ops）语料
# ===========================================================================
def _content_step1(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 1「检索素材」：三条带来源的要点。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    text = (
        "要点,来源\n季度新品已上线,企业知识库/产品手册\n"
        "客诉集中在安装步骤,企业知识库/客服周报\n"
        "同类内容平均打开率 12%,企业知识库/运营月报"
    )
    return _text_artifacts(params, text, artifact_type="table")


def _content_step2(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 2「选题策划」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    text = (
        "选题,标题\n安装避坑,三分钟装机指南\n新品亮点,新品上手第一课\n客服答疑,装机常见问题一次讲清"
    )
    return _text_artifacts(params, text, artifact_type="table")


def _content_step3(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 3「生成稿件」：三个平台各一篇（平台互不相同）。

    Args:
        ctx: 执行上下文。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del script
    channels = [str(item) for item in (params.get("inputs") or {}).get("channels") or []]
    body = [
        f"【{channel}】新品安装避坑指南" + "。" + "安装前后照着做，问题会少很多。" * 12
        for channel in channels[:3]
    ]
    return _text_artifacts(params, "\n\n".join(body))


def _content_step4(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 4「配图建议」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    text = "配图建议：主图用产品正面实拍，次级图用安装步骤截图，风格保持浅色留白。"
    return _text_artifacts(params, text)


def _content_step5(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 5「安排发布排期」：用**真渲染器**出 4 列 CSV。

    排期表不是"随便拼字符串"：这里调用平台自己的 `ArtifactSanitizer.render_file`
    产出带 BOM 的 CSV，引擎的排期护栏随后会把这份 CSV 解析回来校验时间——
    因此本桩同时验证了"渲染 → 落库 → 护栏解析"这条真实链路。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（提供计划时间偏移与平台清单）。

    Returns:
        ActionResult: 成功结果（`schedule` 文件产物）。
    """
    del ctx
    channels = [str(item) for item in (params.get("inputs") or {}).get("channels") or []]
    rows = schedule_rows(channels[:3], offset_days=script.schedule_offset_days)
    blob = ArtifactSanitizer().render_file(
        render_format="csv",
        name="schedule.csv",
        payload={"title": "发布排期表", "columns": SCHEDULE_COLUMNS},
        rows=rows,
    )
    csv_text = blob.content.decode("utf-8")
    draft = ArtifactDraft(
        output_key="schedule",
        type="file",
        name="schedule.csv",
        # `inline_text` 存**完整 CSV 文本**：CSV 本身就是文本产物，
        # 这样即便二进制列不落库，下载到的仍是那张表而不是几行预览。
        inline_text=csv_text,
        storage_key="e2e/schedule.csv",
        content=blob.content,
        preview_text=csv_text[:500],
        size_bytes=blob.size_bytes,
    )
    return ActionResult(
        status="ok",
        artifacts=(draft,),
        data={"schedule": csv_text, "rows": rows},
    )


def _content_step6(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 6「发布」：已接入平台给回执，未接入平台如实标「待人工发布」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（记录发布回执）。

    Returns:
        ActionResult: 成功结果（`receipt` 文本产物）。
    """
    del ctx
    inputs = params.get("inputs") or {}
    channels = [str(item) for item in inputs.get("channels") or []][:3]
    lines = ["平台,状态,回执号"]
    for channel in channels:
        if channel == "微信公众号":
            lines.append(f"{channel},已发布,{STUB_RECEIPT_ID}")
            script.published.append({"channel": channel, "receipt_id": STUB_RECEIPT_ID})
        else:
            # 未接入的平台**不伪造回执**（PRD：不会伪造发布成功的回执）
            lines.append(f"{channel},待人工发布,")
    text = "\n".join(lines)
    draft = ArtifactDraft(
        output_key="receipt",
        type="text",
        name="receipt",
        inline_text=text,
        preview_text=text,
        size_bytes=len(text.encode("utf-8")),
    )
    return ActionResult(
        status="ok",
        artifacts=(draft,),
        data={
            "receipt": text,
            "tool_execution_id": "tex_e2e_content_step6",
            "tool_type": str(params.get("tool_type") or "wechat_mp"),
            "operation": str(params.get("operation") or "publish_article"),
        },
    )


def build_content_script(*, offset_days: int = 3) -> GatewayScript:
    """装配内容运营的桩脚本。

    Args:
        offset_days: 排期计划时间相对今天的天数（负数表示过去）。

    Returns:
        GatewayScript: 桩脚本。
    """
    return GatewayScript(
        plugin_id=CONTENT_PLUGIN,
        handlers={
            1: _content_step1,
            2: _content_step2,
            3: _content_step3,
            4: _content_step4,
            5: _content_step5,
            6: _content_step6,
        },
        schedule_offset_days=offset_days,
    )


def schedule_rows(channels: list[str], *, offset_days: int = 3) -> list[dict[str, str]]:
    """构造排期表行（列顺序与 `SCHEDULE_COLUMNS` 一致）。

    Args:
        channels: 平台清单（取前三）。
        offset_days: 计划时间相对今天的天数。

    Returns:
        list[dict[str, str]]: 排期表行。
    """
    from datetime import datetime, timedelta

    base = datetime.now().replace(second=0, microsecond=0) + timedelta(days=offset_days)
    rows: list[dict[str, str]] = []
    for index, channel in enumerate(channels):
        planned = base + timedelta(days=index)
        rows.append(
            {
                "平台": channel,
                "标题": f"{channel}发布稿",
                "计划时间": planned.strftime("%Y-%m-%d %H:%M"),
                "状态": "已排期",
            }
        )
    return rows


# ===========================================================================
# 行政人事（plg_hr_admin）语料
# ===========================================================================
#: 三位候选人（评分表的固定语料）
CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "姓名": "张伟",
        "匹配分": "88",
        "亮点": "五年同岗位经验；负责过同类项目交付",
        "风险": "上一段任职时间较短，需要确认稳定性",
        "结论": "建议面试",
        "理由": "岗位核心职责全部覆盖，且有过完整项目交付经历，风险点可在面试中确认。",
    },
    {
        "姓名": "李娜",
        "匹配分": "72",
        "亮点": "技术栈匹配；有跨部门协作经验",
        "风险": "缺少团队管理经历，需要确认带人意愿",
        "结论": "待定",
        "理由": "技术能力达标但管理经验不足，是否进入下一轮取决于团队当前的带人需求。",
    },
    {
        "姓名": "王强",
        "匹配分": "66",
        "亮点": "沟通表达清晰；学习意愿强",
        "风险": "核心技能有缺口，需要三到六个月补齐",
        "结论": "建议面试",
        "理由": "基础能力达标且学习意愿强，技能缺口可在入职后补齐，值得当面聊一次。",
    },
)


def _hr_step1(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 1「生成招聘说明」。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    inputs = params.get("inputs") or {}
    text = (
        f"岗位：{inputs.get('position') or '前端工程师'}\n"
        f"部门：{inputs.get('department') or '技术部'}\n"
        "职责：负责产品前端开发与交付；参与需求评审与技术方案设计。\n"
        "要求：具备同岗位经验，能独立承担模块交付。"
    )
    return _text_artifacts(params, text)


def _hr_step2(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 2「简历筛选初评」：用真渲染器出 6 列评分表。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（`screening_blank_risk` 用于注入"风险为空"负样本）。

    Returns:
        ActionResult: 成功结果（`screening` 文件产物）。
    """
    del ctx
    rows = [{key: row[key] for key in SCREENING_COLUMNS} for row in CANDIDATES]
    if script.screening_blank_risk:
        rows[1] = {**rows[1], "风险": ""}
    blob = ArtifactSanitizer().render_file(
        render_format="csv",
        name="screening.csv",
        payload={"title": "筛选评分表", "columns": SCREENING_COLUMNS},
        rows=rows,
    )
    csv_text = blob.content.decode("utf-8")
    draft = ArtifactDraft(
        output_key="screening",
        type="file",
        name="screening.csv",
        inline_text=csv_text,
        storage_key="e2e/screening.csv",
        content=blob.content,
        preview_text=csv_text[:500],
        size_bytes=blob.size_bytes,
    )
    return ActionResult(status="ok", artifacts=(draft,), data={"screening": csv_text, "rows": rows})


def _hr_step3(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 3「生成面试提纲」：每人 5 问，每题带考察点与参考追问。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx, script
    blocks: list[str] = []
    for candidate in CANDIDATES:
        if candidate["结论"] != "建议面试":
            continue
        lines = [f"### {candidate['姓名']}"]
        for index in range(1, 6):
            lines.append(f"{index}. 请举例说明你在同类项目里的第 {index} 项关键做法")
            lines.append(f"   考察点：岗位要求的第 {index} 项能力是否具备")
            lines.append("   参考追问：如果当时资源只有一半，你会怎么调整？")
        blocks.append("\n".join(lines))
    return _text_artifacts(params, "\n\n".join(blocks))


def _hr_step4(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 4「安排面试邀约」：返回固定日程回执。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本。

    Returns:
        ActionResult: 成功结果（`invitation` 文本产物）。
    """
    del ctx
    text = f"邀约已发出，日程编号 {STUB_EVENT_ID}，时间以邮件正文为准。"
    script.published.append({"tool": "email", "event_id": STUB_EVENT_ID})
    draft = ArtifactDraft(
        output_key="invitation",
        type="text",
        name="invitation",
        inline_text=text,
        preview_text=text,
        size_bytes=len(text.encode("utf-8")),
    )
    return ActionResult(
        status="ok",
        artifacts=(draft,),
        data={
            "invitation": text,
            "tool_execution_id": "tex_e2e_hr_step4",
            "tool_type": str(params.get("tool_type") or "email"),
            "operation": str(params.get("operation") or "send_message"),
        },
    )


def _hr_step5(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 5「生成录用通知文案」：**三个金额字段留空**，由人事手填。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果。
    """
    del ctx
    inputs = params.get("inputs") or {}
    salary = "面议" if script.offer_amount_negotiable else ""
    text = (
        f"录用通知\n岗位：{inputs.get('position') or '前端工程师'}\n"
        f"薪资：{salary}\n试用期薪资：\n年终奖：\n"
        "以上金额一栏由人事按公司标准填写后发放。"
    )
    return _text_artifacts(params, text)


def _hr_step6(ctx: RunContext, params: dict[str, Any], script: GatewayScript) -> ActionResult:
    """步骤 6「撰写行政公文」：用真渲染器出 docx。

    Args:
        ctx: 执行上下文（未使用）。
        params: 原语参数。
        script: 桩脚本（未使用）。

    Returns:
        ActionResult: 成功结果（`document` 文件产物）。
    """
    del ctx, script
    inputs = params.get("inputs") or {}
    doc_type = str(inputs.get("doc_type") or "通知")
    body = "一、事项说明\n二、执行要求\n三、反馈时间\n请各部门按要求执行。"
    blob = ArtifactSanitizer().render_file(
        render_format="docx",
        name="document",
        payload={"title": doc_type, "content": body},
        rows=[],
    )
    draft = ArtifactDraft(
        output_key="document",
        type="file",
        name="document.docx",
        inline_text=body,
        storage_key=f"e2e/{doc_type}.docx",
        content=blob.content,
        preview_text=body,
        size_bytes=blob.size_bytes,
    )
    return ActionResult(status="ok", artifacts=(draft,), data={"document": blob.filename})


def build_hr_script(*, blank_risk: bool = False, negotiable: bool = False) -> GatewayScript:
    """装配行政人事的桩脚本。

    Args:
        blank_risk: 是否注入"评分表风险为空"的负样本。
        negotiable: 是否让录用通知的薪资写成「面议」（合规负样本）。

    Returns:
        GatewayScript: 桩脚本。
    """
    return GatewayScript(
        plugin_id=HR_PLUGIN,
        handlers={
            1: _hr_step1,
            2: _hr_step2,
            3: _hr_step3,
            4: _hr_step4,
            5: _hr_step5,
            6: _hr_step6,
        },
        screening_blank_risk=blank_risk,
        offer_amount_negotiable=negotiable,
    )


# ===========================================================================
# 产物草稿助手
# ===========================================================================
def _text_artifacts(
    params: dict[str, Any], text: str, *, artifact_type: str = "text"
) -> ActionResult:
    """按 `produces` 生成文本类产物草稿。

    Args:
        params: 原语参数。
        text: 文本内容。
        artifact_type: 产物类型（`text` / `table`）。

    Returns:
        ActionResult: 成功结果。
    """
    drafts = tuple(
        ArtifactDraft(
            output_key=str(key),
            type=artifact_type,
            # 产物名用 ASCII：中文名会让下载代理的 `Content-Disposition` 崩在 latin-1
            # （GAP-B26-03，已实测），与本用例要验的业务判定无关。
            name=str(key),
            inline_text=text,
            preview_text=text,
            size_bytes=len(text.encode("utf-8")),
        )
        for key in params.get("produces") or []
    )
    return ActionResult(
        status="ok",
        artifacts=drafts,
        data={draft.output_key: text for draft in drafts},
    )


def _file_artifacts(params: dict[str, Any], text: str, *, name: str) -> ActionResult:
    """按 `produces` 生成文件类产物草稿（内容即 `text`，无二进制）。

    `content` 刻意留空：下载代理会回落到 `inline_text`，因此
    `sha256(下载内容) == sha256(preview_text)` 可以逐字比对。

    Args:
        params: 原语参数。
        text: 文件内容。
        name: 文件名。

    Returns:
        ActionResult: 成功结果。
    """
    drafts = tuple(
        ArtifactDraft(
            output_key=str(key),
            type="file",
            name=name,
            inline_text=text,
            preview_text=text,
            size_bytes=len(text.encode("utf-8")),
        )
        for key in params.get("produces") or []
    )
    return ActionResult(
        status="ok",
        artifacts=drafts,
        data={draft.output_key: text for draft in drafts},
    )


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture(autouse=True)
async def _isolate_singletons() -> AsyncIterator[None]:
    """每个 e2e 用例前后重置进程级单例与数据库引擎。

    e2e 走真库，`app.db.engine` 缓存的是**绑定到某个事件循环**的异步引擎；
    pytest-asyncio 每个用例一个新循环，不释放会得到
    "attached to a different loop"。任务/产物仓储与服务同理
    （根 `conftest` 只重置了一部分）。

    Yields:
        AsyncIterator[None]: 无值。
    """

    def _reset() -> None:
        reset_settings_cache()
        reset_task_service()
        reset_plugin_service()
        reset_plugin_grant_service()
        reset_plugin_review_service()
        reset_task_store()
        reset_artifact_store()

    _reset()
    try:
        yield
    finally:
        _reset()
        await dispose_engine()


@pytest.fixture
def e2e_settings(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """e2e 配置（真库 + 桩下游 + 桩鉴权）。

    通过**环境变量 + 全局取值**装配，而不是给 `create_app(settings)` 传实例：
    产物下载代理（`/files/artifacts/{token}`）与令牌签发都直接调 `get_settings()`，
    只有全局一致，下载链接才验得住。

    Args:
        pg_dsn: 测试库连接串（未提供时用例 skip）。
        monkeypatch: pytest 打补丁器。

    Returns:
        Settings: 配置实例。
    """
    monkeypatch.setenv("OA_ENV", "test")
    monkeypatch.setenv("OA_DEPLOY_MODE", "saas")
    monkeypatch.setenv("OA_DOWNSTREAM_ENABLED", "false")
    monkeypatch.setenv("OA_AUTH_MODE", "stub")
    monkeypatch.setenv("OA_JWT_SECRET", "e2e-secret")
    monkeypatch.setenv("OA_RATELIMIT_ENABLED", "false")
    monkeypatch.setenv("OA_BUSINESS_STORE", "postgres")
    monkeypatch.setenv("OA_DATABASE_URL", pg_dsn)
    monkeypatch.setenv("OA_PLUGIN_RETRY_BACKOFF_MS", "0")
    monkeypatch.setenv("OA_LOG_LEVEL", "WARNING")
    reset_settings_cache()
    settings = get_settings()
    assert settings.business_store == "postgres", "e2e 必须跑真库（QA 计划 §5.3）"
    return settings


@pytest.fixture
def tenant() -> str:
    """本用例专用的租户 ID（唯一，避免与并行/历史数据互扰）。

    Returns:
        str: 租户 ID。
    """
    return f"{TENANT_PREFIX}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
async def _purge_tenant_rows(
    _isolate_singletons: None, e2e_settings: Settings, tenant: str
) -> AsyncIterator[None]:
    """用例结束后清掉本租户落库的行（在用例自己的事件循环里做）。

    为什么必须是异步夹具：`platform_scope` 用的是绑定到当前事件循环的异步引擎，
    在同步夹具里 `asyncio.run` 会拿到另一个循环，得到 "attached to a different loop"。

    Args:
        _isolate_singletons: 保证先建后拆（本夹具要比它早结束）。
        e2e_settings: 配置实例。
        tenant: 租户 ID。

    Yields:
        AsyncIterator[None]: 无值。
    """
    del _isolate_singletons
    yield
    await _purge_tenant(e2e_settings, tenant)


async def _purge_tenant(settings: Settings, tenant_id: str) -> None:
    """清理本次用例落库的行（平台作用域，只删自己的租户）。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
    """
    tables = (
        "plugin_artifact",
        "plugin_task_step",
        "plugin_task",
        "plugin_grant",
        "plugin_review",
        "plugin_capability_policy",
        "plugin_installation",
        "billing_order",
        "billing_subscription",
    )
    async with platform_scope(settings) as uow:
        for table in tables:
            await uow.execute(
                f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                {"tenant_id": tenant_id},
            )


def _token(settings: Settings, *, tenant_id: str, user_id: str, roles: list[str]) -> dict[str, str]:
    """签发测试令牌并组装请求头。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
        user_id: 用户 ID。
        roles: 角色列表（`owner` 含 `team:role:manage`）。

    Returns:
        dict[str, str]: 请求头。
    """
    import jwt

    token = jwt.encode(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": roles,
            "email": f"{user_id}@example.com",
            "aud": settings.jwt_audience,
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


@pytest.fixture
def admin_headers(e2e_settings: Settings, tenant: str) -> dict[str, str]:
    """管理员请求头（`owner` 角色，含 `team:role:manage`）。

    Args:
        e2e_settings: 配置实例。
        tenant: 租户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    return _token(e2e_settings, tenant_id=tenant, user_id="usr_e2e_admin", roles=["owner"])


@pytest.fixture
def member_headers(e2e_settings: Settings, tenant: str) -> dict[str, str]:
    """普通成员请求头。

    Args:
        e2e_settings: 配置实例。
        tenant: 租户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    return _token(e2e_settings, tenant_id=tenant, user_id="usr_e2e_member", roles=["member"])


@pytest.fixture
def bare_repo(tmp_path: Path) -> BareRepo:
    """本地裸仓库（1 个初始提交 + main 分支）。

    Args:
        tmp_path: 用例临时目录。

    Returns:
        BareRepo: 裸仓库句柄。
    """
    return BareRepo(tmp_path / "e2e-repo.git")


@pytest.fixture
def script(bare_repo: BareRepo) -> GatewayScript:
    """桩脚本（默认装配研发工作台语料，用例可按需替换）。

    Args:
        bare_repo: 本地裸仓库。

    Returns:
        GatewayScript: 桩脚本。
    """
    return build_dev_script(bare_repo)


@pytest.fixture(autouse=True)
def use_script(
    script: GatewayScript, monkeypatch: pytest.MonkeyPatch
) -> Callable[[GatewayScript], None]:
    """把「下游生成服务 + 办公工具」换成固定语料（e2e 必需，故 autouse）。

    注入点就是生产用的那一个：`app/api/v1/plugin_deps.build_action_gateway()`。
    注入时机必须**先于** `TaskService` 单例构造——`TaskService.__init__` 会用该函数
    现场造引擎，之后再打补丁对已建好的单例无效，所以每次切换都要 `reset_task_service()`。

    Args:
        script: 默认桩脚本（研发工作台语料）。
        monkeypatch: pytest 打补丁器。

    Returns:
        Callable[[GatewayScript], None]: 切换桩脚本的函数。
    """
    holder: dict[str, GatewayScript] = {"script": script}
    monkeypatch.setattr(
        "app.api.v1.plugin_deps.build_action_gateway",
        lambda settings=None: CorpusActionGateway(holder["script"]),
    )

    def apply(target: GatewayScript) -> None:
        """切换当前用例使用的桩脚本。

        Args:
            target: 桩脚本。
        """
        holder["script"] = target
        reset_task_service()

    reset_task_service()
    return apply


@pytest.fixture
async def client(e2e_settings: Settings) -> AsyncIterator[AsyncClient]:
    """ASGI 测试客户端（与用例同一个事件循环，真库里才能复用连接池）。

    Args:
        e2e_settings: 配置实例。

    Yields:
        AsyncIterator[AsyncClient]: 异步客户端。
    """
    app = create_app(e2e_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


# ===========================================================================
# 业务助手（全部走真实端点 / 真实服务，不直接写库）
# ===========================================================================
async def upgrade_to_paid_plan(settings: Settings, tenant_id: str) -> None:
    """把租户套餐升到含插件市场的档位（免费版才验 15001）。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
    """
    subscriptions = SubscriptionService(settings, get_billing_store(settings))
    current = await subscriptions.current(tenant_id=tenant_id)
    if current.plan_code == "professional":
        # 幂等：同一用例里要给多个插件做前置，重复下单会被 15004（未完成订单）拦住
        return
    await subscriptions.change(
        tenant_id=tenant_id,
        actor_id="usr_e2e_admin",
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )


async def set_org_switches(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    enabled: bool,
    capabilities: tuple[str, ...] = ORG_CAPABILITIES,
) -> None:
    """通过 PL13 打开/关闭企业能力开关（管理员操作，真端点）。

    Args:
        client: 异步客户端。
        headers: 管理员请求头。
        enabled: 目标状态。
        capabilities: 能力键列表。
    """
    response = await client.put(
        "/api/v1/plugins/capability-policy",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"policies": [{"capability_key": key, "enabled": enabled} for key in capabilities]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0, response.text


async def install_plugin(
    client: AsyncClient,
    headers: dict[str, str],
    plugin_id: str,
    *,
    grants: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """通过 PL5 安装插件（默认同意全部声明能力）。

    Args:
        client: 异步客户端。
        headers: 请求头。
        plugin_id: 插件标识。
        grants: 授权意图（缺省取声明里的全部能力）。

    Returns:
        dict[str, Any]: 安装结果数据。
    """
    from app.plugins.loader import get_registry

    manifest = get_registry().get(plugin_id)
    assert manifest is not None, f"声明缺失：{plugin_id}"
    payload = grants or dict.fromkeys(manifest.capability_keys, True)
    response = await client.post(
        f"/api/v1/plugins/{plugin_id}/install",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"grants": payload},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


async def create_task(
    client: AsyncClient,
    headers: dict[str, str],
    plugin_id: str,
    inputs: dict[str, Any],
    *,
    conversation_id: str = "conv_e2e",
) -> dict[str, Any]:
    """通过 A1 创建插件任务。

    Args:
        client: 异步客户端。
        headers: 请求头。
        plugin_id: 插件标识。
        inputs: 业务输入。
        conversation_id: 会话 ID。

    Returns:
        dict[str, Any]: A1 数据。
    """
    response = await client.post(
        "/api/v1/agent/tasks",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "plugin_id": plugin_id,
            "conversation_id": conversation_id,
            "goal": "",
            "inputs": inputs,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


async def task_detail(client: AsyncClient, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    """读 A3 任务详情。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。

    Returns:
        dict[str, Any]: 详情数据。
    """
    response = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


async def task_steps(
    client: AsyncClient, headers: dict[str, str], task_id: str
) -> list[dict[str, Any]]:
    """读 A4 步骤详情。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。

    Returns:
        list[dict[str, Any]]: 步骤列表。
    """
    response = await client.get(f"/api/v1/agent/tasks/{task_id}/steps", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]["steps"]


async def decide(
    client: AsyncClient,
    headers: dict[str, str],
    task_id: str,
    decision: str,
    *,
    from_step: int | None = None,
) -> dict[str, Any]:
    """调 WF1（`continue` / `redo` / `retry_step` / `retry_all`）。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。
        decision: 决策。
        from_step: 重试起点（`retry_step` 必填）。

    Returns:
        dict[str, Any]: WF1 数据。
    """
    payload: dict[str, Any] = {"decision": decision}
    if from_step is not None:
        payload["from_step"] = from_step
    response = await client.post(
        f"/api/v1/agent/tasks/{task_id}/retry",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


async def download(client: AsyncClient, download_url: str | None) -> tuple[int, bytes]:
    """按 A3/A4 给出的下载地址取产物字节（真令牌、真代理）。

    Args:
        client: 异步客户端。
        download_url: 下载地址。

    Returns:
        tuple[int, bytes]: 状态码与内容。
    """
    assert download_url, "产物没有下载地址"
    response = await client.get(download_url)
    return response.status_code, response.content


async def artifacts_by_key(settings: Settings, tenant_id: str, task_id: str) -> dict[str, Any]:
    """直接读 `plugin_artifact` 表，按产物键返回落库产物行。

    **为什么 e2e 从表里读而不是从 A4 读**：QA 计划 §4.8 明确要求结果物用例
    「一律绕过 UI，直接对产物/仓库/文件断言」——`plugin_artifact` 才是交付面
    （`content_sha256` / `inline_text` / `content` / `external_url` 都在这里）。
    另：当前 A4 的 `StepItem` 没有 `artifacts[]` / `failure` 字段（见交付报告的缺口
    清单 GAP-B26-01），改读表也能让本批用例不被那个契约缺口卡死。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
        task_id: 任务 ID。

    Returns:
        dict[str, Any]: `output_key → ArtifactRecord`。
    """
    store = get_artifact_store(settings)
    rows = await store.list_by_task(tenant_id, task_id)
    return {str(row.output_key): row for row in rows}


async def step_records(settings: Settings, tenant_id: str, task_id: str) -> dict[int, Any]:
    """直接读 `plugin_task_step` 表，按步骤序号返回步骤行。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
        task_id: 任务 ID。

    Returns:
        dict[int, Any]: `seq → StepRecord`。
    """
    store = get_task_store(settings)
    rows = await store.list_steps(tenant_id, task_id)
    return {int(row.seq): row for row in rows}


def artifact_download_url(settings: Settings, record: Any) -> str:
    """按产物行签发下载地址（与 `TaskService._download_url` 同一条令牌规则）。

    Args:
        settings: 配置实例。
        record: 产物行。

    Returns:
        str: 下载地址。
    """
    from app.core.artifact_token import make_token

    if record.type == "link" and record.external_url:
        return str(record.external_url)
    token = make_token(
        tenant_id=record.tenant_id,
        artifact_id=record.artifact_id,
        version=record.version,
        settings=settings,
    )
    return f"/files/artifacts/{token}"


def artifact_url_claims(url: str | None, settings: Settings) -> tuple[str, str, int] | None:
    """把下载地址还原成「产物身份」三元组（租户 / 产物 / 版本）。

    为什么不能直接比字符串：S9 起令牌载荷带 `exp`（默认 15 分钟有效），
    同一份产物在**不同秒**签发会得到不同令牌。字符串相等因此变成
    「两次调用是否落在同一秒」的抽奖，而用例真正要钉的是
    「页面上的下载地址指向的就是这份产物」。

    Args:
        url: 下载地址（`/files/artifacts/<token>`）。
        settings: 配置实例（令牌校验用）。

    Returns:
        tuple[str, str, int] | None: `(tenant_id, artifact_id, version)`；
        地址为空、非平台代理路径或令牌无效/已过期时返回 None。
    """
    from app.core.artifact_token import parse_token

    if not url:
        return None
    prefix = "/files/artifacts/"
    if not url.startswith(prefix):
        return None
    claims = parse_token(url[len(prefix) :], settings)
    if claims is None:
        return None
    return claims.tenant_id, claims.artifact_id, claims.version


def assert_download_url_matches(url: str | None, settings: Settings, record: Any) -> None:
    """断言下载地址指向 `record`（按产物身份比对，不按令牌字符串比对）。

    Args:
        url: 响应里的下载地址。
        settings: 配置实例。
        record: 期望的产物行。

    Raises:
        AssertionError: 地址缺失、令牌无效/已过期，或指向了别的产物。
    """
    assert url, "下载地址不得为空"
    if record.type == "link" and record.external_url:
        assert url == record.external_url, f"外链产物必须直接指向 {record.external_url}：{url}"
        return
    claims = artifact_url_claims(url, settings)
    assert claims is not None, f"下载令牌无效或已过期：{url}"
    assert claims == (record.tenant_id, record.artifact_id, record.version), (
        f"下载地址指向的产物不一致：{claims} vs "
        f"{(record.tenant_id, record.artifact_id, record.version)}"
    )


def checklist_identity(checklist: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    """把核查清单里的下载地址规范成产物身份，供「刷新后逐项一致」比对。

    Args:
        checklist: 响应里的 `result.checklist`。
        settings: 配置实例。

    Returns:
        list[dict[str, Any]]: 每项把 `download_url` 换成 `__claims` 键。
    """
    normalized: list[dict[str, Any]] = []
    for item in checklist:
        entry = {key: value for key, value in item.items() if key != "download_url"}
        entry["__claims"] = artifact_url_claims(item.get("download_url"), settings)
        normalized.append(entry)
    return normalized


def artifact_of(steps: list[dict[str, Any]], output_key: str) -> dict[str, Any]:
    """从 A4 步骤数据里按产物键取产物。

    Args:
        steps: 步骤列表。
        output_key: 产物键。

    Returns:
        dict[str, Any]: 产物数据。

    Raises:
        AssertionError: 找不到该产物。
    """
    for step in steps:
        for artifact in step.get("artifacts") or []:
            if artifact.get("output_key") == output_key:
                return artifact
    raise AssertionError(f"没有产出 {output_key}（已有：{_artifact_keys(steps)}）")


def _artifact_keys(steps: list[dict[str, Any]]) -> list[str]:
    """列出步骤里出现的产物键（失败信息用）。

    Args:
        steps: 步骤列表。

    Returns:
        list[str]: 产物键列表。
    """
    keys: list[str] = []
    for step in steps:
        for artifact in step.get("artifacts") or []:
            keys.append(str(artifact.get("output_key")))
    return keys


def checklist_map(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 A3 核查清单转成 `key → 项`。

    Args:
        detail: A3 数据。

    Returns:
        dict[str, dict[str, Any]]: 核查项映射。
    """
    return {str(item["key"]): item for item in (detail.get("result") or {}).get("checklist") or []}


def parse_csv_bytes(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """把 CSV 字节解析成表头与行（容忍 BOM）。

    Args:
        content: CSV 字节。

    Returns:
        tuple[list[str], list[dict[str, str]]]: 表头与行。
    """
    import csv
    import io

    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{str(key): str(value or "") for key, value in row.items()} for row in reader]
    return list(reader.fieldnames or []), rows


__all__: list[str] = [
    "CANDIDATES",
    "CONTENT_PLUGIN",
    "DEV_CODE",
    "DEV_CODE_PATH",
    "DEV_PLUGIN",
    "DEV_TEST_CODE",
    "EXPECTED_PLUGIN_IDS",
    "HR_PLUGIN",
    "ORG_CAPABILITIES",
    "SCHEDULE_COLUMNS",
    "SCREENING_COLUMNS",
    "STUB_EVENT_ID",
    "STUB_RECEIPT_ID",
    "BareRepo",
    "CorpusActionGateway",
    "GatewayScript",
    "artifact_download_url",
    "artifact_of",
    "artifact_url_claims",
    "artifacts_by_key",
    "assert_download_url_matches",
    "build_content_script",
    "build_dev_script",
    "build_hr_script",
    "checklist_identity",
    "checklist_map",
    "create_task",
    "decide",
    "download",
    "install_plugin",
    "parse_csv_bytes",
    "schedule_rows",
    "set_org_switches",
    "sha256_of",
    "step_records",
    "task_detail",
    "task_steps",
    "upgrade_to_paid_plan",
]
