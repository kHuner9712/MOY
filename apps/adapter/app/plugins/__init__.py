"""插件域（阶段三）：声明模型、加载器、原语白名单、步骤引擎、脱敏。

分层铁律（`architecture-phase3.md` §3.1）：本包**禁止** `import app.services.*` /
`app.clients.*` / `httpx`，也不写 SQL、不持有外部凭据——原语的真实能力由
`services/action_gateway.py` 通过 `ActionGateway` Protocol 注入（D-16）。
"""

from __future__ import annotations

__all__: list[str] = []
