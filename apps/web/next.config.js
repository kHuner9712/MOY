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
