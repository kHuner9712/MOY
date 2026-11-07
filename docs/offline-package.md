# 离线介质包（3A：构建与校验）

> 对应阶段 3A：**只做构建侧**。目标机上的加载/升级/回滚属 3B，本文末尾列出其前置条件。
> 实现：`deploy/validate_offline_media.py`（构建 + 校验 + 自检）；
> 单测：`apps/adapter/tests/unit/test_offline_media.py`。

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
- 3A 不生成安装器/加载脚本，不执行目标机校验。

## 8. 3B 的前置条件（输入契约）

3B 可直接依赖以下稳定契约，无需再解析目录：

1. 包根目录名 `moy-offline-v<version>-<arch>`；
2. `MANIFEST.json` 的 `schema = moy-offline-manifest/v1`，字段
   `package.{version,arch,vertical,built_at,git_commit}`、`images[].{ref,digest,archive}`、
   `entries[].{path,sha256,size}`、`totals.{entries,bytes}`；
3. `SHA256SUMS` 为 GNU 格式（`<sha256>  <relpath>`，path 升序）；
4. 签名文件固定为 `SHA256SUMS.sig`，公钥由部署方提供（`COSIGN_PUBLIC_KEY`）；
5. 固定子目录：`images/ wheels/ web/ sql/{migrations,init,rollback}/ plugins/ sbom/ metadata/`；
6. 加载前必须先在目标机跑 `verify`（可复用本脚本，或 3B 的加载器内联同一判据）。

3B 需要新增：目标机预检（架构/磁盘/工具）、镜像 `skopeo copy` 到本地存储、
SQL 升/降执行顺序、插件导入、失败回滚、断点续传与幂等重入。
