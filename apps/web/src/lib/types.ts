/**
 * 接口类型定义 —— 与适配层接口规范 v1.0 同步。
 *
 * 命名规范（架构文档 9.1）：
 * - JSON 字段一律 snake_case（与后端契约一致，不做驼峰转换）；
 * - 枚举值一律小写下划线；
 * - 资源 ID 一律 `类型缩写_` + ULID。
 *
 * 说明：`api-client.ts` 中的内联类型面向「单次调用签名」，
 * 本文件承载可复用的领域模型，供组件层 import 使用。
 */

// ---------------------------------------------------------------------------
// 通用
// ---------------------------------------------------------------------------

/** 统一响应包装 */
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
}

/** 业务错误码分段（架构文档 3.0.3） */
export const ERROR_SEGMENT = {
  SUCCESS: 0,
  DOMAIN_MIN: 10001,
  DOMAIN_MAX: 14999,
  COMMON_MIN: 20001,
  COMMON_MAX: 20999,
  SYSTEM_MIN: 30001,
  SYSTEM_MAX: 30999,
} as const;

/** 分页元信息 */
export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/** 分页数据 */
export interface PageResult<T> {
  items: T[];
  meta: PageMeta;
}

/** 资源 ID 前缀（架构文档 9.1） */
export const ID_PREFIX = {
  conversation: 'conv_',
  knowledgeBase: 'kb_',
  document: 'doc_',
  task: 'task_',
  step: 'step_',
  generation: 'gen_',
  connection: 'conn_',
  job: 'job_',
  execution: 'exec_',
  user: 'usr_',
  tenant: 'tnt_',
  organization: 'org_',
  role: 'role_',
} as const;

/** 资源 ID 前缀类型 */
export type IdPrefix = (typeof ID_PREFIX)[keyof typeof ID_PREFIX];

// ---------------------------------------------------------------------------
// 模型服务
// ---------------------------------------------------------------------------

/** 能力档位（契约枚举，新增真实模型不新增枚举值） */
export type ModelProfile = 'general' | 'code' | 'long_context' | 'reasoning';

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system';

/** 结束原因 */
export type FinishReason = 'stop' | 'length' | 'error' | 'cancelled';

/** 业务化用量（字段名 units，附录 A-7） */
export interface Usage {
  prompt_units: number;
  completion_units: number;
  total_units: number;
}

/** 引用片段 */
export interface Citation {
  document_id: string;
  document_name: string;
  segment_content: string;
  score: number;
}

/** 对话消息（前端本地状态） */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  citations?: Citation[];
  pending?: boolean;
}

/** 能力档位条目 */
export interface ProfileItem {
  profile: ModelProfile;
  display_name: string;
  description: string;
  max_context_turns: number;
  status: 'available' | 'unavailable';
}

/** 自研 SSE 事件类型（不复用底座原生事件名） */
export type ModelStreamEvent = 'start' | 'delta' | 'citation' | 'done' | 'error';

// ---------------------------------------------------------------------------
// RAG 服务
// ---------------------------------------------------------------------------

/** 知识库可见性 */
export type Visibility = 'organization' | 'private';

/** 分块策略 */
export type ChunkPolicy = 'default' | 'fine';

/** 索引状态（自研三态） */
export type IndexStatus = 'processing' | 'completed' | 'failed';

/** 知识库条目 */
export interface KnowledgeBase {
  base_id: string;
  name: string;
  description: string;
  visibility: Visibility;
  document_count: number;
  created_at: string;
}

/** 文档条目 */
export interface KnowledgeDocument {
  document_id: string;
  file_name: string;
  index_status: IndexStatus;
  progress: number;
  segment_count?: number;
  error_message?: string | null;
}

/** 检索命中片段 */
export interface SearchSegment {
  document_id: string;
  document_name: string;
  segment_content: string;
  score: number;
  position: { page: number | null; paragraph: number | null } | null;
}

// ---------------------------------------------------------------------------
// Agent 服务
// ---------------------------------------------------------------------------

/** 任务状态（自研五态） */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

/** 步骤状态 */
export type StepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

/** 任务产物 */
export interface Artifact {
  type: 'file' | 'link' | 'text';
  name: string;
  download_url?: string | null;
  url?: string | null;
}

/** 任务结果 */
export interface TaskResult {
  summary: string;
  artifacts: Artifact[];
}

/** 任务条目 */
export interface AgentTask {
  task_id: string;
  conversation_id: string;
  goal: string;
  status: TaskStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  result?: TaskResult | null;
}

/** 任务步骤 */
export interface TaskStep {
  step_id: string;
  sequence: number;
  title: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  output_summary: string | null;
}

/** 自研任务事件类型 */
export type TaskEvent = 'step_started' | 'step_finished' | 'task_completed' | 'error';

// ---------------------------------------------------------------------------
// 工具服务
// ---------------------------------------------------------------------------

/** 工具分类 */
export type ToolCategory = 'communication' | 'development' | 'document' | 'schedule' | 'data';

/** 工具授权方式 */
export type ToolAuthType = 'oauth2' | 'token' | 'none';

/** 工具操作项 */
export interface ToolOperation {
  operation: string;
  display_name: string;
  params_schema: Record<string, unknown>;
}

/** 工具类型条目（前端按此动态渲染，不硬编码工具列表） */
export interface ToolSpec {
  tool_type: string;
  display_name: string;
  category: ToolCategory;
  auth_type: ToolAuthType;
  status: 'available' | 'unavailable';
  operations: ToolOperation[];
  timeout_ms: number;
}

/** 工具连接 */
export interface ToolConnection {
  connection_id: string;
  tool_type: string;
  display_name: string;
  status: 'connected' | 'expired' | 'revoked';
  connected_at: string;
  expires_at: string | null;
}

/** 同步任务 */
export interface SyncJob {
  job_id: string;
  tool_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  processed_count?: number;
  total_count?: number;
  error?: string | null;
  updated_at?: string;
}

// ---------------------------------------------------------------------------
// 用户权限服务
// ---------------------------------------------------------------------------

/** 组织角色 */
export type OrgRole = 'owner' | 'org_admin' | 'member' | 'guest';

/** 租户信息 */
export interface TenantInfo {
  tenant_id: string;
  name: string;
  plan: string;
}

/** 组织信息 */
export interface Organization {
  org_id: string;
  name: string;
  role: OrgRole;
}

/** 当前用户 */
export interface CurrentUser {
  user_id: string;
  display_name: string;
  email: string;
  tenant: TenantInfo;
  organizations: Organization[];
}

/** 组织成员 */
export interface OrganizationMember {
  user_id: string;
  display_name: string;
  email: string;
  role: OrgRole;
  joined_at: string;
}

/** 权限点校验结论 */
export interface VerifyResult {
  allowed: boolean;
  user_id: string;
  tenant_id: string;
  roles: string[];
}

// ---------------------------------------------------------------------------
// 本地会话（前端自持，不经适配层，架构文档 D-03）
// ---------------------------------------------------------------------------

/** 会话（上层自持，适配层只做无状态生成） */
export interface Conversation {
  conversation_id: string;
  title: string;
  updated_at: string;
  message_count: number;
}

/** 业务语言文案中的提示级别 */
export type ToastLevel = 'success' | 'error' | 'info' | 'warning';
