#!/usr/bin/env python3
"""基础设施镜像清单校验（P0 门禁，ADR-0002 §2.4）。

三道校验：
1. **一致性**：编排文件中的每个 `image:` 引用必须能在 `deploy/images.lock`
   中找到 `name:tag` 精确匹配项；
2. **精确版本**：MinIO 必须锁定 AGPL 之前的精确版本线
   `RELEASE.2021-04-22T15-44-28Z`（上游自 2021-05-11 起转为 AGPL-3.0）；
3. **存在性**（可选，`--check-remote`）：对非 `local` 源的镜像做上游存在性核验，
   杜绝「日期与时间戳交叉拼接」的合成标签。

用法：
    python deploy/validate_images.py              # 一致性 + 精确版本
    python deploy/validate_images.py --check-remote   # 追加上游存在性核验

退出码：0 = 通过；1 = 不通过。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

#: 部署目录
DEPLOY_DIR = Path(__file__).resolve().parent

#: 镜像锁文件
LOCK_FILE = DEPLOY_DIR / "images.lock"

#: 编排文件通配
COMPOSE_GLOB = "docker-compose*.yml"

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
    return {str(entry["name"]): entry for entry in images if isinstance(entry, dict) and "name" in entry}


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
                f"{source}: {name}:{tag} 与 images.lock 登记的 "
                f"{name}:{entry.get('tag')} 不一致"
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
            with urllib.request.urlopen(auth_url, timeout=20) as resp:
                token = json.loads(resp.read().decode()).get("token", "")
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.docker.distribution.manifest.v2+json,"
            "application/vnd.oci.image.manifest.v1+json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{registry}/v2/{repo}/manifests/{tag}", headers=headers
        )
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # token 已成功取得但 manifest 被拒 → 确定性不可拉取
        return False if exc.code in {401, 403, 404} else None
    except Exception:  # noqa: BLE001 - 网络异常统一判定为环境受限
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
    auth = (
        "https://auth.docker.io/token?service=registry.docker.io"
        f"&scope=repository:{repo}:pull"
    )
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
    """校验合规敏感镜像的精确版本。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    for name, entry in locked.items():
        tag = str(entry.get("tag", ""))
        if tag in {"latest", "main", "master"}:
            failures.append(f"{name}:{tag} 使用浮动标签，禁止使用")
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
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # 401/403 及其他状态：判定为环境受限，交由调用方降级
        print(f"[WARN] {name}:{tag} 上游查询返回 HTTP {exc.code}，判定为环境受限（非标签不存在）")
        return None
    except Exception as exc:  # noqa: BLE001 - 网络异常统一降级
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
    except Exception as exc:  # noqa: BLE001 - 网络异常统一降级
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
        result = subprocess.run(
            ["git", "ls-remote", "--tags", f"https://github.com/{repo}.git"],
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
            failures.append(
                f"{name}:{tag} 镜像不可拉取——上游仓库可能已下架或被封禁，部署将失败"
            )
            continue
        print(f"[OK]   {name}:{tag} 上游可拉取")
    return failures


def run_self_test() -> list[str]:
    """门禁自测：用已知不存在的镜像验证门禁确实会 FAIL。

    这是 ADR-0002 §2.2.1 的强制要求 —— **门禁必须有负样本验证**，
    否则无法区分「门禁有效」与「门禁恒过」。

    负样本覆盖三类历史真实缺陷：
    1. ``minio/minio`` —— 上游仓库整体下架（git tag 仍在但镜像不可拉取）；
    2. ``squidly/squid`` —— 仓库从未存在（合成仓库名）；
    3. 合成标签 —— 日期与时间戳交叉拼接产物。

    Returns:
        list[str]: 失败项描述（空列表表示自测通过）。
    """
    failures: list[str] = []
    print("[SELFTEST] 门禁负样本自测（验证门禁确实会 FAIL）")

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
        failures.append(
            f"[SELFTEST] squidly/squid 应判为不存在，实际返回 {exists!r}"
        )
    else:
        print("[SELFTEST] ✅ squidly/squid 判为不存在（合成仓库名）")

    # 负样本 3：合成标签 —— 官方镜像上不存在的标签
    exists = _docker_hub_pullable("valkey/valkey", "RELEASE.2021-04-22T23-11-00Z")
    if exists is not False:
        failures.append(
            f"[SELFTEST] 合成标签应判为不存在，实际返回 {exists!r}"
        )
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
                f"{name}:{tag} 的 digest 形态非法：{digest!r}"
                "（要求 `sha256:` + 64 位小写十六进制）"
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

    print("=" * 70)
    print("基础设施镜像清单校验（P0 门禁）")
    print("=" * 70)
    print(f"[INFO] compose 引用 {len(references)} 个镜像，images.lock 登记 {len(locked)} 个")

    failures = check_consistency(locked, references)
    failures += check_exact_versions(locked)
    failures += check_digests(locked)
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
