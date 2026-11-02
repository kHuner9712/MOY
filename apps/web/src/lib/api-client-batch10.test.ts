/**
 * 前端补测第十批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：SSE 流（openStream/dispatchFrame 分帧解析与降级）、upload 上传路径
 * （网关 413/429 拦截）、knowledgeApi/agentApi/toolApi/identityApi/
 * knowledgeExtApi/toolsExtApi/identityExtApi/billingApi/opsApi/pluginsApi/
 * modelApi 剩余方法的 URL 与方法合同。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiClientModule = typeof import('./api-client');

async function load(): Promise<ApiClientModule> {
  return import('./api-client');
}

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

function callOf(
  fetchMock: ReturnType<typeof vi.fn>,
  index = 0,
): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[index];
  return { url: call?.[0] as string, init: call?.[1] as RequestInit };
}

/** 构造 SSE 流响应：每个字符串是一个网络分片（分片边界不应影响分帧） */
function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

/** 生成请求体（M2 流式生成共用） */
const GENERATE_BODY = {
  conversation_id: 'conv_1',
  profile: 'general' as const,
  messages: [{ role: 'user' as const, content: '你好' }],
};

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// SSE 流：openStream / dispatchFrame
// ---------------------------------------------------------------------------

describe('SSE 流 · 分帧解析', () => {
  it('多帧按 \\n\\n 分割并按事件名分发', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          'event:start\ndata: {"generation_id":"g1"}\n\n' +
            'event:delta\ndata: {"text":"你"}\n\n' +
            'event:done\ndata: {"finish_reason":"stop"}\n\n',
        ]),
      ),
    );
    const events: string[] = [];
    await new Promise<void>((resolve, reject) => {
      api.modelApi.stream(GENERATE_BODY, {
        onStart: () => events.push('start'),
        onDelta: () => events.push('delta'),
        onDone: () => {
          events.push('done');
          resolve();
        },
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(events).toEqual(['start', 'delta', 'done']);
  });

  it('分片边界切开的帧仍然正确拼接', async () => {
    const api = await load();
    // 一个 JSON 帧被切成三个网络分片
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(['event:delta\ndata: {"te', 'xt":"你', '好"}\n\n'])),
    );
    const texts: string[] = [];
    await new Promise<void>((resolve, reject) => {
      api.modelApi.stream(GENERATE_BODY, {
        onDelta: (data) => {
          texts.push(data.text);
          resolve();
        },
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(texts).toEqual(['你好']);
  });

  it('多行 data 字段拼接成完整载荷', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(['event:delta\ndata: {"text":\ndata: "跨行"}\n\n'])),
    );
    const texts: string[] = [];
    await new Promise<void>((resolve, reject) => {
      api.modelApi.stream(GENERATE_BODY, {
        onDelta: (data) => {
          texts.push(data.text);
          resolve();
        },
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(texts).toEqual(['跨行']);
  });

  it('无 data 行的帧与非法 JSON 帧被跳过，不影响后续帧', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          'event:ping\n\n' +
            'data: not-json\n\n' +
            'event:done\ndata: {"finish_reason":"stop"}\n\n',
        ]),
      ),
    );
    const events: string[] = [];
    await new Promise<void>((resolve, reject) => {
      api.modelApi.stream(GENERATE_BODY, {
        onStart: () => events.push('start'),
        onDone: () => {
          events.push('done');
          resolve();
        },
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(events).toEqual(['done']);
  });

  it('引用事件携带回链字段', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          'event:citation\ndata: {"document_name":"手册","segment_content":"片段",' +
            '"document_id":"doc_1","base_id":"kb_1","segment_id":"seg_1"}\n\n' +
            'event:done\ndata: {"finish_reason":"stop"}\n\n',
        ]),
      ),
    );
    const citations: Array<{ document_id?: string }> = [];
    await new Promise<void>((resolve, reject) => {
      api.modelApi.stream(GENERATE_BODY, {
        onCitation: (data) => citations.push(data),
        onDone: () => resolve(),
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(citations[0]?.document_id).toBe('doc_1');
  });
});

describe('SSE 流 · 降级分支', () => {
  it('HTTP 非 2xx 触发 onError（30001 统一文案）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse([], 500)),
    );
    const error = await new Promise<{ code: number; message: string }>((resolve) => {
      api.modelApi.stream(GENERATE_BODY, { onError: resolve });
    });
    expect(error.code).toBe(30001);
    expect(error.message).toBeTruthy();
  });

  it('fetch 网络错误触发 onError', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed');
      }),
    );
    const error = await new Promise<{ code: number }>((resolve) => {
      api.modelApi.stream(GENERATE_BODY, { onError: resolve });
    });
    expect(error.code).toBe(30001);
  });

  it('流读取中断触发 onError', async () => {
    const api = await load();
    const broken = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(new Error('stream broke'));
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(broken, { status: 200 })),
    );
    const error = await new Promise<{ code: number }>((resolve) => {
      api.modelApi.stream(GENERATE_BODY, { onError: resolve });
    });
    expect(error.code).toBe(30001);
  });

  it('主动取消（abort）不触发 onError', async () => {
    const api = await load();
    // 挂起流：只有 abort 才结束读取，模拟长连接被取消
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const signal = init?.signal ?? null;
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            signal?.addEventListener('abort', () => {
              controller.error(new DOMException('The operation was aborted.', 'AbortError'));
            });
          },
        });
        return new Response(stream, { status: 200 });
      }),
    );
    const onError = vi.fn();
    const onDone = vi.fn();
    const cancel = api.modelApi.stream(GENERATE_BODY, { onError, onDone });
    await new Promise((resolve) => setTimeout(resolve, 20));
    cancel();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(onError).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it('streamTaskEvents 走 /agent/tasks/{id}/events 并分发步骤事件', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse([
          'event:step_started\ndata: {"step":1}\n\n' +
            'event:task_completed\ndata: {"status":"completed"}\n\n',
        ]),
      ),
    );
    const events: string[] = [];
    await new Promise<void>((resolve, reject) => {
      api.agentApi.streamTaskEvents('t1', {
        onStepStarted: () => events.push('step_started'),
        onTaskCompleted: () => {
          events.push('task_completed');
          resolve();
        },
        onError: (data) => reject(new Error(data.message)),
      });
    });
    expect(callOf(vi.mocked(fetch)).url).toContain('/agent/tasks/t1/events');
    expect(events).toEqual(['step_started', 'task_completed']);
  });
});

// ---------------------------------------------------------------------------
// upload 上传路径
// ---------------------------------------------------------------------------

describe('upload · multipart 上传', () => {
  it('uploadDocument 以 FormData 提交并携带幂等键与 Cookie 凭据', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ document_id: 'd1' })),
    );
    const file = new File(['内容'], '笔记.md', { type: 'text/markdown' });
    await api.knowledgeApi.uploadDocument('kb_1', file);
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/documents');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    const headers = new Headers(init.headers);
    expect(headers.has('Idempotency-Key')).toBe(true);
    // 身份走 httpOnly Cookie：上传也必须带凭据，且**不得**出现 Authorization
    expect(init.credentials).toBe('include');
    expect(headers.get('Authorization')).toBeNull();
  });

  it('上传被网关 413 拦截时抛业务错误 20008', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 413 })),
    );
    const file = new File(['x'], 'big.pdf');
    const error = await api.knowledgeApi.uploadDocument('kb_1', file).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<typeof api.BusinessError>).code).toBe(20008);
  });

  it('上传被网关 429 拦截时抛业务错误 20006', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 429 })),
    );
    const file = new File(['x'], 'a.txt');
    const error = await api.knowledgeApi.uploadDocument('kb_1', file).catch((e: unknown) => e);
    expect((error as InstanceType<typeof api.BusinessError>).code).toBe(20006);
  });
});

// ---------------------------------------------------------------------------
// knowledgeApi / agentApi / toolApi 剩余方法
// ---------------------------------------------------------------------------

describe('api-client · knowledgeApi 剩余', () => {
  it('documentStatus GET 文档索引状态', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ document_id: 'd1' })),
    );
    await api.knowledgeApi.documentStatus('kb_1', 'd1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/documents/d1');
    expect(init.method).toBe('GET');
  });

  it('reindexDocument POST 重建索引', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ progress: 0 })),
    );
    await api.knowledgeApi.reindexDocument('kb_1', 'd1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/documents/d1/reindex');
    expect(init.method).toBe('POST');
  });

  it('deleteDocument DELETE 文档', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted: true })),
    );
    await api.knowledgeApi.deleteDocument('kb_1', 'd1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/documents/d1');
    expect(init.method).toBe('DELETE');
  });
});

describe('api-client · agentApi 剩余', () => {
  it('createTask POST /agent/tasks', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ task_id: 't1' })),
    );
    await api.agentApi.createTask({ conversation_id: 'c1', goal: '写周报' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/agent/tasks');
    expect(init.method).toBe('POST');
  });

  it('retryTask POST /agent/tasks/{id}/retry', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ task_id: 't2' })),
    );
    await api.agentApi.retryTask('t1', { decision: 'redo' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/agent/tasks/t1/retry');
    expect(init.method).toBe('POST');
  });

  it('updateArtifact PUT /agent/tasks/{id}/artifacts/{aid}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ version: 2 })),
    );
    await api.agentApi.updateArtifact('t1', 'a1', { content: '新内容' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/agent/tasks/t1/artifacts/a1');
    expect(init.method).toBe('PUT');
  });
});

describe('api-client · toolApi 剩余', () => {
  it('execute POST /tools/execute', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ execution_id: 'e1' })),
    );
    await api.toolApi.execute({ tool_type: 'wecom', operation: 'send', params: {} });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/execute');
    expect(init.method).toBe('POST');
  });

  it('createSyncJob POST /tools/sync-jobs', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ job_id: 'j1' })),
    );
    await api.toolApi.createSyncJob({ tool_type: 'wecom' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/sync-jobs');
    expect(init.method).toBe('POST');
  });

  it('syncJob GET /tools/sync-jobs/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ job_id: 'j1', progress: 50 })),
    );
    await api.toolApi.syncJob('j1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/sync-jobs/j1');
    expect(init.method).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// identityApi / knowledgeExtApi / toolsExtApi 剩余方法
// ---------------------------------------------------------------------------

describe('api-client · identityApi 剩余', () => {
  it('inviteMember POST /invitations', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ invitation_id: 'i1' })),
    );
    await api.identityApi.inviteMember('org_1', { email: 'a@b.c' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/invitations');
    expect(init.method).toBe('POST');
  });

  it('upsertRole POST /roles', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ role_id: 'r1' })),
    );
    await api.identityApi.upsertRole('org_1', { role_key: 'editor' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/roles');
    expect(init.method).toBe('POST');
  });

  it('auditLogs POST /identity/audit-logs', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], meta: {} })),
    );
    await api.identityApi.auditLogs({ page: 1, result: 'success' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/audit-logs');
    expect(init.method).toBe('POST');
  });
});

describe('api-client · knowledgeExtApi 剩余', () => {
  it('segmentDetail GET 引用片段详情（默认带上下文）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ segment_id: 'seg_1' })),
    );
    await api.knowledgeExtApi.segmentDetail('kb_1', 'd1', 'seg_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/documents/d1/segments/seg_1');
    expect(url).toContain('with_context=true');
    expect(init.method).toBe('GET');
  });

  it('listGrants GET /grants', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], meta: {} })),
    );
    await api.knowledgeExtApi.listGrants('kb_1', { page: 1 });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/grants');
    expect(init.method).toBe('GET');
  });

  it('createGrants POST /grants', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ created_count: 1 })),
    );
    await api.knowledgeExtApi.createGrants('kb_1', {
      subject_type: 'user',
      subject_ids: ['u1'],
      permission: 'viewer',
    });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/grants');
    expect(init.method).toBe('POST');
  });

  it('revokeGrant DELETE /grants/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted: true })),
    );
    await api.knowledgeExtApi.revokeGrant('kb_1', 'g1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/grants/g1');
    expect(init.method).toBe('DELETE');
  });

  it('bindStorageChannel PUT /credential-binding', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ bound: true })),
    );
    await api.knowledgeExtApi.bindStorageChannel('kb_1', 'cred_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1/credential-binding');
    expect(init.method).toBe('PUT');
  });

  it('toolsExtApi.listExecutions GET /tools/executions', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], meta: {} })),
    );
    await api.toolsExtApi.listExecutions({ tool_type: 'wecom', page: 1 });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/executions');
    expect(init.method).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// identityExtApi 剩余方法
// ---------------------------------------------------------------------------

describe('api-client · identityExtApi 剩余', () => {
  it('createDepartment POST /departments', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ department_id: 'dep_1' })),
    );
    await api.identityExtApi.createDepartment('org_1', { name: '研发部' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/departments');
    expect(init.method).toBe('POST');
  });

  it('updateDepartment PATCH /identity/departments/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ department_id: 'dep_1' })),
    );
    await api.identityExtApi.updateDepartment('dep_1', { name: '新名' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/departments/dep_1');
    expect(init.method).toBe('PATCH');
  });

  it('deleteDepartment DELETE /identity/departments/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted: true })),
    );
    await api.identityExtApi.deleteDepartment('dep_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/departments/dep_1');
    expect(init.method).toBe('DELETE');
  });

  it('deleteRole DELETE /roles/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted: true })),
    );
    await api.identityExtApi.deleteRole('org_1', 'r1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/roles/r1');
    expect(init.method).toBe('DELETE');
  });

  it('updateMember PATCH /members/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ member_id: 'm1' })),
    );
    await api.identityExtApi.updateMember('org_1', 'm1', { role: 'admin' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/members/m1');
    expect(init.method).toBe('PATCH');
  });

  it('removeMember DELETE /members/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ removed: true })),
    );
    await api.identityExtApi.removeMember('org_1', 'm1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/members/m1');
    expect(init.method).toBe('DELETE');
  });

  it('batchMembers POST /members/batch', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ success_count: 1 })),
    );
    await api.identityExtApi.batchMembers('org_1', {
      member_ids: ['m1', 'm2'],
      action: 'update_role',
      role: 'admin',
    });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/organizations/org_1/members/batch');
    expect(init.method).toBe('POST');
  });

  it('listAuditActions GET /identity/audit-logs/actions', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ groups: [] })),
    );
    await api.identityExtApi.listAuditActions();
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/audit-logs/actions');
    expect(init.method).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// billingApi / opsApi / pluginsApi / modelApi 剩余方法
// ---------------------------------------------------------------------------

describe('api-client · billingApi 剩余', () => {
  it('changeSubscription POST /billing/subscription', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ subscription_id: 's1' })),
    );
    await api.billingApi.changeSubscription({
      plan_code: 'pro',
      seats: 5,
      action: 'upgrade',
    });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/billing/subscription');
    expect(init.method).toBe('POST');
  });

  it('updateSeats PATCH /billing/subscription/seats', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ seats: 10 })),
    );
    await api.billingApi.updateSeats(10);
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/billing/subscription/seats');
    expect(init.method).toBe('PATCH');
  });

  it('createOrder POST /billing/orders', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ order_id: 'o1' })),
    );
    await api.billingApi.createOrder({
      plan_code: 'pro',
      seats: 5,
      order_type: 'new',
      payment_method: 'online',
    });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/billing/orders');
    expect(init.method).toBe('POST');
  });

  it('getOrder GET /billing/orders/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ order_id: 'o1' })),
    );
    await api.billingApi.getOrder('o1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/billing/orders/o1');
    expect(init.method).toBe('GET');
  });

  it('paymentCallback 不带幂等键（渠道回调自带流水号）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ received: true })),
    );
    await api.billingApi.paymentCallback({ trade_no: 't1' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/billing/payments/callback');
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).has('Idempotency-Key')).toBe(false);
  });
});

describe('api-client · opsApi 剩余', () => {
  it('getTenant GET /ops/tenants/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ tenant_id: 't1' })),
    );
    await api.opsApi.getTenant('t1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/ops/tenants/t1');
    expect(init.method).toBe('GET');
  });

  it('updateTenantStatus PATCH /ops/tenants/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ status: 'suspended' })),
    );
    await api.opsApi.updateTenantStatus('t1', { status: 'suspended', reason: '欠费' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/ops/tenants/t1');
    expect(init.method).toBe('PATCH');
  });

  it('opsApi.listOrders GET /ops/orders', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], meta: {} })),
    );
    await api.opsApi.listOrders({ tenant_id: 't1', page: 1 });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/ops/orders');
    expect(init.method).toBe('GET');
  });

  it('confirmOrder POST /ops/orders/{id}/confirm', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ confirmed: true })),
    );
    await api.opsApi.confirmOrder('o1', { paid_amount_cents: 9900 });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/ops/orders/o1/confirm');
    expect(init.method).toBe('POST');
  });

  it('updateSettings PATCH /ops/settings', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ trial_days: 14 })),
    );
    await api.opsApi.updateSettings({ trial_days: 14 });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/ops/settings');
    expect(init.method).toBe('PATCH');
  });
});

describe('api-client · pluginsApi 剩余', () => {
  it('installed GET /plugins/installed（可按状态筛选）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [] })),
    );
    await api.pluginsApi.installed({ status: 'enabled' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/installed');
    expect(url).toContain('status=enabled');
    expect(init.method).toBe('GET');
  });

  it('capabilities GET /plugins/{id}/capabilities', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ capabilities: [] })),
    );
    await api.pluginsApi.capabilities('plg_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/capabilities');
    expect(init.method).toBe('GET');
  });

  it('setState PATCH /plugins/{id}/state', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ status: 'disabled' })),
    );
    await api.pluginsApi.setState('plg_1', 'disabled');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/state');
    expect(init.method).toBe('PATCH');
  });

  it('updateGrants PUT /plugins/{id}/grants', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ granted_capabilities: [], denied_capabilities: [] })),
    );
    await api.pluginsApi.updateGrants('plg_1', { grants: { 'personal.tool.use': true } });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/grants');
    expect(init.method).toBe('PUT');
  });
});

describe('api-client · modelApi 剩余', () => {
  it('usage GET /model/usage（默认按月）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ period: 'month' })),
    );
    await api.modelApi.usage();
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/model/usage');
    expect(url).toContain('period=month');
    expect(init.method).toBe('GET');
  });

  it('usage 按日统计', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ period: 'day' })),
    );
    await api.modelApi.usage('day');
    expect(callOf(vi.mocked(fetch)).url).toContain('period=day');
  });
});
