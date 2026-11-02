/**
 * 「一次用户动作」的幂等键持有器（审计 Q-11 / S-09 / P1-12）。
 *
 * **为什么单独成文件**：`api-client.ts` 只负责「把键放到请求头上」，
 * 而「这次请求属于哪一次用户动作」是页面层才知道的事。把判定逻辑抽成纯函数，
 * 页面只需持有一个 ref，双击/重试复用同一把键、改了参数才换新键——
 * 这条规则可以脱开 React 与 fetch 单独验证（见 `idempotency.test.ts`）。
 *
 * ⚠️ 一把键只对应**一个写端点**：后端幂等去重指纹含「方法 + 路径 + 租户 + 用户」，
 * 同一把键打到不同端点会被判 20005（键复用冲突）而不是回放结果。一个动作里若需要
 * 调多个不同端点，按端点各生成一把键，不要指望一把键覆盖整段流程。
 */

import { newIdempotencyKey } from '@/lib/api-client';

/** 一次用户动作的幂等键及其「动作指纹」 */
export interface ActionIdempotency {
  /** 幂等键（UUID v4） */
  readonly key: string;
  /**
   * 动作指纹：把决定「这是不是同一件事」的参数拼成的串。
   *
   * 同一指纹 = 用户想做的事没变（重复点击、失败重试）→ 复用原键，后端可去重；
   * 指纹变了（改了席位、换了支付方式、换了套餐）→ 这是另一次动作，必须换新键，
   * 否则后端会把它当成第一次那笔的重复投递，直接返回旧订单（用户改了参数却拿到旧结果）。
   */
  readonly fingerprint: string;
}

/**
 * 解析一次用户动作应使用的幂等键。
 *
 * @param current - 该动作入口当前持有的键（首次为 `null`）
 * @param fingerprint - 本次动作参数算出的指纹
 * @returns 沿用旧键（指纹相同）或新生成的键（首次 / 指纹变化）
 */
export function resolveActionIdempotencyKey(
  current: ActionIdempotency | null,
  fingerprint: string,
): ActionIdempotency {
  if (current !== null && current.key !== '' && current.fingerprint === fingerprint) {
    return current;
  }
  return { key: newIdempotencyKey(), fingerprint };
}

/**
 * 拼装动作指纹。
 *
 * 用 `|` 分隔并逐段转义分隔符，避免 `['a|b', 'c']` 与 `['a', 'b|c']` 撞成同一指纹
 * （撞了就会把两次不同的动作并成一次，正好是幂等键最怕的错）。
 *
 * @param parts - 决定动作身份的字段（顺序固定，undefined/null 统一记为空串）
 * @returns 指纹字符串
 */
export function buildActionFingerprint(
  parts: ReadonlyArray<string | number | null | undefined>,
): string {
  return parts
    .map((part) => (part === null || part === undefined ? '' : String(part)).replace(/\|/g, '\\|'))
    .join('|');
}
