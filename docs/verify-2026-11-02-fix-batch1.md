# 整改批次 1（F1–F8）独立验证报告

- **验证人**：QA 工程师 严过关（Edward）
- **验证时间**：2026-11-02
- **对象**：仓库 `麦子`，分支 `main`，工作区未提交改动 **12 文件**（与工程师声明一致）
- **环境**：`apps/adapter/.venv/Scripts/python.exe`（Python 3.13.14）、`OA_BATCH=B21`、Windows Git Bash
- **原则**：不复述工程师结论，全部用**自有口径**重跑；对每一项主动构造**边界与负样本**

---

## 1. 全量回归

### 命令

```bash
cd /c/Users/15864/Desktop/麦子/apps/adapter
OA_BATCH=B21 .venv/Scripts/python.exe -m pytest tests -q --junitxml=/tmp/qa-regression.xml
```

**退出码 = 1，但退出码不可信。** 日志（`/tmp/qa-regression.log`）只有 35 行，**完全没有任何摘要行**（无
`passed` / `failed` 行），末行是
`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":127,...}`——环境守卫吞掉摘要并改写退出码，
与工程师的说法一致。故以下数字**全部来自 junitxml 解析**，不看退出码。

### 数字（权威口径）

| 指标 | 本次实测 | 审计基线 | 差值 |
|---|---|---|---|
| tests | **2472** | 2465（2222+8+235） | +7（F8 新增 7 条用例） |
| **failures** | **0** | 8 | **−8** |
| **errors** | **0** | 0 | 0 |
| skipped | **235** | 235 | 0 |
| **passed** | **2237** | 2222 | **+15**（= 8 条转绿 + 7 条新增） |

三个数自洽：`2222 + 8 + 7 = 2237`，`2465 + 7 = 2472`。**全绿成立，8 条基线红全部消除。**

### 触达文件单独复核（工程师声明逐条对账）

| 文件 | 用例数 | 结果 | 工程师声明 |
|---|---|---|---|
| `tests/unit/test_job_runner.py` | 15 | 15 passed | 15 passed ✔ |
| `tests/unit/test_auth_startup.py` | 17 | 17 passed | 17 passed ✔ |
| `tests/unit/test_security.py` | 21 | 21 passed | — |
| `tests/security/test_endpoint_permissions.py` | 15 | 15 passed | — |
| 合计 | 68 | 68 passed / 0 fail / 0 err | ✔ |

---

## 2. 八项逐项验证

> 判定：**通过** / **通过但有条件** / **不通过**

| 项 | 判定 | 一句话依据 |
|---|---|---|
| F1 时钟单源 | **通过** | 探针证明 4 处全部走 `self._now()`，且反证了原缺陷机制 |
| F2 依赖白名单 + stale 反向检查 | **通过** | 裸跑 EXIT=0；反向检查可独立判红 |
| F3 baseline 默认值 | **通过** | 裸跑 EXIT=0（基线内 23/外 0/stale 0）；换空基线立刻 EXIT=1 报 23 条 |
| F4 ruff format | **通过** | `--check` EXIT=0，388 files already formatted |
| F5 端点数量文档串 | **通过** | 独立数出 96 装饰器 / 99 OpenAPI 操作 / 81 路径 |
| F6 空权限抛错 | **通过但有条件** | `""`→30004 成立；但空白串归 20003、且 `has_point("")` 仍为 True |
| F7 脱敏 processor | **通过但有条件** | 掩码生效、顺序正确；但后缀例外是「名模式」而非具体字段，`*_prefix`/`*_tail` 原样落盘 |
| F8 启动校验 | **通过（有跟进项）** | 4 场景 + 9 负样本全中；但模板/README 未同步，照文档走会起不来 |

### F1 —— `InMemoryJobStore._stamp()` 单一时间源：**通过**

不读代码下结论，用行为探针证明（探针已删除）。注入 `T0 = 墙钟 − 7h`：

```
[wall_clock] 2026-11-01T18:03:59.676839+00:00
[injected  ] 2026-11-01T11:03:59.676839+00:00
[fields] run_after=2026-11-01T11:03:59.676839Z created_at=...Z updated_at=...Z
[CHECK1 fields == 注入 T0]                 True
[CHECK2 claim（注入时钟落后墙钟 7h）]        n=1   ← 原缺陷核心场景不再恒返 0
[CHECK3 反证：run_after 取墙钟]              n=0   ← 精确复现原缺陷成因
[CHECK4 mark_failed  same_clock]            True
[CHECK5 mark_succeeded same_clock]          True
[CHECK6 run_after 与 updated_at 差值]        0:00:00  ← 不再有固定偏移
```

CHECK3 是这次验证的关键：把 `run_after` 写成墙钟、注入时钟落后 7h，`claim()` 立刻返 0——**这正是原
8 条红的成因**，说明探针真的能打中缺陷，而不是碰巧全绿。

grep 交叉确认：`InMemoryJobStore` 内 `now_iso()` 出现次数 = **0**（仅模块级定义 1 处 + 文档串提及），
`self._now()` / `self._stamp()` 覆盖 `enqueue` / `claim` / `mark_succeeded` / `mark_failed` 全部 4 处。

### F2 —— `validate_deps.py` 白名单 + stale 反向检查：**通过**

```
$ .venv/Scripts/python.exe deploy/validate_deps.py
[INFO] 运行时依赖 16 条     [INFO] 前端依赖 29 条
[OK] 依赖门禁通过           EXIT_DEPS=0
```

反向检查我不是"加个假包看它红"，而是**直接注入集合做负样本**（不落盘、无需还原）：

```
check_whitelist_not_stale({'pyjwt'})                -> 20 条 stale   ← 能红
check_whitelist_not_stale(load_declared_packages()) -> []            ← 真实状态干净
'pyjwt' in load_declared_packages() -> True
'python-jose' in DOCUMENTED_PACKAGES -> False
```

即：正向 + 反向两个方向都真的在工作，`python-jose → pyjwt` 的替换是完整的。

### F3 —— `--baseline` 默认为 `DEFAULT_BASELINE`：**通过**

```
裸跑：      EXIT_IMPORT=0
结论文本：   违规 23 条（基线内 23 / 基线外 0 / stale 0），
             环 3 个（基线内 3 / 新增 0 / stale 0），扫描 203 个文件
```

**单看绿是不够的——必须证明"红的能力还在"**（否则等于把门禁拆了）。负样本：显式喂一个空基线文件：

```
$ validate_import_directions.py --baseline /tmp/empty_baseline.txt
EXIT=1
结论：违规 23 条（基线内 0 / 基线外 23 / stale 0）
[FAIL] 行数 = 23
```

结论：默认基线**恰好**认领了那 23 条已登记豁免，多一条不认、少一条报 stale；违反时仍 exit 1。
门禁活的，且"裸跑 == 带参跑"成立。

### F4 —— ruff format：**通过**（含一次自我纠错，如实记录）

首次运行得到 `4 files would be reformatted, 388 files already formatted`，REAL_EXIT=1。
逐行看 `--check` 明细后确认：**这 4 个未格式化文件是我自己刚写的临时探针**
（`_qa_probe_f1/f6/f7/f8.py`），与工程师改动无关。删除探针后复跑：

```
$ .venv/Scripts/python.exe -m ruff format --check .
REAL_EXIT=0
388 files already formatted
```

**工程师的 388 成立。** 顺带一提：这个数字是**语句后半句**，我第一次被它误导，说明"只引用有利半句"
的汇报习惯有风险——建议以后贴完整 `--check` 退出码。

### F5 —— 端点数量文档串：**通过**

不依赖文档串，自己数：

```
app/api/ 下 @router.{get,post,put,patch,delete} 装饰器 = 96
  get=43 post=27 put=6 patch=8 delete=12
app.openapi()：openapi_operations = 99，openapi_paths = 81
```

`96 端点 / 99 操作` 两个数字**均可独立复现**，且 `main.py` 两处文档串（第 10 行、第 23 行）现在口径一致。
（注：99 > 96 是正常的，同一路径多方法 + 探针端点不会一一对应，不构成矛盾。）

### F6 —— 空权限点抛 `ApiError(30004)`：**通过但有条件**

```
[CONFIG_ERROR] = 30004, [FORBIDDEN] = 20003
[CHECK1 ""]           code=30004  is_config_err=True  is_forbidden=False   ← 达标
[CHECK2 "*" 通配 + ""] code=30004  raised=True                             ← 通配角色不能绕过
[CHECK4 guard_permission("")] code=30004 audit_calls=0                     ← 不写拒绝审计（见 N4）
```

**不过度收紧（正常权限点未被误伤）**：

```
member + knowledge:base:read    -> ALLOWED   （白名单 W-1 延迟判定，符合设计）
member + knowledge:base:delete  -> ALLOWED   （角色矩阵本就含，非本次改动引入）
*      + any:action             -> ALLOWED
owner  + team:role:manage       -> ALLOWED
admin  + team:member:update     -> DENIED 20003  （期望）
```

端点路径未被误伤：`guard_permission` 的 **10 处**调用点（`knowledge_ext` 8 / `tenant_data` 1 /
`tools_ext` 1）全部传**非空常量**；`tenant_data` 那处动态 `permission` 的默认值是
`"team:role:manage"`，两个调用点都没传空。全量回归 0 failure 亦佐证。

**回归面（另有"空权限放行"语义依赖的地方）**——查出 2 处，见 §4 的 N1：

| 位置 | 现状 | 是否需要跟着改 |
|---|---|---|
| `app/services/identity_service.py:119` | `require_permission(auth, request.permission)`，但上游有 schema `min_length=1` + `_validate_permission_point()` 先抛 14004，且整体被 `try/except ApiError → allowed=False` 包住 | 不需改（三重兜底，不会 500） |
| `tests/unit/test_security.py` / `tests/security/test_endpoint_permissions.py` | 2 处旧断言已反转 | 已处理 |

**两个条件（不阻塞，但要登记）**：

1. **空白权限点归错类**：`" "` / `"\t"` 走 `not permission` 为假 → 落到 20003（FORBIDDEN），
   而不是 30004。方向仍是 fail-closed（拒绝），但把"我们的编码错误"记成了"用户无权限"，
   正是本次改动想避免的归因错误。建议判据改 `not permission.strip()`。
2. **判权双出口口径分叉**：见 N1。

### F7 —— 脱敏 processor + 后缀白名单：**通过但有条件**

直接调用 + 走**完整 processors 链**双向验证：

```
[链顺序] merge_contextvars, add_log_level, TimeStamper, StackInfoRenderer,
         ExceptionRenderer, _redact_event_dict, _inject_context, UnicodeDecoder, JSONRenderer
redact 在 inject_context 之前: True   ← 顺序符合设计

{"password":"p","token":"t","nested":{"access_token":"x"}}
  -> {"password":"***","token":"***","nested":{"access_token":"***"}}      ← 顶层+嵌套都掩
{"credential_ref_suffix":"abc123","key_suffix":"zz","token_length":42}
  -> 原样保留                                                              ← 例外按预期生效
{"credential_secret","api_key","jwt_secret","client_secret","app_key","access_key","authorization","refresh_token"}
  -> 全部 "***"                                                            ← 真密钥类未被例外放过
redact 递归：{"password":"p","list":[{"token":"t"},"plain"]} -> {"password":"***","list":[{"token":"***"},"plain"]}
```

**条件（例外范围比"credential_ref_suffix 一个字段"更宽）**：

`_DERIVED_KEY_SUFFIXES` 是 8 个**后缀名模式**，且在 `is_sensitive()` 里**先于**敏感词匹配返回 False。
后果是任何以这些后缀结尾的键都免掩，包括**值的原文前缀/尾部**：

```
{"api_key_prefix":"sk-live-ABCDEF","client_secret_tail":"WXYZ"} -> 原样落盘
is_sensitive("api_key_prefix") -> False
```

当前全仓**没有**这类调用点（grep 只有 `idempotency.py` 的 `key_suffix`，其值是哈希键后 8 位，安全），
所以现状无泄露。但这是一个"名字一撞就漏"的口子。建议：白名单收窄为
`("_suffix", "_hash", "_digest", "_masked", "_present", "_length")`，**去掉 `_prefix` / `_tail`**
（二者当前零调用点，且语义上就是原始密钥的片段）。

### F8 —— `pay_channel=fake` + `jwt_secret` 占位/长度：**通过（附跟进项）**

13 个场景实测（4 个必做 + 9 个边界/负样本），**全部符合预期**：

| # | 场景 | 预期 | 实测 |
|---|---|---|---|
| S1 | production + `pay_channel=fake` | 抛 | **抛 30004** ✔ |
| S2 | development + `pay_channel=fake` | 不抛 | **不抛** ✔（没把开发环境堵死） |
| S3 | production + `jwt_secret=CHANGE_ME_STRONG_PASSWORD` | 抛 | **抛 30004**（理由："命中占位值特征 'change_me'"） ✔ |
| S4 | production + 33 字符随机 + `wechat_pay` | 不抛 | **不抛** ✔ |
| B1 | 恰好 32 字符 | 不抛 | 不抛 ✔（边界正确，`< 32` 而非 `<= 32`） |
| B2 | 31 字符 | 抛 | 抛（"长度 31 < 最低要求 32"） ✔ |
| B3 | 32 字符但含 `password` | 抛 | 抛（占位特征优先于长度） ✔ |
| B4 | staging + fake | 抛 | 抛 ✔（预发同口径） |
| B5 | test + fake + stub | 不抛 | 不抛 ✔ |
| B6 | production + 默认 pay_channel | 不抛 | 不抛 ✔ |
| B7 | 空密钥（原判据不受影响） | 抛 | 抛 ✔ |
| B8 | 全大写 `CHANGEME` | 抛 | 抛 ✔（大小写不敏感） |
| B9 | `PassWord` 混写 | 抛 | 抛 ✔ |

**无误伤其它调用点**：`Settings.pay_channel` 默认值是 `"wechat_pay"`（`config.py:243`），
故新判据不会把"没配 pay_channel 的生产环境"一并打红；B6 已实测。

**额外风险点：全仓短密钥 / 占位密钥扫描（工程师未做这一步，我补做）**

| 位置 | `OA_JWT_SECRET` | 长度 | 与 `OA_ENV` 组合 | 判定 |
|---|---|---|---|---|
| `deploy/.env` | `dev-jwt-secret-for-local-...-32b` | 52 | development | 安全（走 early-return） |
| `deploy/.env.lite` | 48 位 hex | 48 | **production** + `wechat_pay` | **安全**（≥32，无占位特征） |
| `deploy/.env.example:160` | `CHANGE_ME_STRONG_PASSWORD` | 24 | **production** | **现在会启动失败**（见 N3） |
| `deploy/.env.lite.example:119` | `CHANGE_ME_STRONG_PASSWORD` | 24 | **production** | **现在会启动失败**（见 N3） |
| `deploy/install.sh:350` | `openssl rand -hex 24` | 48 | production | 安全（无占位特征，hex 不含 password） |
| `scripts/demo_phase3.sh:161` | `demo-phase3-secret` | 18 | `OA_ENV=test` | 安全（test 走 early-return） |
| 测试夹具 | 见下 | — | — | 已修复 |

**结论：生产/预发的真实配置文件（`.env` / `.env.lite` / `install.sh`）全部合规，没有遗漏未修的短密钥。**
唯一的问题在**模板与文档**（N3），不在运行配置。

`OA_PAY_CHANNEL` 全仓扫描：只有 `.env.example` / `.env.lite` / `.env.lite.example` 三处，**全部是
`wechat_pay`**，无 `production + fake` 的既有组合 → 新判据不会打挂现有部署。

---

## 3. 越界改动审查（逐条）

| # | 越界内容 | 判断 | 理由 |
|---|---|---|---|
| 1 | 反转 2 处旧断言（`test_empty_permission_passes` → `..._raises`、`test_require_permission_ignores_empty_permission` → `..._rejects`） | **必要** | 被反转的旧断言断言的正是审计认定的缺陷行为（"空权限静默放行"）。反转后断言**更严**（30004），不是放宽。若保留旧断言，改与不改必有一半红。 |
| 2 | F7 新增 `_DERIVED_KEY_SUFFIXES` 后缀白名单 | **必要，但范围可收窄** | 不加则 `credential_ref_suffix` 被整段掩成 `***`，违反架构 §11.4"日志保留后缀以便定位"。但工程师顺手放进了 8 个后缀，其中 `_prefix` / `_tail` 会保留密钥原文片段、且全仓零调用点 —— **建议删掉这两个**。 |
| 3 | F8 夹具密钥 `...-32-chars-long` → `...-32-chars-long-v2` | **必要（修夹具，不是改测试意图）** | 实测原值 **31 字符**（`production-secret-32-chars-long`=31；`downgrade-secret-32-chars-long`=30），**名字里的 "32" 本身就是错的**。改后 34/33 字符。断言语义未变（"合法生产配置应当放行"），门槛**更严**（同时满足非空 + ≥32 + 无占位特征），是收紧而非放宽。唯一瑕疵是命名（`-v2` 不如直接改成 `-34-chars-long`），不影响正确性。 |

三条越界**没有一条**是"为了让测试变绿而放宽断言"，方向全部是收紧。可接受。

---

## 4. 新发现的问题

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| **N1** | **中** | **判权两个出口对"空权限"口径分叉**：`require_permission("")` → 30004（fail-loud），但 `PolicyService.has_point("")` → **True**（fail-open）。审计项 P1-11 的意图是"空权限不得静默放行"，目前只修了 1/2 个出口。且 `tests/unit/test_policy_service.py:422` 的 docstring 仍写「**空权限点表示"无需权限"（不少只读端点用它当默认值）**」——**这句话已与事实不符**（全仓 0 处端点传空权限），但它正是错误心智模型的种子，留着会被下一个人照抄。 | `policy_service.py:588-620`；`test_policy_service.py:419-424`（全量回归中该用例仍绿，属遗留语义）；`grep require_point(` 全仓 21 处调用点**全部传非空常量**，故当前无实际泄露路径。 |
| **N2** | 中低 | 空白权限点 `" "` / `"\t"` 被判为 **20003（FORBIDDEN）** 而非 30004，把编码错误归因成用户无权限。fail-closed 没错，但归因反了。 | F6 探针 CHECK1 whitespace/blank-tab 的输出。修法：判据改 `not permission.strip()`。 |
| **N3** | 中低 | **F8 与模板/文档未同步**：`deploy/.env.example:160`、`deploy/.env.lite.example:119` 仍是 `OA_JWT_SECRET=CHANGE_ME_STRONG_PASSWORD`（24 字符），而两文件的 `OA_ENV=production`；`README.md:154` 明确教 `cp .env.example .env`。**改前**：照文档走能起来（但不安全）；**改后**：照文档走直接 30004 起不来。方向正确（宁可起不来），但缺一条指引，新人会卡在启动失败。 | 实测 S3 抛错；`grep OA_ENV= / OA_JWT_SECRET=` 于四个 env 文件；CI（`.github/workflows/*.yml`）**不引用** `.env.example`，故不影响流水线，只影响人工路径。建议：`.env.example` 把该值改为 `<请用 openssl rand -hex 24 生成>`；README 该行补一句"此值必须替换，否则启动报 30004"。 |
| **N4** | 低 | `guard_permission("")` 抛 30004 时**不写"权限被拒"审计**（`guard_permission` 只对 `code == FORBIDDEN` 记账）。语义上可接受（配置错误不是授权拒绝），但线上真出现该 500 时审计里无痕。 | F6 探针 `[CHECK4] code=30004 audit_calls=0`。 |
| **N5** | 低 | `_stamp()` 内 `self._now().astimezone(UTC)`：若注入**朴素 datetime**，`astimezone` 按本机时区解释，会静默偏移 8 小时。因 `claim()` 的 `current` 用同一写法，内部保持一致（不会引发 F1 那类"恒返 0"），但断言绝对时间值的用例会踩坑。建议 `dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt`。 | 代码阅读 + `claim()` 同源写法确认；现有用例均注入 aware 时间，故当前未暴露。 |
| **N6** | 低（既有） | `security.py:355` docstring 写「被 **11 处端点**与 identity_service 共用」，实测 `guard_permission` 端点调用点为 **10 处**。属本次改动之前就存在的文档漂移。 | `grep -c "guard_permission("` = 10 处调用 + 1 处定义。 |

---

## 5. 还原干净性复核（F2/F3 自证）

| 检查 | 方法 | 结果 |
|---|---|---|
| F2 无探针残留 | `git diff deploy/validate_deps.py` 逐行读 | 只有 3 段业务改动（docstring / `python-jose`→`pyjwt` / `load_declared_packages`+`check_whitelist_not_stale`+`main` 挂钩）。**无 `left-pad-stale-probe` 之类残留**。（`left-pad` 出现于 716-725 行是脚本**原有**的内建自检段，不在本次 diff 内。） |
| F3 基线文件未改 | `git status --short deploy/` | 只列出 `M validate_deps.py`、`M validate_import_directions.py`；**`deploy/import_direction_baseline.txt` 不在列表** → 未被改动 ✔ |
| 工作区文件数 | `git status --short` | 仍为 **12 文件**，与验证开始前完全一致；我的 4 个临时探针已全部删除（`ruff format --check` 复跑 EXIT=0 可作旁证） |

**结论：工程师的两处自证已干净还原，工作区无污染。**

---

## 6. 一句话总结论

**这批改动可以进主干。** 全量回归 2472 tests / **0 failures / 0 errors** / 2237 passed / 235 skipped，
8 条基线红全部消除，且每一项都经我独立构造的负样本打中过；3 处越界改动方向全部是收紧，无一放宽。
**无阻塞项**，但建议进主干后立刻做 3 条跟进：**N1**（`has_point("")` 仍 fail-open + 陈旧 docstring，
补平两个判权出口）、**N3**（模板/README 与 F8 同步，否则照文档走会 30004 起不来）、
**F7 白名单收窄**（去掉 `_prefix` / `_tail`）。
