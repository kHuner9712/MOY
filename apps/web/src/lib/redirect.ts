/**
 * 登录跳转与「登录后回到哪里」的唯一口径。
 *
 * 为什么单独一层：
 * 1. 跳登录的动作散落在会话门、登录页、回跳页三处，`next` 的**生成**与**校验**
 *    必须是同一份规则，否则「生成得对、校验得松」就等于留了一个开放重定向；
 * 2. 这些是纯字符串运算，可以被任何环境（含门禁脚本）直接调用，不需要 React。
 *
 * 安全红线（`resolveNextPath` 的判定顺序即约束，一条都不能少）：
 * - 只接受**单斜杠开头**的站内路径；
 * - 拒绝协议相对地址（`//evil.example` 在浏览器里等价于 `https://evil.example`）；
 * - 拒绝反斜杠变体（`/\evil.example`、`\/` 在部分浏览器里会被归一化成 `//`）；
 * - 拒绝任何含 `://` 的串（绝对地址，哪怕它排在斜杠之后）。
 * 不满足任意一条一律回落 `DEFAULT_LANDING_PATH`——**不做**「尽力修正」，
 * 修正逻辑本身（例如把 `//` 裁成一个 `/`）就是漏洞的入口。
 */

/** 登录页路由 */
export const LOGIN_PATH = '/login';

/** 登录成功后的默认落地页（也是 `next` 不合法时的回落值） */
export const DEFAULT_LANDING_PATH = '/chat';

/**
 * 拼装登录页地址（带上登录后要回到的站内路径）。
 *
 * @param pathname - 当前站内路径（来自 `usePathname()`）
 * @param search - 当前查询串（来自 `window.location.search`，可含前导 `?`）
 * @returns 形如 `/login?next=%2Fplugins%3Ftab%3Dinstalled` 的地址
 */
export function buildLoginHref(pathname: string, search = ''): string {
  const safePath = pathname.startsWith('/') ? pathname : `/${pathname}`;
  // 查询串原样带上：深链里的筛选条件不能因为一次登录被吞掉
  const trimmedSearch = search.startsWith('?') ? search.slice(1) : search;
  const target = trimmedSearch === '' ? safePath : `${safePath}?${trimmedSearch}`;
  return `${LOGIN_PATH}?next=${encodeURIComponent(target)}`;
}

/**
 * 校验并解析「登录后回到哪里」。
 *
 * @param raw - 地址栏里的 `next` 原始值（未解码，可能为 null/undefined）
 * @returns 合法的站内路径；任何可疑输入一律回落 `DEFAULT_LANDING_PATH`
 */
export function resolveNextPath(raw: string | null | undefined): string {
  const original = typeof raw === 'string' ? raw.trim() : '';
  if (original === '') {
    return DEFAULT_LANDING_PATH;
  }

  let candidate = original;
  try {
    candidate = decodeURIComponent(original);
  } catch {
    // 编码损坏（`%` 后面不是合法转义）说明这个值不是我们生成的，直接回落
    return DEFAULT_LANDING_PATH;
  }

  // 二次编码（`%2F%2Fevil`）解码后仍是 `//evil`，因此上面一次解码就够，
  // 这里只再做一次首尾空白清理（`%20//evil` 这类变体）
  candidate = candidate.trim();

  if (!candidate.startsWith('/')) return DEFAULT_LANDING_PATH;
  if (candidate.startsWith('//')) return DEFAULT_LANDING_PATH;
  if (candidate.startsWith('/\\')) return DEFAULT_LANDING_PATH;
  if (candidate.includes('://')) return DEFAULT_LANDING_PATH;

  return candidate;
}
