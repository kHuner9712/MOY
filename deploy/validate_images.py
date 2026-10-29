#!/usr/bin/env python3
"""基础设施镜像清单校验（P0 门禁，ADR-0002 §2.4）。

五道校验：
1. **一致性**：编排文件中的每个 `image:` 引用必须能在 `deploy/images.lock`
   中找到 `name:tag` 精确匹配项；
2. **精确标签**（R2-ARC-14）：禁止浮动别名（`latest`/`main`/`master`/…）与
   **只有大版本、没有小版本**的滚动标签（`pg18`、`1`、`2` 之类）——
   `pg18` 只约束 PG 大版本，PG 小版本与 pgvector 扩展版本都随上游滚动，
   「同一 tag 在不同时间拉到不同制品」与 `latest` 是同一类缺陷；
3. **digest 登记**：非 `local` 源必须登记合法 `sha256:` digest（离线包对账依据）；
4. **文档口径同源**（R2-ARC-04）：`docs/architecture-v1.0.md` 里出现的
   基础设施组件版本号（Valkey / SeaweedFS）必须与 `images.lock` 的 tag 一致——
   设计文档写 7.2、锁文件是 9.2 这种"两处各说一套"的漂移，此前没有任何机械校验；
5. **存在性**（可选，`--check-remote`）：对非 `local` 源的镜像做上游存在性核验，
   杜绝「日期与时间戳交叉拼接」的合成标签。

用法：
    python deploy/validate_images.py              # 一致性 + 精确标签 + digest + 文档口径
    python deploy/validate_images.py --check-remote   # 追加上游存在性核验

退出码：0 = 通过；1 = 不通过。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

import yaml

#: 部署目录
DEPLOY_DIR = Path(__file__).resolve().parent

#: 镜像锁文件
LOCK_FILE = DEPLOY_DIR / "images.lock"

#: 编排文件通配
COMPOSE_GLOB = "docker-compose*.yml"

#: 架构文档（设计侧口径；与锁文件交叉校验，R2-ARC-04）
ARCH_DOC = DEPLOY_DIR.parent / "docs" / "architecture-v1.0.md"

#: 浮动标签黑名单（R2-ARC-14）。`stable`/`edge`/`mainline` 同理：指向"当前"
#: 而不是"某一份制品"，与 `latest` 是同一类缺陷。
FLOATING_TAGS: Final[frozenset[str]] = frozenset(
    {
        "latest",
        "main",
        "master",
        "stable",
        "edge",
        "mainline",
        "dev",
        "develop",
        "snapshot",
        "nightly",
        "current",
    }
)

#: 精确标签正则：必须至少 `主版本.次版本`（R2-ARC-14）。
#:
#: 为什么卡在"两个数字段"而不是"三个"：各上游的发版粒度不同——postgres 生态发
#: `0.8.7-pg18-bookworm`（三段）、valkey 发 `9.2-alpine`（两段）、seaweedfs 发
#: `4.48`（两段）。三段硬标准会把这些全部判红，门禁立刻变成噪声。
#: 而 `pg18` 这类**只有大版本**的滚动标签必须拦住：它不约束小版本，
#: 上游一推新 minor，同一 tag 就换了制品（本次审计 PG 18.x 正是如此）。
EXACT_TAG_PATTERN = re.compile(r"^v?[0-9]+\.[0-9]+")

#: 精确标签规则的**显式豁免**（`name -> 理由`）。
#:
#: 每条豁免都是一笔技债，必须写清"为什么现在不能改"，避免豁免表变成抽屉。
TAG_EXCEPTIONS: Final[dict[str, str]] = {
    "node": (
        "Node 官方 alpine 线只按大版本发 `22-alpine`；改 `22.x.y-alpine3.2x` 会绑定"
        "具体 alpine 小版本，牵连前端构建与原生模块编译，需单列重建+复测，"
        "本波（R2-ARC-14）不扩大改动面；digest 已登记，tag 漂移仍可检测"
    ),
}

#: 文档口径校验目标：架构文档里出现该显示名时，紧随其后的版本号必须与锁文件同源。
#: 键 = 文档里的人类可读名，值 = images.lock 里的镜像名。
DOC_VERSION_TARGETS: Final[dict[str, str]] = {
    "Valkey": "valkey/valkey",
    "SeaweedFS": "chrislusf/seaweedfs",
    "PostgreSQL": "pgvector/pgvector",
}

#: 版本锚点**无法从 tag 推出**的组件：值 = images.lock 条目里承载文档版本号的字段名。
#:
#: 为什么 PG 需要单列：tag `0.8.7-pg18-bookworm` 里只有 `pg18`（大版本），
#: 而架构文档写的是 `PostgreSQL 18.6`。硬拿 tag 比对会永远对不上（假红），
#: 拿 `docker` 现场查又在纯静态 CI 里不可用。故由锁文件显式登记 `doc_version`，
#: 并且**要求与本条目 note 里的 `PostgreSQL X.Y` 二次互证**——两处都在锁里、
#: 各自手写，改一处就红，不会退化成"注释和字段一起漂"。
DOC_VERSION_FIELDS: Final[dict[str, str]] = {
    "PostgreSQL": "doc_version",
}

#: 从 note 里抠版本号用的正则模板（`{display}` 由校验目标名替换）。
DOC_VERSION_NOTE_PATTERN: Final[str] = r"{display}\s+v?([0-9]+(?:\.[0-9]+)*)"

# MinIO 已于 2026-10-21 整体替换为 SeaweedFS（Apache-2.0，ADR-0002 v1.3 /
# docs/decisions/0004）：minio/minio 仓库在 Docker Hub 已整体下架，
# 原「锁 AGPL 前版本线 RELEASE.2021-04-22T15-44-28Z」常量随之移除；
# 该标签仍保留在下方负样本自测中，用于验证门禁对「已下架仓库」确实判 FAIL。

#: Docker 官方镜像（Docker Official Images）在 Hub 上位于 library/ 命名空间，
#: 但可省略前缀引用。校验存在性时必须归一化，否则官方镜像会被误判为「不存在」。
DOCKER_OFFICIAL_IMAGES = {
    "postgres",
    "redis",
    "nginx",
    "python",
    "node",
    "ubuntu",
    "alpine",
    "busybox",
    "hello-world",
    "mongo",
    "mysql",
    "traefik",
}


def _normalize_repo(name: str) -> str:
    """归一化镜像仓库名：Docker 官方镜像补全 ``library/`` 命名空间。

    Docker Hub 的官方镜像实际托管在 ``library/<name>``，但允许省略前缀引用。
    直接查询 ``/v2/repositories/postgres/...`` 会返回 404，被误判为「标签不存在」。

    Args:
        name: 镜像名（可能为短名或已含命名空间）。

    Returns:
        str: 归一化后的仓库名。
    """
    if "/" not in name and name in DOCKER_OFFICIAL_IMAGES:
        return f"library/{name}"
    return name


#: 模板标签解析：${VAR:-default}
TEMPLATE_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}")


def resolve_tag(tag: str) -> str:
    """解析镜像标签中的模板语法。

    Args:
        tag: 原始标签，可能是 `${OA_IMAGE_TAG:-1.0.0}` 形态。

    Returns:
        str: 解析后的标签（取模板默认值）。
    """
    match = TEMPLATE_PATTERN.search(tag)
    return match.group(1) if match else tag


def load_lock() -> dict[str, dict[str, str]]:
    """加载镜像锁文件。

    Returns:
        dict[str, dict[str, str]]: 镜像名到其元信息的映射。

    Raises:
        SystemExit: 锁文件缺失或格式非法时退出。
    """
    if not LOCK_FILE.is_file():
        print(f"[FAIL] 缺少镜像锁文件：{LOCK_FILE}")
        raise SystemExit(1)
    data = yaml.safe_load(LOCK_FILE.read_text(encoding="utf-8"))
    images = (data or {}).get("images")
    if not isinstance(images, list) or not images:
        print("[FAIL] images.lock 缺少 images 列表")
        raise SystemExit(1)
    return {
        str(entry["name"]): entry for entry in images if isinstance(entry, dict) and "name" in entry
    }


def split_image(image: str) -> tuple[str, str] | None:
    """拆分镜像引用为 `(镜像名, 标签)`。

    注意：镜像名本身可能含 registry 前缀与端口（如 `quay.io/coreos/etcd`），
    标签与镜像名之间才用第一个冒号分隔；不能直接用 `rsplit(":", 1)`，
    否则 `officeai/adapter:${OA_IMAGE_TAG:-1.0.0}` 会在模板冒号处被误切。

    Args:
        image: 镜像引用字符串。

    Returns:
        tuple[str, str] | None: 拆分结果；无标签时返回 None。
    """
    match = re.match(r"^(?P<name>[^:]+):(?P<tag>.+)$", image)
    if not match:
        return None
    name = match.group("name")
    tag = match.group("tag")
    if "@sha256:" in tag:
        # digest 锁定形式，无需比对 tag
        return None
    return name, resolve_tag(tag)


def collect_compose_references() -> dict[tuple[str, str], str]:
    """收集编排文件中的全部镜像引用。

    Returns:
        dict[tuple[str, str], str]: `(镜像名, 标签)` 到来源说明的映射。
    """
    references: dict[tuple[str, str], str] = {}
    for compose in sorted(DEPLOY_DIR.glob(COMPOSE_GLOB)):
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        services = (data or {}).get("services", {})
        for service_name, service in services.items():
            image = (service or {}).get("image")
            if not image:
                continue
            parsed = split_image(str(image))
            if parsed is None:
                continue
            references[parsed] = f"{compose.name} / {service_name}"
    return references


def collect_dockerfile_froms() -> dict[tuple[str, str], str]:
    """收集全部 Dockerfile 的 ``FROM`` 基础镜像引用（ENG-04）。

    Returns:
        dict[tuple[str, str], str]: ``(镜像名, 标签)`` 到来源说明的映射。
    """
    from_pattern = re.compile(r"^FROM\s+([\w./:-]+)(?:@sha256:[0-9a-f]+)?(?:\s+AS\s+\w+)?\s*$")
    references: dict[tuple[str, str], str] = {}
    for dockerfile in sorted(DEPLOY_DIR.parent.glob("apps/*/Dockerfile")):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("FROM"):
                continue
            match = from_pattern.match(line)
            if match is None:
                continue
            parsed = split_image(match.group(1))
            if parsed is None:
                continue
            references[parsed] = f"{dockerfile.name} / FROM {match.group(1)}"
    return references


def check_consistency(
    locked: dict[str, dict[str, str]], references: dict[tuple[str, str], str]
) -> list[str]:
    """校验编排引用 ⊆ 锁文件登记。

    Args:
        locked: 锁文件映射。
        references: 编排引用集合。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    for (name, tag), source in sorted(references.items()):
        entry = locked.get(name)
        if entry is None:
            failures.append(f"{source}: {name}:{tag} 未在 images.lock 中登记")
        elif str(entry.get("tag")) != tag:
            failures.append(
                f"{source}: {name}:{tag} 与 images.lock 登记的 {name}:{entry.get('tag')} 不一致"
            )
    return failures


def _registry_pull_probe(
    registry: str, repo: str, tag: str, auth_url: str | None = None
) -> bool | None:
    """按**真实 docker pull 路径**探测镜像可拉取性。

    流程：取 registry token → 请求 manifest。这与 `docker pull` 实际走的路径一致，
    因此能发现「仓库已下架但 Hub API 仍返回元数据」的情况。

    返回三态（关键）：
    - True：manifest 取到（HTTP 200），镜像可拉取；
    - False：token 取得成功但 manifest 被拒（401/403/404）——**镜像不可拉取**，
      这是仓库被下架/被封禁的确定性信号；
    - None：网络不可达或 token 接口异常——环境受限，无法判定。

    Args:
        registry: registry 根地址（如 `https://registry-1.docker.io`）。
        repo: 仓库路径（如 `minio/minio`）。
        tag: 标签。
        auth_url: token 获取地址；None 表示该 registry 允许匿名拉取。

    Returns:
        bool | None: True=可拉取；False=不可拉取；None=环境受限。
    """
    try:
        token = ""
        if auth_url:
            with urllib.request.urlopen(auth_url, timeout=20) as resp:  # noqa: S310
                token = json.loads(resp.read().decode()).get("token", "")
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.docker.distribution.manifest.v2+json,"
            "application/vnd.oci.image.manifest.v1+json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # 这里的 URL 全部由 `split_image()` 从 `images.lock` 解析出的 registry 常量拼成
        # （https，不含用户输入），S310 担心的 `file:` / 自定义 scheme 不成立。
        request = urllib.request.Request(  # noqa: S310 - URL 来自 images.lock 常量
            f"{registry}/v2/{repo}/manifests/{tag}",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=20) as resp:  # noqa: S310
            # `urlopen()` 的返回值在 typeshed 里是 `Any`：先落到有注解的局部量，
            # 否则 `Any == 200` 仍是 `Any`，函数会以 `Any` 冒充 `bool | None`。
            manifest_status: int = resp.status
            return manifest_status == 200
    except urllib.error.HTTPError as exc:
        # token 已成功取得但 manifest 被拒 → 确定性不可拉取
        return False if exc.code in {401, 403, 404} else None
    except Exception:  # 网络异常统一判定为环境受限
        return None


#: 对照镜像：用于区分「目标镜像不可拉取」与「网络环境整体受限」
#: 对照组可拉取而目标不可拉取 → 目标确定有问题（判 FAIL）
#: 对照组同样不可拉取 → 环境受限（降级放行 + 警告）
DOCKER_HUB_CONTROL = ("library/alpine", "3.20")
QUAY_CONTROL = ("prometheus/prometheus", "v2.54.0")

#: Docker Hub 官方镜像需补 library/ 前缀
DOCKER_HUB_OFFICIAL = {
    "postgres",
    "redis",
    "nginx",
    "python",
    "node",
    "ubuntu",
    "alpine",
    "busybox",
    "hello-world",
    "mongo",
    "mysql",
    "traefik",
}


def _hub_repo(name: str) -> str:
    """归一化 Docker Hub 仓库路径（官方镜像补 `library/` 前缀）。

    Args:
        name: 镜像名。

    Returns:
        str: 归一化后的仓库路径。
    """
    if "/" not in name:
        return f"library/{name}"
    return name


def _docker_hub_pullable(name: str, tag: str) -> bool | None:
    """探测 Docker Hub 镜像可拉取性（走真实 pull 路径）。

    Args:
        name: 镜像名。
        tag: 标签。

    Returns:
        bool | None: True=可拉取；False=不可拉取；None=环境受限。
    """
    repo = _hub_repo(name)
    auth = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    return _registry_pull_probe("https://registry-1.docker.io", repo, tag, auth)


def _quay_pullable(name: str, tag: str) -> bool | None:
    """探测 quay.io 镜像可拉取性。

    Args:
        name: 镜像名（形如 `quay.io/coreos/etcd`）。
        tag: 标签。

    Returns:
        bool | None: True=可拉取；False=不可拉取；None=环境受限。
    """
    parts = [p for p in name.split("/") if p not in {"quay.io", "docker-hub"}]
    if len(parts) < 2:
        return None
    namespace, repo = parts[-2], parts[-1]
    auth = f"https://quay.io/v2/auth?service=quay.io&scope=repository:{namespace}/{repo}:pull"
    return _registry_pull_probe("https://quay.io", f"{namespace}/{repo}", tag, auth)


def check_exact_versions(locked: dict[str, dict[str, str]]) -> list[str]:
    """校验标签的**精确性**（R2-ARC-14：把"写在注释里的策略"变成判据）。

    两条判据：
    1. 不得是浮动别名（`FLOATING_TAGS`）——这是原有判据；
    2. 必须含 `主版本.次版本`（`EXACT_TAG_PATTERN`）——`pg18` 这类只有大版本的
       滚动标签直接判红；`TAG_EXCEPTIONS` 里的镜像显式豁免（并写明理由）。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    for name, entry in sorted(locked.items()):
        tag = str(entry.get("tag", "")).strip()
        if tag.lower() in FLOATING_TAGS:
            failures.append(f"{name}:{tag} 使用浮动标签（指向「当前」而非某一份制品），禁止使用")
            continue
        if EXACT_TAG_PATTERN.match(tag):
            continue
        if name in TAG_EXCEPTIONS:
            print(f"[INFO] {name}:{tag} 精确标签豁免：{TAG_EXCEPTIONS[name]}")
            continue
        failures.append(
            f"{name}:{tag} 不是精确标签（要求形如 `1.2.3` / `1.2-alpine` / `v1.2.3`；"
            "`pg18` 这类只约束大版本的滚动标签会让同一 tag 在不同时间拉到不同制品）"
            "——确有必要请在 validate_images.py 的 TAG_EXCEPTIONS 里写明理由"
        )
    return failures


def _version_from_tag(tag: str) -> str:
    """取 tag 的版本号前缀（`0.8.7-pg18-bookworm` → `0.8.7`，`9.2-alpine` → `9.2`）。

    Args:
        tag: 镜像标签。

    Returns:
        str: 版本号；解析不到返回空串。
    """
    match = re.match(r"^v?([0-9]+(?:\.[0-9]+)*)", tag.strip())
    return match.group(1) if match else ""


def check_doc_versions(
    locked: dict[str, dict[str, str]],
    doc_path: Path = ARCH_DOC,
    targets: dict[str, str] | None = None,
) -> list[str]:
    """把架构文档里的基础设施版本号与 `images.lock` 交叉校验（R2-ARC-04）。

    审计发现的口径漂移：`docs/architecture-v1.0.md` 写 Valkey 7.2 / SeaweedFS 3.80，
    而 `images.lock` 已是 9.2 / 4.48 —— 设计文档与唯一事实来源各说一套，
    读者（交付/运维/新同事）按文档准备环境就会装错版本。

    判据：
      - 文档里每个 `<显示名> <版本号>` 必须与锁文件的**版本锚点**字面相等：
        默认锚点 = tag 的版本号前缀（Valkey `9.2-alpine` → `9.2`）；
        锚点无法从 tag 推出时（PostgreSQL 的 tag 只有 `pg18`），锚点取
        `DOC_VERSION_FIELDS` 指定的锁字段（`doc_version`），并要求与该条目 note 里的
        `PostgreSQL X.Y` **互证**——两处锁内声明不一致即红；
      - 文档里**完全没有**该组件的版本声明 → 也判红（否则"删掉那一行"就能绕过校验）；
      - 锁文件缺少锚点（如 `doc_version` 未登记）→ 也判红，而不是静默跳过
        （静默跳过 = 组件悄悄退出校验范围，正是 PG 一直没被查出来的原因）。

    Args:
        locked: 锁文件映射。
        doc_path: 架构文档路径。
        targets: 校验目标（默认为 `DOC_VERSION_TARGETS`；自检用自定义子集）。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    if not doc_path.is_file():
        return [f"缺少架构文档 {doc_path}，无法交叉校验基础设施版本口径"]

    active_targets = DOC_VERSION_TARGETS if targets is None else targets
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    for display, image in sorted(active_targets.items()):
        entry = locked.get(image)
        if entry is None:
            failures.append(f"images.lock 未登记 {image}（文档口径无对照物）")
            continue
        anchor_field = DOC_VERSION_FIELDS.get(display)
        if anchor_field is None:
            expected = _version_from_tag(str(entry.get("tag", "")))
            anchor_desc = f"`{entry.get('tag')}`"
            if not expected:
                failures.append(f"{image} 的 tag 无法解析出版本号，文档口径无法校验")
                continue
        else:
            expected = str(entry.get(anchor_field, "") or "").strip()
            anchor_desc = f"`{anchor_field}: {expected}`"
            if not expected:
                failures.append(
                    f"{image} 缺少 `{anchor_field}` 字段：{display} 的版本无法从 tag "
                    f"`{entry.get('tag')}` 推出，必须在 images.lock 显式登记文档口径版本"
                )
                continue
            note_pattern = re.compile(DOC_VERSION_NOTE_PATTERN.format(display=re.escape(display)))
            note_match = note_pattern.search(str(entry.get("note", "")))
            if note_match is not None and note_match.group(1) != expected:
                failures.append(
                    f"{image} 的 `{anchor_field}`={expected} 与该条目 note 里的"
                    f"`{display} {note_match.group(1)}` 不一致（锁内两处声明互相矛盾）"
                )
                continue
        pattern = re.compile(DOC_VERSION_NOTE_PATTERN.format(display=re.escape(display)))
        seen = 0
        mismatches = 0
        for lineno, line in enumerate(lines, start=1):
            for match in pattern.finditer(line):
                seen += 1
                found = match.group(1)
                if found != expected:
                    mismatches += 1
                    failures.append(
                        f"{doc_path.name}:{lineno} 写 `{display} {found}`，"
                        f"而 images.lock 是 {anchor_desc}（版本 {expected}）"
                        "——设计文档与唯一事实来源漂移"
                    )
        if seen == 0:
            failures.append(
                f"{doc_path.name} 未声明 `{display} <版本>`，无法与 images.lock 交叉校验"
                "（删掉声明同样等于失去约束）"
            )
        elif mismatches == 0:
            print(
                f"[OK]   {doc_path.name} 中 {display} 版本 {seen} 处，均与锁文件一致（{expected}）"
            )
    return failures


def _docker_hub_exists(name: str, tag: str) -> bool | None:
    """通过 Docker Hub Registry API 校验标签是否存在。

    区分「标签不存在」与「网络环境受限」两种失败：
    - HTTP 404 → 标签确实不存在（False，判定 FAIL）；
    - HTTP 401/403 → 鉴权被拒，通常是企业网络策略阻断（None，降级放行）；
    - 超时/连接错误 → 网络不可达（None，降级放行）。

    二者必须区分：若把 401 误判为「不存在」，受限网络下 CI 会全线飘红，
    门禁将被团队忽略，反而失去防护作用（ADR-0002 §2.4 实现要求第 3 条）。

    Args:
        name: 镜像名。
        tag: 标签。

    Returns:
        bool | None: True=存在；False=不存在；None=环境受限，无法判定。
    """
    url = f"https://hub.docker.com/v2/repositories/{_normalize_repo(name)}/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            hub_status: int = resp.status
            return hub_status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # 401/403 及其他状态：判定为环境受限，交由调用方降级
        print(f"[WARN] {name}:{tag} 上游查询返回 HTTP {exc.code}，判定为环境受限（非标签不存在）")
        return None
    except Exception as exc:  # 网络异常统一降级
        print(f"[WARN] {name}:{tag} 上游查询失败（{type(exc).__name__}），判定为环境受限")
        return None


def _quay_exists(name: str, tag: str) -> bool | None:
    """通过 quay.io API 校验标签是否存在。

    Args:
        name: 镜像名（形如 `quay.io/coreos/etcd`）。
        tag: 标签。

    Returns:
        bool | None: True=存在；False=不存在；None=环境受限，无法判定。
    """
    parts = [p for p in name.split("/") if p not in {"quay.io", "docker-hub"}]
    if len(parts) < 2:
        return False
    namespace, repo = parts[-2], parts[-1]
    url = f"https://quay.io/api/v1/repository/{namespace}/{repo}/tag/?specificTag={tag}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
            return bool(payload.get("tags"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        print(f"[WARN] {name}:{tag} 上游查询返回 HTTP {exc.code}，判定为环境受限（非标签不存在）")
        return None
    except Exception as exc:  # 网络异常统一降级
        print(f"[WARN] {name}:{tag} 上游查询失败（{type(exc).__name__}），判定为环境受限")
        return None


def _github_tags(repo: str) -> set[str]:
    """列出 GitHub 仓库的全部标签。

    Args:
        repo: 仓库名（形如 `minio/minio`）。

    Returns:
        set[str]: 标签集合；获取失败时返回空集。
    """
    try:
        # `git` 是宿主机 PATH 上的标准工具（CI runner 与本地开发机均自带），
        # argv 全为常量 + 由调用方给定的仓名，不含用户输入拼接。
        result = subprocess.run(  # noqa: S603 - argv 由常量构造，无外部输入
            ["git", "ls-remote", "--tags", f"https://github.com/{repo}.git"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception:
        return set()
    tags: set[str] = set()
    for line in result.stdout.splitlines():
        ref = line.split("refs/tags/")[-1].strip()
        if ref.endswith("^{}"):
            ref = ref[:-3]
        if ref:
            tags.add(ref)
    return tags


def check_remote_existence(locked: dict[str, dict[str, str]]) -> list[str]:
    """校验上游镜像**真实可拉取**（杜绝合成标签与已下架仓库）。

    判定分两段：
    1. **git tag 校验**（仅 `source: github`）：tag 必须存在于上游仓库，
       用于发现「日期与时间戳交叉拼接」的合成标签；
    2. **registry pull 路径校验**（所有源）：走 `取 token → 取 manifest`，
       与 `docker pull` 实际路径一致，能发现「仓库整体下架」的情况。

    环境受限的区分（关键）：用**对照镜像**判定网络是否整体可用——
    对照组可拉取而目标不可拉取 → 目标确定有问题（FAIL）；
    对照组同样不可拉取 → 环境受限（降级放行 + 警告）。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []

    # ---- 对照组：判定网络环境是否整体可用 ----
    hub_control = _docker_hub_pullable(*DOCKER_HUB_CONTROL)
    quay_control = _quay_pullable(*QUAY_CONTROL)
    hub_restricted = hub_control is not True
    quay_restricted = quay_control is not True
    if hub_restricted:
        print("[WARN] Docker Hub 对照镜像不可拉取，判定为网络环境受限")
    if quay_restricted:
        print("[WARN] quay.io 对照镜像不可拉取，判定为网络环境受限")

    for name, entry in sorted(locked.items()):
        tag = str(entry.get("tag", ""))
        source = str(entry.get("source", "docker-hub"))
        if source == "local":
            print(f"[SKIP] {name}:{tag} 本地构建，跳过上游存在性核验")
            continue

        if source == "github":
            # GitHub 源：先查 git tag（发现合成标签），再查 registry 实际可拉取性
            available = _github_tags(name)
            if not available:
                print(f"[WARN] {name}:{tag} 无法获取上游标签列表，降级为 registry 校验")
            elif tag not in available:
                failures.append(
                    f"{name}:{tag} 在上游仓库不存在（疑似日期与时间戳交叉拼接的合成标签）"
                )
                continue

        # ---- registry pull 路径校验（权威判定）----
        if source == "quay":
            exists = _quay_pullable(name, tag)
            restricted = quay_restricted
        else:
            exists = _docker_hub_pullable(name, tag)
            restricted = hub_restricted

        if exists is None:
            print(f"[WARN] {name}:{tag} 上游存在性无法判定（环境受限），跳过核验")
            continue
        if not exists:
            if restricted:
                # 对照组也不可拉取 → 无法区分「目标下架」与「网络阻断」，降级放行
                print(
                    f"[WARN] {name}:{tag} 不可拉取，但对照组同样不可拉取，"
                    "判定为网络环境受限，跳过核验"
                )
                continue
            failures.append(f"{name}:{tag} 镜像不可拉取——上游仓库可能已下架或被封禁，部署将失败")
            continue
        print(f"[OK]   {name}:{tag} 上游可拉取")
    return failures


def run_offline_self_test() -> list[str]:
    """**离网**负样本自测：精确标签规则与文档口径规则（R2-ARC-14 / R2-ARC-04）。

    这两条新规则都是纯静态判据，不需要网络，因此必须能在任何环境里证明"会红"。
    没有负样本的门禁无法区分「有效」与「恒过」。

    覆盖：
    1. `pgvector/pgvector:pg18` —— 只有大版本的滚动标签，必须判红；
    2. `nginx:latest` —— 浮动别名，必须判红；
    3. `nginx:1.28-alpine` —— 精确标签，必须放行（防"恒红"）；
    4. 文档写 `Valkey 7.2` 而锁文件是 `9.2` —— 必须判红（口径漂移）；
    5. 文档与锁文件一致 —— 必须放行；
    6. 文档完全没写版本 —— 必须判红（删声明不能成为绕过手段）。

    Returns:
        list[str]: 失败项描述（空列表表示自测通过）。
    """
    failures: list[str] = []
    print("[SELFTEST] 离网自测：精确标签规则 + 文档口径规则")

    # ---- 精确标签规则 ----
    rolling_lock = {
        "pgvector/pgvector": {"tag": "pg18"},
    }
    if not check_exact_versions(rolling_lock):
        failures.append(
            "[SELFTEST] `pgvector/pgvector:pg18`（只有大版本的滚动标签）应判红，实际放行"
        )
    else:
        print("[SELFTEST] ✅ pg18 判红（滚动标签）")

    floating_lock = {"nginx": {"tag": "latest"}}
    if not check_exact_versions(floating_lock):
        failures.append("[SELFTEST] `nginx:latest` 应判红，实际放行")
    else:
        print("[SELFTEST] ✅ latest 判红（浮动别名）")

    exact_lock = {"nginx": {"tag": "1.28-alpine"}}
    if check_exact_versions(exact_lock):
        failures.append(
            "[SELFTEST] `nginx:1.28-alpine` 是精确标签，不应判红，"
            f"实际 {check_exact_versions(exact_lock)}"
        )
    else:
        print("[SELFTEST] ✅ 1.28-alpine 放行（精确标签）")

    # 豁免表生效：node:22-alpine 属显式豁免，必须放行（否则豁免表形同虚设）
    node_lock = {"node": {"tag": "22-alpine"}}
    if check_exact_versions(node_lock):
        failures.append("[SELFTEST] `node:22-alpine` 在 TAG_EXCEPTIONS 中，应放行")
    else:
        print("[SELFTEST] ✅ node:22-alpine 按豁免放行")

    # ---- 文档口径规则 ----
    # PG 的版本锚点在 `doc_version`（tag 只有 `pg18`），故样本里同时给 note 供互证。
    doc_lock = {
        "valkey/valkey": {"tag": "9.2-alpine"},
        "chrislusf/seaweedfs": {"tag": "4.48"},
        "pgvector/pgvector": {
            "tag": "0.8.7-pg18-bookworm",
            "doc_version": "18.6",
            "note": "pgvector 0.8.7 + PostgreSQL 18.6",
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_doc = Path(tmp) / "doc.md"
        tmp_doc.write_text(
            "# 选型\n| 缓存 | Valkey 7.2 |\n| 存储 | SeaweedFS 4.48 |\n"
            "| 关系库 | PostgreSQL 18.6 |\n",
            encoding="utf-8",
        )
        hits = check_doc_versions(doc_lock, tmp_doc)
        if not any("Valkey" in item and "7.2" in item for item in hits):
            failures.append(f"[SELFTEST] 文档写 Valkey 7.2（锁文件 9.2）应判红，实际 {hits}")
        else:
            print("[SELFTEST] ✅ 文档版本漂移判红（Valkey 7.2 vs 9.2）")

        consistent_doc = (
            "# 选型\n| 缓存 | Valkey 9.2 |\n| 存储 | SeaweedFS 4.48 |\n"
            "| 关系库 | PostgreSQL 18.6 |\n"
        )
        tmp_doc.write_text(consistent_doc, encoding="utf-8")
        if check_doc_versions(doc_lock, tmp_doc):
            failures.append(
                "[SELFTEST] 文档与锁文件一致时不应判红，"
                f"实际 {check_doc_versions(doc_lock, tmp_doc)}"
            )
        else:
            print("[SELFTEST] ✅ 文档与锁文件一致放行（含 PG 的 doc_version 锚点）")

        tmp_doc.write_text("# 选型\n| 缓存 | Valkey |\n", encoding="utf-8")
        hits = check_doc_versions(doc_lock, tmp_doc)
        if not any("未声明" in item for item in hits):
            failures.append(f"[SELFTEST] 文档缺版本声明应判红（防绕过），实际 {hits}")
        else:
            print("[SELFTEST] ✅ 文档缺版本声明判红")

        # PG 专项负样本 1：文档写 16、锁里 doc_version=18.6 → 必须红
        tmp_doc.write_text(
            consistent_doc.replace("PostgreSQL 18.6", "PostgreSQL 16"), encoding="utf-8"
        )
        hits = check_doc_versions(doc_lock, tmp_doc)
        if not any("PostgreSQL" in item and "16" in item for item in hits):
            failures.append(f"[SELFTEST] 文档写 PostgreSQL 16（锁 18.6）应判红，实际 {hits}")
        else:
            print("[SELFTEST] ✅ PostgreSQL 版本漂移判红（16 vs doc_version 18.6）")

        # PG 专项负样本 2：doc_version 与同条目 note 矛盾 → 必须红（锁内两处互证）
        contradictory_lock = {
            "pgvector/pgvector": {
                "tag": "0.8.7-pg18-bookworm",
                "doc_version": "18.6",
                "note": "pgvector 0.8.7 + PostgreSQL 18.4",
            }
        }
        pg_only = {"PostgreSQL": "pgvector/pgvector"}
        hits = check_doc_versions(contradictory_lock, tmp_doc, pg_only)
        if not any("note" in item for item in hits):
            failures.append(f"[SELFTEST] doc_version 与 note 矛盾应判红，实际 {hits}")
        else:
            print("[SELFTEST] ✅ doc_version 与 note 矛盾判红（锁内互证）")

        # PG 专项负样本 3：缺 doc_version → 必须红（不许静默退出校验范围）
        missing_anchor_lock = {"pgvector/pgvector": {"tag": "0.8.7-pg18-bookworm"}}
        hits = check_doc_versions(missing_anchor_lock, tmp_doc, pg_only)
        if not any("doc_version" in item for item in hits):
            failures.append(f"[SELFTEST] 缺 doc_version 锚点应判红，实际 {hits}")
        else:
            print("[SELFTEST] ✅ 缺 doc_version 锚点判红（不静默跳过）")

    return failures


def run_self_test() -> list[str]:
    """门禁自测：用已知不存在的镜像验证门禁确实会 FAIL。

    这是 ADR-0002 §2.2.1 的强制要求 —— **门禁必须有负样本验证**，
    否则无法区分「门禁有效」与「门禁恒过」。

    负样本覆盖三类历史真实缺陷（需要网络）：
    1. ``minio/minio`` —— 上游仓库整体下架（git tag 仍在但镜像不可拉取）；
    2. ``squidly/squid`` —— 仓库从未存在（合成仓库名）；
    3. 合成标签 —— 日期与时间戳交叉拼接产物。

    离网判据（精确标签 / 文档口径）由 `run_offline_self_test()` 覆盖：
    即使网络受限也必须执行，否则"新加的两条规则"永远得不到负样本证明。

    Returns:
        list[str]: 失败项描述（空列表表示自测通过）。
    """
    failures: list[str] = []
    print("[SELFTEST] 门禁负样本自测（验证门禁确实会 FAIL）")

    # 离网部分先跑：不受网络限制影响
    failures += run_offline_self_test()

    # 先确认对照镜像可拉取，否则网络受限，负样本结果不可信
    control = _docker_hub_pullable(*DOCKER_HUB_CONTROL)
    if control is not True:
        print(f"[SELFTEST] ⚠️ 对照镜像不可拉取（{control!r}），网络环境受限，自测结论不可信")
        return failures

    # 负样本 1：minio/minio —— 仓库已下架（git tag 仍在但镜像不可拉取）
    exists = _docker_hub_pullable("minio/minio", "RELEASE.2021-04-22T15-44-28Z")
    if exists is not False:
        failures.append(
            f"[SELFTEST] minio/minio 应判为不可拉取，实际返回 {exists!r}"
            "（门禁可能仍在用 git tag 代替 registry pull 路径校验）"
        )
    else:
        print("[SELFTEST] ✅ minio/minio 判为不可拉取（仓库已下架）")

    # 负样本 2：squidly/squid —— 仓库从未存在（合成仓库名）
    exists = _docker_hub_pullable("squidly/squid", "5.2")
    if exists is not False:
        failures.append(f"[SELFTEST] squidly/squid 应判为不存在，实际返回 {exists!r}")
    else:
        print("[SELFTEST] ✅ squidly/squid 判为不存在（合成仓库名）")

    # 负样本 3：合成标签 —— 官方镜像上不存在的标签
    exists = _docker_hub_pullable("valkey/valkey", "RELEASE.2021-04-22T23-11-00Z")
    if exists is not False:
        failures.append(f"[SELFTEST] 合成标签应判为不存在，实际返回 {exists!r}")
    else:
        print("[SELFTEST] ✅ 合成标签判为不存在")

    # 正样本：对照组本身必须判为可拉取（防止门禁恒 FAIL）
    exists = _docker_hub_pullable(*DOCKER_HUB_CONTROL)
    if exists is not True:
        failures.append(
            f"[SELFTEST] 对照镜像 {DOCKER_HUB_CONTROL[0]}:{DOCKER_HUB_CONTROL[1]} "
            f"应判为可拉取，实际返回 {exists!r}"
        )
    else:
        print("[SELFTEST] ✅ 对照镜像判为可拉取（正样本通过）")

    return failures


#: 合法 digest 形态：`sha256:` + 64 位小写十六进制
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def check_digests(locked: dict[str, dict[str, str]]) -> list[str]:
    """校验每个镜像都登记了**可核验的 digest**（T4-05 第三批 3b 新增）。

    为什么必须进 CI 门禁：
      离线包用 `docker save` 出 tar，落地后 `docker load` 只按 `name:tag` 打标。
      **tag 可被覆盖**（上游重推同名 tag、本地重建自研镜像），digest 不能。
      `manifest.json` 与 `images.list` 都要按 digest 交付，锁文件里缺 digest
      等于"离线包与验收单无法对账"，出问题时无法证明现场跑的是哪一份镜像。

    规则：
      - `source != local`：**必须**有 digest 且形态合法（上游镜像 digest 稳定，
        重新 pull 仍是同一个）；
      - `source: local`：缺失只告警——自研镜像的 digest 由构建/推送生成，
        每次重建都会变，强制要求会让每次构建都改锁文件（噪声大于收益）。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    for name, entry in sorted(locked.items()):
        tag = str(entry.get("tag", ""))
        source = str(entry.get("source", "docker-hub"))
        digest = str(entry.get("digest", "") or "").strip()
        if not digest:
            if source == "local":
                print(f"[WARN] {name}:{tag} 未登记 digest（自研镜像，允许缺失）")
            else:
                failures.append(
                    f"{name}:{tag} 未在 images.lock 登记 digest"
                    "（离线包无法与现场镜像对账；用 `docker manifest inspect` 补）"
                )
            continue
        if not DIGEST_PATTERN.match(digest):
            failures.append(
                f"{name}:{tag} 的 digest 形态非法：{digest!r}（要求 `sha256:` + 64 位小写十六进制）"
            )
            continue
        print(f"[OK]   {name}:{tag} digest 已登记（{digest[:19]}…）")
    return failures


def main() -> int:
    """执行校验。

    Returns:
        int: 退出码（0 通过 / 1 不通过）。
    """
    parser = argparse.ArgumentParser(description="基础设施镜像清单校验")
    parser.add_argument(
        "--check-remote",
        action="store_true",
        help="追加上游存在性核验（需要网络）",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="门禁自测：用已知不存在的镜像验证门禁会 FAIL（需要网络）",
    )
    args = parser.parse_args()

    locked = load_lock()
    references = collect_compose_references()
    # ENG-04（审计 2026-10-28）：Dockerfile 的 FROM 基础镜像同样必须在锁内
    # （含 digest 登记）——构建期镜像可被上游重推覆盖 tag，不入锁则构建不可复现。
    dockerfile_froms = collect_dockerfile_froms()

    print("=" * 70)
    print("基础设施镜像清单校验（P0 门禁）")
    print("=" * 70)
    # E501（HEAD 既有，:656）：原为一行 137 列，按 ruff 口径拆成隐式拼接。
    print(
        f"[INFO] compose 引用 {len(references)} 个镜像，"
        f"Dockerfile FROM {len(dockerfile_froms)} 个，"
        f"images.lock 登记 {len(locked)} 个"
    )

    failures = check_consistency(locked, references)
    failures += check_consistency(locked, dockerfile_froms)
    failures += check_exact_versions(locked)
    failures += check_digests(locked)
    # R2-ARC-04：设计文档（architecture-v1.0.md）的基础设施版本号必须与锁文件同源
    failures += check_doc_versions(locked)
    if args.check_remote:
        failures += check_remote_existence(locked)
    if args.self_test:
        failures += run_self_test()

    for failure in failures:
        print(f"[FAIL] {failure}")

    if failures:
        print(f"\n[FAIL] 镜像清单校验不通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 镜像清单校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
