/**
 * `api-client` 行为回归网（W-02，Batch 2 矩阵 #1-#4、#7-#14）。
 *
 * 为什么必须这么写（踩过的坑，别改回去）：
 * - `api-client.ts:225-260` 的 `accessToken` / `currentTenantId` 是**模块级可变状态**，
 *   用例之间会串味（单跑绿、全跑红）。所以每个用例 `vi.resetModules()` 后
 *   **动态 import**，拿到全新实例；
 * - `session-events` 也**必须动态 import**：`resetModules` 之后再静态拿到的会是
 *   被丢弃的旧实例，`onUnauthorized` 订阅不到广播，用例会假绿；
 * - 一律走公共 API（`pluginsApi` / `knowledgeApi` / `identityApi`），**不导出**
 *   `request` / `unwrap`：某分支若只能靠私有函数触发，说明它在生产路径不可达，
 *   那就不是该测的东西。
 *
 * 矩阵 #5（413）/ #6（429）在 W-07 随 `throwIfGatewayStatusRejected()` 一起落地；
 * #15/#16 的分页越界回落见 `components/ui/pagination.test.ts`。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** 被测模块的类型（动态 import 后才有实例） */
type ApiClientModule = typeof import('./api-client');
type SessionEventsModule = typeof import('./session-events');

/** 当前用例的动态模块实例 */
interface Loaded {
  api: ApiClientModule;
  events: SessionEventsModule;
}

/**
 * 重置模块图并重新加载被测模块。
 *
 * @returns 全新的 `api-client` 与 `session-events` 实例（两者互相引用同一个实例）
 */
async function load(): Promise<Loaded> {
  const api = await import('./api-client');
  const events = await import('./session-events');
  return { api, events };
}

/**
 * 构造统一信封响应。
 *
 * @param data - `data` 字段
 * @param init - 状态码与 `code`/`message`
 * @returns fetch 可返回的 Response
 */
function envelope(
  data: unknown,
  init: { status?: number; code?: number; message?: string } = {},
): Response {
  const { status = 200, code = 0, message = '' } = init;
  return new Response(JSON.stringify({ code, message, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * 取出第 N 次 fetch 调用的请求头。
 *
 * @param fetchMock - fetch 的 mock
 * @param index - 第几次调用（默认第一次）
 * @returns Headers 实例
 */
function headersOf(fetchMock: ReturnType<typeof vi.fn>, index = 0): Headers {
  const call = fetchMock.mock.calls[index];
  const init = call?.[1] as RequestInit | undefined;
  return init?.headers as Headers;
}

/**
 * 订阅「会话已失效」广播并返回计数函数。
 *
 * @param events - 动态加载的 session-events
 * @returns 返回累计广播次数的函数
 */
function countUnauthorized(events: SessionEventsModule): () => number {
  let count = 0;
  events.onUnauthorized(() => {
    count += 1;
  });
  return () => count;
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('api-client · 信封与业务错误', () => {
  it('#1 信封 code=0 直接返回 data', async () => {
    const { api } = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [{ plugin_id: 'demo_plugin' }] })),
    );

    const result = await api.pluginsApi.installed();

    expect(result.items).toHaveLength(1);
    expect(result.items[0]?.plugin_id).toBe('demo_plugin');
  });

  it('#2 登录失效的业务码抛 BusinessError 并广播会话失效', async () => {
    const { api, events } = await load();
    const calls = countUnauthorized(events);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' }),
      ),
    );

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(calls()).toBe(1);
  });

  it('#3 HTTP 401 + 空体（网关层）不退化成 AdapterUnavailableError', async () => {
    const { api, events } = await load();
    const calls = countUnauthorized(events);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })));

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect(error).not.toBeInstanceOf(api.AdapterUnavailableError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect((error as Error).message).toBe('登录状态已失效，请重新登录');
    expect(calls()).toBe(1);
  });

  it('#4 HTTP 402 + 额度用尽的业务码走既有文案，且不广播会话失效', async () => {
    const { api, events } = await load();
    const { toBusinessMessage } = await import('./business-errors');
    const calls = countUnauthorized(events);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ code: 15006, message: '', data: null }), {
        status: 402,
        headers: { 'Content-Type': 'application/json' },
      })),
    );

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(15006);
    // message 为空 → UI 侧按 code 取兜底表，前端不复制字符串
    expect(toBusinessMessage(error)).toBe('本月可用量已用完，请联系管理员调整');
    expect(calls()).toBe(0);
  });
});

describe('api-client · 传输层降级', () => {
  it('#5 HTTP 413（网关无信封）→ 文件过大文案，且不广播会话失效', async () => {
    const { api, events } = await load();
    const { toBusinessMessage } = await import('./business-errors');
    const calls = countUnauthorized(events);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 413 })));

    const error = await api.knowledgeApi.uploadDocument('kb_1', new File(['x'], 'a.txt')).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20008);
    // 文案按已注册码取，前端不复制字符串
    expect(toBusinessMessage(error)).toBe('文件超出大小限制（上限 50MB）');
    expect(calls()).toBe(0);
  });

  it('#6 HTTP 429（网关无信封）→ 稍后再试文案，且不广播会话失效', async () => {
    const { api, events } = await load();
    const { toBusinessMessage } = await import('./business-errors');
    const calls = countUnauthorized(events);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 429 })));

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20006);
    expect(toBusinessMessage(error)).toBe('当前使用人数较多，请稍后再试');
    expect(calls()).toBe(0);
  });

  it('#7 HTTP 502 + HTML → AdapterUnavailableError（502 确实无法解释原因）', async () => {
    const { api } = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response('<html><body>502 Bad Gateway</body></html>', {
            status: 502,
            headers: { 'Content-Type': 'text/html' },
          }),
      ),
    );

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect((error as Error).message).toBe('服务暂时不可用，请稍后再试');
  });

  it('#8 HTTP 200 但 body 是 HTML → AdapterUnavailableError', async () => {
    const { api } = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response('<html>proxy login</html>', {
            status: 200,
            headers: { 'Content-Type': 'text/html' },
          }),
      ),
    );

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
  });

  it('#9 超时（60s 未响应）→ AdapterUnavailableError「服务响应超时，请重试」', async () => {
    const { api } = await load();
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => {
              reject(new DOMException('Aborted', 'AbortError'));
            });
          }),
      ),
    );

    const assertion = expect(api.pluginsApi.installed()).rejects.toThrow('服务响应超时，请重试');
    await vi.advanceTimersByTimeAsync(60_000);
    await assertion;
  });
});

describe('api-client · 企业标识请求头', () => {
  it('#10 未设置企业标识时请求不带标识头', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async () => envelope({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await api.pluginsApi.installed();

    expect(headersOf(fetchMock).get('X-Tenant-Id')).toBeNull();
  });

  it('#11 标识被清空后仍不带标识头，且不得写出字面量 "null"', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async () => envelope({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    api.setTenantId(null);
    await api.pluginsApi.installed();

    const tenant = headersOf(fetchMock).get('X-Tenant-Id');
    expect(tenant).toBeNull();
    expect(tenant).not.toBe('null');
  });

  it('#12 设置企业标识后请求头等于该值', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async () => envelope({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    api.setTenantId('tnt_dev');
    await api.pluginsApi.installed();

    expect(headersOf(fetchMock).get('X-Tenant-Id')).toBe('tnt_dev');
  });
});

describe('api-client · 能力开关双向映射（A-7）', () => {
  it('请求体用后端字段名、响应映射回前端字段名（缺一半就是保存恒失败）', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      envelope({
        policies: [
          {
            capability_key: 'org.repo.write',
            display_name: '写入代码仓库',
            // 后端回的是 `enabled`
            enabled: true,
            updated_by_name: '管理员',
            updated_at: '2026-10-25T00:00:00Z',
          },
        ],
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await api.pluginsApi.setCapabilityPolicy({
      policies: [{ capability_key: 'org.repo.write', org_switch_enabled: true }],
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const sent = JSON.parse(String(init?.body)) as { policies: Record<string, unknown>[] };

    // 请求方向：逐项只带 `capability_key` + `enabled`（后端 extra="forbid"）
    expect(sent).toEqual({ policies: [{ capability_key: 'org.repo.write', enabled: true }] });
    expect(JSON.stringify(sent)).not.toContain('org_switch_enabled');

    // 响应方向：`enabled` 必须映射回 `org_switch_enabled`
    expect(result.policies[0]?.org_switch_enabled).toBe(true);
    expect(result.policies[0]?.capability_key).toBe('org.repo.write');
  });
});

describe('api-client · 写接口与上传', () => {
  it('#13 POST 两次都带 Idempotency-Key 且两值不同', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async () => envelope({ base_id: 'kb_1', name: '知识库' }));
    vi.stubGlobal('fetch', fetchMock);

    await api.knowledgeApi.createBase({ name: '知识库' });
    await api.knowledgeApi.createBase({ name: '知识库' });

    const first = headersOf(fetchMock, 0).get('Idempotency-Key');
    const second = headersOf(fetchMock, 1).get('Idempotency-Key');
    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    expect(first).not.toBe(second);
  });

  it('#14 上传未设置标识时也不带标识头（multipart 是独立实现）', async () => {
    const { api } = await load();
    const fetchMock = vi.fn(async () =>
      envelope({
        document_id: 'doc_1',
        file_name: 'a.txt',
        index_status: 'processing',
        progress: 0,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await api.knowledgeApi.uploadDocument('kb_1', new File(['hello'], 'a.txt'));

    const headers = headersOf(fetchMock);
    expect(headers.get('X-Tenant-Id')).toBeNull();
    expect(headers.get('Idempotency-Key')).toBeTruthy();
  });
});
