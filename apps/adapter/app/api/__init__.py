"""API 路由包。

约定（架构文档 3.0.1 / 3.0.2）：
- 所有端点统一返回 `{code, message, data}`；
- 除 `/healthz`、`/readyz` 外均需 `Authorization: Bearer`；
- 路由层**只做**参数装配与响应封装，禁止出现协议转换与业务逻辑。
"""

__all__: list[str] = []
