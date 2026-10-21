"""仓储层包（架构文档 2.1：`db` 层只允许 SQL 与会话管理）。

每个仓储都继承 `BaseRepository`，由基类统一注入租户条件——
代码评审规则：**无 `tenant_id` 条件的查询一律打回**（架构文档 3.5「应用层双保险」）。
"""

from __future__ import annotations

from app.db.repositories.base import BaseRepository

__all__ = ["BaseRepository"]
