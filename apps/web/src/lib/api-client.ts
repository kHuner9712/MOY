/**
 * 适配层 API 客户端 —— 前端**唯一**允许调用后端的出口。
 *
 * 红线（架构文档 1.4 / 6.2）：
 * - 本文件是前端访问自研统一适配层的唯一通道；
 * - 严禁在本文件或任何前端代码中出现底座地址、底座路径、底座端口；
 * - 严禁绕过本文件直接 fetch 其他后端服务。
 *
 * 统一约定（架构文档 3.0.1 / 3.0.2 / 3.0.4）：
 * - 所有接口返回 `{code, message, data}`，`code=0` 成功；
 * - 除健康检查外均需 `Authorization: Bearer <token>`；
 * - 所有写接口必须携带 `Idempotency-Key`（UUID v4，24h 内有效）。
 */

// ---------------------------------------------------------------------------
// 基础类型
// ---------------------------------------------------------------------------

/** 统一响应包装 */
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T | null;
}

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

/** 健康检查数据 */
export interface HealthData {
  status: string;
  app: string;
  version: string;
  env: string;
  deploy_mode: string;
  downstream_enabled: boolean;
}

/** 适配层不可用等网络层异常 */
export class AdapterUnavailableError extends Error {
  constructor(message = '服务暂时不可用，请稍后再试') {
    super(message);
    this.name = 'AdapterUnavailableError';
  }
}

/** 业务错误：携带 code 与业务语言 message */
export class BusinessError extends Error {
  readonly code: number;

  constructor(code: number, message: string) {
    super(message);
    this.name = 'BusinessError';
    this.code = code;
  }
}

// ---------------------------------------------------------------------------
// 运行时配置
// ---------------------------------------------------------------------------

/** 适配层地址（来自 NEXT_PUBLIC_ADAPTER_BASE_URL） */
const ADAPTER_BASE_URL: string =
  process.env.NEXT_PUBLIC_ADAPTER_BASE_URL ?? 'http://127.0.0.1:8000';

/** API 版本前缀 */
const API_PREFIX = '/api/v1';

/** 链路追踪请求头 */
const TRACE_HEADER = 'X-Trace-Id';

/** 租户请求头 */
const TENANT_HEADER = 'X-Tenant-Id';

/** 幂等键请求头 */
const IDEMPOTENCY_HEADER = 'Idempotency-Key';

/** 默认租户（私有化模式） */
const DEFAULT_TENANT_ID = 'default';

/** 请求超时（毫秒） */
const REQUEST_TIMEOUT_MS = 60_000;

/** 流式首包超时（毫秒），对齐适配层 3.0.4 策略 */
const STREAM_TIMEOUT_MS = 15_000;

// ---------------------------------------------------------------------------
// 令牌与请求头
// ---------------------------------------------------------------------------

/** 访问令牌内存缓存（由登录流程写入） */
let accessToken: string | null = null;

/** 当前租户 */
let currentTenantId: string = DEFAULT_TENANT_ID;

/**
 * 设置访问令牌。
 *
 * @param token - OIDC 访问令牌
 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/**
 * 获取当前访问令牌。
 *
 * @returns 令牌字符串；未登录时为 null
 */
export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * 设置当前租户。
 *
 * @param tenantId - 租户标识
 */
export function setTenantId(tenantId: string): void {
  currentTenantId = tenantId;
}

/** 生成 UUID v4 幂等键 */
function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // 兜底实现：老旧浏览器环境
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const rand = Math.floor(Math.random() * 16);
    const value = ch === 'x' ? rand : (rand & 0x3) | 0x8;
    return value.toString(16);
  });
}

/** 构造请求头 */
function buildHeaders(options: {
  method: string;
  body?: BodyInit | null;
  idempotent?: boolean;
  tenantId?: string;
  accept?: string;
}): Headers {
  const headers = new Headers();
  headers.set('Accept', options.accept ?? 'application/json');

  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`);
  }
  if (!options.tenantId) {
    headers.set(TENANT_HEADER, currentTenantId);
  }

  if (options.body !== undefined && options.body !== null) {
    headers.set('Content-Type', 'application/json');
  }

  const isWrite = options.method !== 'GET' && options.method !== 'HEAD';
  if (isWrite && options.idempotent !== false) {
    headers.set(IDEMPOTENCY_HEADER, newIdempotencyKey());
  }

  return headers;
}

/** 带超时的 fetch */
async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new AdapterUnavailableError('服务响应超时，请重试');
    }
    throw new AdapterUnavailableError();
  } finally {
    clearTimeout(timer);
  }
}

/** 读取统一响应并按 code 分流 */
async function unwrap<T>(response: Response): Promise<T> {
  let payload: ApiResponse<T>;
  try {
    payload = (await response.json()) as ApiResponse<T>;
  } catch {
    throw new AdapterUnavailableError();
  }

  if (payload.code === 0) {
    return payload.data as T;
  }
  throw new BusinessError(payload.code, payload.message);
}

/** 请求选项 */
interface RequestOptions {
  /** 查询参数 */
  query?: Record<string, string | number | boolean | undefined>;
  /** 请求体（对象，自动 JSON 序列化） */
  body?: unknown;
  /** 是否携带幂等键，默认写接口自动携带 */
  idempotent?: boolean;
  /** 自定义租户 */
  tenantId?: string;
  /** 超时（毫秒） */
  timeoutMs?: number;
}

/** 拼接查询串 */
function buildQuery(query: RequestOptions['query']): string {
  if (!query) {
    return '';
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : '';
}

/** 执行一次适配层请求 */
async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const url = `${ADAPTER_BASE_URL}${API_PREFIX}${path}${buildQuery(options.query)}`;
  const headers = buildHeaders({
    method,
    body: options.body === undefined ? null : JSON.stringify(options.body),
    idempotent: options.idempotent,
    tenantId: options.tenantId,
  });

  const response = await fetchWithTimeout(
    url,
    {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
    },
    options.timeoutMs ?? REQUEST_TIMEOUT_MS,
  );

  const traceId = response.headers.get(TRACE_HEADER);
  if (traceId) {
    // 便于问题排查：链路 ID 只进控制台，不展示给终端用户
    console.debug('[trace]', traceId, method, path);
  }

  return unwrap<T>(response);
}

/** 执行 multipart 上传 */
async function upload<T>(path: string, form: FormData, options: RequestOptions = {}): Promise<T> {
  const url = `${ADAPTER_BASE_URL}${API_PREFIX}${path}`;
  const headers = new Headers();
  headers.set('Accept', 'application/json');
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`);
  }
  headers.set(TENANT_HEADER, options.tenantId ?? currentTenantId);
  headers.set(IDEMPOTENCY_HEADER, newIdempotencyKey());

  const response = await fetchWithTimeout(
    url,
    { method: 'POST', headers, body: form, cache: 'no-store' },
    options.timeoutMs ?? REQUEST_TIMEOUT_MS,
  );
  return unwrap<T>(response);
}

// ---------------------------------------------------------------------------
// 模型服务 M1-M4
// ---------------------------------------------------------------------------

/** 生成请求 */
export interface GenerateRequest {
  conversation_id: string;
  profile: 'general' | 'code' | 'long_context' | 'reasoning';
  messages: Array<{ role: 'user' | 'assistant' | 'system'; content: string }>;
  context?: { history_window?: number; memory_hints?: string[] };
  knowledge_base_ids?: string[];
  options?: { temperature?: number; max_tokens?: number };
}

/** 生成结果 */
export interface GenerateResult {
  generation_id: string;
  content: string;
  finish_reason: 'stop' | 'length' | 'error' | 'cancelled';
  usage: { prompt_units: number; completion_units: number; total_units: number };
  citations: Array<{
    document_id: string;
    document_name: string;
    segment_content: string;
    score: number;
  }>;
}

/** 能力档位条目 */
export interface ProfileItem {
  profile: 'general' | 'code' | 'long_context' | 'reasoning';
  display_name: string;
  description: string;
  max_context_turns: number;
  status: 'available' | 'unavailable';
}

/** 模型服务接口 */
export const modelApi = {
  /** M1 非流式生成 */
  generate(body: GenerateRequest): Promise<GenerateResult> {
    return request<GenerateResult>('POST', '/model/generate', { body });
  },

  /** M3 能力档位目录 */
  catalog(): Promise<{ profiles: ProfileItem[] }> {
    return request<{ profiles: ProfileItem[] }>('GET', '/model/catalog');
  },

  /** M4 用量统计（业务化口径：次数/额度） */
  usage(period: 'day' | 'month' = 'month'): Promise<{
    period: string;
    conversation_count: number;
    document_qa_count: number;
    quota: {
      plan_name: string;
      total_units: number;
      used_units: number;
      remaining_units: number;
    };
  }> {
    return request('GET', '/model/usage', { query: { period } });
  },

  /** M2 流式生成：回调自研事件（start/delta/citation/done/error） */
  stream(
    body: GenerateRequest,
    handlers: {
      onStart?: (data: { generation_id: string }) => void;
      onDelta?: (data: { text: string }) => void;
      onCitation?: (data: { document_name: string; segment_content: string }) => void;
      onDone?: (data: {
        finish_reason: string;
        usage: { prompt_units: number; completion_units: number; total_units: number };
      }) => void;
      onError?: (data: { code: number; message: string }) => void;
    },
  ): () => void {
    return openStream('/model/generate/stream', body, handlers);
  },
};

// ---------------------------------------------------------------------------
// 通用 SSE 读取
// ---------------------------------------------------------------------------

/** 打开一条 SSE 流，返回取消函数 */
function openStream(
  path: string,
  body: unknown,
  handlers: Record<string, ((data: never) => void) | undefined>,
): () => void {
  const url = `${ADAPTER_BASE_URL}${API_PREFIX}${path}`;
  const controller = new AbortController();

  void (async () => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: buildHeaders({ method: 'POST', body: JSON.stringify(body) }),
        body: JSON.stringify(body),
        signal: controller.signal,
        cache: 'no-store',
      });

      if (!response.ok || !response.body) {
        const onError = handlers.onError as ((d: { code: number; message: string }) => void) | undefined;
        onError?.({ code: 30001, message: '服务暂时不可用，请稍后再试' });
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });

        let separator = buffer.indexOf('\n\n');
        while (separator !== -1) {
          const rawFrame = buffer.slice(0, separator);
          buffer = buffer.slice(separator + 2);
          dispatchFrame(rawFrame, handlers);
          separator = buffer.indexOf('\n\n');
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const onError = handlers.onError as ((d: { code: number; message: string }) => void) | undefined;
      onError?.({
        code: error instanceof AdapterUnavailableError ? 30002 : 30001,
        message: error instanceof Error ? error.message : '服务暂时不可用，请稍后再试',
      });
    }
  })();

  return () => controller.abort();
}

/** 解析并分发单个 SSE 帧 */
function dispatchFrame(rawFrame: string, handlers: Record<string, ((data: never) => void) | undefined>): void {
  let eventName = 'message';
  const dataLines: string[] = [];

  for (const line of rawFrame.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) {
    return;
  }

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join('\n'));
  } catch {
    return;
  }

  const handler = handlers[eventName];
  handler?.(payload as never);
}

// ---------------------------------------------------------------------------
// RAG 服务 R1-R7
// ---------------------------------------------------------------------------

/** 知识库条目 */
export interface KnowledgeBaseItem {
  base_id: string;
  name: string;
  description: string;
  visibility: 'organization' | 'private';
  document_count: number;
  created_at: string;
}

/** RAG 服务接口 */
export const knowledgeApi = {
  /** R1 创建知识库 */
  createBase(body: { name: string; description?: string; visibility?: 'organization' | 'private' }): Promise<{
    base_id: string;
    name: string;
    created_at: string;
  }> {
    return request('POST', '/knowledge/bases', { body });
  },

  /** R2 知识库列表 */
  listBases(params: { page?: number; page_size?: number; keyword?: string } = {}): Promise<
    PageResult<KnowledgeBaseItem>
  > {
    return request('GET', '/knowledge/bases', { query: params });
  },

  /** R3 上传文档（multipart，异步索引） */
  uploadDocument(
    baseId: string,
    file: File,
    chunkPolicy: 'default' | 'fine' = 'default',
  ): Promise<{
    document_id: string;
    file_name: string;
    index_status: 'processing' | 'completed' | 'failed';
    progress: number;
  }> {
    const form = new FormData();
    form.set('file', file);
    form.set('chunk_policy', chunkPolicy);
    return upload(`/knowledge/bases/${baseId}/documents`, form);
  },

  /** R4 索引进度查询 */
  documentStatus(baseId: string, docId: string): Promise<{
    document_id: string;
    index_status: 'processing' | 'completed' | 'failed';
    progress: number;
    segment_count: number;
    error_message: string | null;
  }> {
    return request('GET', `/knowledge/bases/${baseId}/documents/${docId}`);
  },

  /** R5 重建索引 */
  reindexDocument(baseId: string, docId: string): Promise<{
    document_id: string;
    index_status: string;
    progress: number;
  }> {
    return request('POST', `/knowledge/bases/${baseId}/documents/${docId}/reindex`);
  },

  /** R6 删除文档 */
  deleteDocument(baseId: string, docId: string): Promise<{ id: string; deleted: boolean }> {
    return request('DELETE', `/knowledge/bases/${baseId}/documents/${docId}`);
  },

  /** R7 独立检索 */
  search(body: { base_id: string; query: string; top_k?: number; score_threshold?: number }): Promise<{
    segments: Array<{
      document_id: string;
      document_name: string;
      segment_content: string;
      score: number;
      position: { page: number | null; paragraph: number | null } | null;
    }>;
  }> {
    return request('POST', '/knowledge/search', { body });
  },
};

// ---------------------------------------------------------------------------
// Agent 服务 A1-A6
// ---------------------------------------------------------------------------

/** 任务条目 */
export interface TaskItem {
  task_id: string;
  conversation_id: string;
  goal: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  created_at: string;
  updated_at: string;
}

/** Agent 服务接口 */
export const agentApi = {
  /** A1 创建任务 */
  createTask(body: {
    conversation_id: string;
    goal: string;
    profile?: 'general' | 'code' | 'long_context' | 'reasoning';
    tool_grants?: string[];
    inputs?: Record<string, unknown>;
    options?: { max_steps?: number; timeout_seconds?: number };
  }): Promise<{ task_id: string; status: string; created_at: string; estimated_seconds: number }> {
    return request('POST', '/agent/tasks', { body });
  },

  /** A2 任务列表 */
  listTasks(params: { page?: number; page_size?: number; status?: string } = {}): Promise<
    PageResult<TaskItem>
  > {
    return request('GET', '/agent/tasks', { query: params });
  },

  /** A3 任务状态查询 */
  getTask(taskId: string): Promise<{
    task_id: string;
    status: string;
    progress: number;
    current_step: string | null;
    result: { summary: string; artifacts: Array<Record<string, unknown>> } | null;
    usage: { total_units: number };
    updated_at: string;
  }> {
    return request('GET', `/agent/tasks/${taskId}`);
  },

  /** A4 步骤详情 */
  listSteps(taskId: string): Promise<{
    task_id: string;
    steps: Array<{
      step_id: string;
      sequence: number;
      title: string;
      status: string;
      started_at: string | null;
      finished_at: string | null;
      output_summary: string | null;
    }>;
  }> {
    return request('GET', `/agent/tasks/${taskId}/steps`);
  },

  /** A5 取消任务 */
  cancelTask(taskId: string): Promise<{ task_id: string; status: string; updated_at: string }> {
    return request('POST', `/agent/tasks/${taskId}/cancel`);
  },

  /** A6 任务事件推送 */
  streamTaskEvents(
    taskId: string,
    handlers: {
      onStepStarted?: (data: Record<string, unknown>) => void;
      onStepFinished?: (data: Record<string, unknown>) => void;
      onTaskCompleted?: (data: Record<string, unknown>) => void;
      onError?: (data: { code: number; message: string }) => void;
    },
  ): () => void {
    return openStream(`/agent/tasks/${taskId}/events`, {}, handlers);
  },
};

// ---------------------------------------------------------------------------
// 工具服务 T1-T7
// ---------------------------------------------------------------------------

/** 工具类型条目（前端按注册表动态渲染，不硬编码工具列表） */
export interface ToolItem {
  tool_type: string;
  display_name: string;
  category: string;
  auth_type: 'oauth2' | 'token' | 'none';
  status: 'available' | 'unavailable';
  operations: Array<{ operation: string; display_name: string; params_schema: Record<string, unknown> }>;
  timeout_ms: number;
}

/** 工具服务接口 */
export const toolApi = {
  /** T1 工具类型注册表 */
  registry(): Promise<{ tools: ToolItem[] }> {
    return request('GET', '/tools/registry');
  },

  /** T2 发起工具授权连接 */
  createConnection(body: { tool_type: string; auth_code: string; redirect_uri?: string }): Promise<{
    connection_id: string;
    tool_type: string;
    status: string;
    expires_at: string | null;
  }> {
    return request('POST', '/tools/connections', { body });
  },

  /** T3 已连接工具列表 */
  listConnections(params: { page?: number; page_size?: number; tool_type?: string } = {}): Promise<
    PageResult<{
      connection_id: string;
      tool_type: string;
      display_name: string;
      status: string;
      connected_at: string;
      expires_at: string | null;
    }>
  > {
    return request('GET', '/tools/connections', { query: params });
  },

  /** T4 断开工具连接 */
  deleteConnection(connId: string): Promise<{ id: string; deleted: boolean }> {
    return request('DELETE', `/tools/connections/${connId}`);
  },

  /** T5 执行工具操作 */
  execute(body: { tool_type: string; operation: string; params: Record<string, unknown> }): Promise<{
    execution_id: string;
    tool_type: string;
    operation: string;
    status: 'success' | 'failed';
    result: Record<string, unknown> | null;
    error: string | null;
  }> {
    return request('POST', '/tools/execute', { body });
  },

  /** T6 创建数据同步任务 */
  createSyncJob(body: { tool_type: string; operation?: string; scope?: string }): Promise<{
    job_id: string;
    tool_type: string;
    status: string;
    progress: number;
    created_at: string;
  }> {
    return request('POST', '/tools/sync-jobs', { body });
  },

  /** T7 查询同步任务进度 */
  syncJob(jobId: string): Promise<{
    job_id: string;
    tool_type: string;
    status: string;
    progress: number;
    processed_count: number;
    total_count: number;
    error: string | null;
    updated_at: string;
  }> {
    return request('GET', `/tools/sync-jobs/${jobId}`);
  },
};

// ---------------------------------------------------------------------------
// 用户权限服务 U1-U6
// ---------------------------------------------------------------------------

/** 当前用户信息 */
export interface ProfileData {
  user_id: string;
  display_name: string;
  email: string;
  tenant: { tenant_id: string; name: string; plan: string };
  organizations: Array<{ org_id: string; name: string; role: string }>;
}

/** 用户权限服务接口 */
export const identityApi = {
  /** U1 令牌与权限点校验 */
  verify(body: { permission: string; resource?: { type: string; id: string } }): Promise<{
    allowed: boolean;
    user_id: string;
    tenant_id: string;
    roles: string[];
  }> {
    return request('POST', '/identity/verify', { body });
  },

  /** U2 当前用户信息 */
  profile(): Promise<ProfileData> {
    return request('GET', '/identity/profile');
  },

  /** U3 组织成员列表 */
  listMembers(
    orgId: string,
    params: { page?: number; page_size?: number; role?: string; keyword?: string } = {},
  ): Promise<
    PageResult<{
      user_id: string;
      display_name: string;
      email: string;
      role: string;
      joined_at: string;
    }>
  > {
    return request('GET', `/identity/organizations/${orgId}/members`, { query: params });
  },

  /** U4 邀请成员 */
  inviteMember(
    orgId: string,
    body: { email: string; role?: string; message?: string },
  ): Promise<{ invitation_id: string; org_id: string; email: string; role: string; status: string }> {
    return request('POST', `/identity/organizations/${orgId}/invitations`, { body });
  },

  /** U5 创建/分配角色 */
  upsertRole(
    orgId: string,
    body: { role_key: string; display_name?: string; permissions?: string[]; members?: string[] },
  ): Promise<{ role_id: string; role_key: string; display_name: string; permissions: string[] }> {
    return request('POST', `/identity/organizations/${orgId}/roles`, { body });
  },

  /** U6 审计日志查询 */
  auditLogs(body: {
    page?: number;
    page_size?: number;
    actor_id?: string;
    action?: string;
    resource_type?: string;
    start_at?: string;
    end_at?: string;
  }): Promise<PageResult<Record<string, unknown>>> {
    return request('POST', '/identity/audit-logs', { body });
  },
};

// ---------------------------------------------------------------------------
// 健康检查（免鉴权）
// ---------------------------------------------------------------------------

/** 存活探针 */
export async function checkHealth(): Promise<HealthData> {
  const url = `${ADAPTER_BASE_URL}/healthz`;
  const response = await fetchWithTimeout(url, { method: 'GET', cache: 'no-store' }, 5_000);
  return unwrap<HealthData>(response);
}

/** 就绪探针 */
export async function checkReady(): Promise<{ status: string }> {
  const url = `${ADAPTER_BASE_URL}/readyz`;
  const response = await fetchWithTimeout(url, { method: 'GET', cache: 'no-store' }, 5_000);
  return unwrap<{ status: string }>(response);
}

/** 适配层地址（供诊断页面展示，不含任何底座信息） */
export function getAdapterBaseUrl(): string {
  return ADAPTER_BASE_URL;
}

export { STREAM_TIMEOUT_MS };
