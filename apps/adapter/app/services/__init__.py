"""协议转换服务包。

**唯一允许写协议转换的位置（架构文档 6.2 关键文件职责）**：
本包内允许出现底座原生字段名与下游调用；包外（含 `api/`、`schemas/`）
一律只能是自研契约形态。

边界红线（架构文档 1.2）：本包**禁止**出现会话归属判断、权限策略、套餐额度计算、
提示词拼装、结果后处理与格式化等业务逻辑——这些能力一律放业务能力层或插件层。
"""

from app.services.agent_service import AgentService, get_agent_service
from app.services.identity_service import IdentityService, get_identity_service
from app.services.model_service import ModelService, get_model_service
from app.services.rag_service import RagService, get_rag_service
from app.services.tool_service import ToolService, get_tool_service

__all__ = [
    "AgentService",
    "IdentityService",
    "ModelService",
    "RagService",
    "ToolService",
    "get_agent_service",
    "get_identity_service",
    "get_model_service",
    "get_rag_service",
    "get_tool_service",
]
