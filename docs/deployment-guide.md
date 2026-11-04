# Moy（麦子）V1.0 部署手册

> 适用版本：V1.0（镜像标签 `1.0.0`）
> 适用读者：客户方运维工程师、数据库管理员（DBA）、交付实施工程师
> 文档性质：**面向客户交付**。本文所有命令均在交付物中真实存在，可逐条执行。

---

## 1. 手册怎么用

### 1.1 三条阅读路径

| 你的场景 | 读哪几章 |
| --- | --- |
| 第一次装（干净机器） | 第 2 章 → 第 3 章 → 第 4 章 → 第 5 章 |
| 已有环境要升级 | 第 2 章 → 第 3 章 → **第 6 章（重点）** → 第 10 章 |
| 出问题了 | 第 12 章（故障排查）→ 第 11 章（备份恢复） |

### 1.2 一条必须先知道的纪律

**命令的退出码就是判断依据，不要看屏幕最后一行。** 本平台的部署脚本按"每个退出码只有一个含义"设计，运维照码决定下一步，不靠猜：

| 退出码 | 含义 | 谁处理 |
| --- | --- | --- |
| `0` | 成功（含"已装过、本次无实质变更"） | — |
| `1` | 通用失败，上方必有一行 `[FAIL]` 指明哪一步 | 运维看日志 |
| `2` | 前置检查未通过（缺命令 / 版本过低 / 内存不足 / 端口被占 / 配置未替换 / 数据库不健康） | 运维按提示补齐 |
| `3` | 镜像不可用（离线包校验失败、镜像导入失败、镜像指纹与清单不符）；**回滚/恢复场景下还表示"未声明维护窗口"** | 运维核对交付包 / 补 `OA_MAINTENANCE=1` |
| `4` | 数据库缺少"记忆检索"所需的扩展 | **DBA**（第 6.3 节） |
| `5` | 迁移被数据库拒绝（**migrate 的 DSN 不是属主/迁移角色**，缺 `public` 的 CREATE 权限） | 运维核对 DSN；必要时 DBA |
| `6` | 预留：现场基线被改动过。**V1.0 只登记不判定**，实际很少出现 | 运维自查（第 6.3.6 节） |
| `7` | 该版本没有自动回滚脚本 | DBA（第 7.3 节） |

各脚本实际会返回哪些码（避免"等一个永远不会出现的码"）：

| 脚本 | 可能返回 |
| --- | --- |
| `deploy/install.sh` | `0` `1` `2` `3` `5`（`4`/`6` 为升级场景预留，安装不会返回） |
| `deploy/upgrade.sh` | `0` `1` `2` `3` `4` `5` `6` `7` |
| `deploy/rollback.sh` | `0` `1` `2` `3` `7` |
| `deploy/backup.sh` | `0` `1` `2` |
| `deploy/restore.sh` | `0` `1` `2` `3` |

> **角色分工（硬要求，`1.0.0` 起）**：迁移用**属主/迁移角色** DSN（`POSTGRES_USER`，如 `officeai`），
> 运行期用**应用角色** DSN（`officeai_app`，NOSUPERUSER + NOBYPASSRLS、对 `public` 无 CREATE）。
> 两者必须分离：迁移要建表建索引，运行角色按设计不能做 DDL。
> **不要**给应用角色授 `CREATE` —— 那会让"迁移跑通"与"RLS 隔离生效"直接互斥。
>
> 迁移与回滚另有一道**维护闸门**：未显式声明"现在是维护窗口"时，升级/回滚/恢复脚本直接以 `3` 退出，一个容器都不动（第 8 章）。

---

## 2. 环境要求

### 2.1 硬件（单机轻量版）

| 项 | 最低 | 建议 | 依据 |
| --- | --- | --- | --- |
| CPU | 4 核 | 8 核 | 常驻 9 个服务进程，其中身份服务为单线程占主导 |
| 内存 | **8 GiB（硬判据）** | 16 GiB | 安装脚本读的是 **Docker 引擎可用内存**，不是物理内存；实测空载峰值 1.93 GiB、起栈瞬时更高 |
| 磁盘 | 60 GiB | 200 GiB | 镜像约 9 GB + 数据库增长 + 备份留 3 份 |
| 架构 | x86_64 | x86_64 | 离线包按 x86_64 构建；arm64 需另行构建 |

> 内存判据为什么是"引擎可用内存"：容器跑在 Docker 的 VM/cgroup 里。宿主 16 GiB 但 Docker 只分配 4 GiB 的机器，照样会被系统 OOM 打断，表现为**容器反复重启、日志里没有任何异常**——这是最难排查的一类故障，所以在安装前就拦住。

### 2.2 软件

| 软件 | 版本要求 | 检查命令 |
| --- | --- | --- |
| Docker Engine | **24.0+** | `docker version --format '{{.Server.Version}}'` |
| Docker Compose | **v2.24+**（插件形态，不是老的 `docker-compose` v1） | `docker compose version --short` |
| openssl | 任意近期版本 | `openssl version` |
| sha256sum | coreutils 自带 | `sha256sum --version` |
| curl | 任意近期版本 | `curl --version` |
| bash | 4.0+ | `bash --version` |

- Docker 低于 24.0：编排里的内存上限不生效，容量判据失控。
- Compose 低于 v2.24：编排文件用了 `env_file` 的 `required: false` 语法，低版本会直接拒绝解析。
- 上述依赖**安装脚本会自己检查**，缺了会打印"缺什么、怎么补"，退出码 `2`。

### 2.3 网络与端口

| 端口 | 用途 | 是否必须对外开放 |
| --- | --- | --- |
| **80**（可改） | 平台统一入口（页面 + 接口都走它） | **是**，唯一对外端口 |
| 443 | TLS。**两种形态**：A 由客户前置 LB/网关终止（平台不开 443）；B 由平台自建边缘终止（轮 2 交付，平台开 443） | A 形态：按客户规范；B 形态：**是** |

- 轻量版**只发布一个端口**。数据库、缓存、身份服务、业务服务一律不映射宿主端口——它们没有面向公网的鉴权模型，暴露即风险。
- 80 被占用时不要改编排文件，用安装参数换端口（只影响新生成的环境文件）：
  ```bash
  bash deploy/install.sh --online --proxy-port 8080
  ```
- 私有化环境默认**不出网**：出站控制由客户网络策略/代理执行（V1.0 无 `OA_OFFLINE_MODE`
  运行时开关，该键已于 2026-11-04 审计删除；离线包构建脚本仍待排期，见 `deploy/offline/README.md`）。
  若客户允许出站，仅需放通模型服务与客户已连接的工具平台域名。

### 2.4 系统与时钟

- Linux 发行版：主流 x86_64 发行版均可（内核 ≥ 4.19，cgroup v2 更好）。
- 时区与时钟：容器统一使用 UTC 存储时间，界面按浏览器时区展示。**宿主机必须开启 NTP**：时钟漂移超过 5 分钟会导致登录令牌被判失效、审计时间线错乱。排查命令：
  ```bash
  timedatectl status
  ```

---

## 3. 交付包与完整性校验

### 3.1 包内结构

```
交付包/
├── deploy/                        # 编排、脚本、初始化 SQL、镜像清单
│   ├── install.sh                 # 一键安装
│   ├── docker-compose.lite.yml    # 轻量版编排（单机 9 服务）
│   ├── docker-compose.yml         # 全量版编排（含独立检索服务与对象存储）
│   ├── .env.lite.example          # 轻量版配置样例（模板，不含真实口令）
│   ├── images.lock                # 镜像清单：名称 + 标签 + 指纹（digest）
│   ├── nginx/                     # 入口网关配置
│   ├── migrations/                # 增量变更脚本（含 rollback/ 下的回退脚本）
│   ├── offline/
│   │   ├── images/                # 镜像归档 *.tar + checksums.sha256
│   │   └── plugins/               # 模型服务插件离线包
│   ├── backup.sh / restore.sh     # 备份 / 恢复
│   └── validate_*.py              # 交付自检脚本
├── docs/                          # 本手册与合规文档
├── sql/                           # DBA 用的一次性脚本（见第 6、11 章）
└── (可选) sbom.cdx.json           # 组件清单（供客户安全评审）
```

### 3.2 完整性校验（**装之前必须做**）

镜像归档目录内应同时存在 `checksums.sha256`（或 `checksums.txt`）与若干 `*.tar`。校验必须在该目录内执行——清单里写的是相对文件名：

```bash
cd deploy/offline/images
sha256sum -c checksums.sha256
```

期望输出：每一行都是 `xxx.tar: OK`，且**没有 `FAILED`**。

若清单缺失，用归档自身重新生成（**只能在受信任的介质上做**）：

```bash
cd deploy/offline/images
sha256sum *.tar > checksums.sha256
```

然后逐项核对镜像指纹与清单是否一致：

```bash
grep -c "digest:" deploy/images.lock    # 应等于镜像条目数，为 0 说明清单被改坏
```

> 判据：`sha256sum -c` 通过 ≠ 镜像与 `images.lock` 同批次。安装脚本还会逐个核对镜像指纹（第 4.3 节），两者都过才算"包是对的"。离线包与 `images.lock` **必须成对交付**，混批次会在核对阶段以退出码 `3` 明确失败，不会装到一半。

---

## 4. 全新部署

### 4.1 一条命令

```bash
# 离线（推荐，内网无外网）
bash deploy/install.sh --offline

# 离线包放在别处（例如 U 盘挂载点）
bash deploy/install.sh --offline --images-dir /mnt/usb/officeai-images

# 在线（有外网镜像仓库时；按指纹拉取，标签不作为依据）
bash deploy/install.sh --online
```

安装脚本做的事（顺序固定，任一步失败即停，不留半装状态）：

1. **前置检查**：Docker/Compose 版本、引擎可用内存、宿主端口占用、配置占位符 `CHANGE_ME_*` 是否已全部替换。
2. **镜像准备**：离线包校验与导入，或在线按指纹拉取；逐个核对指纹。
3. **生成环境文件**：从 `.env.lite.example` 生成 `deploy/.env.lite`，随机口令用 `openssl rand -hex 24` 生成（hex 字符集天然 URL 安全，避免口令嵌进连接串后出错）。
4. **建库与初始化**：创建业务库、身份库、插件库，执行初始化脚本。
5. **执行数据库变更**：以一次性任务方式运行（不挂在业务服务启动路径上，避免多副本同时改表）。
6. **启动服务**：拉起常驻的 9 个服务。
7. **健康检查**：轮询入口健康检查，默认上限 **180 秒**（实测首次启动约 95–101 秒，180 秒是下限而非余量）。
8. **打印一次性管理员口令**（只在本次输出，**必须当场记录**），并写安装日志 `deploy/install.log`（运行时生成）与安装台账 `deploy/.installed-manifest.json`。

### 4.2 安装后必须做的三件事

```bash
# ① 服务数量应为 9（常驻）；dify-web（管理控制台）与 migrate 是"按需启动"，不计入
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps --services --filter status=running | wc -l

# ② 入口健康检查
curl -s http://127.0.0.1/healthz           # 期望 ok

# ③ 业务就绪探针
curl -s http://127.0.0.1/api/v1/readyz     # 期望 ready
```

> 若客户用非默认端口，把 `http://127.0.0.1` 换成 `http://127.0.0.1:<端口>`。

### 4.3 镜像指纹核对（失败即更换交付包）

```bash
bash deploy/validate_lite_up.sh --check-only    # 静态断言：编排、配置、镜像清单一致性
```

期望：全部断言通过、退出码 `0`。若报"镜像指纹与 images.lock 不符"，说明现场镜像与清单不是同一批次，**不要继续安装**，更换交付包。

### 4.4 安装台账（升级的唯一比对基准）

安装成功后会生成 `deploy/.installed-manifest.json`，其中记录：版本、镜像指纹、编排文件摘要、5 份初始化脚本的指纹。

**不要手工编辑这个文件。** 升级前会用它判断"现场是否被人手改过初始化脚本或换了镜像"——不一致时升级以退出码 `6` 中止。这是保护措施，不是障碍。

---

## 5. 首次登录与初始化

| 步骤 | 操作 | 说明 |
| --- | --- | --- |
| 1 | 浏览器打开 `http://<服务器地址>` | 只有入口端口对外 |
| 2 | 用安装日志里的一次性管理员口令登录 | 口令只打印一次，装完立即改 |
| 3 | 修改管理员口令 | 首次登录强制 |
| 4 | 创建部门与成员（或接入客户已有的统一身份源） | 见第 6 章与用户手册 |
| 5 | 配置可用的模型服务与工具授权 | 离线环境需先安装离线插件包：`deploy/offline/plugins/` |
| 6 | 跑一次对话做冒烟 | 见下表 |

冒烟检查（三项，全部通过才算装好）：

| 检查 | 操作 | 期望 |
| --- | --- | --- |
| 对话 | 发一句"你好" | 有回答，且不出现任何内部组件名 |
| 文档问答 | 上传 1 份 PDF 后提问 | 回答带引用出处，可点开定位原文 |
| 工具 | 在设置里连接一个邮箱或代码仓库账号 | 显示"已连接"，不再是"未连接" |

---

## 6. 升级已有环境

> **本章是升级操作的唯一权威步骤。** 存量环境升级与全新安装的本质区别只有一句话：**库是已经存在的，所以初始化脚本里那些"建库级对象"的动作不会重跑**。

### 6.1 升级前必读：五种升级结果（先认清"看哪一行"）

| 结果 | 最后一行 | 退出码 | 现场结果 |
| --- | --- | --- | --- |
| 成功 | `DONE` | `0` | 新版本已运行，数据完整 |
| 预检中止 | 预检失败提示（或 `DRY_RUN_OK` 后的正式流程） | `2`（环境问题）/ `4`（缺扩展） | **数据库一个字节都没动**，老版本继续服务 |
| 还没开始迁移就失败 | `FAILED_NO_MIGRATION` | 触发失败的那个码（通常是 `1`） | 无需回滚（没有改动），修好后重跑 |
| 失败但**自动回退成功** | `ROLLED_BACK <已退回的版本>` | **透传本次失败的原始码**（`1` 或 `5`）——**不是 0** | 已回到升级前状态；仍需人工定位失败原因 |
| 失败且**回退也没成** | `ROLLBACK_FAILED rc=<码>` | `7`（目标版本无自动回退脚本）或 `1` | **禁止放流量**，交 DBA 按 runbook 处理 |

> 为什么"回退成功"仍返回非 0：退出码表达的是"这次升级有没有成功"，不是"服务现在好不好"。回退成功只说明**现场安全了**，升级这件事仍然失败——运维必须去看日志定位原因。只有 `DONE` 才是升级成功。

### 6.2 升级顺序（不可颠倒）

```bash
# ① 先干跑一遍：只做预检，不停服务、不备份、不改库
OA_MAINTENANCE=1 bash deploy/upgrade.sh --dry-run
#    看到 DRY_RUN_OK 才继续；看到退出码 4，先去 6.3 节让 DBA 建扩展

# ② 正式升级（脚本自己停入口、自己放流量；不需要你手工停）
OA_MAINTENANCE=1 bash deploy/upgrade.sh
#    结果看最后一行：DONE = 成功；ROLLED_BACK = 已自动退回升级前

# ③ 清掉维护模式声明
unset OA_MAINTENANCE
```

**不需要手工 `stop proxy web`**：升级脚本在进入"改动"阶段前会自己停入口（先 `proxy` 后 `web`），并用退出陷阱保证**任何路径**（成功、失败、Ctrl-C）都把服务恢复回来。你手工停反而会让它在日志里记一条"入口服务本来就没在跑"。

升级脚本内部顺序**不允许调整**：

```
维护闸门 → 前置检查 → 扩展预检 → 停入口 → 整库备份 → 数据库变更 → 冒烟三项
   → 通过：放流量、打印 DONE
   → 失败：自动调用回滚、打印 ROLLED_BACK
```

为什么预检必须排在变更之前：本次新增能力中有一项需要数据库侧的检索扩展，而后续的变更脚本会直接用到该扩展提供的列类型。如果扩展不存在，变更会在**执行到一半**时失败——那时库已经部分改变，回退代价远高于提前中止。所以这一条前置检查是硬顺序。

**常用参数**：

| 参数 | 用途 |
| --- | --- |
| `--dry-run` | 只做预检（含扩展预检），不停服务、不备份、不改库；成功后打印 `DRY_RUN_OK` |
| `--skip-backup` | 跳过备份（**仅当你刚做过一次并确认可用**；不建议） |
| `--env-file <路径>` | 指定环境文件（默认 `deploy/.env.lite`） |
| `--project-name <名>` | 指定编排项目名（默认 `officeai-lite`） |
| `--ready-timeout <秒>` | 冒烟阶段等待就绪的上限（默认 180） |

### 6.3 升级预检 ①：数据库扩展（**V1.0 唯一需要 DBA 动手的地方**）

#### 6.3.1 一句原话，先记住它

> **镜像换了不等于扩展装了。**

换成带扩展安装文件的数据库镜像，只代表"扩展的安装文件在镜像里"；**每一个数据库仍然需要单独启用一次**该扩展。全新安装时由初始化流程以数据库超级用户自动完成；**存量环境的升级不走初始化流程（库已存在），所以这一步无法自动完成**。

#### 6.3.2 为什么必须超级用户

- 该扩展的登记属性是 `trusted = false`（实测：`vector | 0.8.7 | trusted=false | superuser=true`），即**只有超级用户**能启用。
- 平台的应用账号是库属主，但**不是超级用户**（`NOSUPERUSER`）。实测直接执行会报：

  ```
  ERROR:  permission denied to create extension "vector"
  HINT:   Must be superuser to create this extension.
  ```

- 该扩展是**按库安装的物理对象**，不是实例级开关：`pg_available_extensions` 里 `available = t` 只说明"文件在镜像里"，`pg_extension` 里 `installed = t` 才说明"这个库真的启用了"。所以**每个库各要执行一次**。

#### 6.3.3 DBA 只需执行这一条语句（约 1 分钟）

用**数据库超级用户**连到**业务库**执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

按现场形态选执行方式：

```bash
# 方式 A：宿主机上直接执行（超级用户 = 环境变量 OA_POSTGRES_USER，默认 officeai）
psql -U officeai -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 方式 B：进容器执行（推荐，连接串与库名都取自现场配置）
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite \
  exec -T postgres psql -U officeai -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 方式 C：执行随包脚本（等价，且会自动校验版本 ≥ 0.8 并打印结果）
psql -U officeai -d officeai -f sql/enable-vector-extension.sql
```

> 该语句**幂等**：已启用时零改动，重跑无副作用。
> 若现场还有独立的插件库（默认 `dify`），同样执行一次；`sql/enable-vector-extension.sql` 的注释里给了逐库命令。

#### 6.3.4 验证

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

期望：返回一行，版本 ≥ `0.8`。

#### 6.3.5 预检失败时的现场表现（请照此判断，不要只看日志末尾）

- 退出码 **`4`**；
- 终端打印**可直接照做**的业务文案（不是技术黑话）：

  ```
  检测到数据库尚未启用「记忆检索」所需的扩展，升级已中止（数据库未做任何改动）。

  请让您的数据库管理员执行下面这一条语句（需要数据库超级用户权限，约 1 分钟）：

      psql -U <超级用户> -d <业务库> -c "CREATE EXTENSION IF NOT EXISTS vector;"

  执行完成后重新运行 upgrade.sh 即可。此步骤只需做一次。
  ```

- 同时**打印 `sql/enable-vector-extension.sql` 的全文**，并给出失败对照表（文件缺失 / 权限不足 / 版本过低三种情况各自的处理方式）；
- **数据库未被改动**，老版本继续正常对外服务。DBA 执行完上面的语句后重跑升级即可。

#### 6.3.6 退出码 `6` 的含义（**V1.0 只登记不判定**）

退出码 `6` 预留给"现场初始化脚本/镜像指纹与安装台账不一致"这一情形。**V1.0 的升级脚本只登记、不判定**——原因很实在：只把初始化脚本的注释改一行也会导致指纹变化，判红会误伤，而误伤的代价是客户不敢升级。

所以 V1.0 现场看到退出码 `6` 的机率极低。但仍建议每次升级前自查一次基线：

```bash
# 查看台账里记录的基线（版本、镜像指纹、编排摘要、5 份初始化脚本指纹）
cat deploy/.installed-manifest.json | head -40
```

- 若确认**有人手改了初始化脚本**：把文件恢复为交付包原始版本。
- 若确认是**正常升级就该换镜像**：说明交付包与台账不同批次。不要手改台账；联系交付负责人取成对版本的包与台账。

### 6.4 升级过程中可能遇到的阻断项与退出码

| 检查 | 不通过时 | 处置 |
| --- | --- | --- |
| Docker 守护进程可用 | 退出码 `2` | 启动 Docker 后重跑；数据库未被改动 |
| 环境文件存在（默认 `deploy/.env.lite`） | 退出码 `2` | 从 `.env.lite.example` 生成或指定 `--env-file` |
| 本项目的数据库容器存在且 **healthy** | 退出码 `2` | 等它健康再升级：库不健康时备份与迁移拿到的是残缺快照 |
| 数据库扩展已启用（版本 ≥ 0.8） | 退出码 `4` | 见 6.3 节，**转 DBA** |
| 应用账号有建表权限 | 退出码 `5` | 应用账号被降权，转 DBA 处理 |
| `curl` 可用（冒烟要用） | 退出码 `2` | 安装 curl 后重跑 |

> 退出码 `2` 与 `4` 的区别很重要：`2` 是"环境有问题"（脚本/配置/容器），`4` 是"库缺能力，去找 DBA"。升级脚本刻意把这两类分开，避免把"连不上库"误报成"让 DBA 去建扩展"。

### 6.5 升级过程中发生了什么（可核对的清单）

1. **停入口**：先 `proxy` 后 `web`；脚本记录"本来在跑的服务"，退出时按同一名单拉回来。
2. `deploy/backup.sh` 生成整库备份到 `deploy/backups/<UTC 时间戳>/`，并留下指纹（各表行数 + 抽查行内容摘要）。备份失败 → **中止，不进入升级**。
3. 数据库变更以**一次性单实例任务**执行（带互斥锁，多副本同时升级只有一个真在改表；DDL 与台账在同一事务）。
4. **三项冒烟**（不是"端口开着就算好"）：

   | 探针 | 断言 | 失败含义 |
   | --- | --- | --- |
   | ① 就绪 | `/readyz` 返回 200 | 应用自检（库连接、隔离策略、缓存）未全绿 |
   | ② 结构 | 迁移台账非空，且**已登记到本次目标版本** | "DDL 跑完了"与"台账记上了"没有同时成立 |
   | ③ 服务 | `/metrics` 返回 200 且含请求计数 | 业务进程半死，只是网关还在转发 |

5. 任一步失败 → 自动调用回滚（第 7 章），末尾打印 `ROLLED_BACK`；全部通过 → 放流量、打印 `DONE`。
6. 全程写升级日志 `deploy/backups/upgrade.log`（运行时生成，放在 `backups/` 下是因为它已在忽略清单里，不会污染工作区）。**排障只认这个文件**，不看终端滚动输出。

### 6.6 交付包里缺少 `upgrade.sh` 时的手工等同路径

`deploy/upgrade.sh` 与 `deploy/rollback.sh` 属 V1.0 交付物。**若现场包内确实缺失，说明交付包不完整**，请先联系交付负责人补齐。若客户现场不具备等待条件，可按下列顺序手工完成同等动作（**不得跳过预检**）：

```bash
export OA_MAINTENANCE=1

# ① 预检：扩展是否存在（用应用账号即可读，不需要超级用户）
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite \
  exec -T postgres psql -U officeai -d officeai -tAc \
  "SELECT 1 FROM pg_extension WHERE extname='vector' AND extversion >= '0.8';"
# 无输出 → 按 6.3.3 让 DBA 执行，然后重跑本步

# ② 整库备份（失败必须中止，不要进入下一步）
bash deploy/backup.sh

# ③ 停入口
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite stop proxy web

# ④ 导入新镜像（离线）或按指纹拉取（在线）
docker load -i deploy/offline/images/<新版本镜像>.tar

# ⑤ 执行数据库变更（一次性单实例任务）
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite run --rm migrate

# ⑥ 起服务并做冒烟三项
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite up -d
curl -sf http://127.0.0.1/api/v1/readyz && echo READY
```

任一步失败 → 按第 7 章回退。**上述①②的顺序不可交换**：没有备份就不许停机。

---

## 7. 回滚

### 7.1 命令

```bash
# ① 先判定，不动任何东西
OA_MAINTENANCE=1 bash deploy/rollback.sh --dry-run 0008

# ② 停入口（回滚脚本**不会**替你停流量，它只管数据库）
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite stop proxy web

# ③ 执行回滚
OA_MAINTENANCE=1 bash deploy/rollback.sh 0008

# ④ 恢复入口
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite start web proxy
unset OA_MAINTENANCE
```

示例：

```bash
bash deploy/rollback.sh 0008             # 退掉 0008 这一个变更
bash deploy/rollback.sh 0009 0010 0011   # 可一次指定多个（倒序执行）
bash deploy/rollback.sh                  # 不给参数：退掉台账里最新的一条
```

> **回滚脚本是"薄封装"**：它只做三件事——确认维护模式、判定目标版本有没有自动回退脚本、把版本号交给迁移命令行工具。**反向脚本的执行、倒序、删台账、回滚前强制备份全部在迁移工具内部**。所以你不能指望它帮你停流量、也不能指望它"顺手"多退几个版本。

| 参数 | 用途 |
| --- | --- |
| `--dry-run` | 只判定目标版本能否自动回退，不执行任何反向脚本；打印 `DRY_RUN_OK` |
| `--env-file <路径>` | 指定环境文件 |
| `--project-name <名>` | 指定编排项目名 |

### 7.2 回滚的边界（必读）

| 目标版本 | 脚本行为 | 现场含义 |
| --- | --- | --- |
| `0006` 及以后 | 正常回退（倒序执行对应回退脚本 + 删台账行） | 能力级回退，数据不丢 |
| `0005` 及更早（含"退到上一大版本"） | **退出码 `7`** + 打印「该版本无自动回滚脚本，请 DBA 按 runbook 手工处理」 | **不会执行任何回退脚本**，需 DBA 介入 |

为什么早期版本没有自动回退脚本：那几条是历史基线（建表 + 隔离策略 + 产物列），为它们补写反向脚本等于给客户库里 30 多张表准备一套"删除脚本"——一旦误跑就是灾难性数据丢失，而它换来的能力（退回到更早的大版本）在 V1.0 交付场景里没人会用到。**风险与收益不成比例，所以刻意不提供。**

- **不自动级联**：`rollback.sh 0008` 只退 `0008`，不会顺手把 `0006`/`0007` 一起退。要退多层必须显式列全。
- **回退前强制备份**：脚本会先做一次整库导出；缺备份工具的机器上直接拒绝回滚（而不是"尽力而为"）。对会删数据的变更，回退前还会单独把相关表导出到 `rollback/<时间戳>/`，否则拒绝执行。

### 7.3 目标版本低于 `0006` 时怎么办

1. 脚本以退出码 `7` 停止，库未被改动。
2. DBA 按本章与第 11 章，从对应时间点的备份做**整库恢复**（这是唯一安全路径）。
3. 恢复后核对：`bash deploy/restore.sh` 的行数校验必须通过（退出码 `0`；`1` = 恢复报错，`3` = 行数/内容对不上，均禁止放流量）。
4. 若客户坚持"只退功能不退数据"，属于定制事项，走交付变更单，不在本手册范围。

### 7.4 回滚后必须验证的四项

```bash
curl -s http://127.0.0.1/healthz                                   # ① ok
curl -s http://127.0.0.1/api/v1/readyz                             # ② ready
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps   # ③ 9 个服务健康
# ④ 登录 → 打开一个历史会话 → 消息逐字完整（这是"数据没丢"的唯一用户可见证据）
```

---

## 8. 维护窗口与变更纪律

### 8.1 维护闸门的真实语义（别误解）

```bash
export OA_MAINTENANCE=1
```

- 它对**流量没有任何拦截作用**——把它当成"应用会返回 503"是错的，平台里没有任何代码读这个变量。
- 它的真实作用是**意图声明 + 误操作防护**：防止半夜手滑直接跑回滚。未设置时，回滚/恢复脚本以退出码 **`3`** 退出，**一个容器都不动**。
- **真正拦流量的是停入口**：

  ```bash
  docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite stop proxy web
  ```

  先停入口（`proxy`）再停业务（`web`）；恢复时反序（先 `web` 后 `proxy`）。
- 为什么不靠应用的"就绪探针"来拦流量：编排层停入口不依赖应用健康状态，应用已经挂了也照样能停。

### 8.2 变更纪律清单

| 纪律 | 理由 |
| --- | --- |
| 任何 DDL（表结构变更）必须走迁移脚本，禁止手工 `ALTER` | 手工改动不进台账，下次升级判定为"基线被改"（退出码 6） |
| 任何升级前必须有当日可用备份 | 回滚到版本下限时只能靠备份 |
| 升级窗口内不同时改配置与改版本 | 出问题无法区分是版本还是配置 |
| 不手工编辑 `deploy/.installed-manifest.json` 与迁移台账表 | 它们是升级判定的唯一基准 |
| 变更前后各记录一次 `docker compose ps` 输出 | 服务数量异常是最早的故障信号 |

---

## 9. 备份与恢复

### 9.1 备份

```bash
bash deploy/backup.sh                              # 备份到 deploy/backups/<UTC 时间戳>/
bash deploy/backup.sh --out-dir /data/backup       # 换备份根目录（建议放独立磁盘）
bash deploy/backup.sh --artifacts-dir /srv/artifacts   # 额外打包产物文件目录
```

产物：

| 文件 | 内容 |
| --- | --- |
| `db.dump` | 整库导出（含产物二进制） |
| `meta.json` | 版本、镜像、各表行数、导出文件摘要（给人看，也给升级脚本读） |
| `meta.counts.tsv` | 表 → 行数（恢复时逐表比对） |
| `meta.samples.tsv` | 表 / 租户 / 主键 / 行内容摘要（恢复时抽查） |
| `artifacts.tar` | 仅当显式给了 `--artifacts-dir` 且目录存在 |

退出码：`0` 成功；`1` 备份失败（**不会留下半份备份**——整份目录以 `.partial` 结尾，成功后原子改名）；`2` 前置检查未通过（缺命令、环境文件缺失、库不可达）。

> 为什么先写 `.partial` 再改名：备份写到一半被打断（磁盘满、Ctrl-C、SSH 断）时，一个"看起来完整"的目录比没有目录危险得多——恢复时会被当成可用备份。

备份策略建议：**每日一次全量 + 保留 7 天 + 每月一份归档**。备份文件与数据库**不要放同一块盘**。

### 9.2 恢复

```bash
OA_MAINTENANCE=1 bash deploy/restore.sh --from deploy/backups/20261023T035500Z
OA_MAINTENANCE=1 bash deploy/restore.sh                    # 不给 --from 时用最新一份
OA_MAINTENANCE=1 bash deploy/restore.sh --from <dir> --stop-apps   # 抢不到锁时
```

退出码（**`2` 与 `3` 的区别决定下一步**）：

| 码 | 含义 | 现场状态 |
| --- | --- | --- |
| `0` | 恢复成功，服务已重新拉起 | 可放流量 |
| `1` | 恢复执行失败 | **服务保持停止** |
| `2` | 前置检查未通过（最常见：没设 `OA_MAINTENANCE=1`） | **一个容器都没动** |
| `3` | 恢复了但对不上（行数丢失 / 抽查内容不一致） | **服务保持停止，禁止放流量** |

恢复动作的真实顺序：先停入口 → 断开其它服务对库的连接（否则恢复时拿不到表锁，表现为脚本卡住不动）→ 恢复前先把当前库再导一份（防止恢复错文件后无退路）→ 恢复 → 逐表行数核对 + 抽查内容 → 通过后才起服务。

> **核对失败为什么不自动重启服务**：半恢复的库比停机的库危险得多——业务能连上、能读到缺失的数据、还能继续写，而运维以为恢复成功了。宁可让客户看到"服务不可用"，也不要看到"服务正常但数据少了"。

---

## 10. 轻量版与全量版的差异

| 维度 | 轻量版 `deploy/docker-compose.lite.yml` | 全量版 `deploy/docker-compose.yml` |
| --- | --- | --- |
| 常驻服务数 | **9** | 17（含独立检索服务三件套、对象存储、两个探针 exporter、监控） |
| 数据库 | 单个数据库实例，业务/身份/插件分库 | 同一实例 + 独立检索服务集群 + 对象存储 |
| 内存门槛 | 8 GiB（实测空载 1.93 GiB） | 建议 16 GiB 起 |
| 磁盘 | 60 GiB 起 | 200 GiB 起（检索服务与对象存储另计） |
| 适用规模 | 单机、中小团队、单租户（私有化） | 多租户（SaaS）、较大并发、需要独立扩缩容 |
| 监控 | 不内置 | 内置指标采集 + 5 条告警规则（`deploy/prometheus.yml`、`deploy/alert.rules.yml`） |
| 入口端口 | 只暴露 1 个 | 业务与前端 2 个；基础设施一律绑回环 |
| 证书 | 默认由客户前置网关终止（形态 A）；可选平台自建 TLS 终止（形态 B，轮 2 交付） | 同左 |

轻量版的**已知限制**（是取舍，不是缺陷）：

1. 不含代码沙箱：Dify 工作流里的"代码执行"节点不可用；平台自研工具走适配层，不受影响。
2. 不含统一出站代理：私有化出站应由网络策略（安全组/防火墙）控制，那才是这类部署真正的边界。
3. Dify 管理控制台默认不启动（`profiles: [admin]`，且只发布到 `127.0.0.1`）。
4. 对象存储用本地卷：**不适用于多机部署**。
5. 不含周期清理任务：历史数据清理需按第 13 章"季度手工清理"执行。

选择建议：单机私有化交付一律用轻量版；只有客户明确要求多租户 SaaS 或需要独立扩缩容时才上全量版。**两种形态的性能数字不可互相引用**（见《性能披露》）。

---

## 11. 升级/回滚用的 DBA 脚本

| 脚本 | 何时用 | 谁执行 |
| --- | --- | --- |
| `sql/enable-vector-extension.sql` | 存量升级前启用检索扩展（第 6.3 节） | DBA（超级用户） |
| `sql/retention-audit.sql` | 操作记录保留期清理（应用侧无权限，必须 DBA 执行） | DBA（库属主） |
| `sql/tenant-export-manual.sql` | 私有化环境下按客户要求导出全库 | DBA |
| `sql/full-export.sql` | 全库导出（迁移/下线场景） | DBA |
| `sql/full-purge.sql` | 全库清空（**危险操作**，需双人复核 + 维护窗口 + 已确认备份） | DBA |

> 操作记录表的保留期清理**必须交给 DBA**：应用账号对该表无删除权限。清理任务运行时会逐字输出 `audit_log=skipped(no privilege)`——这一行是"应用侧知道自己没权限"的唯一证据，**不要把它当报错删掉**。

---

## 12. 常见故障排查（12 条）

### 12.1 入口 80 端口被占用

**现象**：安装退出码 `2`，提示端口被占。
**定位**：
```bash
ss -ltnp | grep ':80 '        # Linux
netstat -ano | grep ':80 '    # Windows
```
**处置**：换端口重装（只影响新生成的环境文件）：
```bash
bash deploy/install.sh --online --proxy-port 8080
```
不要手改编排文件的端口映射——下次升级会以"编排文件摘要与台账不符"中止。

### 12.2 磁盘不足（备份或升级中途失败）

**现象**：`backup.sh` 退出码 `1`；或升级在备份阶段失败；日志出现 `No space left on device`。
**定位**：
```bash
df -h /var/lib/docker
docker system df
du -sh deploy/backups/*
```
**处置**：删旧备份（保留最近 3 份 + 一份归档），清理悬空镜像 `docker image prune -f`。**不要用 `docker system prune -a`**——它会把正在使用的镜像一起删掉，重启服务直接失败。

### 12.3 升级中止，退出码 4（缺数据库扩展）

**现象**：升级退出码 `4`，打印"检测到数据库尚未启用「记忆检索」所需的扩展"。
**处置**：让 DBA 执行第 6.3.3 节的语句，然后重跑升级。**注意：换镜像解决不了这个问题**——镜像换了不等于扩展装了。

### 12.4 退出码 6（现场基线被改过）

**现象**：升级退出码 `6`。
**处置**：见第 6.3.6 节。恢复初始化脚本原始版本，或换成与台账成对版本的交付包。

### 12.5 证书 / HTTPS 相关

**现象**：浏览器提示不安全；登录后立刻跳回登录页；或登录成功但业务接口全部 401。

#### 12.5.1 先确认当前是哪种形态（两种并存）

| 形态 | 何时选它 | 平台开放端口 | TLS 由谁终止 | 生效时点 |
| --- | --- | --- | --- | --- |
| **A. 前置网关终止（推荐）** | 客户已有 LB / 网关 / WAF，或需要与企业既有证书体系统一 | 由前置网关发布（平台侧可只发布 80） | 客户前置 LB / 网关 | 需自备 HTTP-only 边缘配置（V1.0 未内置，见下） |
| **B. 平台自建 TLS 终止** | 私有化现场确实没有 LB，又必须对外提供 HTTPS | 80 + 443 | 平台自身（`edge` + `nginx/edge.conf`） | **已交付**，随主 compose 的 `edge` 默认启动 |

**默认推荐 A**，理由不是省事，是**责任边界**：证书生命周期由客户既有体系管理，平台不持有私钥，轮换不需要动平台；出现证书问题时责任方一目了然。只有"现场确实没有 LB 且必须 HTTPS"时才选 B。

> 交付现状：B 形态已并入主 compose 的 `edge`（`nginx:1.28-alpine`，同时监听 80 与 443：80 只做探活与 301 跳转，443 终止 TLS 后承载全部路由）。`docker-compose.tls.yml` 与 `edge-tls.conf` **不再需要，也未创建**。
> A 形态不在默认交付内：`edge.conf` 固定监听 443 并要求证书文件存在，若由前置网关单独终止 TLS，需自备一份 HTTP-only 边缘配置（或去掉 443 端口映射并替换 `edge.conf`）。

#### 12.5.2 B 形态（平台自建 TLS）的证书约定

- 证书放 `deploy/nginx/tls/fullchain.pem` 与 `deploy/nginx/tls/privkey.pem`；`chmod 600 privkey.pem`。
- `deploy/nginx/tls/` 已在 `.gitignore` 内：**证书不入库、不进镜像**。
- 启动：`docker compose -f deploy/docker-compose.yml up -d`（`edge` 已包含在主 compose 中，无需覆盖文件）。
- 轮换证书后只需 `docker compose exec edge nginx -s reload`，**不要重启容器**（会掐断进行中的 SSE 流）。
- 自签证书**仅限验证用途**，必须导入客户端信任链，否则用户看到证书告警——这一条要写进验收单，避免现场把"证书告警"当成"部署失败"。

#### 12.5.3 `OA_PUBLIC_BASE_URL` 决定 Cookie 的 `Secure`（唯一判定处）

登录态承载在 httpOnly Cookie 上，它的 `Secure` 属性**只由 `OA_PUBLIC_BASE_URL` 的 scheme 决定**：

| `OA_PUBLIC_BASE_URL` 取值 | Cookie `Secure` | 适用 |
| --- | --- | --- |
| `https://<域名>` | **设** | A 形态与 B 形态都属于此类——只要**对外**是 HTTPS 就必须设 |
| `http://<域名或IP>` | 不设 | 纯内网 HTTP；浏览器接受，但 Cookie 明文传输 |

三条必须记住的：

1. **判定只读配置、不读请求头**。平台**不采信** `X-Forwarded-Proto` 来决定 `Secure`——否则任何人伪造该头就能让服务端下发或不带 `Secure`。所以 A 形态下即使容器内部看到的是 `http`，`OA_PUBLIC_BASE_URL` **也必须写成 `https://`**。
2. **它是单一真源**：同一个值派生 `KC_HOSTNAME`、`OA_WEB_OIDC_ISSUER`、`OA_JWT_ISSUER`。**只改它一处**，不要单独去改后三个——那三个差一个字符（多/少尾斜杠）就是"所有接口 401"。
3. **留空或写成 `http://localhost` 会让登录页跳回用户自己的机器**。标准栈因此配置为 `docker compose up` 时**直接拒绝启动**（有意为之）：失败要发生在部署那一刻，不能发生在用户点登录那一刻。

> 轻量版无需手工设：它沿用既有的 `OA_LITE_PUBLIC_URL`（编排里插值过去），改端口时仍只用 `install.sh --proxy-port`。

#### 12.5.4 按现象的处置

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 浏览器提示不安全 | 证书链或域名不匹配 | 确认 `OA_PUBLIC_BASE_URL` 与证书域名一致；A 形态还要确认网关已换好证书 |
| 登录后立刻跳回登录页 | A 形态下网关未透传 `X-Forwarded-Proto: https`；或 `OA_PUBLIC_BASE_URL` 写成了 `http://` | 网关补 `X-Forwarded-Proto: https`；把 `OA_PUBLIC_BASE_URL` 改成 `https://` 后重启适配层 |
| **登录成功但业务接口全部 401** | **不是证书问题** | 属令牌校验链路（签名算法 / issuer / aud 不匹配），见 `docs/design-2026-11-02-keycloak-realm.md` §2 |
| 纯内网只有 HTTP | 无 TLS | 预期行为：会打 `cookie_secure_disabled` 启动告警，**属告警不属故障**；同时需调整前端安全头的 `connect-src`（见《安全白皮书》已知边界） |
| 443 端口起不来（B 形态） | 证书文件缺失或权限不对 | `docker compose exec edge nginx -t` 先行验证；确认两个 pem 都已挂载且 `privkey.pem` 为 600 |

### 12.6 时区 / 时钟漂移

**现象**：审计时间对不上；登录提示"登录状态已失效"。
**定位**：`timedatectl status`、`date -u`（与 `date` 比较）。
**处置**：开启 NTP（`timedatectl set-ntp true`），偏差 > 5 分钟必须校正。数据存储统一 UTC，界面按浏览器时区展示，不要改容器时区来"对齐显示"。

### 12.7 健康检查不通过（`/readyz` 长期非 ready）

**定位顺序**（不要一上来就重启）：
```bash
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite logs --tail=100 adapter
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite logs --tail=50 postgres
```
**常见根因**：
| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 数据库容器反复重启 | 数据目录挂载路径错误（PG 18+ 必须挂 `/var/lib/postgresql`，不是 `/var/lib/postgresql/data`） | 修正编排挂载点后重建 |
| 应用启动即退出，日志有配置类报错 | 必填配置缺失（如凭据加密密钥） | 补齐配置后重启 |
| 插件服务在启动窗口崩溃 | 数据库尚未真正就绪（初始化窗口内探针误判为就绪） | 等数据库健康检查通过后再起业务，不要在窗口内重启插件服务 |
| 内存不足 | 引擎可用内存 < 8 GiB | 扩容或调高 Docker 内存上限 |

### 12.8 服务数量不是 9

**现象**：`docker compose ps` 显示 8 个或更多。

**口径先说清楚**：常驻服务是 **9 个**（`proxy` / `adapter` / `web` / `postgres` / `valkey` / `keycloak` / `dify-api` / `dify-worker` / `plugin-daemon`）。另有两个"非常驻"：`dify-web`（管理控制台，默认不启动）与 `migrate`（一次性任务，跑完即退），**两者都不计入这 9 个**。

| 现象 | 含义 | 处置 |
| --- | --- | --- |
| **9 个** | 正常 | — |
| **8 个** | 少了 1 个常驻服务 | 逐个核对上表 9 个名字，缺哪个看它的 `logs` 与状态 |
| **10 个及以上** | 有人手动起了 `dify-web`（`--profile admin`）或其它调试服务 | 一般无害；确认不是误操作后可用 `docker compose stop <服务名>` 收掉 |
| 数量在 8↔9 之间反复跳 | 某个服务反复重启（最典型是内存不足） | 看 `docker ps -a` 的 `Restarting` 状态与宿主内存 |

### 12.9 容器名冲突（同一台机器装两套）

**现象**：`Conflict. The container name "/oal-valkey" is already in use`。
**原因**：编排里容器名是写死的，换项目名不会改变容器名（只有卷和网络会加前缀）。
**处置**：一台机器只装一套。要并行测试请用不同物理机/虚拟机。

### 12.10 上传大文件失败

**现象**：上传 50 MB 左右文件报错或浏览器直接 413。
**原因**：网关的上传上限与平台的上传上限**必须一致**（当前均为 50 MB）。网关小而平台大 → 用户拿到纯文本 413，前端会异常。
**处置**：核对网关配置的 `client_max_body_size` 与配置项 `OA_MAX_UPLOAD_BYTES` 一致。

### 12.11 工具显示"尚未连接"或"授权已过期"

**现象**：任务里工具调用失败，提示"该工具尚未连接，请先在设置中完成授权"。
**处置**：到设置页重新连接该工具。若刚做完凭据轮换，属预期现象——旧授权密文需要按《密钥轮换清单》的步骤处理。

### 12.12 升级后界面文案出现内部术语

**现象**：某个错误提示里出现组件名/参数名。
**处置**：属缺陷。请把截图与时间点反馈给交付负责人，并附 `deploy/backups/upgrade.log` 对应时段。平台对外的错误提示必须全部是业务语言。

---

## 13. 例行运维（季度）

| 周期 | 事项 | 命令/位置 |
| --- | --- | --- |
| 每日 | 备份成功校验 | `ls -lt deploy/backups/ \| head`；看最新目录是否有 `db.dump` 与 `meta.json` |
| 每日 | 服务健康 | `docker compose ps` 全 healthy |
| 每周 | 磁盘水位 | `df -h`；使用率 > 80% 提前扩容 |
| 每月 | 备份可恢复性抽验 | 在测试机执行 `restore.sh`，核对退出码 `0` |
| 每季度 | 操作记录保留期清理 | DBA 执行 `sql/retention-audit.sql` |
| 每季度 | 历史数据清理（轻量版无周期任务） | 按数据保留期配置逐表清理，见第 13.1 节 |
| 每季度 | 凭据轮换 | 按《密钥轮换清单》逐项执行 |
| 每年 | 离线包与依赖清单复核 | 核对 `deploy/images.lock` 与客户安全评审结论 |

### 13.1 数据保留期（合同条款，必须与配置一致）

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `OA_RETENTION_AUDIT_DAYS` | 180 天 | 操作记录保留期（**DBA 执行清理**） |
| `OA_RETENTION_USAGE_MONTHS` | 13 个月 | 用量明细保留期 |
| `OA_RETENTION_TASK_DAYS` | 180 天 | 任务与产物保留期 |
| `OA_RETENTION_CONVERSATION_DAYS` | 365 天 | 会话保留期 |
| `OA_CONVERSATION_SOFT_DELETE_RECOVER_DAYS` | 30 天 | 会话删除后可恢复期 |

> 这些值**属于合同条款的一部分**，只能改配置，不得改代码调参。改动前须与客户确认，并同步更新《隐私政策》的对应章节。

---

## 14. 交付物核对表

| 交付物 | 路径 | 用途 | 核对方式 |
| --- | --- | --- | --- |
| 安装脚本 | `deploy/install.sh` | 全新部署 | `bash deploy/install.sh --help` |
| 编排放（轻量） | `deploy/docker-compose.lite.yml` | 单机 9 服务 | `docker compose -f ... config -q` |
| 编排（全量） | `deploy/docker-compose.yml` | 多租户/大并发 | 同上 |
| 编排（SaaS 联调） | `deploy/docker-compose.saas.yml` | SaaS 形态本地联调 | 同上 |
| 配置样例 | `deploy/.env.lite.example` | 生成现场配置 | 与现场 `deploy/.env.lite` 逐项比对 |
| 镜像清单 | `deploy/images.lock` | 指纹基准 | `grep -c "digest:"` |
| 备份 / 恢复 | `deploy/backup.sh` / `deploy/restore.sh` | 第 9 章 | `bash deploy/backup.sh --help` |
| 升级 / 回滚 | `deploy/upgrade.sh` / `deploy/rollback.sh` | 第 6、7 章 | `bash deploy/upgrade.sh --help`、`bash deploy/rollback.sh --help`；若包内缺失，按第 6.6 节手工路径执行，并联系交付负责人补齐 |
| DBA 脚本 | `sql/enable-vector-extension.sql` 等 5 份 | 第 6、11、13 章 | `ls sql/` |
| 自检脚本 | `deploy/validate_lite_up.sh`、`deploy/validate_secrets.py`、`deploy/validate_images.py`、`deploy/validate_packaging.py` | 交付前自检 | 逐个 `--help` |
| 组件清单 | 包内 `sbom.cdx.json` | 客户安全评审 | `jq '.components \| length'` > 0 |
| 合规三件套 | `docs/compliance/privacy-policy.md`、`docs/compliance/dpa-template.md`、`docs/compliance/security-whitepaper.md` | 法务/安全评审 | 人工审阅 |
| 用户手册 | `docs/user-guide.md` | 交付培训 | 人工审阅 |
| 性能披露 | `docs/perf-disclosure.md` | 容量规划 | 人工审阅（**只认实测产物**） |
| 密钥轮换 | `docs/key-rotation-checklist.md` | 第 13 章例行轮换 | 人工审阅 |

---

## 15. 获取支持

排障信息三件套，一次性备齐再联系支持，能省一半往返：

1. `deploy/install.log` 或 `deploy/backups/upgrade.log`（**完整文件**，不要截图；两者均在安装/升级时生成）
2. `docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps` 输出；
3. 出问题的时间点（含时区）+ 操作步骤 + 浏览器/终端截图。

> 提交日志前请自查：日志里不得包含客户真实口令、连接串中的密码、工具授权凭证。平台自身不打印这些内容；若发现打印，属安全缺陷，请立即上报。

---

## 16. 对外暴露面收口（G1，2026-11-02）

审计 2026-11-02 P0-4 的收口口径。三套编排（full / saas / lite）统一如下：

### 16.1 收敛后的对外暴露面（唯一对外通道 = edge/proxy 的 443）

| 端口 | 归属 | 绑定 | 说明 |
| --- | --- | --- | --- |
| **443/tcp** | edge / proxy | `0.0.0.0`（对外唯一入口） | TLS 终止 + 全部路由（`/`、`/api/`、`/sso/`、`/files/artifacts/`、`/realms/`、`/resources/`） |
| 80/tcp | edge / proxy | `0.0.0.0` | **只做两件事**：`/healthz` 探活（compose/k8s 用）+ 其余 301 跳 https |
| adapter 8000 | 应用 | **127.0.0.1**（仅本机调试） | 不对外；绕过边缘 = 绕过限流/安全头/证书 |
| web 3000 | 应用 | **127.0.0.1**（仅本机调试） | 同上 |
| postgres/valkey/milvus/对象存储/etcd 等 | 基础设施 | 127.0.0.1 或不出网 | milvus 19530、对象存储 8333、无鉴权 admin 23646 均绑回环（ARC-01，审计 2026-10-31 H3） |
| keycloak 8080 | 身份底座 | **不映射宿主端口** | 浏览器经边缘 `^~ /realms/`、`^~ /resources/` 同源访问 |

### 16.2 本地 / 单机怎么起

```bash
# 1) 生成边缘 TLS 自签证书（**仅限验证**；生产以真实证书覆盖同名文件）
sh deploy/gen-selfsigned-cert.sh           # 产物 deploy/nginx/tls/{fullchain,privkey}.pem

# 2) 按既有安装流程起栈（以轻量版为例；full/saas 同理）
cp deploy/.env.lite.example deploy/.env.lite   # 填齐必填项
docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite up -d

# 3) 访问入口
#    https://<host>/          （浏览器告警属预期：自签证书仅限验证）
#    http://<host>/           → 301 跳 https
```

证书挂载口径与 `lite-tls.conf` 逐字一致：容器内固定读 `/etc/nginx/tls/{fullchain,privkey}.pem`，
宿主侧由 compose 从 `deploy/nginx/tls/` 只读挂入（**不入镜像、不入库**，`.gitignore` 已含 `*.pem`）。

### 16.3 如何验证（可复核断言，全部应为真）

```bash
# A. 端口收口：除 edge/proxy 的 80/443 与 127.0.0.1 绑定外，不得有裸绑全网卡的端口
docker compose -f deploy/docker-compose.yml config | grep -E 'published|target'   # published 只应出现 80/443 与 127.0.0.1 前缀
#   断言：grep -c '"8000:8000\|"3000:3000' deploy/docker-compose*.yml  → 0

# B. 明文跳转与 TLS 终止
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1/       # 301 + https://…
curl -sk https://127.0.0.1/healthz                                                # ok（-k 仅因自签）
curl -skI https://127.0.0.1/ | grep -i strict-transport-security                  # HSTS 在

# C. 门禁（仓库既有，全部 EXIT=0）
python deploy/validate_deploy.py && python deploy/validate_secrets.py
```

k8s 形态（`deploy/k8s/base/`）与上述口径一致：Service 仅 ClusterIP、不对外；
对外走 Ingress（TLS 用 Secret 引用）；密钥全部走 `officeai-secrets`（由
`kubectl create secret --from-env-file=deploy/.env` 生成，禁止入库）。清单定位仍是
**V1.1 准备物**（V1.0 以 compose + 边缘上线），详见 `deploy/k8s/README.md`。
