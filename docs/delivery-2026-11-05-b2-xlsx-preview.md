# 交付说明：xlsx 成果物 + 离线 Office 预览（B2，2026-11-05）

对应 [gap-matrix-2026-11-03.md](file:///c:/Users/15864/Desktop/麦子/docs/gap-matrix-2026-11-03.md) §3.1 的准入基线第 1 项（B2）。
实施计划：[plan-b2-xlsx-office-preview.md](file:///c:/Users/15864/Desktop/麦子/.trae/documents/plan-b2-xlsx-office-preview.md)。

---

## 1. 交付内容

| 能力 | 行为 | 默认开关 |
| --- | --- | --- |
| xlsx 成果物 | 插件 `render_file(format="xlsx")` 生成真 OOXML 工作簿（表头加粗、列宽自适应）；`openpyxl` 缺失时**如实降级 CSV** | 无开关（依赖镜像内已安装） |
| 表格产物导出 Excel | `GET /files/artifacts/{token}/export?format=xlsx`，把 CSV 内容转真工作簿，**不新增版本、不改任务状态** | `OA_ARTIFACT_XLSX_ENABLED=true`（默认开） |
| 文件产物下载 MIME 修复 | docx/xlsx 下载响应的 `Content-Type` 是真实 MIME，不再恒为 `text/plain` | 无开关（存量 bug 修复） |
| 离线预览 | `GET /files/artifacts/{token}/preview`，用镜像内 LibreOffice headless 把 docx/xlsx（或表格产物先转 xlsx）转 PDF，`inline` 返回 | `OA_OFFICE_PREVIEW_ENABLED=false`（默认关） |
| 前端入口 | 表格产物显示「下载 Excel」；`preview_url` 存在时显示「预览」（新窗口打开 PDF）；编辑弹窗格式下拉多出「Excel 表格」 | 跟随后端字段 |

**明确不支持**：pptx 预览（镜像刻意不装 `libreoffice-impress`，按矩阵默认决策降级为"仅下载"）；
Office 人机双写/协同编辑（见 gap-matrix §5 不做清单）。

---

## 2. 接口与数据模型变更（只增不改）

| 变更 | 位置 | 兼容性 |
| --- | --- | --- |
| `StepArtifact` 新增 `content_type` / `xlsx_url` / `preview_url` | `app/schemas/task.py` | 均带默认值（空串/`null`），旧客户端忽略即可 |
| `GET /files/artifacts/{token}/export` | `app/api/v1/artifact_files.py` | 新增子路由；`/files/` 不在 `/api/v1`，端点计数门禁不受影响 |
| `GET /files/artifacts/{token}/preview` | 同上 | 同上 |
| `StepSpec.render_format` / `ArtifactEditRequest.render_format` 枚举加 `xlsx` | `app/plugins/schema.py`、`app/schemas/task.py` | 加性枚举值 |
| `ArtifactDraft.content_type` 新增字段 | `app/plugins/actions.py` | 有默认值，既有构造点不受影响 |
| 仓储 MIME 解析优先级改为 **扩展名 > 声明（白名单）> 类型 > 兜底** | `app/db/repositories/artifact_repo.py` | 声明为 xlsx 的表格产物不再被 `type=table` 覆盖为 CSV |

**DB 迁移：无。** `plugin_artifact.content_type` 列已存在（`init-schema-phase3.sql`），本轮只把真实 MIME 真正喂进链路。

---

## 3. 验证边界（**必读，不许把 CI 绿当功能已验证**）

| 环节 | 验证程度 | 说明 |
| --- | --- | --- |
| xlsx 生成 / 导出 / MIME 修复 | ✅ 已覆盖 | 单测 + 接口测试断言真工作簿部件（`[Content_Types].xml`、`xl/workbook.xml`）、MIME 落库、响应头 |
| 预览端点编排（参数/超时/`%PDF-` 校验/缓存/并发闸门） | ✅ 已覆盖 | `tests/unit/test_office_preview.py`，**注入替身 runner，不起真进程** |
| 预览响应头（含"不含 sandbox"负向断言） | ✅ 已覆盖 | `tests/integration/test_artifact_preview_headers.py` |
| **LibreOffice 真实转换效果（排版、字体、中文是否方框）** | ❌ **未验证** | 本机无 `soffice`、CI 无 Docker；**仅 mock/接口层验证，真机效果未验证，需部署环境验收** |
| 镜像构建期冒烟（装没装成功） | ⚠️ 构建时验证 | `apps/adapter/Dockerfile` 在 runtime 阶段真实转换 docx/xlsx 并校验 `%PDF-`；**本次未在本机执行 docker build** |

结论：**代码链路可用性已由测试锁定；LibreOffice 转换的真实效果必须按第 5 节在部署环境验收后，才能对外称"预览可用"。**

---

## 4. 回退方式（出事 1 分钟内可退回）

| 现象 | 处置 |
| --- | --- |
| 预览报 5xx / PDF 排版异常 | `.env` 置 `OA_OFFICE_PREVIEW_ENABLED=false` 并重启 adapter → 端点返回 503，前端不再出现「预览」按钮 |
| 导出 xlsx 异常 | 置 `OA_ARTIFACT_XLSX_ENABLED=false` → 端点 404，前端不出现「下载 Excel」 |
| 预览 PDF 被网关拦住 | 检查 nginx 是否仍在 `location ^~ /files/artifacts/`：预览依赖正则 `location ~ ^/files/artifacts/[^/]+/preview$`，`^~` 会跳过正则 |

> 两个开关都是**只影响新增入口**，不回滚既有下载链路。

---

## 5. 部署后 5 分钟手工验收（真机效果唯一判据）

前置：镜像已重建（含 LibreOffice），`.env` 设 `OA_OFFICE_PREVIEW_ENABLED=true`，重启 adapter 与 nginx。

### 5.1 容器内自检（约 30 秒）

```bash
docker compose exec adapter soffice --version                    # 打印 LibreOffice 版本
docker compose exec adapter fc-list | grep -i "Noto Sans CJK"   # 至少一行中文字体
```

两条都必须有输出；任一为空即"镜像/字体没装好"，不必继续。

### 5.2 xlsx 导出（约 2 分钟）

1. 在 Web 端执行**内容运营**插件任务（`plg_content_ops`），跑到「排期表」步骤。
2. 产物卡片上点「下载 Excel」。
3. 预期：得到 `.xlsx` 文件，Excel / WPS 能直接打开，**列名与中文内容正确**（不是 CSV 改名、不是打不开）。

### 5.3 Office 预览（约 2 分钟）

1. 执行**行政人事**插件任务（`plg_hr_admin`）的「撰写行政公文」步骤，得到 docx 产物。
2. 产物卡片点「预览」。
3. 预期：浏览器新窗口**内嵌打开 PDF**（不是下载、不是白屏），**中文正常显示**（不是方框/乱码）。

### 5.4 响应头核对（约 30 秒）

对任意预览链接执行（把 `{token}` 换成实际令牌）：

```bash
curl -sI "https://<站点>/files/artifacts/{token}/preview" | grep -Ei \
  "content-type|content-disposition|x-content-type-options|x-frame-options|content-security-policy"
```

必须同时满足：

- `Content-Type: application/pdf`
- `Content-Disposition: inline`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Content-Security-Policy: frame-ancestors 'self'`，且**整行不含 `sandbox`**

若出现 `sandbox` 或 `X-Frame-Options: DENY`，是 nginx 配置回退到下载块，按第 4 节检查。

### 5.5 判定

| 结果 | 结论 |
| --- | --- |
| 5.1–5.4 全部符合 | 预览功能验收通过，可对外说明"docx/xlsx 可在线预览" |
| 5.1 通过、5.3 中文为方框 | 字体未生效（检查 `fonts-noto-cjk`），预览"可用但中文异常"，按缺陷处理 |
| 5.3 白屏/下载而非内嵌 | 响应头被网关改回 `sandbox`/`DENY`，按 5.4 定位 |
| 5.1 无输出 | 镜像未装 LibreOffice，不可开启预览开关 |

---

## 6. 残余风险与取舍

| 项 | 说明 |
| --- | --- |
| 镜像体积 | 预计 +350–500MB（writer + calc + 中文字体）；未装 impress 已是最小集 |
| 预览性能 | 首次转换含冷启动（数秒级），默认超时 30s、并发 2、进程内缓存 300s/32 条；多副本各算各的 |
| 安全头差异 | 预览**必须**无 `sandbox`，下载**必须**有 `sandbox`；两条链路语义不同，测试已双向锁定，禁止"统一" |
| 真实排版保真度 | LibreOffice 与 Microsoft Office 存在字体替换/分页差异，属转换器固有边界；验收只保证"可读、中文正常" |
| 超大文件 | 预览输入 >20MB 直接 413，引导下载后查看 |
