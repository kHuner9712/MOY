# B5 气隙离线包交付验收（3A 构建 + 3B 目标机加载/升级/回滚）

> **文档性质**：交付验收清单与判定标准。适用于第一次在目标机执行完整离线链路。
> **对应能力**：能力差距矩阵 #10「部署形态（气隙私有化）」/ 准入基线 B5。
> **实现脚本**：`deploy/validate_offline_media.py`（3A 构建/校验）、
> `deploy/validate_offline_apply.py`（3B 预检/加载/升级/回滚）。
> **详细操作**：`docs/offline-package.md` §9–§11。

---

## 0. 必须先读：验收前提

**3A/3B 的 CI 通过只代表逻辑与状态机可复现，不代表真机链路已验证。** 当前事实：

| 事实 | 状态 |
| --- | --- |
| 3A 真实搬运（skopeo/crane 拉取 19 个镜像、导出 OCI 归档） | **尚未执行**（首次真机生成尚未执行） |
| 3A 真实 cosign 签名与验签 | **尚未执行**（CI 用假签名器） |
| 3B 真实 `skopeo copy` 到 Docker | **尚未执行**（CI 用 FakeExecutor） |
| 3B 真实 `psql` 执行迁移/down 脚本 | **尚未执行**（CI 用 FakeExecutor） |
| 3B 真实插件导入器 | **尚未执行**（CI 用 FakeExecutor） |
| 3B 真实磁盘余量判定 | **尚未执行**（CI 用注入的 `free_bytes`） |
| `install.sh --offline` 安装分支 | **未接入**，V1.0 不提供一键离线安装 |

> **纪律**：在本文件的「必须真机执行」栏全部勾选并有证据前，**不得对外称「离线交付已验证」**，
> 只能称「离线构建/加载逻辑已实现，真机验收进行中」。CI 绿、单测绿、自检绿都不是真机证据。

---

## 1. 验收对象与边界

### 1.1 纳入验收

| 环节 | 入口 | 证据形式 |
| --- | --- | --- |
| 介质生成 | `python deploy/validate_offline_media.py build ...` | 构建日志 + `MANIFEST.json` + `SHA256SUMS` |
| 完整性校验 | `python deploy/validate_offline_media.py verify <pkg>` | 退出码 + `[OK] 校验通过：N 个条目 / X MiB` |
| 目标机预检 | `python deploy/validate_offline_apply.py preflight <pkg>` | 退出码 + 输出 |
| 目标机加载 | `... load <pkg> --database-url ...` | 退出码 + `state.json` + Docker/DB/插件核对 |
| 功能冒烟 | 部署手册 §4.2 三条检查 | 命令输出/截图 |
| 升级 | `... upgrade <pkg-new>` | 退出码 + `state.json` 的 `previous`/版本 |
| 回滚 | `... rollback` | 退出码 + `state.json` + 版本回退 |
| 回滚后再升级 | `... upgrade <pkg-new>`又一次 | 退出码 + 版本 = 新版本 |

### 1.2 不纳入验收

- `install.sh --offline` 一键安装（未接入；验收时用 3B CLI 逐步执行）；
- 硬件容量/性能 SLA（见矩阵 B7，单独压测）；
- K8s 形态、HA/多副本（lite compose 单机形态为前提）；
- 客户定制模型/插件的行为正确性（只验收“包内插件能被导入”，不验收业务效果）。

---

## 2. 判定标准

| 结论 | 判定条件（全部满足） | 可否对外表述 |
| --- | --- | --- |
| **通过** | ① 第 3 节 CI 清单全部勾选且证据在案；② 第 4 节真机清单全部勾选；③ 无不通过项 | 可称「B5 离线链路已验收」 |
| **有条件通过** | ① CI 清单全绿；② 真机核心项（生成/校验/预检/加载/冒烟）全过；③ 升级、回滚、回滚后升级、真实插件导入、真实 cosign 中**至少 1 项未执行**，已登记责任人与补验期限 | **只能称「核心链路通过，边界项待补验」**；不得称已验证 |
| **不通过** | 命中任一：校验失败仍继续加载 / 篡改或换包被接受 / 重复 load 产生重复建表或重复插件 / 崩溃重入全量重来 / 非法升级被允许 / 回滚中途失败却返回 0 / 真机 psql 失败 / 冒烟失败 | 停止上线，按 `docs/offline-package.md` §10 处置 |

补充口径：

- 任一条「必须真机执行」项**没有证据**（只有口头说明）视为未执行；
- 回滚演练必须在试点开始前完成（见 `docs/pilot-plan-2026-11-07.md` §4），未演练 = 有条件通过上限；
- 「有条件通过」的补验项超过约定期限未闭环，结论降为**不通过**。

---

## 3. 验收清单 A：CI 已覆盖（可在提交 SHA 上复跑）

> 复跑方式：`python deploy/validate_offline_apply.py self-test` 与
> `pytest apps/adapter/tests/unit/test_offline_apply.py -q`（或直接看 CI Adapter 对应 step）。
> 证据填：CI run 编号 + 步骤名 + 本地复跑退出码。

- [ ] **A1 预检拒绝**：磁盘不足、缺 `skopeo`/`cosign`/`psql`/插件导入器、架构不符均 `exit 1`，不写状态。
- [ ] **A2 写前 fail-closed**：哈希篡改、签名缺失、`MANIFEST` 与 `SHA256SUMS` 不一致均拒绝，不执行任何包内 SQL。
- [ ] **A3 加载顺序**：镜像 → wheels → SQL 升序 → 插件，全部只取 `MANIFEST` 登记项。
- [ ] **A4 幂等**：同一介质重复 `load` 不产生新命令（0 次 skopeo/pip/psql/loader）。
- [ ] **A5 崩溃重入**：加载中途失败记 `partial`，重跑只执行剩余步骤，不重搬镜像。
- [ ] **A6 版本兼容矩阵**：同大版本最多跨 1 个次版本；跨大版本/跳 2 个/降级/同版本拒绝。
- [ ] **A7 升级只跑新增迁移**：同名旧迁移不重跑，新迁移按清单执行。
- [ ] **A8 升级失败保 `previous`**：中途失败后可立即 `rollback`。
- [ ] **A9 回滚顺序**：down SQL 文件名逆序 → 旧镜像 → 插件移除/恢复。
- [ ] **A10 回滚中途失败**：`exit 3`、`status=rollback_failed`、`completed/pending` 落盘、不报成功。
- [ ] **A11 回滚安全停点**：当前介质丢失/校验失败时不执行未校验 SQL，给人工介入指令。
- [ ] **A12 CLI 自检**：`self-test` 退出码 0，输出「自检通过」。

> 以上 A1–A12 都**不证明真机可用**：命令执行层是 `FakeExecutor`，介质是假归档。

---

## 4. 验收清单 B：必须真机执行（每条都要命令输出/截图/文件路径）

### B0 前置

- [ ] **B0.1 构建机就绪**：`skopeo`、`cosign`、`syft`（或 crane）、`sha256sum`、Node/npm、Python 可用。
- [ ] **B0.2 目标机就绪**：`x86_64`、Docker 24+、Compose v2.24+、`psql`、插件导入器。
- [ ] **B0.3 介质双版本**：至少准备相邻两版介质（如 `1.0.0` 与 `1.1.0`），否则升级/回滚只能判“有条件通过”。
- [ ] **B0.4 数据库角色**：迁移用属主/迁移角色 DSN；运行期应用角色不授 DDL。

### B1 生成与校验（构建机）

- [ ] **B1.1 生成**：`build` 退出码 0，产出 `dist/offline/moy-offline-v<version>-x86_64/`。
- [ ] **B1.2 结构**：`images/ wheels/ web/ sql/{migrations,init,rollback}/ plugins/ sbom/ metadata/` 齐全，`MANIFEST.json` schema 为 `moy-offline-manifest/v1`。
- [ ] **B1.3 体积**：`df`/`du` 实测与 `MANIFEST.totals.bytes` 偏差 ≤5%。
- [ ] **B1.4 签名**：`SHA256SUMS.sig` 由真实 cosign 生成，公钥 `COSIGN_PUBLIC_KEY` 已交付客户。
- [ ] **B1.5 校验**：`validate_offline_media.py verify <pkg>` 退出码 0，输出 `[OK] 校验通过`。
- [ ] **B1.6 篡改演练**：复制介质改 1 字节，`verify` 必须非 0 并指出具体条目（演练后销毁该副本）。

### B2 目标机预检与加载

- [ ] **B2.1 预检**：`preflight` 退出码 0，输出 `[OK] 预检通过：<pkg>`；磁盘余量 ≥ `totals.bytes + max(20%, 512 MiB)`。
- [ ] **B2.2 状态初始**：加载前 `state.json` 不存在（或 `status=never_loaded`）。
- [ ] **B2.3 加载**：`load` 退出码 0，输出 `[OK] 已加载 <version>：<pkg>`，`state.json` `status=loaded`。
- [ ] **B2.4 镜像实核**：`docker images` 能看到包内 ref/tag；`docker compose ... ps` 服务数符合部署手册。
- [ ] **B2.5 数据库实核**：迁移表/关键表存在；迁移执行顺序与 `MANIFEST` 一致（`psql` 查迁移记录或表结构）。
- [ ] **B2.6 插件实核**：插件导入器 `install` 成功；平台插件列表出现包内插件（不验收业务效果）。
- [ ] **B2.7 幂等实核**：再跑一次 `load`，退出 0 且 `[OK] ... 无需重复执行`，无新容器/新表/重复插件。
- [ ] **B2.8 崩溃重入实核**：在**可丢弃的测试库**中人为中断（例如停库/杀进程）后重跑，只补剩余步骤，最终 `loaded`。

### B3 功能冒烟（目标机）

- [ ] **B3.1** `curl -s http://127.0.0.1/healthz` → `ok`。
- [ ] **B3.2** `curl -s http://127.0.0.1/api/v1/readyz` → `ready`。
- [ ] **B3.3** 登录 → 发一句对话 → 有回答且不出现内部组件名。
- [ ] **B3.4** 上传 1 份 PDF 提问 → 回答带引用出处。
- [ ] **B3.5** 连接 1 个工具账号 → 显示“已连接”。

### B4 升级 / 回滚 / 回滚后再升级

- [ ] **B4.1 升级**：`upgrade <pkg-new>` 退出码 0，输出 `[OK] 已从 <old> 升级到 <new>`，`state.json` 有 `previous`。
- [ ] **B4.2 升级后冒烟**：B3.1–B3.5 全部重跑通过。
- [ ] **B4.3 回滚**：`rollback` 退出码 0，输出 `[OK] 已回滚到 <old>`，`status=rolled_back`、`previous=null`。
- [ ] **B4.4 回滚后冒烟**：B3.1–B3.5 通过，数据未丢失（抽查关键业务记录）。
- [ ] **B4.5 回滚后再升级**：再次 `upgrade <pkg-new>` 退出码 0，版本回新，冒烟通过。
- [ ] **B4.6 中途失败演练**（测试环境）：人为让 down SQL 失败，确认 `exit 3`、状态落盘、按人工介入后可续跑（结果记入试点演练模板）。

> B4.6 若在试点生产窗口执行，必须在**维护窗口 + 已备份**前提下进行；否则只在演练环境执行并登记。

---

## 5. 首次真机全流程：脚本化步骤

> 约定：`<PKG_OLD>` = 旧版本介质目录，`<PKG_NEW>` = 新版本介质目录，
> `<STATE>` = `/var/lib/moy-offline`（需可写），`<DSN>` = 迁移角色连接串（不要写进脚本/日志）。
> 每一步执行后先看**退出码**，再看输出；退出码非 0 立即停止，不得继续下一步。

### S1 生成介质（构建机）

```bash
python deploy/validate_offline_media.py build --version 1.0.0 --arch x86_64 --images-dist <镜像来源>
```

- **预期**：退出码 `0`；输出介质目录与 `MANIFEST.json`；`totals.bytes` 非 0。
- **失败判定**：缺工具/拉取失败/导出失败 → 不进入 S2；按输出补齐工具或镜像来源，不得手工拼包。

### S2 校验（构建机 + 目标机各做一次）

```bash
export COSIGN_PUBLIC_KEY=/path/to/cosign.pub
python deploy/validate_offline_media.py verify <PKG_OLD>
```

- **预期**：退出码 `0`；`[OK] 校验通过：N 个条目 / X MiB`。
- **失败判定**：任一 `[FAIL] <path>：<reason>` → 更换介质；**禁止跳过校验**。

### S3 目标机预检

```bash
python deploy/validate_offline_apply.py preflight <PKG_OLD> --state-dir <STATE>
```

- **预期**：退出码 `0`；`[OK] 预检通过：<pkg 名>`；`state.json` 不生成。
- **失败判定**：`disk`/`tool:*`/`arch` 任一 `[FAIL]` → 清理磁盘或安装工具/换包；不进入 S4。

### S4 首次加载

```bash
python deploy/validate_offline_apply.py load <PKG_OLD> \
  --state-dir <STATE> --database-url "<DSN>"
```

- **预期**：退出码 `0`；`[OK] 已加载 1.0.0：<PKG_OLD>`；`state.json` `status=loaded`、`current_version=1.0.0`。
- **失败判定**：退出码 `1` 且 `status=partial` → 按 `last_error` 修复后**重跑同一命令**；
  若重复失败 2 次 → 停止，按 `docs/offline-package.md` §10.1 处置。

### S5 功能冒烟

```bash
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps
curl -s http://127.0.0.1/healthz
curl -s http://127.0.0.1/api/v1/readyz
```

- **预期**：服务齐、`ok`、`ready`；再按 B3.3–B3.5 人工检查。
- **失败判定**：任一不通过 → 不上报“加载完成”；按部署手册第 12 章排障，必要时回滚到未加载状态（清库/恢复备份）。

### S6 升级到新版本

```bash
python deploy/validate_offline_apply.py verify <PKG_NEW>
python deploy/validate_offline_apply.py upgrade <PKG_NEW> \
  --state-dir <STATE> --database-url "<DSN>"
```

- **预期**：退出码 `0`；`[OK] 已从 1.0.0 升级到 1.1.0`；`state.json` `previous.version=1.0.0`。
- **失败判定**：`升级被拒绝：<reason>` → 按提示逐级升级，不得改版本号绕过；
  中途失败且 `status=partial` → 可重跑 `upgrade`，或直接进入 S7 回滚。

### S7 回滚

```bash
python deploy/validate_offline_apply.py rollback --state-dir <STATE> --database-url "<DSN>"
```

- **预期**：退出码 `0`；`[OK] 已回滚到 1.0.0`；`status=rolled_back`；冒烟通过。
- **失败判定**：退出码 `3` → **立即停止上线**，按 `docs/offline-package.md` §10.2；
  在完成人工介入并再次验证前，不得启动业务容器、不得对外称回滚成功。

### S8 回滚后再升级

```bash
python deploy/validate_offline_apply.py upgrade <PKG_NEW> \
  --state-dir <STATE> --database-url "<DSN>"
```

- **预期**：退出码 `0`；版本回到 `1.1.0`；`previous.version=1.0.0`；冒烟通过。
- **失败判定**：任何非 0 → 保持 1.0.0 运行，停止验收，记录为不通过/有条件通过（视根因）。

### S9 归档证据

```bash
cp <STATE>/state.json ./evidence/state-final.json
docker images > ./evidence/docker-images.txt
```

- **预期**：证据目录含 S1–S8 全部命令输出、退出码、`state.json`、冒烟截图。
- **失败判定**：证据不全 = 对应验收项视为未执行。

---

## 6. 未验证项与验证条件（与 `docs/offline-package.md` §11 一致）

| 项 | 验证条件 | 责任方 |
| --- | --- | --- |
| 真实镜像导入 Docker | 目标机 Docker 24+，执行 S4 后 `docker images` 核对 ref/tag | 实施工程师 |
| 真实 psql 迁移/回滚 | 迁移角色 DSN + 测试库，执行 S4/S6/S7，核对表结构与迁移记录 | DBA |
| 真实插件导入器 | 提供 `dify-plugin-import install/remove` 包装，执行 S4 后核对插件列表 | 实施工程师 + 客户运维 |
| 真实 cosign 验签 | 构建产物 + 客户侧公钥，执行 S2 | 交付负责人 |
| 真实磁盘判定 | 目标机 `preflight` 与 `df` 手工对账 | 客户运维 |
| 回滚演练 | 试点前在演练环境执行并填写模板 | 实施方 + 客户见证 |

---

## 7. 验收结论签署

| 项 | 内容 |
| --- | --- |
| 验收结论 | ☐ 通过 ☐ 有条件通过（未执行项：__________）☐ 不通过 |
| 证据包位置 | __________（含 S1–S9 输出与 `state.json`） |
| 实施方签字 / 日期 | __________ |
| 客户方签字 / 日期 | __________ |
| 见证人签字 / 日期 | __________ |

> 签署前请确认：第 3 节与第 4 节没有空白项；「有条件通过」的未执行项已写责任人与期限。
> 签字页与试点签字页（`docs/pilot-plan-2026-11-07.md` §7）分开归档，不可互相替代。
