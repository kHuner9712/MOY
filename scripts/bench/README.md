# `scripts/bench/` —— 性能基准脚本

> 对应文档：`docs/architecture-phase4.md` §9.6（性能基线口径）、`docs/perf-disclosure.md`
> （对外披露的取数纪律）。
>
> **一句话先说清本目录的定位**：这里的脚本只负责**测量并落盘**，不负责下结论。
> 「跑出来的数字能不能对外说」由 `docs/perf-disclosure.md` 的三档证据等级（A/B/C）
> 决定，不由脚本决定。

---

## 1. 三个脚本各测什么

| 脚本 | 覆盖场景 | 主要指标 | 对应 §9.6 行 |
| --- | --- | --- | --- |
| `bench_chat.py --mode chat` | S1 对话 | `first_token_ms`（首字）、`first_byte_ms`（首字节）、`full_response_ms`（整段） | 首字 P95 ≤1s |
| `bench_chat.py --mode session` | S5 会话恢复 | `session_restore_ms`（`GET .../messages?limit=30`） | 会话恢复 ≤300ms |
| `bench_kb.py --mode search` | S2 知识库检索 | `search_ms`（R7 独立检索） | 知识库检索 |
| `bench_kb.py --mode parse` | S3 文档解析 | `parse_ms`（R3 上传 → R4 轮询到可检索的**整段墙钟**） | 文档解析耗时 |
| `bench_plugin.py` | S4 插件任务 | `e2e_ms`（建任务 → 终态）、`create_ms`（A1 接口） | 插件任务端到端 |
| `_common.py` | —— | 分位计算、原始样本落盘、披露标签校验 | 被上面三个引用 |

**本目录不测的指标**（各有归属，别在这里找）：

| 指标 | 归属 |
| --- | --- |
| 首屏 LCP | 前端性能采集（Chrome Performance API / 5 次中位数、冷缓存），不在后端脚本里 |
| 起服务时间 ≤180s | `deploy/validate_lite_up.sh` + `scripts/bench/lite-probe/probe.py` |
| 内存峰值 | `scripts/bench/lite-probe/probe.py`（Docker `stats` 口径 + cgroup 上界口径双披露） |
| 记忆召回 ≤300ms | 无独立接口入口，需在**预置 200 条记忆**的环境上对比开关前后的首字延迟；本目录**不虚构一个「记忆召回接口」**来凑指标 |

---

## 2. 用法

### 2.1 前置

```bash
# 脚本用 httpx + asyncio，用适配层的 venv 最省事（依赖与 pyproject 版本约束一致）
PY=apps/adapter/.venv/Scripts/python.exe      # Windows
PY=apps/adapter/.venv/bin/python              # Linux/macOS

# 凭据（也可以用 --token / --base-url 显式传）
export OA_BENCH_BASE_URL=http://127.0.0.1:8000
export OA_BENCH_TOKEN=<Bearer 令牌>
export OA_BENCH_TENANT_ID=default             # 可选；SaaS 模式必填
```

### 2.2 四类测量

```bash
# S1 对话首字（桩上游，100 并发 × 500 次）
$PY scripts/bench/bench_chat.py --mode chat --upstream mock \
  --concurrency 100 --requests 500 --out results/2026-10-30/s1_mock.json

# S1 对话首字（真实上游，必须写明档位与网络）
$PY scripts/bench/bench_chat.py --mode chat --upstream real --profile general \
  --concurrency 10 --requests 100 --label real-general-lan \
  --out results/2026-10-30/s1_real.json

# S5 会话恢复（30 条消息）
$PY scripts/bench/bench_chat.py --mode session --conversation-id conv_01H... \
  --limit 30 --requests 200 --out results/2026-10-30/s5_session.json

# S2 知识库检索
$PY scripts/bench/bench_kb.py --mode search --base-id kb_001 --query "差旅报销标准" \
  --doc-count 120 --concurrency 100 --out results/2026-10-30/s2_search.json

# S3 文档解析（并发 1：解析共享同一处理通道，并发会互相排队，量出的不是单篇耗时）
$PY scripts/bench/bench_kb.py --mode parse --base-id kb_001 --doc-count 20 \
  --concurrency 1 --requests 5 --sample-file ./样例.md --out results/2026-10-30/s3_parse.json

# S4 插件任务端到端
$PY scripts/bench/bench_plugin.py --plugin-id plg_content_ops --inputs ./inputs-content.json \
  --auto-confirm --requests 20 --out results/2026-10-30/s4_plugin.json
```

### 2.3 全部参数

```bash
$PY scripts/bench/bench_chat.py --help
$PY scripts/bench/bench_kb.py --help
$PY scripts/bench/bench_plugin.py --help
```

公共参数（三个脚本都有）：

| 参数 | 说明 |
| --- | --- |
| `--base-url` / `--token` / `--tenant-id` | 目标与凭据；也可用 `OA_BENCH_*` 环境变量 |
| `--concurrency` / `--requests` | 并发数与总请求数 |
| `--out` | 结果 JSON 路径；**缺省**写到 `scripts/bench/out/`（已被 `.gitignore` 忽略） |
| `--timeout` | 单请求超时（默认 120s） |
| `--min-samples` | 成功样本下限，低于即退出 4 |
| `--label` | 本次运行标签（写进 JSON，便于多轮对比） |
| `--trust-env` | **默认关闭**：不读 `HTTP_PROXY`。原因见 §4.2 |

---

## 3. 输出格式与复算

每次运行写一个 JSON（`schema: moy-bench/1`）：

```jsonc
{
  "schema": "moy-bench/1",
  "scenario": "chat",
  "generated_at": "2026-10-30T10:00:00+0800",
  "target": { "base_url": "http://127.0.0.1:8000" },
  "params": { "concurrency": 100, "requests": 500, "timeout_s": 120, "min_samples": 1 },
  "samples": { "total": 500, "success": 498, "error_rate": 0.004 },
  "metrics": {
    "first_token_ms": {
      "stats": { "count": 498, "p50": 120.5, "p95": 480.2, "p99": null, "p99_reliable": false,
                 "min": 88.1, "max": 1200.4, "mean": 180.3 },
      "threshold_ms": 1000.0, "verdict": "pass",
      "error_count": 2, "error_samples": ["ReadTimeout: ..."],
      "raw_samples_ms": [120.5, 98.2, ...]
    }
  },
  "disclosure": { "upstream": "mock", "disclosable": true, "blockers": [], "requirement": "..." },
  "disclaimer": "本机结果不代表生产……",
  "recompute_hint": "分位数可用 raw_samples_ms 复算……"
}
```

**复算是硬要求**（`docs/perf-disclosure.md` §3.2）：客户必须能拿 `raw_samples_ms` 自己算出
同样的 P95。算法统一在 `_common.percentile()`：线性插值，`rank=(n-1)*p/100`，
相邻两点按小数部分加权——与 `numpy.percentile` 默认口径一致，可交叉核对。

```bash
# 用原始样本独立复算 P95，验证与产物里的一致
$PY - <<'PY'
import json, sys
sys.path.insert(0, 'scripts/bench')
import _common
data = json.load(open('results/2026-10-30/s1_mock.json', encoding='utf-8'))
for name, metric in data['metrics'].items():
    raw = metric['raw_samples_ms']
    print(name, 'p95 复算 =', round(_common.percentile(raw, 95), 2), '产物记录 =', metric['stats']['p95'])
PY
```

**`p99` 可能是 `null`**：成功样本 < 100 时不算 p99 并标 `p99_reliable: false`。
50 个样本算出来的 p99 就是最大值本身，标出来比藏起来诚实。

---

## 4. 三条纪律（脚本里已工程化，别绕过）

### 4.1 没有数据就不产出文件

| 情形 | 退出码 | 行为 |
| --- | --- | --- |
| 正常完成，无失败请求 | 0 | 写 JSON |
| 跑完但有失败请求 | 1 | 写 JSON（`error_rate > 0`，`error_samples` 带前 5 条） |
| 用法错误 / 缺 httpx | 2 | 打印原因，不写文件 |
| **缺凭据 / 服务不可达 / 目标资源不存在** | **3** | 打印 `BLOCKED: 未执行`，**不写任何文件** |
| **成功样本为 0，或低于 `--min-samples`** | **4** | 打印「数据不足」，**不写任何文件** |

**为什么坚持不写半份数据**：一个 `"p95": 0.0` 的占位文件会被下游当成实测值抄进对客文档。
「没测」必须长得像「没测」——这是本项目 R2-QA-02 的教训（缺凭据不许静默跳过）。

CI 里可以直接用退出码判断：

```bash
$PY scripts/bench/bench_chat.py ... --out results/$(date +%F)/s1_mock.json
case $? in
  0) echo "有数据，可入披露流程" ;;
  1) echo "有失败请求，先看 error_samples，不得直接披露" ;;
  3|4) echo "未执行/数据不足：**不要**把这条写进披露表" ;;
esac
```

### 4.2 默认不走环境代理

`--trust-env` 默认关闭，即**不读** `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。
理由：基准测试若悄悄走了代理，量到的是**代理的耗时**，不是被测服务的。这种数字比没数据更危险。
目标确实需要代理时显式加 `--trust-env`，并在报告里写明「经代理」。

> 这不是理论问题：在带 `http_proxy` 的机器上，httpx 默认走代理，对 `127.0.0.1` 的请求会
> 绕一圈甚至挂住。本脚本开发时就踩到过一次。

### 4.3 上游类型必须标注

`--upstream mock|real|unspecified`（`bench_chat.py`）：

- `mock`：桩上游，结果里 `disclosable: true`，但披露时必须**逐字**写「不含上游模型延迟」；
- `real`：必须同时写明档位与网络环境；
- `unspecified`：`disclosable: false`，`blockers` 里写明原因——**不允许直接对外披露**。

这条做进产物文件，而不是靠写报告的人记得。

---

## 5. 本机结果不代表生产

**所有输出的 `disclaimer` 字段都写着这句话，不是客套。** 至少以下四项在本机一定与生产不同：

1. **数据量**：检索延迟随文档数增长而劣化，`--doc-count` 必须记录且贴近目标环境；
2. **并发模型**：本脚本的并发是「N 个在途请求」，不是真实用户的思考时间分布；
   100 并发连续打 5 分钟，与被 100 个人零散使用，压力曲线完全不同；
3. **网络与 TLS**：同机回环没有任何网络延迟，跨机房 + TLS 终止会增加几十毫秒；
4. **上游**：mock 组完全不含模型推理时间，真实上游的 P95 由供应商决定，我们只能测量不能承诺。

**因此**：对外披露前必须在**目标环境**复测，并按 `docs/perf-disclosure.md` §3.3
记录硬件 / 软件 / 编排 / 数据库 / 网络 / 上游六项。缺任一项按 C 级（待压测）处理，
不可引用。

---

## 6. 与 `lite-probe/` 的关系

`scripts/bench/lite-probe/` 是**容器层探针**（冷启动、就绪顺序、内存峰值），
测的是「起得来、占多少内存」；本目录三个脚本测的是**接口层延迟**。
两者产物都属 A 级证据，但**不可互相替代**：探针测不出检索 P95，本目录测不出内存峰值。

`lite-probe/` 不是本目录的一部分，改动互不影响。
