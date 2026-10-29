/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // standalone 输出：Docker 镜像最小化运行（Dockerfile runner 阶段依赖）
  output: 'standalone',
  // 生产环境禁用源码映射，避免泄露内部实现细节
  productionBrowserSourceMaps: false,
  // 统一编译 experimental（Next 14 中该字段位于顶层 experimental）
  experimental: {
    optimizePackageImports: ['lucide-react', 'date-fns'],
  },
  // 说明：Next 16 起 `next build` 不再支持 `eslint` 配置项（该键已无效果，
  // 保留会误导后来者以为「构建期跳过 lint」这个开关还在）。lint 由 CI 的
  // 独立步骤 `npm run lint:web` 把关，构建阶段不做 lint。
  async headers() {
    // 除 Referrer-Policy 外的公共安全头（两条规则共用，避免抄两遍后只改一处）
    const baseline = [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'X-Frame-Options', value: 'DENY' },
      // R2-ENG-06：强制 HTTPS。同源部署下由编排层反代终止 TLS，本头仍要下发
      // ——浏览器只要看到它，后续对该主机的明文请求就会被升级，
      // 从而消除「首跳被中间人降级」的窗口。`includeSubDomains` 与私有化现场的
      // 单域名部署不冲突（子域若确实只有 HTTP，需要现场改这一段）。
      {
        key: 'Strict-Transport-Security',
        value: 'max-age=31536000; includeSubDomains',
      },
      // R2-ENG-06：显式关停不需要的浏览器能力。三个都置空（`()` = 全部拒绝），
      // 产品当前不采集摄像头/麦克风/定位，默认全开只会给钓鱼页留越多越好的面。
      {
        key: 'Permissions-Policy',
        value: 'camera=(), microphone=(), geolocation=()',
      },
      // R2-ENG-06：CSP。
      //
      // ⚠ 这是**可用的保守起点**，不是最终形态。为什么必须留 `'unsafe-inline'`：
      // Next.js App Router 的 RSC 载荷是以内联 `<script>`（`self.__next_f.push(...)`）
      // 注入的，纯 `script-src 'self'` 会让页面首帧白屏且控制台只有一条难以归因的
      // CSP 违规。要收紧到无 `'unsafe-inline'`，需改用 nonce 方案（middleware 生成
      // 每请求 nonce 并经 `next/headers` 透传给 `<Script nonce>`），这超出本次修复范围。
      //
      // `img-src` 保留 `data:`（内联图标）与 `https:`（外部头像/预览图）；
      // `connect-src` 放开 `http:` 是为了私有化现场「前端与适配层不同源且只有 HTTP」，
      // 该场景下收紧会直接打断登录。`frame-ancestors 'none'` 与上面的
      // `X-Frame-Options: DENY` 是同一条约束的两种声明（现代浏览器只认 CSP），
      // 保留两者是为了兼容旧浏览器。
      {
        key: 'Content-Security-Policy',
        value: [
          "default-src 'self'",
          "script-src 'self' 'unsafe-inline'",
          "style-src 'self' 'unsafe-inline'",
          "img-src 'self' data: https:",
          "font-src 'self' data:",
          "connect-src 'self' http: https:",
          "object-src 'none'",
          "base-uri 'self'",
          "form-action 'self'",
          "frame-ancestors 'none'",
        ].join('; '),
      },
    ];
    return [
      {
        // 全局默认：跨站只带 origin，不带完整路径
        source: '/:path*',
        headers: [
          ...baseline,
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
      {
        // 统一登录回跳页：地址栏里带着授权码与一次性随机值，**任何请求都不许把它们
        // 带进 Referer**（否则会落到本站 nginx/网关的访问日志里）。
        //
        // 为什么必须落在**响应头**上：页面里的 `metadata.referrer = 'no-referrer'`
        // 只是一个 <meta>，HTTP 头优先级更高——上面那条全局规则会把它覆盖掉，
        // 表现为「页内声明了 no-referrer，首屏同源子资源请求却仍带 ?code」。
        //
        // 为什么放在全局规则**之后**：Next 对命中的多条 header 规则按顺序写入同一个
        // 响应头集合（`resHeaders[key] = value`），后命中的同名字段覆盖前者，
        // 因此不会出现两个 Referrer-Policy 值（已对照 next/dist/server 的实现确认）。
        source: '/auth/callback',
        headers: [...baseline, { key: 'Referrer-Policy', value: 'no-referrer' }],
      },
    ];
  },
};

module.exports = nextConfig;
