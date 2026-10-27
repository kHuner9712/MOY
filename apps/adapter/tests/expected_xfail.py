"""阶段三 xfail 预期数（**唯一真源**，供骨架与批次门禁共用）。

为什么单独成模块：
1. `tests/integration/test_plugin_contract_skeleton.py`（骨架）与批次门禁脚本需要**同一个**
   预期值，写在两个地方必然漂移；
2. 数值必须是**显式常量**而非由用例列表长度推导——否则「删一条用例 + 删一条登记」
   会让两个数一起降、汇总断言仍绿，漂移被掩盖；
3. 每批应下降多少，由 `EXPECTED_XFAIL_BY_BATCH` 表达，验收报告直接引它，
   不必靠人回忆「这批该转几条」。

维护规则（**人工，禁止自动推导**）：
- 某条延后用例在批次 N 转绿后：把骨架里该条的 `landed_in` 填成 `"BN"`，
  同时把 `EXPECTED_XFAIL` 减 1、`EXPECTED_XFAIL_BY_BATCH["BN"]` 减 1；
- 阶段终态：`EXPECTED_XFAIL == 0`。

里程碑口径（2026-10-23 定稿；PL14 挪 B18 后校正）：
**B16 = 6 → B17 结束 3 → B18 结束 2 → B19 结束 0**。
"""

from __future__ import annotations

from typing import Final

#: 当前仍应处于 `xfail` 的条数（正常模式门禁：`xfailed` 必须等于它）
EXPECTED_XFAIL: Final[int] = 0

#: 各批次剩余的 `xfail` 条数：本批结束时，本批归属项必须归 0
EXPECTED_XFAIL_BY_BATCH: Final[dict[str, int]] = {}

#: 当前 `status=red`（已实现但红着）的条数——**缺陷不得沉淀为背景红**
EXPECTED_RED: Final[int] = 0

#: 「未闭口**用例**」总量 = xfail + red。
#: **注意与缺陷台账的 `status="open"` 不是一个轴**：
#: 本常量数的是断言层（骨架用例），台账数的是缺陷层。
#: 命名带 `CASES` 就是为了不再被当成同一个数。**单调不增**，批次结束时必须与实测相等。
#:
#: 为什么单列：若只盯 `xfailed`，把 `xfail` 改成 `red`（断言写了但红）就能让
#: `xfailed` 从 3 降到 0 而**一条能力都没多**——这是"把红变黄"的变体：
#: 改状态标记而不改实现。把两个桶之和纳入同一计数，状态迁移可以，但总量不许涨。
EXPECTED_OPEN_CASES: Final[int] = 0

OPEN_CASES_HISTORY: Final[tuple[int, ...]] = (6, 6, 2, 0)  # B16=6、B17=6、B18=2、B19=0

#: `red` 条数的历史序列（append-only）。**必须单调不增**——否则「把 xfail 改成 red」
#: 会让 red 从 3 涨到 6、同时 xfailed 归 0，总量看似没变而进度也没变（洗标记）。
#: B16 结束 0、B17 结束 3（B17 三条缺陷）、B18 结束 0（已随 B18 全部关闭）
RED_HISTORY: Final[tuple[int, ...]] = (0, 3, 0)

#: 缺陷台账（append-only）。`red` 条目只能用这里 `status="open"` 的编号——
#: 防「编造编号」与「3 条红共用一个号」两种伪造（骨架汇总 ⑭⑮）。
#:
#: 语义边界（易混，写清楚）：
#: - `red`：**已被骨架断言钉住、且当前是红的**缺陷；
#: - `open`：**尚未关闭**的缺陷（可能由骨架钉，也可能由别的用例钉，如 g11）。
#: 因此 ⑮ 用**子集**（red ⊆ open），不是双向相等——否则会把「g11 钉住的迁移缺陷」
#: 硬塞进骨架，扩大骨架职责。
DEFECT_LEDGER: Final[tuple[dict[str, str], ...]] = (
    # B17 的两条 P1（已随 B18 关闭）
    {"id": "P3-B17-01", "level": "P1", "status": "fixed"},
    {"id": "P3-B17-02", "level": "P1", "status": "fixed"},
    # B18 缺陷（等级按方案 §7.1「后果面」定：迁移不可重复 = P0，门禁假红 = P1）
    {"id": "P3-B18-01", "level": "P0", "status": "fixed"},  # 0003 只对账 → b5dbd69 幂等建表
    {"id": "P3-B18-02", "level": "P0", "status": "fixed"},  # checksum drift → 同源 b5dbd69
    {"id": "P3-B18-03", "level": "P1", "status": "fixed"},  # G-13/17002 → c340e70 visibility 豁免
    # 新增号（勿与 B18-02 混用）：改名遗留的 schema_migration 孤儿行。
    # 影响面（按代码事实，勿夸大）：`build_plan()` 只遍历磁盘脚本，孤儿行不进
    # pending/drifted/checked → **不影响 `--apply`**；30004 的真因是 checksum 漂移
    # （P3-B18-02，环境态）。本条只是版本表残留旧名，属数据卫生/可观测性 → P2。
    {
        "id": "P3-B18-04",
        "level": "P2",
        "status": "fixed",  # dd28d13：orphans/aliased 可见化 + --prune-orphans + 0002 别名映射
        "pinned_by": "test_real_postgres_migration_is_idempotent",
    },
    # B19：任务推进从未执行（任务永远 running 且不报错），由骨架外的单测抓到 → 已修
    {"id": "P3-B19-01", "level": "P0", "status": "fixed"},  # ae69a4a（test_task_flow 抓到）
    # B19：0004 迁移守卫块标签不匹配（DO $guard$ vs END $$）→ 已修
    {"id": "P3-B19-02", "level": "P0", "status": "fixed"},  # b75f0ab（空库 apply 直接语法失败）
    # 同一文件的第二个缺陷：0004 无条件 ALTER 在已初始化库必失败（权限模型，非语法）→ 三分支
    {"id": "P3-B19-03", "level": "P1", "status": "fixed"},  # d75aa3a（init 脚本补列）
    # B19 漏网 P0：create_task() 的 PG INSERT 列数/值数不匹配 + 引用不存在的 queue_position 列。
    # **HEAD 仍在**（25 列/24 值）；工作区已改（23/23）但**未提交**。
    # 闭合条件必须是「已提交 + PG create_task 用例走通」，不能只看工作区。
    {
        "id": "P3-B19-04",
        "level": "P0",
        "status": "fixed",  # 9a85de6 修；改坏即红已验证（删一列→报不匹配→20 红）
        "pinned_by": "SQL↔DDL 静态门禁 + PG create_task 用例",
        "due_batch": "B21",
        "due_action": "①修复已提交②PG create_task 走通③改坏即红（工作区不算）",
    },
    # B20 两条挂账（到期 B21，到期即红）
    # 升级 P1→P0：缺口落在核心路径（task 47.81% / artifact 60.67% / plugin 67.80%），
    # 且未覆盖是**整段方法**（586-612 / 645-656 / 685-708 …）而非异常分支
    # → 按 §7.1 后果面「生产实现路径未被验」定 P0。**P1 可带出批次，P0 不能。**
    {
        "id": "P3-B20-01",
        "level": "P0",
        # 关闭依据（`d20d749` 导出树，coverage json 平账核对）：
        # covered_branches 2176 / num_branches 2840 = 76.62%，门槛 ceil(0.766×2840)=2176，**余量 0**
        # 注：曾误用 num_partial_branches（436，为"部分分支未走的行数"）当 missing，
        #     算出 2404/84.65%/余量+228 —— 已作废；covered+missing=2840 平账可自证。
        # 余量 0 属已知脆弱点：观察项 due 阶段四首周，动作=分支余量补到 ≥50 条。
        "status": "fixed",
        "pinned_by": "G-05 覆盖率（business+db）",
        "due_batch": "B21",
        "due_action": "补三 PG repo 语句与分支至终裁四档（总88/biz+db86/分支76.6/核心85+60）",
    },
    {
        "id": "P3-B20-02",
        "level": "P1",
        # 6f81495：guardrails **44 个用例** passed / **34 个样本**在库
        # （42 应有 − 8 缺：⑦3 ⑧1 ②2 ⑥2）；**用例数与样本数单位不可混用**
        "status": "fixed",
        "pinned_by": "TC-P3-HR23-02 / 追溯表 §3.4 ⑨ 组",
        "due_batch": "B21",
        "due_action": "补 R3 样本 5 条（3 面议类必判失败 + 2 合法值通过）并实测",
    },
    # 阶段三收口新增（2026-10-23）：错误码收口时翻出的两个真缺陷。
    # 与 B17-B20 那批不同——这两条不是"测试没写"，是**功能缺口本身**。
    {
        "id": "P3-B22-01",
        "level": "P1",
        # PRD 5.3「额度用尽（软限制，不停服）」要求四项行为：
        # ① 对话限长 ② 文档处理暂停 ③ 自动化任务排队不启动 ④ 通知管理员。
        # 实测 `over_quota_warning` 只被计费域（BL/U4）消费，
        # M1/M2/R3/A1 四条路径**零接入**——超额租户可无限跑文档与自动化。
        # 处置：15006 已接线为响应 `data` 的非阻断提示字段（见 errors.py 注册项），
        # 四项行为本身按 PRD 属阶段四范围，本批只记录不实现。
        "status": "open",
        "pinned_by": "PRD 5.3 额度用尽软限制 / 15006 接线缺口",
        # 阶段四首批（阶段四批次表尚未拆分，编号沿用 B 序列；首批暂记为 B30）。
        # **必须写批次号**：门禁的过期判定已改为格式校验 + 数值比较，
        # 写"阶段四"这类非批次号会被直接判红（字符串比较会让它永久逃检）。
        "due_batch": "B30",
        "due_action": "补 PRD 5.3 四项行为（对话限长/文档暂停/自动化排队/管理员通知），"
        "15006 提示字段已随错误码收口落地",
    },
    {
        "id": "P3-B22-02",
        "level": "P2",
        # PRD P0-P3-02 ③ 要求「单步执行超时自动失败」。
        # **本条是"登记时属实、一天后即与代码相反"的典型，故写清时间线**：
        # - 38ed5d3（2026-10-22，阶段三收口）登记时确实成立：
        #   `plugin_task_timeout_seconds` 全文零读取，步骤超时只走底座动作超时
        #   →「TC-P3-02③ 无实现可测」；
        # - 2c85efb（2026-10-23，T4-04.4）落地单步超时，747280c（P1-9 三拆）把它
        #   迁进 facade/runner —— 台账未回填，从那天起账上说"无实现"，代码里有实现。
        # 关闭依据（三处 file:line，逐条可核）：
        # ① `app/business/task/facade.py:127-130`：`step_timeout_seconds` 缺省取
        #    `conf.plugin_task_timeout_seconds` —— 该配置项**已不再零读取**；
        # ② `app/business/task/runner.py:477-521` `_run_step_with_timeout()`：
        #    每步 `asyncio.wait_for(..., timeout=float(deps.step_timeout_seconds))`，
        #    `TimeoutError` → `STEP_FAILED` + 原因「这一步执行超过 N 秒，已停止等待」，
        #    已落库产物不回滚；唯一调用点 `runner.py:434`（**按步**限时，非整任务）；
        # ③ `tests/unit/test_task_step_lock.py:82-140` `TestStepTimeout` 两条用例：
        #    `test_slow_step_times_out_quickly`（elapsed < 3.0 + STEP_FAILED + 文案含
        #    「超过」「停止等待」）、`test_timeout_keeps_previously_saved_artifacts`。
        # 注意行为边界（免得被读成"整任务也有超时"）：限时是**每步**的，
        # 整任务外层没有 `wait_for`——这正是 PRD 要的语义（8 步任务共用一个总时限
        # 等于每步都没限）。
        "status": "fixed",
        "pinned_by": "test_task_step_lock.py::TestStepTimeout（单步超时两条用例）",
        # 保留 due_batch：与同族 P3-B22-04（fixed + due B30）同形，留作"曾欠账到 B30"
        # 的审计痕迹。到期判定只对 `status == "open"` 生效（骨架汇总 ⑯），
        # 故本行不参与过期检查。
        "due_batch": "B30",
        "due_action": "接上任务级超时（读 plugin_task_timeout_seconds 并让引擎消费），"
        "或明确注销该配置项并在 PRD 标注不做",
    },
    {
        "id": "P3-B22-03",
        "level": "P1",
        # 产物二进制内容未持久化：`ArtifactRecord.content` 的注释写明
        # 「仅内存后端保留，PG 后端为 b""」。下载端点回落 `inline_text`，
        # 于是 PG（私有化）模式下导出公文得到的是**文本预览字节，不是 Word 文件**。
        # 验收报告「4 类公文可导出真 docx（可解压 word/document.xml）」只在内存模式成立。
        # 属典型「内存全绿、生产全废」，与 P3-B19-04 同族。
        "status": "fixed",
        # 关闭依据：0005 迁移补 `content BYTEA`（三段式 + to_regclass 守卫），
        # PostgresArtifactStore 的 INSERT/UPDATE/_to_artifact 三路接上真实字节；
        # 实测 PG 后端下载回真 zip（37241B、magic=PK、17 个部件含 word/document.xml、
        # sha256 与库内一致）。判据落点：test_artifact_content_pg.py（9 条，真库）
        # + test_artifact_content_column.py（13 条静态防回归）。
        "pinned_by": "test_artifact_content_pg.py / test_artifact_content_column.py",
        "due_batch": "B21",
        "due_action": "plugin_artifact 增二进制内容列（BYTEA，迁移 + init-schema 双更新），"
        "仓储读写与下载端点改用真实字节；或用对象存储并把 content_type/长度登记进库",
    },
    {
        "id": "P3-B22-04",
        "level": "P2",
        # `plugin_artifact` **没有 content_type 列**，`_to_artifact` 恒回落默认值
        # `text/plain; charset=utf-8`，于是下载 docx 时浏览器可能按文本渲染
        # （`Content-Disposition: attachment` 未设、`filename*` 已正确）。
        # 影响面：内存后端与 PG 后端**一致**，不是本次改动引入。
        "status": "fixed",
        # 关闭依据（阶段四 T4-01.5）：
        # ① 迁移 `0006_plugin_artifact_content_type.sql` 加 `content_type VARCHAR(128) NULL`
        #    （三段式守卫：init 建的新库零 DDL / 空库补列 / 无权限给可照做的报错）；
        #    两份编排的 `init-schema-phase3.sql` 已直接建列，新装库无需 DDL；
        # ② 写入三处归一化真实 MIME：PG `add_artifacts` INSERT、PG `save_version` UPDATE、
        #    内存 `add_artifacts`/`save_version`，以及读取侧 `_to_artifact` 的老库回落，
        #    统一走 `artifact_repo.resolve_content_type()`（扩展名 > 类型 > 声明 > 兜底）；
        # ③ `docx` → application/vnd.openxmlformats-officedocument.wordprocessingml.document，
        #    `md` → text/markdown; charset=utf-8，`csv` → text/csv; charset=utf-8。
        # 判据落点：`tests/integration/test_artifact_content_pg.py`（真库，含 MIME 断言）。
        "pinned_by": "test_artifact_content_pg.py / 0006 迁移",
        "due_batch": "B30",
        "due_action": "加 content_type TEXT 列并在渲染/写入三处赋值；下载端点按实际类型返回",
    },
    {
        "id": "P3-B22-05",
        "level": "P1",
        # PRD 第 260 行（P0-P3-21）原文：「每份输出 匹配分 / 亮点 ≥2 / 风险 ≥1 /
        # 结论 / 理由 ≥30 字。**「风险」为空判该行失败**」；验收标准①再次强调「风险」非空。
        # 实测 `grep -rn "风险" app/plugins/` 在引擎与人事声明里 **0 命中**——
        # 护栏只覆盖了 Offer 金额与排期时间，没有"风险为空则该行判失败重跑"。
        # 后果：模型返回空风险时该行仍算通过，评分表看起来齐全。
        "status": "fixed",
        # 关闭依据（5c11690 + f661ff7）：引擎第四道护栏 `_guard_screening`——
        # 缺列 / 某行空 / 零行放行 / 别名容忍（风险|risk|risk 点）；
        # **f661ff7 是自证阶段发现的漏判修正**：真实链路产出的是 markdown 表格
        # （模型 data.content 原样落 inline_text），只按 CSV 解析会"整行一列"→
        # 缺列不触发、逐行判空也判不出 → 每次筛选都假绿。现同时认 CSV 与 markdown。
        # 判据落点：tests/unit/test_guard_screening.py（23 条，含 7 处改坏即红自证）
        # + e2e test_blank_risk_row_fails_the_screening_step（产品级自证）。
        "pinned_by": "tests/unit/test_guard_screening.py + e2e 空风险用例",
        "due_batch": "B21",
        "due_action": "已落地：引擎补行级质量门，空风险该步 failed 且 retryable=True",
    },
    {
        "id": "P3-B22-06",
        "level": "P2",
        # PRD 第 261 行验收标准②：「第 21 份 → 「一次最多 20 份，请分批上传」」。
        # 实测 `grep -rn "20 份\|max_resumes\|resume_limit" app/` 0 命中——
        # 上传不限量，第 21 份会被照常处理，用户拿不到该提示。
        # 另：PRD 要求 pdf 简历解析，本版无 PDF 解析能力（仅 docx），
        # 属"能力缺口"而非"判据缺失"，一并记录以便阶段四排期。
        "status": "fixed",
        # 关闭依据（c707774）：声明式 `InputSpec.max_items`（加性可选，仅 file/multi_select）
        # + `preflight()` 数量校验（列表/元组计数，单字符串按 1 份不误拒），
        # 人事声明的 `resumes` 加一行 `"max_items": 20`。
        # 主理人独立复验：1/20 份通过、21/25 份 20001、单字符串不误拒（消息逐字见 PRD）。
        # **仍未覆盖的部分单列**：PDF 解析能力（本版仅 docx）属能力缺口，不在本条。
        "pinned_by": "tests/unit/test_guard_screening.py（max_items 组）/ engine.preflight",
        "due_batch": "B21",
        "due_action": "已落地：>20 份拒绝并给「一次最多 20 份，请分批上传」；PDF 解析另立能力缺口",
    },
)


#: 当前批次是**运行时上下文**，不是跨批事实——所以用函数读环境变量，**不写常量**：
#: 写成常量会和 `EXPECTED_XFAIL` 一样漂移，而「默认成最新批」更糟：
#: 最新批的 `due_batch` 最宽松，一切都会绿 → 该红的不红，P2 永久挂账。
#: 因此**缺失即报错**（KeyError → 红），默认值必须指向"报错"而不是"放行"。
def current_batch() -> str:
    """读取当前批次（`OA_BATCH`，如 `B19`）。

    Returns:
        str: 批次号。

    Raises:
        KeyError: 未设置 `OA_BATCH` —— **故意不设默认值**，缺失即红。
    """
    import os

    try:
        return os.environ["OA_BATCH"]
    except KeyError as exc:  # pragma: no cover - 由断言路径覆盖
        raise RuntimeError(
            "未设置 OA_BATCH：请设置当前批次，例如 `OA_BATCH=B19`（用于缺陷到期检查）"
        ) from exc


__all__: list[str] = [
    "DEFECT_LEDGER",
    "EXPECTED_OPEN_CASES",
    "EXPECTED_RED",
    "EXPECTED_XFAIL",
    "EXPECTED_XFAIL_BY_BATCH",
    "OPEN_CASES_HISTORY",
    "RED_HISTORY",
    "current_batch",
]
