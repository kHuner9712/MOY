/**
 * 幂等键作用域回归网（审计 Q-11 / S-09 / P1-12）。
 *
 * 缺陷形状（整改前）：`buildHeaders()` 对**每个**写请求现场 `newIdempotencyKey()`，
 * 于是双击提交 / 网络重试 / 回退再提交各拿到一把新键，后端 `IdempotencyManager`
 * 永远看不到重复投递——重复下单、重复扣额度是真实业务损失。
 *
 * 本文件锁死三件事：
 * 1. 同一「用户动作」内的多次写请求共用同一把键（含 multipart 上传）；
 * 2. 不同动作、以及作用域退出后的请求，必须换新键（否则会把两次不同的动作并成一次）；
 * 3. 兜底随机源不再是 `Math.random()`，而是 `crypto.getRandomValues()`。
 *
 * 与既有 api-client 测试同一套写法：模块级可变状态会串味，故每个用例
 * `vi.resetModules()` 后动态 `import`。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** 被测模块类型（动态 import 后才有实例） */
type ApiClientModule = typeof import('./api-client');

/** UUID v4 的严格格式（version=4、variant=8/9/a/b） */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * 构造统一信封响应。
 *
 * @param data - `data` 字段
 * @returns fetch 可返回的 Response
 */
function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ code: 0, message: '', data }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * 取第 N 次 fetch 调用的请求头。
 *
 * @param fetchMock - fetch 的 mock
 * @param index - 第几次调用（从 0 开始）
 * @returns Headers 实例
 */
function headersOf(fetchMock: ReturnType<typeof vi.fn>, index: number): Headers {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit | undefined;
  return init?.headers as Headers;
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api-client · 幂等键按用户动作绑定', () => {
  it('同一动作内对同一端点的重复投递共用同一把幂等键', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    // 后端去重指纹含「方法 + 路径」：同一把键打到**不同**端点会被判 20005 冲突，
    // 所以作用域的语义是「同一端点的重复投递」，这里刻意用同一端点两发。
    await api.withIdempotencyKey(api.newIdempotencyKey(), async () => {
      await api.memoryApi.removeMany('identity');
      await api.memoryApi.removeMany('identity');
    });

    const first = headersOf(fetchMock, 0).get('Idempotency-Key');
    const second = headersOf(fetchMock, 1).get('Idempotency-Key');
    expect(first).toMatch(UUID_V4);
    expect(first).toBe(second);
  });

  it('multipart 上传同样落在作用域内（不走 buildHeaders 也必须同键）', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    const key = api.newIdempotencyKey();
    await api.withIdempotencyKey(key, async () => {
      await api.knowledgeApi.uploadDocument('base_1', new File(['x'], 'a.txt'));
      await api.knowledgeApi.uploadDocument('base_1', new File(['y'], 'b.txt'));
    });

    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBe(key);
    expect(headersOf(fetchMock, 1).get('Idempotency-Key')).toBe(key);
  });

  it('不同动作生成不同幂等键', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    const keyA = api.newIdempotencyKey();
    const keyB = api.newIdempotencyKey();
    expect(keyA).not.toBe(keyB);

    await api.withIdempotencyKey(keyA, () => api.memoryApi.removeMany());
    await api.withIdempotencyKey(keyB, () => api.memoryApi.removeMany());

    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBe(keyA);
    expect(headersOf(fetchMock, 1).get('Idempotency-Key')).toBe(keyB);
  });

  it('作用域成功退出后即释放：后续请求不再复用该键', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    const key = api.newIdempotencyKey();
    await api.withIdempotencyKey(key, () => api.memoryApi.remove('mem_1'));
    await api.memoryApi.remove('mem_1');

    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBe(key);
    expect(headersOf(fetchMock, 1).get('Idempotency-Key')).not.toBe(key);
  });

  it('作用域抛错也释放（不能让一次失败把后续动作都钉在同一把键上）', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    const key = api.newIdempotencyKey();
    await expect(
      api.withIdempotencyKey(key, async () => {
        throw new Error('动作失败');
      }),
    ).rejects.toThrow('动作失败');

    await api.memoryApi.remove('mem_1');
    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).not.toBe(key);
  });

  it('嵌套作用域内层键优先，退出后还原外层键', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    const outer = api.newIdempotencyKey();
    const inner = api.newIdempotencyKey();

    await api.withIdempotencyKey(outer, async () => {
      await api.memoryApi.removeMany();
      await api.withIdempotencyKey(inner, async () => {
        await api.memoryApi.removeMany();
      });
      await api.memoryApi.removeMany();
    });

    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBe(outer);
    expect(headersOf(fetchMock, 1).get('Idempotency-Key')).toBe(inner);
    expect(headersOf(fetchMock, 2).get('Idempotency-Key')).toBe(outer);
  });

  it('空键视为未声明：不压栈，两次请求各自兜底生成', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.withIdempotencyKey('   ', async () => {
      await api.memoryApi.remove('mem_1');
      await api.memoryApi.remove('mem_2');
    });

    const first = headersOf(fetchMock, 0).get('Idempotency-Key');
    const second = headersOf(fetchMock, 1).get('Idempotency-Key');
    expect(first).toMatch(UUID_V4);
    expect(first).not.toBe(second);
  });

  it('键首尾空白会被裁掉（避免发出后端认不出的键）', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.withIdempotencyKey('  action-key-1  ', () => api.memoryApi.remove('mem_1'));
    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBe('action-key-1');
  });

  it('作用域不影响读请求，也不覆盖 idempotent=false', async () => {
    const api: ApiClientModule = await import('./api-client');
    const fetchMock = vi.fn(async () => envelope({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.withIdempotencyKey(api.newIdempotencyKey(), async () => {
      await api.identityApi.profile();
      await api.billingApi.paymentCallback({ order_id: 'ord_1' });
    });

    expect(headersOf(fetchMock, 0).get('Idempotency-Key')).toBeNull();
    expect(headersOf(fetchMock, 1).get('Idempotency-Key')).toBeNull();
  });
});

describe('api-client · 幂等键兜底随机源', () => {
  it('有 crypto.randomUUID 时直接使用它', async () => {
    const api: ApiClientModule = await import('./api-client');
    vi.stubGlobal('crypto', { randomUUID: () => 'fixed-uuid-from-crypto' });
    expect(api.newIdempotencyKey()).toBe('fixed-uuid-from-crypto');
  });

  it('无 randomUUID 时用 crypto.getRandomValues 拼出 v4，绝不调用 Math.random', async () => {
    const api: ApiClientModule = await import('./api-client');
    const randomSpy = vi.spyOn(Math, 'random');
    const getRandomValues = vi.fn((target: Uint8Array) => {
      for (let index = 0; index < target.length; index += 1) {
        target[index] = index;
      }
      return target;
    });
    vi.stubGlobal('crypto', { getRandomValues, randomUUID: undefined });

    const key = api.newIdempotencyKey();

    // 逐字节写入 0..15 → 版本位 0x46、变体位 0x88，结果可完全推定，
    // 从而证明取值来自 getRandomValues 而不是别的来源
    expect(key).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f');
    expect(key).toMatch(UUID_V4);
    expect(getRandomValues).toHaveBeenCalledTimes(1);
    expect(randomSpy).not.toHaveBeenCalled();
  });

  it('crypto 完全缺失时仍能出 v4，且两次不同、不用 Math.random', async () => {
    const api: ApiClientModule = await import('./api-client');
    const randomSpy = vi.spyOn(Math, 'random');
    vi.stubGlobal('crypto', undefined);

    const first = api.newIdempotencyKey();
    const second = api.newIdempotencyKey();

    expect(first).toMatch(UUID_V4);
    expect(second).toMatch(UUID_V4);
    expect(first).not.toBe(second);
    expect(randomSpy).not.toHaveBeenCalled();
  });
});
