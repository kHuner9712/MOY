# QA 独立验证脚本（W1 第一批）

本目录存放 QA（严过关 / Yan）的**独立验证脚本**：自建请求与造数，不复用工程师
用例，也不进 pytest 套件（文件名不带 `test_` 前缀，默认收集规则不会命中；
`pyproject.toml` 的 `python_files` 未做任何放宽）。

## 文件

| 文件 | 口径 | 验证对象 |
| --- | --- | --- |
| `qa_w1_v1_verify.py` | 内存 | `GET /api/v1/stats/tasks`（7d/30d 分子分母手工复算、空租户、未登录、参数边界）与 `GET /api/v1/capabilities/recommend`（六类目标命中、易混词、空 goal、参数边界、无命中兜底、工具卡） |
| `qa_w1_v1_pg_verify.py` | PostgreSQL | 同一批造数灌入 `PostgresTaskStore` 与 `InMemoryTaskStore`：仓储层 PG 聚合 SQL == 内存实现 == QA 手工复算期望；端点层 `business_store=postgres` 起真实应用打统计端点（AC-A7 双后端一致）。结束清理 QA 库自建租户数据 |

判据来源：`docs/prd-w1-metrics-entry.md` v1.1（R1-R4、AC-A1~A3、AC-B1~B5）、
`docs/architecture-w1-metrics-entry.md` §3/§7。

## 跑法

内存口径（无需数据库）：

```bash
cd apps/adapter
./.venv/Scripts/python.exe tests/qa/qa_w1_v1_verify.py
```

PG 口径（需要可达的 PostgreSQL，测试库、非客户库）：

```bash
cd apps/adapter
OA_TEST_DATABASE_URL=<PG DSN> ./.venv/Scripts/python.exe tests/qa/qa_w1_v1_pg_verify.py
```

两个脚本都以退出码汇报结果：`0` 全部通过，非 0 存在失败项（PG 脚本缺
`OA_TEST_DATABASE_URL` 时返回 2，绝不假绿）。明细看 stdout 的 `[PASS]/[FAIL]`
行与末尾汇总。

## 注意

- 脚本会 **reset 进程级单例并直写存储造数**，不要与 pytest 套件混跑。
- PG 脚本只清理自己造的租户数据（`tnt_qa_pg_*`），不动其他租户的行。
