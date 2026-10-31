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
// 公共
// ---------------------------------------------------------------------------

import type {
  CapabilityCard,
  CapabilityInputBrief,
  CapabilityPolicyItem,
  CapabilityPolicyRequest,
  CapabilityRecommendData,
  InstalledPluginItem,
  PluginCapabilitiesData,
  PluginDetail,
  PluginGrantsUpdateRequest,
  PluginGrantsUpdateResult,
  PluginInstallRequest,
  PluginInstallResult,
  PluginMarketData,
  PluginMarketSort,
  PluginReviewRequest,
  PluginReviewResult,
  PluginReviewsData,
  PluginRollbackResult,
  PluginSettingsResult,
  PluginState,
  PluginStateResult,
  PluginUninstallResult,
  PluginUpgradeResult,
  TaskArtifactUpdateRequest,
  TaskArtifactUpdateResult,
  TaskDetail,
  TaskQueueInfo,
  TaskRetryRequest,
  TaskRetryResult,
  TaskStatsData,
  TaskStatsRange,
  TaskStatus,
  ActionGroup,
  AuditLogItem,
  BatchMemberRequest,
  BatchMemberResult,
  BillingInvoiceItem,
  BillingOrderDetail,
  BillingOrderItem,
  BillingUsageDetail,
  ChangeSubscriptionRequest,
  ChangeSubscriptionResult,
  ChunkPolicy,
  CreateDepartmentRequest,
  CreateDepartmentResult,
  CreateGrantRequest,
  CreateGrantResult,
  CreateOrderRequest,
  CreateOrderResult,
  CredentialBindingResult,
  CurrentUser,
  DeleteBaseResult,
  DeleteDepartmentResult,
  DeleteGrantResult,
  DepartmentNode,
  DocumentListQuery,
  FlatPageResult,
  GenerateRequest,
  GenerateResult,
  GrantItem,
  KnowledgeBaseDetail,
  KnowledgeDocumentItem,
  OpsConfirmOrderResult,
  OpsMetrics,
  OpsMetricsRange,
  OpsOrderItem,
  OpsSettings,
  OpsTenantDetail,
  OpsTenantItem,
  OpsTenantStatusResult,
  OrganizationMember,
  PaymentCallbackResult,
  PermissionGroup,
  PlanItem,
  ProfileItem,
  RemoveMemberResult,
  RoleItem,
  SegmentDetail,
  SubscriptionDetail,
  TaskItem,
  TaskStep,
  ToolConnection,
  ToolExecutionItem,
  ToolSpec,
  UpdateBaseRequest,
  UpdateBaseResult,
  UpdateDepartmentRequest,
  UpdateDepartmentResult,
  UpdateMemberRequest,
  UpdateMemberResult,
  UpdateOpsSettingsRequest,
  UpdateSeatsResult,
  UsageGroupBy,
  VerifyResult,
  Visibility,
  MemoryBatchDeleteResult,
  MemoryCategory,
  MemoryDeleteResult,
  MemoryPage,
  MemorySettings,
  MemorySettingsUpdate,
} from '@/lib/types';

import { emitUnauthorized } from './session-events';

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

/**
 * 「登录状态已失效」的业务错误码。
 *
 * 与后端 `AppCode.UNAUTHORIZED` 一致；本文件是前端唯一的处置点（见 `unwrap`）。
 */
const UNAUTHORIZED_CODE = 20002;

/** 请求超时（毫秒） */
const REQUEST_TIMEOUT_MS = 60_000;

/** 流式首包超时（毫秒），对齐适配层 3.0.4 策略 */
const STREAM_TIMEOUT_MS = 15_000;

// ---------------------------------------------------------------------------
// 令牌与请求头
// ---------------------------------------------------------------------------

/** 访问令牌内存缓存（由登录流程写入） */
let accessToken: string | null = null;

/**
 * 当前租户（`null` = 尚不知道，一律**不带**该请求头）。
 *
 * 为什么初值不是 `'default'`：租户只能从服务端学到。若这里预设一个值，
 * 首个 `profile` 就会带上 `X-Tenant-Id: <猜的租户>`，而后端 `resolve_tenant_id`
 * 让请求头**优先于**令牌声明 —— 客户端与服务端互相自举，用户永远学不到真租户
 * （SaaS 下表现为首个 `profile` 直接 403，或把会话永久钉在猜的那个租户上）。
 *
 * 正确顺序：先**不带头**问一次 `profile`（后端按令牌声明解析），拿到
 * `tenant.tenant_id` 后再 `setTenantId(...)`，此后每个请求的头都等于令牌租户。
 * 私有化模式后端忽略该头，行为不变。
 */
let currentTenantId: string | null = null;

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
 * @param tenantId - 租户标识；传 `null` 表示「尚未知」，之后发起的请求不带该头
 */
export function setTenantId(tenantId: string | null): void {
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
  // 只有「已经知道租户」时才带该头：未知时留空，让后端按令牌声明解析。
  // 反过来（预设一个默认值）会让首个 profile 自证租户，形成循环。
  if (!options.tenantId && currentTenantId !== null) {
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
async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
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
  // 阶段四：`15006`（额度用尽）的传输层状态由 200 改为 **402**。
  // 这里**不按 status 分流**，一律走 `code`——这正是要保住的行为：
  //   402 仍然是「业务响应」，必须落进 BusinessError 展示后端 message，
  //   绝不能因为 `!response.ok` 被当成网络/系统错误（那会退化成"系统繁忙，
  //   请稍后再试"，用户永远不知道是额度用完了）。
  // 现状成立的前提是 `fetchWithTimeout` 不对 4xx/5xx 抛错（fetch 语义），
  // 且后端 402 响应体始终是统一信封 `{code,message,data}`。
  if (payload.code === UNAUTHORIZED_CODE) {
    // 会话失效的处置**只有这一处出口**：先在传输层广播，由会话门统一清会话并跳登录。
    // 不在各页面重复实现，是因为 20 个页面平均 3 个查询 = 60 个调用点，
    // 新增页面必然漏写，漏写后就会把「登录状态已失效」这种内部状态词留给用户。
    emitUnauthorized();
  }
  throw new BusinessError(payload.code, payload.message);
}

/**
 * 查询参数值类型。
 *
 * 允许直接传入接口定义的查询对象（interface 不带索引签名），
 * 序列化时统一 `String(value)`，因此不做类型收窄要求。
 */
type QueryParams = Record<string, unknown>;

/** 请求选项 */
interface RequestOptions {
  /** 查询参数 */
  query?: QueryParams;
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

/**
 * 网关层状态码 → 业务错误（413 / 429）。
 *
 * 网关（nginx / 反向代理）的 413、429 **不带统一信封**，落到 `unwrap` 会被退化成
 * 「服务暂时不可用」——用户永远不知道是文件太大还是请求太密。码复用后端已注册的
 * 20008（PAYLOAD_TOO_LARGE）/ 20006（RATE_LIMITED）；文案交给 `toBusinessMessage`
 * 按 code 取表，此处**不复制字符串**（`business-errors.ts` 本批不改）。
 *
 * 抽成函数是因为 `request()` 与 `upload()` 是两条独立实现：只补 `request()` 的话，
 * 恰恰最容易触发 413 的**大文件上传**仍然拿不到正确文案。
 *
 * @param response - fetch 的响应
 * @throws BusinessError 413 → 20008，429 → 20006
 */
function throwIfGatewayStatusRejected(response: Response): void {
  if (response.status === 413 || response.status === 429) {
    throw new BusinessError(response.status === 413 ? 20008 : 20006, '');
  }
}

/** 执行一次适配层请求 */
async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
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
    console.info('[trace]', traceId, method, path);
  }

  // HTTP 401 先于信封解析判定：网关 / 反向代理层的 401 可能没有统一信封，
  // 那样 `unwrap` 会退化成 AdapterUnavailableError，用户看到「服务暂时不可用」，
  // 而真实原因是登录过期——错因误导比错误本身更难排。
  if (response.status === 401) {
    emitUnauthorized();
    throw new BusinessError(UNAUTHORIZED_CODE, '登录状态已失效，请重新登录');
  }

  // 网关层的 413 / 429 同样**没有统一信封**，必须在 `unwrap` 之前拦下。
  // 顺序不能变：401 → 413/429 → `unwrap(code=20002)`；三者互不覆盖（上面那条 throw 即返回）。
  throwIfGatewayStatusRejected(response);

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
  // 与 `buildHeaders` 同一口径：租户未知时不带头（否则会写出字面量 "null"）
  const tenantHeader = options.tenantId ?? currentTenantId;
  if (tenantHeader !== null && tenantHeader !== undefined && tenantHeader !== '') {
    headers.set(TENANT_HEADER, tenantHeader);
  }
  headers.set(IDEMPOTENCY_HEADER, newIdempotencyKey());

  const response = await fetchWithTimeout(
    url,
    { method: 'POST', headers, body: form, cache: 'no-store' },
    options.timeoutMs ?? REQUEST_TIMEOUT_MS,
  );
  // 上传是 413 的主战场（网关 body limit 先于业务层生效），必须与 `request()` 同口径
  throwIfGatewayStatusRejected(response);
  return unwrap<T>(response);
}

// ---------------------------------------------------------------------------
// 模型服务 M1-M4
// ---------------------------------------------------------------------------

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
      onCitation?: (data: {
        document_name: string;
        segment_content: string;
        /** 阶段二加性字段（引用回链用） */
        document_id?: string;
        base_id?: string;
        base_name?: string;
        segment_id?: string;
        position?: { page: number | null; paragraph: number | null } | null;
      }) => void;
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
// 我的记忆（阶段四 MM1-MM4）
// ---------------------------------------------------------------------------

/**
 * 我的记忆接口（阶段四 MM1-MM4）。
 *
 * 两条约定：
 * 1. **不传 `user_id`**：身份由令牌决定，前端无从指定"查谁的记忆"；
 * 2. 写操作（MM2/MM3/MM4）由 `buildHeaders` 自动补 `Idempotency-Key`，
 *    重复点击不会产生第二次删除或第二次开关。
 */
export const memoryApi = {
  /** MM1 列表（类别筛选 + 分页；同包返回全局开关状态） */
  list(
    params: { category?: MemoryCategory | ''; page?: number; page_size?: number } = {},
  ): Promise<MemoryPage> {
    return request<MemoryPage>('GET', '/model/memories', {
      query: {
        category: params.category || undefined,
        page: params.page,
        page_size: params.page_size,
      },
    });
  },

  /** MM2 删除单条（不可撤销） */
  remove(memoryId: string): Promise<MemoryDeleteResult> {
    return request<MemoryDeleteResult>(
      'DELETE',
      `/model/memories/${encodeURIComponent(memoryId)}`,
      {
        idempotent: true,
      },
    );
  },

  /** MM3 按类别删除（不传类别 = 清空全部） */
  removeMany(category?: MemoryCategory | ''): Promise<MemoryBatchDeleteResult> {
    return request<MemoryBatchDeleteResult>('DELETE', '/model/memories', {
      query: { category: category || undefined },
      idempotent: true,
    });
  },

  /** MM4 更新开关 */
  updateSettings(body: MemorySettingsUpdate): Promise<MemorySettings> {
    return request<MemorySettings>('PUT', '/model/memories/settings', {
      body,
      idempotent: true,
    });
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
        const onError = handlers.onError as
          | ((d: { code: number; message: string }) => void)
          | undefined;
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
      const onError = handlers.onError as
        | ((d: { code: number; message: string }) => void)
        | undefined;
      onError?.({
        code: error instanceof AdapterUnavailableError ? 30002 : 30001,
        message: error instanceof Error ? error.message : '服务暂时不可用，请稍后再试',
      });
    }
  })();

  return () => controller.abort();
}

/**
 * SSE 事件名 → 回调键。
 *
 * 后端事件名是裸业务名（`delta` / `step_started` / `task_completed`），
 * 而回调对象的键是驼峰（`onDelta` / `onStepStarted` / `onTaskCompleted`）。
 * 分发必须做这一层映射，否则 `handlers['delta']` 永远取不到 `onDelta`——
 * 流式对话与任务事件推送的**全部回调都会静默失联**（第十批补测发现的缺陷）。
 *
 * @param eventName - SSE 帧的事件名（snake_case，如 `step_started`）
 * @returns 回调键（如 `onStepStarted`）
 */
function handlerKeyFor(eventName: string): string {
  const pascal = eventName
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');
  return `on${pascal}`;
}

/** 解析并分发单个 SSE 帧 */
function dispatchFrame(
  rawFrame: string,
  handlers: Record<string, ((data: never) => void) | undefined>,
): void {
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

  const handler = handlers[handlerKeyFor(eventName)];
  handler?.(payload as never);
}

// ---------------------------------------------------------------------------
// RAG 服务 R1-R7
// ---------------------------------------------------------------------------

/**
 * 知识库条目（R2 列表元素）。
 *
 * 阶段二为加性扩展（`chunk_policy` / `my_role` / `updated_at`），
 * 字段定义统一归档在 `lib/types.ts`，此处只保留别名以兼容既有引用。
 */
export type KnowledgeBaseItem = KnowledgeBaseDetail;

/** RAG 服务接口 */
export const knowledgeApi = {
  /** R1 创建知识库（阶段二加性字段：`chunk_policy`、`visibility=restricted`） */
  createBase(body: {
    name: string;
    description?: string;
    visibility?: Visibility;
    chunk_policy?: ChunkPolicy;
  }): Promise<{
    base_id: string;
    name: string;
    chunk_policy?: ChunkPolicy;
    created_at: string;
  }> {
    return request('POST', '/knowledge/bases', { body });
  },

  /** R2 知识库列表 */
  listBases(
    params: { page?: number; page_size?: number; keyword?: string } = {},
  ): Promise<PageResult<KnowledgeBaseItem>> {
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
  documentStatus(
    baseId: string,
    docId: string,
  ): Promise<{
    document_id: string;
    index_status: 'processing' | 'completed' | 'failed';
    progress: number;
    segment_count: number;
    error_message: string | null;
  }> {
    return request('GET', `/knowledge/bases/${baseId}/documents/${docId}`);
  },

  /** R5 重建索引 */
  reindexDocument(
    baseId: string,
    docId: string,
  ): Promise<{
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
  search(body: {
    base_id: string;
    query: string;
    top_k?: number;
    score_threshold?: number;
  }): Promise<{
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

/** Agent 服务接口 */
export const agentApi = {
  /**
   * A1 创建任务。
   *
   * 阶段三加性扩展：带 `plugin_id` 时按插件声明校验 `inputs` 并走本地任务引擎；
   * 不带时行为与阶段一**完全一致**（底座代理双模式）。
   */
  createTask(body: {
    conversation_id: string;
    goal: string;
    profile?: 'general' | 'code' | 'long_context' | 'reasoning';
    tool_grants?: string[];
    inputs?: Record<string, unknown>;
    options?: { max_steps?: number; timeout_seconds?: number };
    /** 插件任务必填；有值时 `inputs` 按插件声明校验 */
    plugin_id?: string;
  }): Promise<
    {
      task_id: string;
      status: TaskStatus;
      created_at: string;
      estimated_seconds: number;
      /** 重试产生的新任务会带上来源任务 id；首次为 null */
      retry_of?: string | null;
      /** 总步数（插件任务为声明步骤数） */
      steps_total?: number;
    } & TaskQueueInfo
  > {
    return request('POST', '/agent/tasks', { body });
  },

  /** A2 任务列表（阶段三加性筛选：插件 / 发起人 / 创建时间范围） */
  listTasks(
    params: {
      page?: number;
      page_size?: number;
      status?: string;
      plugin_id?: string;
      initiator_id?: string;
      created_from?: string;
      created_to?: string;
    } = {},
  ): Promise<PageResult<TaskItem>> {
    return request('GET', '/agent/tasks', { query: params });
  },

  /** A3 任务详情（含核查清单、告警与人工接管信息） */
  getTask(taskId: string): Promise<TaskDetail> {
    return request('GET', `/agent/tasks/${taskId}`);
  },

  /** A4 步骤详情（每步含产物与失败信息） */
  listSteps(taskId: string): Promise<{ task_id: string; steps: TaskStep[] }> {
    return request('GET', `/agent/tasks/${taskId}/steps`);
  },

  /** A5 取消任务 */
  cancelTask(taskId: string): Promise<{ task_id: string; status: string; updated_at: string }> {
    return request('POST', `/agent/tasks/${taskId}/cancel`);
  },

  /**
   * WF1 重试 / 继续 / 重做（阶段三唯一的任务级写端点）。
   *
   * - `waiting_confirm` + `decision=continue`：原地推进，不新建任务；
   * - `waiting_confirm` + `decision=redo`：新建任务，只重跑确认步及其下游；
   * - `failed` + `from_step`：新建任务，前序产物复制不重跑；
   * - 其余状态一律 17001（「该任务无法重试，请查看任务状态」）。
   */
  retryTask(taskId: string, body: TaskRetryRequest = {}): Promise<TaskRetryResult> {
    return request('POST', `/agent/tasks/${taskId}/retry`, { body });
  },

  /**
   * WF2 编辑产物并重新导出。
   *
   * 仅 `draft` / `doc` / `table` 可编辑；`link` / `file` 只读（UI 不渲染编辑入口）。
   * 保存即渲染：`download_url` 拿到的是新版本，**任务状态与执行流程都不变**；`version` 每次 +1。
   */
  updateArtifact(
    taskId: string,
    artifactId: string,
    body: TaskArtifactUpdateRequest,
  ): Promise<TaskArtifactUpdateResult> {
    return request('PUT', `/agent/tasks/${taskId}/artifacts/${artifactId}`, { body });
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

/** 工具服务接口 */
export const toolApi = {
  /** T1 工具类型注册表 */
  registry(): Promise<{ tools: ToolSpec[] }> {
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
  listConnections(
    params: { page?: number; page_size?: number; tool_type?: string } = {},
  ): Promise<PageResult<ToolConnection>> {
    return request('GET', '/tools/connections', { query: params });
  },

  /** T4 断开工具连接 */
  deleteConnection(connId: string): Promise<{ id: string; deleted: boolean }> {
    return request('DELETE', `/tools/connections/${connId}`);
  },

  /** T5 执行工具操作 */
  execute(body: {
    tool_type: string;
    operation: string;
    params: Record<string, unknown>;
  }): Promise<{
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
  verify(body: {
    permission: string;
    resource?: { type: string; id: string };
  }): Promise<VerifyResult> {
    return request('POST', '/identity/verify', { body });
  },

  /** U2 当前用户信息 */
  profile(): Promise<CurrentUser> {
    return request('GET', '/identity/profile');
  },

  /** U3 组织成员列表 */
  listMembers(
    orgId: string,
    params: { page?: number; page_size?: number; role?: string; keyword?: string } = {},
  ): Promise<PageResult<OrganizationMember>> {
    return request('GET', `/identity/organizations/${orgId}/members`, { query: params });
  },

  /** U4 邀请成员 */
  inviteMember(
    orgId: string,
    body: { email: string; role?: string; message?: string },
  ): Promise<{
    invitation_id: string;
    org_id: string;
    email: string;
    role: string;
    status: string;
  }> {
    return request('POST', `/identity/organizations/${orgId}/invitations`, { body });
  },

  /** U5 创建/分配角色 */
  upsertRole(
    orgId: string,
    body: { role_key: string; display_name?: string; permissions?: string[]; members?: string[] },
  ): Promise<{ role_id: string; role_key: string; display_name: string; permissions: string[] }> {
    return request('POST', `/identity/organizations/${orgId}/roles`, { body });
  },

  /** U6 操作记录查询（阶段二加性 query：关键字/动作分组/资源类型/结果/时间范围） */
  auditLogs(body: {
    page?: number;
    page_size?: number;
    actor_id?: string;
    actor_keyword?: string;
    action?: string;
    action_group?: string;
    resource_type?: string;
    result?: 'success' | 'failed';
    start_at?: string;
    end_at?: string;
  }): Promise<PageResult<AuditLogItem>> {
    return request('POST', '/identity/audit-logs', { body });
  },
};

// ---------------------------------------------------------------------------
// 知识库扩展 KB1-KB8（架构文档 §4.2）
// ---------------------------------------------------------------------------

/**
 * 知识库扩展接口。
 *
 * 覆盖阶段二 KB1-KB8 共 8 个端点。字段口径与 `docs/architecture-phase2.md`
 * §4.2 表逐条一致（snake_case，不做驼峰转换）。
 */
export const knowledgeExtApi = {
  /** KB1 修改知识库设置（名称/描述/内容切分精细度/可见范围，至少一项） */
  updateBase(baseId: string, body: UpdateBaseRequest): Promise<UpdateBaseResult> {
    return request('PATCH', `/knowledge/bases/${baseId}`, { body });
  },

  /** KB2 删除知识库（返回随库一并移除的文档数量，供二次确认展示） */
  deleteBase(baseId: string): Promise<DeleteBaseResult> {
    return request('DELETE', `/knowledge/bases/${baseId}`);
  },

  /** KB3 文档清单（分页 + 关键字 + 处理阶段筛选 + 排序） */
  listDocuments(
    baseId: string,
    params: DocumentListQuery = {},
  ): Promise<PageResult<KnowledgeDocumentItem>> {
    return request('GET', `/knowledge/bases/${baseId}/documents`, { query: params });
  },

  /**
   * KB4 引用片段详情（回链）。
   *
   * 注意：`status=stale` 属于**正常响应**（HTTP 200 + `code=0`），
   * 前端据此提示「该引用来自旧版本内容，原文已更新」，不得当错误处理。
   */
  segmentDetail(
    baseId: string,
    documentId: string,
    segmentId: string,
    withContext = true,
  ): Promise<SegmentDetail> {
    return request(
      'GET',
      `/knowledge/bases/${baseId}/documents/${documentId}/segments/${segmentId}`,
      { query: { with_context: withContext } },
    );
  },

  /** KB5 知识库授权清单（分页） */
  listGrants(
    baseId: string,
    params: { page?: number; page_size?: number } = {},
  ): Promise<PageResult<GrantItem>> {
    return request('GET', `/knowledge/bases/${baseId}/grants`, { query: params });
  },

  /** KB6 批量添加成员授权（对象 ≤50，重复对象计入跳过） */
  createGrants(baseId: string, body: CreateGrantRequest): Promise<CreateGrantResult> {
    return request('POST', `/knowledge/bases/${baseId}/grants`, { body });
  },

  /** KB7 撤销成员授权 */
  revokeGrant(baseId: string, grantId: string): Promise<DeleteGrantResult> {
    return request('DELETE', `/knowledge/bases/${baseId}/grants/${grantId}`);
  },

  /**
   * KB8 更新知识库的存储通道绑定。
   *
   * 权限点为 `ops:console:access`，**业务界面不得调用**（仅运维控制台使用）。
   * 保留本方法是为了让 38 个端点在请求层有唯一出口，避免别处直接 fetch。
   */
  bindStorageChannel(baseId: string, credentialRef: string): Promise<CredentialBindingResult> {
    return request('PUT', `/knowledge/bases/${baseId}/credential-binding`, {
      body: { credential_ref: credentialRef },
    });
  },
};

// ---------------------------------------------------------------------------
// 工具扩展 TL1（架构文档 §4.3）
// ---------------------------------------------------------------------------

/** 工具扩展接口 */
export const toolsExtApi = {
  /** TL1 工具调用记录（按工具类型筛选，分页） */
  listExecutions(
    params: { tool_type?: string; page?: number; page_size?: number } = {},
  ): Promise<PageResult<ToolExecutionItem>> {
    return request('GET', '/tools/executions', { query: params });
  },
};

// ---------------------------------------------------------------------------
// 组织与权限扩展 ID1-ID11（架构文档 §4.4）
// ---------------------------------------------------------------------------

/** 组织与权限扩展接口 */
export const identityExtApi = {
  /** ID1 部门层级（`flat=true` 返回平铺数组） */
  listDepartments(orgId: string, flat = false): Promise<{ departments: DepartmentNode[] }> {
    return request('GET', `/identity/organizations/${orgId}/departments`, { query: { flat } });
  },

  /** ID2 新建部门（超 5 层返回 14006） */
  createDepartment(orgId: string, body: CreateDepartmentRequest): Promise<CreateDepartmentResult> {
    return request('POST', `/identity/organizations/${orgId}/departments`, { body });
  },

  /** ID3 修改或移动部门 */
  updateDepartment(
    departmentId: string,
    body: UpdateDepartmentRequest,
  ): Promise<UpdateDepartmentResult> {
    return request('PATCH', `/identity/departments/${departmentId}`, { body });
  },

  /** ID4 删除部门（仍有成员或子部门时返回 14006） */
  deleteDepartment(departmentId: string): Promise<DeleteDepartmentResult> {
    return request('DELETE', `/identity/departments/${departmentId}`);
  },

  /** ID5 权限点目录（按分组返回，20 个权限点） */
  listPermissions(): Promise<{ groups: PermissionGroup[] }> {
    return request('GET', '/identity/permissions');
  },

  /** ID6 角色列表（含内置角色与权限点集合） */
  listRoles(orgId: string): Promise<{ items: RoleItem[] }> {
    return request('GET', `/identity/organizations/${orgId}/roles`);
  },

  /** ID7 删除角色（被引用时返回 20005） */
  deleteRole(orgId: string, roleId: string): Promise<{ role_id: string; deleted: boolean }> {
    return request('DELETE', `/identity/organizations/${orgId}/roles/${roleId}`);
  },

  /** ID8 修改成员（角色/部门/主部门/在册状态） */
  updateMember(
    orgId: string,
    memberId: string,
    body: UpdateMemberRequest,
  ): Promise<UpdateMemberResult> {
    return request('PATCH', `/identity/organizations/${orgId}/members/${memberId}`, { body });
  },

  /** ID9 移出成员（其知识库转交企业管理员） */
  removeMember(orgId: string, memberId: string): Promise<RemoveMemberResult> {
    return request('DELETE', `/identity/organizations/${orgId}/members/${memberId}`);
  },

  /** ID10 批量变更成员（单批 ≤100） */
  batchMembers(orgId: string, body: BatchMemberRequest): Promise<BatchMemberResult> {
    return request('POST', `/identity/organizations/${orgId}/members/batch`, { body });
  },

  /** ID11 操作动作目录（筛选项文案来源） */
  listAuditActions(): Promise<{ groups: ActionGroup[] }> {
    return request('GET', '/identity/audit-logs/actions');
  },
};

// ---------------------------------------------------------------------------
// 计费域 BL1-BL10（架构文档 §4.5）
// ---------------------------------------------------------------------------

/** 计费接口 */
export const billingApi = {
  /** BL1 套餐目录 */
  listPlans(): Promise<{ plans: PlanItem[] }> {
    return request('GET', '/billing/plans');
  },

  /** BL2 订阅详情（无订阅时返回免费版合成视图，不返回 404） */
  getSubscription(): Promise<SubscriptionDetail> {
    return request('GET', '/billing/subscription');
  },

  /** BL3 变更套餐（订阅/升级/降级/取消续费） */
  changeSubscription(body: ChangeSubscriptionRequest): Promise<ChangeSubscriptionResult> {
    return request('POST', '/billing/subscription', { body });
  },

  /** BL4 调整席位 */
  updateSeats(seats: number): Promise<UpdateSeatsResult> {
    return request('PATCH', '/billing/subscription/seats', { body: { seats } });
  },

  /** BL5 用量明细（`group_by` 为管理视图，需管理员） */
  getUsage(params: { period?: string; group_by?: UsageGroupBy } = {}): Promise<BillingUsageDetail> {
    return request('GET', '/billing/usage', { query: params });
  },

  /** BL6 创建订单（已有待支付订单时返回 15004） */
  createOrder(body: CreateOrderRequest): Promise<CreateOrderResult> {
    return request('POST', '/billing/orders', { body });
  },

  /** BL7 订单列表 */
  listOrders(
    params: { status?: string; page?: number; page_size?: number } = {},
  ): Promise<PageResult<BillingOrderItem>> {
    return request('GET', '/billing/orders', { query: params });
  },

  /** BL8 订单详情 */
  getOrder(orderId: string): Promise<BillingOrderDetail> {
    return request('GET', `/billing/orders/${orderId}`);
  },

  /** BL9 账单列表 */
  listInvoices(period = ''): Promise<{ items: BillingInvoiceItem[] }> {
    return request('GET', '/billing/invoices', { query: { period } });
  },

  /**
   * BL10 支付结果回调。
   *
   * 该端点是**支付渠道服务端**调用的入口（免 Bearer、验签 + 流水号幂等），
   * 业务界面不得调用；保留方法只为让 38 个端点都有唯一出口。
   */
  paymentCallback(payload: Record<string, unknown>): Promise<PaymentCallbackResult> {
    return request('POST', '/billing/payments/callback', { body: payload, idempotent: false });
  },
};

// ---------------------------------------------------------------------------
// 运营域 OPS1-OPS8（架构文档 §4.6）
// ---------------------------------------------------------------------------

/** 运营接口（仅运营账号可用；企业账号调用一律 20003） */
export const opsApi = {
  /** OPS1 企业列表 */
  listTenants(
    params: { keyword?: string; status?: string; page?: number; page_size?: number } = {},
  ): Promise<FlatPageResult<OpsTenantItem>> {
    return request('GET', '/ops/tenants', { query: params });
  },

  /** OPS2 企业详情（企业信息 + 订阅摘要 + 用量摘要 + 近期订单） */
  getTenant(tenantId: string): Promise<OpsTenantDetail> {
    return request('GET', `/ops/tenants/${tenantId}`);
  },

  /** OPS3 启用或停用企业 */
  updateTenantStatus(
    tenantId: string,
    body: { status: 'active' | 'suspended'; reason?: string },
  ): Promise<OpsTenantStatusResult> {
    return request('PATCH', `/ops/tenants/${tenantId}`, { body });
  },

  /** OPS4 订单列表 */
  listOrders(
    params: { tenant_id?: string; status?: string; page?: number; page_size?: number } = {},
  ): Promise<FlatPageResult<OpsOrderItem>> {
    return request('GET', '/ops/orders', { query: params });
  },

  /** OPS5 订单核销（金额不符返回 15009，已处理返回 15008） */
  confirmOrder(
    orderId: string,
    body: { paid_amount_cents: number; remark?: string },
  ): Promise<OpsConfirmOrderResult> {
    return request('POST', `/ops/orders/${orderId}/confirm`, { body });
  },

  /** OPS6 平台统计 */
  getMetrics(range: OpsMetricsRange = '7d'): Promise<OpsMetrics> {
    return request('GET', '/ops/metrics', { query: { range } });
  },

  /** OPS7 读取系统配置 */
  getSettings(): Promise<OpsSettings> {
    return request('GET', '/ops/settings');
  },

  /** OPS8 修改系统配置（只改传入项） */
  updateSettings(body: UpdateOpsSettingsRequest): Promise<OpsSettings> {
    return request('PATCH', '/ops/settings', { body });
  },
};

// ---------------------------------------------------------------------------
// 插件域 PL1-PL13（架构文档 §5.2）
// ---------------------------------------------------------------------------

/**
 * 后端 `CapabilityPolicyItem` 的**原始**形状。
 *
 * 后端字段名是 `enabled`（`app/schemas/plugin.py` 的 `CapabilityPolicyItem`），
 * 前端业务名是 `org_switch_enabled`（"企业总开关是否开启"，与 `PluginCapabilityItem`
 * 同名同义）。Batch 2 裁决（§7-1）：**不改后端字段名**，在本文件的一处入口做映射，
 * 这样 `team/page.tsx` 的 `setSaved(data.policies)` 拿到的是前端口径，
 * 不必每个消费点各写一遍（写 9 遍必然漏写）。
 */
interface BackendCapabilityPolicyItem {
  capability_key: string;
  display_name: string;
  /** 后端字段名（映射前） */
  enabled?: boolean;
  /** 前端口径（后端若已改名则直接可用） */
  org_switch_enabled?: boolean;
  updated_by_name: string;
  updated_at: string;
}

/**
 * 后端 `CapabilityPolicyRequestItem` 的**原始**形状。
 *
 * 后端请求模型是 `extra="forbid"`（`app/schemas/plugin.py` 的
 * `CapabilityPolicyRequest`），多一个 `org_switch_enabled` 就直接 422/20001——
 * 所以请求侧必须映射成 `enabled`，不能把前端形状原样发出去。
 */
interface BackendCapabilityPolicyRequestItem {
  capability_key: string;
  enabled: boolean;
}

/**
 * 把前端能力开关请求映射为后端形状（请求方向的 A-7 映射）。
 *
 * 为什么两端都要映射：A-7 的裁决是「不改后端字段名」。只做响应映射**只修了一半**——
 * 读取正常、保存恒失败（QA 实测：同一 body 把 `org_switch_enabled` 换成 `enabled`
 * 即 200，说明后端一直是对的，是前端发错了形状）。
 *
 * @param body - 前端口径的请求体
 * @returns 后端口径的请求体（逐项只带 `capability_key` + `enabled`）
 */
function adaptCapabilityPolicyRequest(body: CapabilityPolicyRequest): {
  policies: BackendCapabilityPolicyRequestItem[];
} {
  return {
    policies: (body.policies ?? []).map((item) => ({
      capability_key: item.capability_key,
      enabled: item.org_switch_enabled,
    })),
  };
}

/**
 * 把后端 `CapabilityPolicyItem` 映射为前端 `CapabilityPolicyItem`。
 *
 * @param item - 后端返回的原始条目
 * @returns 前端口径的条目（`org_switch_enabled` 一定有值）
 */
function adaptCapabilityPolicy(item: BackendCapabilityPolicyItem): CapabilityPolicyItem {
  const enabled = item.enabled ?? item.org_switch_enabled ?? false;
  return {
    capability_key: item.capability_key,
    display_name: item.display_name,
    org_switch_enabled: enabled,
    updated_by_name: item.updated_by_name,
    updated_at: item.updated_at,
  };
}

/**
 * 插件接口（阶段三 13 个端点）。
 *
 * 统一说明：
 * - 全部走本文件的 `request()`，`{code,message,data}` 非 0 抛 `BusinessError`；
 * - 写接口默认携带 `Idempotency-Key`（`request()` 对非 GET 自动加）；
 * - 套餐拦截点在 PL5/PL6（`15001`），**不在** PL1/PL2/PL4——
 *   市场与详情照常返回，只是列表项 `installable=false`。
 */
export const pluginsApi = {
  /** PL1 市场列表（分类 / 关键字 / 排序 / 分页） */
  market(
    params: {
      category?: string;
      keyword?: string;
      sort?: PluginMarketSort;
      page?: number;
      page_size?: number;
    } = {},
  ): Promise<PluginMarketData> {
    return request('GET', '/plugins/market', { query: params });
  },

  /** PL2 插件详情（含能力、步骤标题、产物声明、版本、评价统计） */
  detail(pluginId: string): Promise<PluginDetail> {
    return request('GET', `/plugins/market/${pluginId}`);
  },

  /** PL3 我已安装的插件（不分页，无 meta；可按状态筛选） */
  installed(params: { status?: PluginState } = {}): Promise<{ items: InstalledPluginItem[] }> {
    return request('GET', '/plugins/installed', { query: params });
  },

  /** PL4 能力清单（逐能力返回 `org_switch_enabled`，前端不得硬编码） */
  capabilities(pluginId: string): Promise<PluginCapabilitiesData> {
    return request('GET', `/plugins/${pluginId}/capabilities`);
  },

  /** PL5 安装（`grants` 必须覆盖全部必需能力，否则 16005） */
  install(pluginId: string, body: PluginInstallRequest): Promise<PluginInstallResult> {
    return request('POST', `/plugins/${pluginId}/install`, { body });
  },

  /** PL6 启用 / 停用（停用不打断在跑任务，响应给 running_task_count） */
  setState(pluginId: string, status: PluginState): Promise<PluginStateResult> {
    return request('PATCH', `/plugins/${pluginId}/state`, { body: { status } });
  },

  /** PL7 卸载（历史任务与产物保留，响应给 kept_task_count） */
  uninstall(pluginId: string): Promise<PluginUninstallResult> {
    return request('DELETE', `/plugins/${pluginId}`);
  },

  /** PL8 升级到指定版本 */
  upgrade(pluginId: string, targetVersion: string): Promise<PluginUpgradeResult> {
    return request('POST', `/plugins/${pluginId}/upgrade`, {
      body: { target_version: targetVersion },
    });
  },

  /** PL9 回退到上一个版本（无上一版本时 16008） */
  rollback(pluginId: string): Promise<PluginRollbackResult> {
    return request('POST', `/plugins/${pluginId}/rollback`);
  },

  /** PL10 保存插件配置（键必须 ⊆ `settings_schema`） */
  setSettings(pluginId: string, settings: Record<string, unknown>): Promise<PluginSettingsResult> {
    return request('PUT', `/plugins/${pluginId}/settings`, { body: { settings } });
  },

  /** PL11 评价列表（含聚合评分） */
  reviews(
    pluginId: string,
    params: { page?: number; page_size?: number } = {},
  ): Promise<PluginReviewsData> {
    return request('GET', `/plugins/${pluginId}/reviews`, { query: params });
  },

  /** PL12 提交评价（同一成员重复提交为覆盖更新） */
  submitReview(pluginId: string, body: PluginReviewRequest): Promise<PluginReviewResult> {
    return request('POST', `/plugins/${pluginId}/reviews`, { body });
  },

  /** PL13 企业能力总开关（仅 org.* 三项；个人能力恒开；响应回全量 6 项） */
  setCapabilityPolicy(
    body: CapabilityPolicyRequest,
  ): Promise<{ policies: CapabilityPolicyItem[] }> {
    // 双向映射（裁决 §7-1）：请求 `org_switch_enabled → enabled`、响应 `enabled →
    // org_switch_enabled`，都在本方法这一处收敛。后端字段名不改——改它要动 6 个消费方
    // 加数据迁移，收益不抵成本；但请求侧**必须**换名，后端请求模型 `extra="forbid"`，
    // 发前端的字段名会直接被拒（保存恒失败）。
    return request<{ policies: BackendCapabilityPolicyItem[] }>(
      'PUT',
      '/plugins/capability-policy',
      {
        body: adaptCapabilityPolicyRequest(body),
      },
    ).then((data) => ({
      ...data,
      policies: (data.policies ?? []).map(adaptCapabilityPolicy),
    }));
  },

  /** PL14 已安装后改授权（required=true 的能力不允许撤回） */
  updateGrants(
    pluginId: string,
    body: PluginGrantsUpdateRequest,
  ): Promise<PluginGrantsUpdateResult> {
    return request('PUT', `/plugins/${pluginId}/grants`, { body });
  },
};

// ---------------------------------------------------------------------------
// W1 增量：工作流任务统计（切片 A）
// ---------------------------------------------------------------------------

/** 任务统计接口（只读聚合，无分页无幂等键） */
export const statsApi = {
  /**
   * 工作流任务统计（M1 完成率 / M3 时长 / M4 重复使用率 / M5 单位成功用量）。
   *
   * 口径：仅工作流任务域；空数据全 0；百分比 0~100 浮点。
   *
   * @param range - 统计窗口（`7d` / `30d`，缺省 `7d`）
   */
  tasks(range: TaskStatsRange = '7d'): Promise<TaskStatsData> {
    return request<TaskStatsData>('GET', '/stats/tasks', { query: { range } });
  },
};

// ---------------------------------------------------------------------------
// W1 增量：目标式能力推荐（切片 B）
// ---------------------------------------------------------------------------

/** 能力推荐接口（规则推荐，实时组装目录，不查库） */
export const capabilitiesApi = {
  /**
   * 目标式能力推荐（Top N + 命中理由 + 所需输入提示）。
   *
   * 空目标返回目录不报错；无命中走目录兜底 + 引导文案（不得空列表）。
   *
   * @param goal - 用户目标（业务语言，上限 4000 字，与创建任务入参一致）
   * @param limit - 返回条数（1..10，缺省 3）
   */
  recommend(goal: string, limit: number = 3): Promise<CapabilityRecommendData> {
    return request<CapabilityRecommendData>('GET', '/capabilities/recommend', {
      query: { goal, limit },
    });
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
