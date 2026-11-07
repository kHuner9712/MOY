# 离线介质包（3A 构建校验 + 3B 目标机加载/升级/回滚）

> 阶段 3A：构建与校验（`deploy/validate_offline_media.py`）。
> 阶段 3B：目标机加载、升级与回滚（`deploy/validate_offline_apply.py`）。
> 交付验收文档与试点方案属 3C，本文不涉及。
>
> **前提（必须知道）**：3A 的**真实搬运尚未执行**（首次真机生成尚未执行），
> 仓库内只有构建与校验的纯逻辑与假介质自检。3B 按 3A 的固定契约实现，但
> **没有拿真实介质包在真机跑过**；首次上线前必须按 §2 在构建机生成真包，
> 再按 §9 在目标机逐步执行。不要把本文的"自检通过"理解为"真机已验证"。
>
> 单测：`apps/adapter/tests/unit/test_offline_media.py`（3A，18 个）、
> `apps/adapter/tests/unit/test_offline_apply.py`（3B，32 个）。

## 1. 介质包内容

目录名固定为 `moy-offline-v<version>-<arch>`，结构如下（3B 的加载器按此契约读取）：

```text
moy-offline-v1.0.0-x86_64/
├── MANIFEST.json                 # schema、版本、架构、垂直、镜像 digest、逐文件 sha256/size、工具版本
├── SHA256SUMS                    # 除自身与签名外全部文件的 sha256（path 升序，跨平台确定）
├── SHA256SUMS.sig                # cosign sign-blob 的 detached 签名
├── images/
│   ├── <镜像 slug>-x86_64.oci.tar  # skopeo 搬运的 OCI 归档（compose 运行时镜像）
│   └── images.lock               # 构建时的镜像真源快照
├── wheels/
│   ├── requirements.txt          # uv export 的完整依赖锁（含传递依赖）
│   └── *.whl                     # pip download 的离线 wheel
├── web/
│   ├── package.json / package-lock.json  # 前端依赖真源快照
│   └── npm-cache.tar.gz          # npm ci --cache 产出的离线缓存
├── sql/
│   ├── migrations/*.sql          # 迁移（升）
│   ├── init/*.sql                # 初始化 SQL（schema/RLS/角色/库）
│   └── rollback/*.sql            # 回滚 SQL（降）
├── plugins/
│   └── *.difypkg                 # 插件清单与插件包（默认垂直：内容运营）
├── sbom/
│   ├── sbom.cdx.json             # 产品级 SBOM（deploy/sbom/generate.py）
│   └── images/<镜像 slug>-x86_64.cdx.json  # syft 逐镜像 SBOM
└── metadata/
    ├── version.json              # version / arch / vertical / built_at / git_commit
    └── NOTICE.third-party.md     # 镜像许可证（取自 images.lock）与插件清单
```

镜像范围是**三套编排里 `image:` 声明的运行时镜像**（adapter、web、edge/nginx、postgres、
milvus、valkey、keycloak、dify 等），构建时按 `images.lock` 的 digest 拉取；编排引用
必须全部登记在锁文件里，否则构建直接失败。

## 2. 生成步骤

前置工具（缺任何一个构建直接退出 2，并打印"需要安装什么"，不生成空包）：

| 工具 | 用途 | 安装 |
| --- | --- | --- |
| `skopeo` | 镜像搬运（`docker://` → OCI 归档） | `apt install skopeo` / `dnf install skopeo` |
| `syft` | 镜像 SBOM | <https://github.com/anchore/syft#installation> |
| `cosign` | `SHA256SUMS` 签名 | <https://docs.sigstore.dev/cosign/installation/> |
| `uv` | 导出 Python 依赖锁 | `pipx install uv` |
| `npm` | 前端离线缓存 | 随 Node.js 22 |

```bash
# 0) 仓库就绪检查（纯逻辑，不需要任何工具）
python deploy/validate_offline_media.py

# 1) 构建（默认 x86_64 / 版本取 package.json / 垂直 plg_content_ops）
python deploy/validate_offline_media.py build --out dist/offline

# 2) 可选覆盖
python deploy/validate_offline_media.py build --out dist/offline \
    --version 1.0.0 --arch x86_64 --vertical plg_content_ops

# 3) 签名密钥（build 内调用 cosign sign-blob）
export COSIGN_PUBLIC_KEY=/path/to/cosign.pub   # verify 阶段使用
```

产物写入 `dist/offline/moy-offline-v<version>-<arch>/`（`dist/` 已在 `.gitignore`）；
构建的临时目录一律位于系统临时目录，**不留在仓库树内**。

重复构建同一版本时，目标目录已存在会直接失败（避免半成品混入旧文件），
确认无残留后再重跑。

## 3. 校验步骤

```bash
python deploy/validate_offline_media.py verify dist/offline/moy-offline-v1.0.0-x86_64
```

校验顺序：目录命名/固定结构/MANIFEST 元数据 → 逐条 `SHA256SUMS` 比对 →
未登记文件检测 → `cosign verify-blob`。失败时**逐条打印具体路径**，并以 1 退出：

```text
[FAIL] images/adapter-1.0.0-x86_64.oci.tar：sha256 不匹配（清单 3f2a1… / 实际 9c0b4…）
[FAIL] wheels/pydantic-2.13.5.whl：文件缺失
[FAIL] metadata/injected.json：不在 SHA256SUMS 内（未登记或已被篡改）
[FAIL] SHA256SUMS.sig：签名校验失败
[FAIL] 校验未通过（4 项）
```

`verify` 要求 `COSIGN_PUBLIC_KEY`；缺 cosign 或密钥属"无法判定"，同样非零退出，
不会把"没验"当成"验过"。

## 4. 目标架构

本轮只做 `x86_64`。目录名与镜像归档名都带架构后缀；`--arch` 只接受
`SUPPORTED_ARCHES` 中的值。扩展 arm64 时需同步：`SUPPORTED_ARCHES`、
镜像搬运的 `--platform` 参数、`docs/offline-package.md` 与测试矩阵。

## 5. 体积预期

以下为**估算**（真实搬运未在本机/CI 验证，实际以 `MANIFEST.json` 的
`totals.bytes` 为准）：

| 组成 | 估算 |
| --- | --- |
| 容器镜像（19 个运行时镜像的 OCI 归档） | 8–12 GB（dify/底座类镜像占大头） |
| Python wheels | 120–250 MB |
| 前端 npm 缓存 | 80–200 MB |
| SQL / 插件 / SBOM / NOTICE | < 100 MB |
| 合计 | **约 9–13 GB** |

介质包按"不压缩的 OCI 归档 + gzip 缓存"组织，便于目标机直接 `skopeo copy` 加载；
如需缩小体积，可在 3B 增加 `--compress` 选项（本轮不做）。

## 6. CI 覆盖与未验证项

CI（`ci-adapter.yml`）执行且必须通过的部分：

- `validate_offline_media.py self-test`：1 条正样本 + 8 条红灯用例（含**改 1 个字节**
  必须红、删除/新增文件、架构不符、空清单、签名拒绝、签名缺失）；
- `apps/adapter/tests/unit/test_offline_media.py`：18 个用例，覆盖计划/解析/
  MANIFEST/校验和/篡改/CLI 退出码/缺工具 fail-closed。

**明确未验证**：真实镜像搬运、wheels 下载、npm 缓存、syft/cosign 调用。原因：
CI runner 无 Docker daemon 与外网凭据，也没有签名私钥。这些步骤必须在**构建机**
上验证，验证条件：

1. 已安装并登录容器运行时（自研 `officeai/*` 镜像在本地 daemon）；
2. 可访问镜像仓库与 PyPI/npm registry；
3. 已提供 `cosign` 签名密钥（keyless 或 key-pair）；
4. 完成后跑 `verify`，并用 `skopeo inspect oci-archive:...` 抽查归档可读。

CI **不提供 allow-skip 开关**来掩盖未执行：构建步骤根本不在 workflow 中，
未验证事实写在本节与脚本帮助里，而不是一个默认跳过的绿色步骤。

## 7. 已知限制

- 只支持 `x86_64`，单架构归档；
- 前端离线依赖用 `npm ci --cache` 的缓存 tarball，不是完整 `node_modules` 快照；
  目标机需 Node 22 与同版本 npm；
- 自研镜像从本地 daemon 搬运（`docker-daemon:`），CI 无法覆盖；
- 插件目录当前仅含 `deepseek-0.0.25.difypkg`；垂直默认 `plg_content_ops`，
  可被 `--vertical` 覆盖，但插件包本身不在本轮扩充；
- 3A 不生成安装器/加载脚本；目标机加载由 3B 的 `validate_offline_apply.py` 提供。

## 8. 3B 的输入契约（已按此实现）

3B 直接依赖以下稳定契约，不再解析目录：

1. 包根目录名 `moy-offline-v<version>-<arch>`；
2. `MANIFEST.json` 的 `schema = moy-offline-manifest/v1`，字段
   `package.{version,arch,vertical,built_at,git_commit}`、`images[].{ref,digest,archive}`、
   `entries[].{path,sha256,size}`、`totals.{entries,bytes}`；
3. `SHA256SUMS` 为 GNU 格式（`<sha256>  <relpath>`，path 升序）；
4. 签名文件固定为 `SHA256SUMS.sig`，公钥由部署方提供（`COSIGN_PUBLIC_KEY`）；
5. 固定子目录：`images/ wheels/ web/ sql/{migrations,init,rollback}/ plugins/ sbom/ metadata/`；
6. 加载前必须先跑 `verify`（3B 已内联同一判据，见 §9）。

3B 已实现：目标机预检（架构/磁盘/工具）、镜像 `skopeo copy` 到本地存储、
SQL 升/降执行顺序、插件导入、区分成功/失败的回滚、断点续传与幂等重入。

## 9. 目标机全流程（预检 → 校验 → 加载 → 验证 → 升级 → 回滚）

### 9.1 前置条件

| 项 | 要求 | 检查 |
| --- | --- | --- |
| 架构 | 与介质包 `package.arch` 一致（本轮 `x86_64`） | `uname -m` |
| 磁盘 | 空余 ≥ `totals.bytes + max(20%, 512 MiB)` | `df -h` |
| 工具 | `skopeo`、`cosign`、`psql`、插件导入器（默认 `dify-plugin-import`） | `command -v` |
| 插件导入器 | 能在本机执行 `install <file>` / `remove <file>`；没有则该命令预检失败 | `dify-plugin-import --help` |
| 数据库 | 迁移用的属主/迁移角色 DSN（与运行期应用角色分离，见部署手册 §1.2） | `psql "$OA_DATABASE_URL" -c 'select 1'` |
| 介质 | 已在构建机生成并按 §3 校验通过 | 见 §2、§3 |

> 插件导入器的命令约定：`<plugin_loader> install <绝对路径>` 与 `remove <绝对路径>`，
> 返回码 0 视为成功。它由部署方提供包装脚本，3B 不内置任何插件平台逻辑。

### 9.2 状态文件

- 默认路径 `/var/lib/moy-offline/state.json`，`--state-dir` 可覆盖（CI/自检必须用临时目录）。
- schema `moy-offline-state/v1`，字段：`current_version`、`status`、`applied`（幂等键与时间）、
  `previous`（升级前的版本/目录/镜像/插件快照）、`history`、`fingerprint`（`SHA256SUMS` 的 sha256）、
  `last_error`（失败步骤/条目/原因）、`rollback_completed` / `rollback_pending`。
- **不记录数据库连接串、口令、密钥**；`--database-url` 只用于本次进程。
- 状态文件原子写（同目录临时文件 + `os.replace`），进程崩溃不会留下半个 JSON。

`status` 取值：`never_loaded`、`loading`、`partial`（有步骤失败，可续跑）、
`loaded`、`rolling_back`、`rollback_failed`（需人工）、`rolled_back`。

### 9.3 命令与退出码

```bash
# ① 预检（不写任何东西）
python deploy/validate_offline_apply.py preflight /path/to/moy-offline-v1.0.0-x86_64 \
    --state-dir /var/lib/moy-offline

# ② 校验（不写任何东西；缺 cosign/COSIGN_PUBLIC_KEY 也按失败处理）
export COSIGN_PUBLIC_KEY=/path/to/cosign.pub
python deploy/validate_offline_apply.py verify /path/to/moy-offline-v1.0.0-x86_64

# ③ 首次加载（幂等；中断后重跑同一条命令）
python deploy/validate_offline_apply.py load /path/to/moy-offline-v1.0.0-x86_64 \
    --state-dir /var/lib/moy-offline --database-url "$OA_DATABASE_URL"

# ④ 目标机验证（不在包内执行脚本；由实施人员按部署手册跑）
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps
curl -s http://127.0.0.1/healthz
curl -s http://127.0.0.1/api/v1/readyz

# ⑤ 升级（先做版本兼容校验，再走与 load 相同的幂等步骤）
python deploy/validate_offline_apply.py upgrade /path/to/moy-offline-v1.1.0-x86_64 \
    --state-dir /var/lib/moy-offline --database-url "$OA_DATABASE_URL"

# ⑥ 回滚（回到上一版本）
python deploy/validate_offline_apply.py rollback \
    --state-dir /var/lib/moy-offline --database-url "$OA_DATABASE_URL"

# 自检（CI 真跑入口；不需要 Docker/网络）
python deploy/validate_offline_apply.py self-test
```

| 退出码 | 含义 | 现场动作 |
| --- | --- | --- |
| `0` | 成功（含"已加载且指纹一致，无需重复执行"） | 继续下一步 |
| `1` | 预检/校验/加载/升级失败，**未完成** | 按输出修好后重跑同一条命令；已完成的步骤会跳过 |
| `3` | 回滚中途失败（已停在安全点） | **禁止启动业务容器**，按 §10.2 人工介入 |

### 9.4 加载步骤与顺序（严格按 MANIFEST，不扫目录）

1. **镜像**：对 `MANIFEST.images[]` 逐个
   `skopeo copy --all oci-archive:<包>/<archive>:<tag> docker-daemon:<ref>`；
2. **wheels**：`python -m pip install --no-index --find-links <包>/wheels -r wheels/requirements.txt`；
3. **SQL**：只执行 `sql/migrations/` 下清单登记的文件，按**文件名升序**，
   每条 `psql "$OA_DATABASE_URL" -v ON_ERROR_STOP=1 --single-transaction -f <file>`；
4. **插件**：只导入 `plugins/` 下清单登记的文件，逐条 `<loader> install <file>`。

**幂等键**：`image:<archive>`、`pip:<requirements 的 sha256>`、
`sql:<相对路径>`、`plugin:install|remove:<相对路径>`、`rollback_image:<archive>`。
重入时按状态文件跳过已完成键，不重复建表、不重复导入插件、不重复打 tag。

### 9.5 升级规则

- 允许：同大版本且次版本最多跨 **1**（如 `1.0.0 → 1.1.0`、`1.1.0 → 1.1.1`）；
- 拒绝：跨大版本（`1.x → 2.x`）、次版本跨 2 个及以上（`1.0.0 → 1.2.0`）、
  降级与同版本（`1.1.0 → 1.0.0` / `1.1.0 → 1.1.0`，降级请用 `rollback`）；
- 被拒绝时退出码 `1`，状态文件不变，输出明确说明"最多跨 1 个次版本，请逐级升级"；
- 升级开始前先把 `previous` 快照写入状态，**升级中途失败也能立即 rollback**；
- 新增迁移文件按相对路径执行，旧迁移（同名同路径）不会重跑。

### 9.6 回滚步骤

1. 校验**当前版本**介质包（不执行未校验的包内 SQL）；
2. 执行当前版本 `sql/rollback/` 下清单登记文件，按文件名**逆序**；
3. 重新导入上一版本镜像（tag 回退）；4. 移除当前版本插件，恢复上一版本插件；
5. 成功：`status=rolled_back`，`current_version` 回退，`previous=None`。

## 10. 失败语义、处置表与人工介入点

### 10.1 失败处置表

| 现象 | 退出码 | 状态文件 | 处置 |
| --- | --- | --- | --- |
| 缺 `skopeo`/`cosign`/`psql`/插件导入器 | `1` | 不生成（未写入） | 按输出安装缺失工具后重跑 `preflight` |
| 磁盘不足 | `1` | 不生成 | 清理到 `totals.bytes + max(20%,512 MiB)` 以上 |
| 架构不符 | `1` | 不生成 | 换对应架构的介质包 |
| 哈希/签名/未登记文件/MANIFEST 不一致 | `1` | 不生成 | 更换介质包，**不得**跳过校验 |
| 加载某一步失败（镜像/wheels/SQL/插件） | `1` | `status=partial`、`last_error={step,target,detail}` | 修复该项后**重跑同一 load**；已完成步骤自动跳过 |
| 同版本换包（指纹不同） | `1` | 不变 | 用原包，或人工确认后按升级流程处理 |
| 已加载旧版本却直接 `load` 新版本 | `1` | 不变 | 改用 `upgrade` |
| 非法升级（跨大版本/跳 2 个次版本/降级） | `1` | 不变 | 逐级升级或改用 `rollback` |
| 升级中途失败 | `1` | `status=partial` 且保留 `previous` | 重跑 `upgrade`；或直接 `rollback` |
| 回滚前旧/当前介质校验失败 | `1` | 不变（回滚未开始） | 补齐/更换介质包；不得执行未校验 SQL |
| 当前版本介质包丢失 | `3` | `rollback_failed`、`pending=[sql,images,plugins]` | 找回介质包重跑，或按 §10.2 手工执行 |
| 回滚中途失败（SQL/镜像/插件） | `3` | `rollback_failed`、`completed`/`pending` 已落盘 | 按 §10.2 处理；**不得启动业务容器** |

### 10.2 人工介入点（回滚失败）

出现退出码 `3` 时，脚本**已停止且未标记成功**，当前可能处于"部分新版本 + 部分旧版本"。
按顺序执行：

1. 查看 `state.json` 的 `rollback_completed` 与 `rollback_pending`，确认停在哪一步；
2. 修复该步的根因（数据库连接/权限、镜像归档可读性、插件导入器）；
3. 重跑同一条 `rollback` 命令：已完成键会跳过，从 `pending` 首项继续；
4. 若无法自动续跑，按 `sql/rollback/` 文件名**逆序**手工执行剩余 SQL
   （`psql ... -v ON_ERROR_STOP=1 --single-transaction -f <file>`），再手工导入旧镜像与插件；
5. 全部完成后把状态文件 `status` 改为 `rolled_back`、`current_version` 改为旧版本；
6. **在回滚完成并通过 §9.3 ④ 验证前，不要启动业务容器**，避免新旧 schema 混用。

## 11. CI 覆盖与未验证边界（3B）

CI（`ci-adapter.yml`「离线介质目标机链路自检」）**真跑**的部分：

- `validate_offline_apply.py self-test`：假介质包 + 真实临时文件系统 + `FakeExecutor`，
  覆盖预检通过/磁盘不足/缺工具/签名与哈希失败/MANIFEST 不一致/正常加载/幂等重入/
  加载中途失败与崩溃断点续跑/非法与合法升级/回滚成功/回滚中途失败（退出码 3）；
- `apps/adapter/tests/unit/test_offline_apply.py`：32 个用例，含版本矩阵、状态 roundtrip、
  同版本换包拒绝、跨版本直接 load 拒绝、升级只跑新增迁移、升级失败保留 previous、
  回滚成功/中途失败重入/介质丢失人工停点、CLI 自检。

**明确未在 CI 验证**（不设 allow-skip，事实写在这里）：

| 未验证项 | 为什么 | 验证条件 |
| --- | --- | --- |
| `skopeo copy` 真实导入 Docker | runner 无 Docker daemon 与镜像归档 | 目标机有 Docker 24+；用真包执行 `load`，再 `docker images` 核对 ref/tag |
| 真实 `psql` 执行迁移/回滚 | runner 无数据库 | 目标/测试库执行 `load` 与 `rollback`，核对表结构与迁移记录 |
| 真实插件导入器 | 平台侧包装命令不在仓库 | 提供 `dify-plugin-import install/remove` 后执行 `load`，核对插件列表 |
| 真实磁盘余量判定 | runner 磁盘与目标机不同 | 目标机执行 `preflight`，与 `df` 手工核对 |
| 真实 cosign 签名校验 | 无私钥/公钥 | 构建机产物 + `COSIGN_PUBLIC_KEY` 执行 `verify` |

**首次真机生成尚未执行**：上述验证的第一步是先在构建机按 §2 生成真实介质包
（3A 的真实搬运也未验证），再在目标机按 §9 执行。在这之前，3B 的"通过"只代表
逻辑与状态机在 CI 可复现，**不代表真机链路已打通**。
