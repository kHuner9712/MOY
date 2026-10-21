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
  eslint: {
    // CI 中由独立的 lint 步骤把关，构建阶段不阻断
    ignoreDuringBuilds: true,
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
