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
  /** 所属知识库 ID（阶段二加性字段，用于回链） */
  base_id?: string;
  /** 所属知识库名称 */
  base_name?: string;
  /** 片段稳定标识（阶段二加性字段，KB4 回链必填） */
  segment_id?: string;
  /** 原文位置（页码 / 段落），无页码信息时两项为 null */
  position?: { page: number | null; paragraph: number | null } | null;
}

/** 对话消息（前端本地状态） */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  citations?: Citation[];
  pending?: boolean;
}

/** 生成请求（M1/M2 入参） */
export interface GenerateRequest {
  conversation_id: string;
  profile: ModelProfile;
  messages: Array<{ role: MessageRole; content: string }>;
  context?: { history_window?: number; memory_hints?: string[] };
  knowledge_base_ids?: string[];
  options?: { temperature?: number; max_tokens?: number };
}

/** 生成结果（M1/M2 响应） */
export interface GenerateResult {
  generation_id: string;
  content: string;
  finish_reason: FinishReason;
  usage: Usage;
  citations: Citation[];
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

/**
 * 知识库可见性。
 *
 * 阶段二新增 `restricted`（指定成员可见），属**加性加宽**：
 * 旧前端未识别该值时由组件层映射表兜底展示「指定成员可见」。
 */
export type Visibility = 'organization' | 'private' | 'restricted';

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

/** 任务状态（自研五态 + 阶段三加性新增 `waiting_confirm` = 等人工接管） */
export type TaskStatus =
  | 'pending'
  | 'running'
  | 'waiting_confirm'
  | 'completed'
  | 'failed'
  | 'cancelled';

/**
 * 步骤状态（与后端 `plugin_task_step.status` 逐字一致）。
 *
 * 注意：`waiting_confirm` 是**任务级**状态，步骤此时仍为 `pending`；
 * 时间线组件为展示统一另接受 `succeeded` 作为 `completed` 的同义值。
 */
export type StepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

/**
 * 任务产物（兼容别名）。
 *
 * 阶段三把产物的完整形态收敛为 `TaskArtifact`（含 5 类内容、过期时间等），
 * 这里保留 `Artifact` 名字，避免既有引用失效。
 */
export type Artifact = TaskArtifact;

/** 任务结果（阶段三加性：核查清单 + 告警） */
export interface TaskResult {
  summary: string;
  artifacts: Artifact[];
  /** 结果物核查清单（三插件项数固定为 4 / 3 / 4） */
  checklist?: TaskChecklistItem[];
  /** 降级继续时的告警（「哪一步没做成、影响什么」） */
  warnings?: TaskWarning[];
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

/** 任务条目 */
export interface TaskItem {
  task_id: string;
  conversation_id: string;
  goal: string;
  status: TaskStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  result?: TaskResult | null;
  /** 插件任务所属插件（阶段三加性；无插件为空） */
  plugin_id?: string | null;
  /** 降级告警条数（阶段三加性，列表页展示「N 条提醒」） */
  warning_count?: number;
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
  /** 该步产物（阶段三加性）；已完成步骤至少 1 个 */
  artifacts?: TaskArtifact[];
  /** 失败信息（阶段三加性）；成功或未执行为 null */
  failure?: TaskStepFailure | null;
  /** 已尝试次数（0..1，重试上限 1 次） */
  attempt?: number;
  /** 耗时（毫秒） */
  duration_ms?: number | null;
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

/** 操作记录条目（U6，阶段二加性字段） */
export interface AuditLogItem {
  log_id: string;
  /** 操作动作编码 */
  action: string;
  /** 操作动作的用户可见名称 */
  action_display_name?: string;
  /** 操作人展示名 */
  actor_name?: string;
  /** 操作人在该企业的角色 */
  actor_role?: string;
  /** 资源类型（知识库/文档/成员/订单…） */
  resource_type?: string;
  /** 结果：成功 / 失败 */
  result?: 'success' | 'failed';
  /** 变更摘要（业务语言） */
  change_summary?: string;
  /** 脱敏来源地址 */
  ip_masked?: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// 阶段二：知识库扩展（KB1-KB8，架构文档 §4.2）
// ---------------------------------------------------------------------------

/** 平铺分页数据（运营域部分端点直接返回页码字段，不包 `meta`） */
export interface FlatPageResult<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/** 当前用户在某知识库上的角色（R2 `my_role`） */
export type KnowledgeBaseRole = 'owner' | 'editor' | 'viewer';

/** 文档处理阶段（五态，与三态 `index_status` 语义对齐） */
export type DocumentStage = 'uploaded' | 'parsing' | 'preparing' | 'completed' | 'failed';

/** 知识库条目（R2，阶段二加性字段：`chunk_policy` / `my_role` / `updated_at`） */
export interface KnowledgeBaseDetail extends KnowledgeBase {
  /** 内容切分精细度（仅对之后上传的文档生效） */
  chunk_policy?: ChunkPolicy;
  /** 当前用户在该库上的角色 */
  my_role?: KnowledgeBaseRole;
  /** 最近更新时间 */
  updated_at?: string;
}

/** KB1 修改知识库设置请求（至少一项） */
export interface UpdateBaseRequest {
  name?: string;
  description?: string;
  chunk_policy?: ChunkPolicy;
  visibility?: Visibility;
}

/** KB1 修改知识库设置响应 */
export interface UpdateBaseResult {
  base_id: string;
  name: string;
  description: string;
  chunk_policy: ChunkPolicy;
  visibility: Visibility;
  updated_at: string;
}

/** KB2 删除知识库响应 */
export interface DeleteBaseResult {
  base_id: string;
  deleted: boolean;
  deleted_document_count: number;
}

/** KB3 文档清单条目 */
export interface KnowledgeDocumentItem {
  document_id: string;
  file_name: string;
  /** 文件大小（字节） */
  file_size: number;
  uploader_name: string;
  uploaded_at: string;
  index_status: IndexStatus;
  stage: DocumentStage;
  /** 阶段的用户可见文案（后端直出，避免前端漏映射） */
  stage_display_name: string;
  progress: number;
  /** 预估剩余秒数；无法预估时为 null */
  estimated_remaining_seconds: number | null;
  segment_count: number;
  /** 处理未成功的原因（业务语言） */
  error_message: string | null;
}

/**
 * KB3 文档清单查询参数。
 *
 * 声明为类型别名（而非 interface）以便直接传给查询串构造器：
 * 类型别名具备隐式索引签名，interface 不具备。
 */
export type DocumentListQuery = {
  page?: number;
  page_size?: number;
  keyword?: string;
  stage?: DocumentStage;
  /** 排序串，形如 `uploaded_at:desc` */
  sort?: string;
};

/** KB4 引用片段可定位状态（`stale` 是正常响应，不是错误） */
export type SegmentStatus = 'valid' | 'stale';

/** KB4 引用片段详情 */
export interface SegmentDetail {
  segment_id: string;
  document_id: string;
  document_name: string;
  base_id: string;
  base_name: string;
  /** 片段正文；不可展示时为 null */
  content: string | null;
  context_before: string | null;
  context_after: string | null;
  position: { page: number | null; paragraph: number | null } | null;
  status: SegmentStatus;
}

/** 授权对象类型（成员 / 部门 / 角色） */
export type GrantSubjectType = 'user' | 'department' | 'role';

/** 授权级别（可提问 / 可上传与管理） */
export type GrantPermission = 'viewer' | 'editor';

/** KB5 授权清单条目 */
export interface GrantItem {
  grant_id: string;
  subject_type: GrantSubjectType;
  subject_id: string;
  subject_name: string;
  permission: GrantPermission;
  granted_by_name: string;
  granted_at: string;
}

/** KB6 批量授权请求 */
export interface CreateGrantRequest {
  subject_type: GrantSubjectType;
  /** 授权对象 ID 列表（1-50 个，重复对象计入跳过） */
  subject_ids: string[];
  permission: GrantPermission;
}

/** KB6 批量授权响应 */
export interface CreateGrantResult {
  created_count: number;
  skipped_count: number;
}

/** KB7 撤销授权响应 */
export interface DeleteGrantResult {
  grant_id: string;
  deleted: boolean;
}

/** KB8 更新知识库凭据绑定响应（运维通道专用，业务界面不调用） */
export interface CredentialBindingResult {
  base_id: string;
  bound: boolean;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// 阶段二：工具扩展（TL1，架构文档 §4.3）
// ---------------------------------------------------------------------------

/** 工具调用结果 */
export type ToolExecutionStatus = 'success' | 'failed';

/** TL1 工具调用记录条目 */
export interface ToolExecutionItem {
  execution_id: string;
  tool_type: string;
  tool_display_name: string;
  operation: string;
  operation_display_name: string;
  status: ToolExecutionStatus;
  /** 失败原因（业务语言）；成功时为 null */
  error_message: string | null;
  executed_at: string;
  operator_name: string;
}

// ---------------------------------------------------------------------------
// 阶段二：组织与权限域（ID1-ID11，架构文档 §4.4）
// ---------------------------------------------------------------------------

/** 组织层级上限（第 6 层不允许创建） */
export const DEPARTMENT_MAX_LEVEL = 5;

/** 部门树节点（`level` 1-5） */
export interface DepartmentNode {
  department_id: string;
  name: string;
  /** 上级部门 ID；一级部门为空串 */
  parent_id: string;
  /** 从根到本部门的名称链 */
  path_names: string[];
  level: number;
  manager_name: string;
  member_count: number;
  children: DepartmentNode[];
}

/** ID2 新建部门请求 */
export interface CreateDepartmentRequest {
  name: string;
  /** 上级部门 ID；留空为一级部门 */
  parent_id?: string;
}

/** ID2 新建部门响应 */
export interface CreateDepartmentResult {
  department_id: string;
  name: string;
  parent_id: string;
  level: number;
}

/** ID3 修改/移动部门请求（三项至少一项） */
export interface UpdateDepartmentRequest {
  name?: string;
  parent_id?: string;
  manager_id?: string;
}

/** ID3 修改/移动部门响应 */
export interface UpdateDepartmentResult {
  department_id: string;
  name: string;
  parent_id: string;
  level: number;
  manager_name: string;
  updated_at: string;
}

/** ID4 删除部门响应 */
export interface DeleteDepartmentResult {
  department_id: string;
  deleted: boolean;
}

/** 权限点条目 */
export interface PermissionItem {
  permission: string;
  display_name: string;
  description: string;
}

/** 权限点分组 */
export interface PermissionGroup {
  group: string;
  group_name: string;
  permissions: PermissionItem[];
}

/** ID6 角色条目 */
export interface RoleItem {
  role_id: string;
  role_key: string;
  display_name: string;
  /** 内置角色不可删除 */
  is_builtin: boolean;
  member_count: number;
  permissions: string[];
}

/** 成员在册状态 */
export type MemberStatus = 'active' | 'invited' | 'disabled';

/** ID8 修改成员请求（四项至少一项） */
export interface UpdateMemberRequest {
  role?: string;
  department_ids?: string[];
  primary_department_id?: string;
  status?: MemberStatus;
}

/** ID8 修改成员响应 */
export interface UpdateMemberResult {
  member_id: string;
  role: string;
  department_ids: string[];
  status: string;
  updated_at: string;
}

/** ID9 移出成员响应 */
export interface RemoveMemberResult {
  member_id: string;
  removed: boolean;
  /** 转交给企业管理员的库数量 */
  transferred_knowledge_base_count: number;
}

/** 批量成员动作 */
export type BatchMemberAction = 'update_role' | 'remove';

/** ID10 批量成员操作请求 */
export interface BatchMemberRequest {
  /** 成员 ID 列表（1-100） */
  member_ids: string[];
  action: BatchMemberAction;
  role?: string;
}

/** ID10 批量成员操作的失败原因 */
export interface BatchMemberFailure {
  member_id: string;
  reason: string;
}

/** ID10 批量成员操作响应 */
export interface BatchMemberResult {
  succeeded: number;
  failed: BatchMemberFailure[];
}

/** 操作动作条目（ID11） */
export interface ActionItem {
  action: string;
  display_name: string;
}

/** 操作动作分组（ID11） */
export interface ActionGroup {
  group: string;
  group_name: string;
  actions: ActionItem[];
}

// ---------------------------------------------------------------------------
// 阶段二：计费域（BL1-BL10，架构文档 §4.5）
// ---------------------------------------------------------------------------

/** 套餐功能开关 */
export interface PlanFeatureItem {
  feature_key: string;
  display_name: string;
  included: boolean;
  /** 数量上限；null 表示不限 */
  limit: number | null;
}

/** 套餐条目 */
export interface PlanItem {
  plan_code: string;
  display_name: string;
  /** 每席位每月价格（分） */
  price_per_seat_month_cents: number;
  currency: string;
  min_seats: number;
  quota_units_per_seat_month: number;
  quota_unit_label: string;
  purchasable_quota: boolean;
  features: PlanFeatureItem[];
  status: string;
  trial_days: number;
}

/** 待生效的套餐变更 */
export interface PendingPlanChange {
  plan_code: string;
  effective_at: string;
}

/** BL2 订阅详情（无订阅时返回免费版合成视图，不是 404） */
export interface SubscriptionDetail {
  subscription_id: string;
  plan_code: string;
  plan_name: string;
  status: string;
  seats_total: number;
  seats_used: number;
  started_at: string;
  current_period_end: string;
  quota_units_total: number;
  quota_units_used: number;
  auto_renew: boolean;
  pending_change: PendingPlanChange | null;
}

/** 套餐变更动作 */
export type SubscriptionAction = 'subscribe' | 'upgrade' | 'downgrade' | 'cancel_renew';

/** BL3 套餐变更请求 */
export interface ChangeSubscriptionRequest {
  plan_code: string;
  seats: number;
  action: SubscriptionAction;
}

/** BL3 套餐变更响应 */
export interface ChangeSubscriptionResult {
  subscription_id: string;
  status: string;
  effective_at: string;
  /** 关联订单；无需支付时为空串 */
  order_id: string;
  over_quota_warning: boolean;
  /** 非阻断提示码（15005 = 降级周期末生效），仅用于选文案，不展示给用户 */
  notice_code: number;
  notice_message: string;
}

/** BL4 调整席位响应 */
export interface UpdateSeatsResult {
  seats_total: number;
  seats_used: number;
  effective_at: string;
  order_id: string;
  over_quota_warning: boolean;
}

/** BL5 分组维度 */
export type UsageGroupBy = 'member' | 'department' | 'capability';

/** BL5 用量分项 */
export interface UsageBreakdownItem {
  key: string;
  display_name: string;
  units: number;
  percent: number;
}

/** BL5 按日用量点 */
export interface UsageDailyTrendItem {
  date: string;
  units: number;
}

/** BL5 用量明细 */
export interface BillingUsageDetail {
  period: string;
  quota_units_total: number;
  quota_units_used: number;
  quota_units_remaining: number;
  usage_percent: number;
  over_quota_warning: boolean;
  quota_unit: string;
  breakdown: UsageBreakdownItem[];
  daily_trend: UsageDailyTrendItem[];
}

/** 订单类型 */
export type OrderType = 'new' | 'renew' | 'upgrade' | 'seats';

/** 支付方式 */
export type PaymentMethod = 'online' | 'offline_transfer';

/** BL6 下单请求 */
export interface CreateOrderRequest {
  plan_code: string;
  seats: number;
  order_type: OrderType;
  payment_method: PaymentMethod;
}

/** BL6 下单响应 */
export interface CreateOrderResult {
  order_id: string;
  amount_cents: number;
  currency: string;
  status: string;
  payment_method: string;
  /** 在线支付跳转地址；对公转账时为空串 */
  pay_url: string;
  /** 对公转账收款信息；在线支付时为 null */
  bank_info: Record<string, unknown> | null;
  expires_at: string;
}

/** BL7 订单列表条目 */
export interface BillingOrderItem {
  order_id: string;
  order_type: string;
  plan_code: string;
  seats: number;
  amount_cents: number;
  status: string;
  payment_method: string;
  created_at: string;
}

/** BL8 订单详情 */
export interface BillingOrderDetail {
  order_id: string;
  status: string;
  amount_cents: number;
  payment_no_masked: string;
  paid_at: string;
  invoice_status: string;
  plan_code: string;
  seats: number;
  created_at: string;
  expires_at: string;
}

/** BL9 账单条目 */
export interface BillingInvoiceItem {
  invoice_id: string;
  period: string;
  amount_cents: number;
  status: string;
  download_url: string;
}

/** BL10 支付回调应答（对渠道方语义，业务界面不调用） */
export interface PaymentCallbackResult {
  code: string;
  message: string;
}

// ---------------------------------------------------------------------------
// 阶段二：运营域（OPS1-OPS8，架构文档 §4.6）
// ---------------------------------------------------------------------------

/** 统计区间 */
export type OpsMetricsRange = '7d' | '30d';

/** 企业状态 */
export type OpsTenantStatus = 'active' | 'suspended';

/** OPS1 企业列表条目（不含邮箱明文与任何内容字段） */
export interface OpsTenantItem {
  tenant_id: string;
  name: string;
  plan_code: string;
  seats_total: number;
  seats_used: number;
  status: string;
  created_at: string;
  quota_units_used: number;
}

/** OPS2 企业基本信息 */
export interface OpsTenantProfile {
  tenant_id: string;
  name: string;
  status: string;
  plan_code: string;
  deploy_mode: string;
  created_at: string;
  suspended_reason: string;
}

/** OPS2 订阅摘要 */
export interface OpsSubscriptionSummary {
  plan_code: string;
  plan_name: string;
  status: string;
  seats_total: number;
  seats_used: number;
  current_period_end: string;
  auto_renew: boolean;
}

/** OPS2 用量摘要（只有聚合数字） */
export interface OpsUsageSummary {
  period: string;
  quota_units_total: number;
  quota_units_used: number;
  usage_percent: number;
  member_count: number;
}

/** 运营订单条目（OPS2 `recent_orders` 与 OPS4 `items` 共用） */
export interface OpsOrderItem {
  order_id: string;
  tenant_id: string;
  tenant_name: string;
  order_type: string;
  plan_code: string;
  seats: number;
  amount_cents: number;
  status: string;
  payment_method: string;
  created_at: string;
  paid_at: string;
}

/** OPS2 企业详情 */
export interface OpsTenantDetail {
  tenant: OpsTenantProfile;
  subscription: OpsSubscriptionSummary;
  usage: OpsUsageSummary;
  recent_orders: OpsOrderItem[];
}

/** OPS3 启用/停用企业响应 */
export interface OpsTenantStatusResult {
  tenant_id: string;
  status: string;
  updated_at: string;
}

/** OPS5 核销结果 */
export interface OpsConfirmOrderResult {
  order_id: string;
  status: string;
  subscription_status: string;
}

/** OPS6 套餐分布条目 */
export interface OpsPlanDistributionItem {
  plan_code: string;
  plan_name: string;
  tenant_count: number;
}

/** OPS6 用量 Top 企业条目 */
export interface OpsTopTenantItem {
  tenant_id: string;
  name: string;
  plan_code: string;
  quota_units_used: number;
}

/** OPS6 平台统计 */
export interface OpsMetrics {
  range: string;
  tenant_count: number;
  active_tenant_count: number;
  active_user_count: number;
  conversation_count: number;
  document_count: number;
  quota_units_total: number;
  plan_distribution: OpsPlanDistributionItem[];
  top_tenants: OpsTopTenantItem[];
}

/** OPS7 / OPS8 系统配置 */
export interface OpsSettings {
  quota_warn_threshold: number;
  trial_days: number;
  quota_unit_label: string;
  announcement: string;
  plan_status: Record<string, string>;
  updated_by: string;
  updated_at: string;
}

/** OPS8 修改系统配置请求（只改传入项） */
export interface UpdateOpsSettingsRequest {
  quota_warn_threshold?: number;
  trial_days?: number;
  quota_unit_label?: string;
  announcement?: string;
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

// ---------------------------------------------------------------------------
// 阶段三：插件域（PL1-PL13，架构文档 §5.2）
//
// 命名口径：
// - 字段名一律 snake_case，与适配层逐字对齐，不做驼峰转换；
// - 能力 key（`personal.*` / `org.*`）**只用于请求与判断**，界面展示必须走
//   `lib/capability-labels.ts` 的业务名称，key 不得进 DOM（G-16 断言）。
// ---------------------------------------------------------------------------

/** 插件分类（PRD 六分类，市场筛选用） */
export type PluginCategoryKey = 'dev' | 'content' | 'hr' | 'data' | 'collab' | 'other';

/** 我在这个插件上的状态 */
export type PluginInstalledStatus = 'none' | 'enabled' | 'disabled';

/** 市场排序方式 */
export type PluginMarketSort = 'recommended' | 'newest' | 'rating';

/** 启停状态（PL6 请求取值） */
export type PluginState = 'enabled' | 'disabled';

/** 能力的授权层（个人能力不可被企业关闭；企业能力受总开关控制） */
export type CapabilityCategory = 'personal' | 'org';

/** 授权能力 key（封闭 6 项，架构文档 §2.2） */
export type PluginCapabilityKey =
  | 'personal.knowledge.read'
  | 'personal.tool.use'
  | 'personal.file.read'
  | 'org.directory.read'
  | 'org.repo.write'
  | 'org.external.publish';

/** 能力条目（授权抽屉逐项展示：名称 / 为什么需要 / 不同意的后果） */
export interface PluginCapabilityItem {
  capability_key: PluginCapabilityKey | string;
  display_name: string;
  /** 为什么需要这项能力（业务语言） */
  reason: string;
  /** 不同意的后果（业务语言） */
  denial_effect: string;
  category: CapabilityCategory;
  /** 是否必需：必需能力未授权则不可安装（PL5 返回 16005） */
  required: boolean;
  /**
   * 企业是否已开启该项能力（PL4 返回，**前端禁止硬编码**）。
   *
   * 个人能力（personal.*）恒为 true；企业能力关闭时安装会拿到 16006。
   */
  org_switch_enabled: boolean;
}

/** 市场列表条目（PL1） */
export interface PluginMarketItem {
  plugin_id: string;
  name: string;
  summary: string;
  category: PluginCategoryKey | string;
  icon_key: string;
  rating_avg: number;
  rating_count: number;
  latest_version: string;
  installed_status: PluginInstalledStatus;
  has_update: boolean;
  /** 当前套餐是否可安装；false 时市场照常展示，仅在安装/启用时拦截 */
  installable: boolean;
}

/** 市场分类项（PL1 `categories[]`，count 为该分类下可见插件数） */
export interface PluginCategoryItem {
  key: string;
  name: string;
  count: number;
}

/** 市场列表响应（PL1） */
export interface PluginMarketData {
  items: PluginMarketItem[];
  meta: PageMeta;
  categories: PluginCategoryItem[];
}

/** 插件产物声明（PL2 `outputs[]`，驱动结果区渲染） */
export interface PluginOutputSpec {
  key: string;
  label: string;
  type: 'text' | 'link' | 'file' | 'table';
}

/** 插件配置项声明（PL2 `settings_schema[]`，键必须 ⊆ 本表才可写 PL10） */
export interface PluginSettingSpec {
  key: string;
  label: string;
  type: 'text' | 'select';
  options?: string[];
}

/** 版本简介（PL2 `versions[]`，只保留最近 2 个版本） */
export interface PluginVersionBrief {
  version: string;
  published_at: string;
}

/**
 * 版本说明。
 *
 * 契约里 PL2 记作 `changelog[]`，而声明文件里 `changelog` 是单个字符串（≤300 字），
 * 因此两种形态都要能接：渲染前统一用 `toStringArray()` 归一。
 */
export type PluginChangelog = string[] | string;

/** 插件详情（PL2） */
export interface PluginDetail {
  plugin_id: string;
  name: string;
  summary: string;
  description: string;
  category: PluginCategoryKey | string;
  icon_key: string;
  vendor?: string;
  rating_avg: number;
  rating_count: number;
  latest_version: string;
  capabilities: PluginCapabilityItem[];
  /** 步骤标题（用户只看到标题；执行原语不进界面） */
  step_titles: string[];
  outputs: PluginOutputSpec[];
  settings_schema: PluginSettingSpec[];
  changelog?: PluginChangelog;
  versions: PluginVersionBrief[];
  installed_status: PluginInstalledStatus;
  /** 已安装版本；未安装时为空串 */
  installed_version: string;
  has_update: boolean;
  installable: boolean;
}

/** 能力清单响应（PL4） */
export interface PluginCapabilitiesData {
  items: PluginCapabilityItem[];
  /** 我已授权的能力 key */
  granted_capabilities: string[];
  /** 企业能力开关最后更新时间 */
  org_switch_updated_at: string;
  /** 当前套餐是否可安装 */
  installable: boolean;
  /**
   * 企业能力开关快照（可选）。
   *
   * 设计以 `items[].org_switch_enabled` 为准；本字段仅为兼容后端可能下发的扁平视图，
   * 前端不得据此覆盖逐能力字段（见 B22 回传的歧义点 #3）。
   */
  org_capabilities?: string[];
}

/** 已安装条目（PL3） */
export interface InstalledPluginItem {
  plugin_id: string;
  name: string;
  category: PluginCategoryKey | string;
  version: string;
  latest_version: string;
  has_update: boolean;
  status: Exclude<PluginInstalledStatus, 'none'>;
  /** 最近使用时间；从未使用为 null */
  last_used_at: string | null;
  running_task_count: number;
}

/** 安装请求（PL5）：grants 必须覆盖声明的全部必需能力 */
export interface PluginInstallRequest {
  grants: Record<string, boolean>;
  settings?: Record<string, unknown>;
}

/** 安装结果（PL5） */
export interface PluginInstallResult {
  plugin_id: string;
  status: PluginState;
  version: string;
  granted_capabilities: string[];
  denied_capabilities: string[];
}

/** 启停结果（PL6）：running_task_count 供「在跑任务不受影响」提示 */
export interface PluginStateResult {
  plugin_id: string;
  status: PluginState;
  running_task_count: number;
}

/** 卸载结果（PL7）：历史任务与产物保留 */
export interface PluginUninstallResult {
  plugin_id: string;
  uninstalled: boolean;
  running_task_count: number;
  kept_task_count: number;
}

/** 升级结果（PL8） */
export interface PluginUpgradeResult {
  plugin_id: string;
  version: string;
  previous_version: string;
  has_update: boolean;
}

/** 回退结果（PL9） */
export interface PluginRollbackResult {
  plugin_id: string;
  version: string;
  previous_version: string;
}

/** 配置保存结果（PL10） */
export interface PluginSettingsResult {
  plugin_id: string;
  settings: Record<string, unknown>;
  updated_at: string;
}

/** 评价条目（PL11） */
export interface PluginReviewItem {
  review_id: string;
  rating: number;
  content: string;
  /** 匿名评价时为「某位同事」 */
  author_name: string;
  department_name: string;
  created_at: string;
}

/** 评价列表响应（PL11） */
export interface PluginReviewsData {
  items: PluginReviewItem[];
  meta: PageMeta;
  rating_avg: number;
  rating_count: number;
}

/** 提交评价请求（PL12）：同一成员重复提交为覆盖更新 */
export interface PluginReviewRequest {
  rating: number;
  content: string;
  anonymous: boolean;
}

/** 提交评价结果（PL12），rating_avg/count 由服务端重算 */
export interface PluginReviewResult {
  review_id: string;
  rating: number;
  created_at: string;
  rating_avg: number;
  rating_count: number;
}

/** 企业能力开关条目（PL13） */
export interface CapabilityPolicyItem {
  capability: PluginCapabilityKey | string;
  display_name: string;
  enabled: boolean;
  updated_by_name: string;
  updated_at: string;
}

/** 企业能力开关写请求（PL13）：只允许 org.* 三项 */
export interface CapabilityPolicyRequest {
  policies: Array<{ capability: string; enabled: boolean }>;
}

// ---------------------------------------------------------------------------
// 阶段三：任务域加性扩展（A1-A4 与 WF1，架构文档 §5.3）
// ---------------------------------------------------------------------------

/**
 * 任务结果里的核查清单项。
 *
 * `status` 三态：已核查 / 待人工确认（降级跳过时标待确认）/ 未核查。
 */
export interface TaskChecklistItem {
  /** 清单项稳定标识 */
  key: string;
  /** 用户可见的核查项名称 */
  name: string;
  /** 产出该项的步骤序号 */
  source_step: number;
  status: 'checked' | 'pending' | 'unchecked';
}

/** 任务告警（降级继续时「哪一步没做成、影响什么」） */
export interface TaskWarning {
  step_seq: number;
  /** 业务语言说明 */
  message: string;
}

/** 人工接管信息（A3 `confirm`）：任务停在某步等用户决定 */
export interface TaskConfirmInfo {
  step_seq: number;
  reason: string;
  /** 需要用户重点看的字段值摘要 */
  summary: Record<string, unknown>;
}

/** 步骤产物（A4 `steps[].artifacts[]`，5 类渲染见 ArtifactCard） */
export interface TaskArtifact {
  artifact_id: string;
  task_id?: string;
  step_id?: string;
  /** 对应声明里的 outputs[].key */
  output_key?: string;
  type: 'text' | 'link' | 'file' | 'table';
  name: string;
  /** 文本类内容（已脱敏，≤32KB） */
  inline_text?: string | null;
  /** 表格类预览（前 5 行） */
  preview_text?: string | null;
  /** 链接类地址（http/https） */
  external_url?: string | null;
  /** 文件类下载地址 */
  storage_key?: string | null;
  size_bytes?: number | null;
  /** 过期时间（180 天），过期后提示「产物已过期，可重新发起任务」 */
  expires_at?: string | null;
  created_at?: string;
}

/** 步骤失败信息（A4 `steps[].failure`）：失败是状态，不是整页错误 */
export interface TaskStepFailure {
  /** 业务语言原因 */
  reason_text: string;
  /** 是否可重试（决定是否展示「重试本步」） */
  retryable: boolean;
  /** 建议动作（业务语言） */
  operator_action: string;
}

/** 任务详情（A3） */
export interface TaskDetail {
  task_id: string;
  conversation_id?: string;
  goal: string;
  status: TaskStatus;
  progress: number;
  current_step: string | null;
  plugin_id?: string | null;
  /** 重试来源任务 */
  retry_of?: string | null;
  steps_total?: number;
  result: TaskResult | null;
  /** 人工接管信息；非 waiting_confirm 时为 null */
  confirm?: TaskConfirmInfo | null;
  usage?: { total_units?: number };
  created_at?: string;
  updated_at: string;
}

/** WF1 重试请求（唯一的任务级写端点） */
export interface TaskRetryRequest {
  /** 从第几步重跑；不传由后端按状态推断 */
  from_step?: number;
  /** 仅 waiting_confirm 态使用：继续 / 重做 */
  decision?: 'continue' | 'redo';
}

/** WF1 重试结果 */
export interface TaskRetryResult {
  task_id: string;
  retry_of: string;
  status: TaskStatus;
  from_step: number;
}

/** A1 创建插件任务时的排队信息（并发上限 3，超出排队） */
export interface TaskQueueInfo {
  /** 是否进入排队（仅排队时下发） */
  queued?: boolean;
  /** 前面还有几个任务在跑（仅排队时下发） */
  ahead_count?: number;
  /**
   * 排队位次（可选）。
   *
   * 契约以 `queued + ahead_count` 为准；本字段为后端可能附带的冗余位次，
   * 前端展示优先用 `ahead_count`（见 B22 回传的歧义点 #4）。
   */
  queue_position?: number;
}
