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

#: MinIO 必须锁定的 AGPL 前精确版本线
MINIO_APPROVED_TAG = "RELEASE.2021-04-22T15-44-28Z"

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


def check_exact_versions(locked: dict[str, dict[str, str]]) -> list[str]:
    """校验合规敏感镜像的精确版本。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    minio = locked.get("minio/minio")
    if minio is None:
        failures.append("images.lock 未登记 minio/minio")
    elif str(minio.get("tag")) != MINIO_APPROVED_TAG:
        failures.append(
            f"minio/minio 必须锁定 AGPL 前的精确版本线 {MINIO_APPROVED_TAG}，"
            f"实际为 {minio.get('tag')}"
        )
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
    """校验上游标签真实存在（杜绝合成标签）。

    Args:
        locked: 锁文件映射。

    Returns:
        list[str]: 失败项描述；空列表表示通过。
    """
    failures: list[str] = []
    for name, entry in sorted(locked.items()):
        tag = str(entry.get("tag", ""))
        source = str(entry.get("source", "docker-hub"))
        if source == "local":
            print(f"[SKIP] {name}:{tag} 本地构建，跳过上游存在性核验")
            continue
        if source == "github" or name.startswith("minio/"):
            available = _github_tags(name)
            if not available:
                print(f"[WARN] {name}:{tag} 无法获取上游标签列表，跳过核验")
                continue
            if tag not in available:
                failures.append(
                    f"{name}:{tag} 在上游仓库不存在（疑似日期与时间戳交叉拼接的合成标签）"
                )
                continue
        else:
            exists = (
                _quay_exists(name, tag) if source == "quay" else _docker_hub_exists(name, tag)
            )
            if exists is None:
                # 环境受限（网络策略/鉴权/超时）：降级放行 + 警告，不得判 FAIL
                print(f"[WARN] {name}:{tag} 上游存在性无法判定（环境受限），跳过核验")
                continue
            if not exists:
                failures.append(
                    f"{name}:{tag} 上游不存在——部署将拉取失败"
                )
                continue
        print(f"[OK]   {name}:{tag} 上游存在")
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
    args = parser.parse_args()

    locked = load_lock()
    references = collect_compose_references()

    print("=" * 70)
    print("基础设施镜像清单校验（P0 门禁）")
    print("=" * 70)
    print(f"[INFO] compose 引用 {len(references)} 个镜像，images.lock 登记 {len(locked)} 个")

    failures = check_consistency(locked, references)
    failures += check_exact_versions(locked)
    if args.check_remote:
        failures += check_remote_existence(locked)

    for failure in failures:
        print(f"[FAIL] {failure}")

    if failures:
        print(f"\n[FAIL] 镜像清单校验不通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 镜像清单校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
