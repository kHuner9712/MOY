import nextVitals from "eslint-config-next/core-web-vitals";

// Next 16 起 `next lint` 移除，ESLint 9 使用扁平配置（2026-10-21 升级迁移）。
// eslint-config-next 16 原生导出扁平配置数组（Linter.Config[]），直接展开。
const eslintConfig = [
  {
    ignores: [
      ".next/**",
      // 构建残留目录（Windows 上 .next 被占住时改名保留的副本）。
      // 与仓库根 `.gitignore` 保持**同一组显式模式**：
      // 刻意不用 `.next*` 通配——收得太宽会顺带忽略将来合法的源码目录。
      ".next-bak-*/**",
      ".next.stale-*/**",
      "node_modules/**",
      "next-env.d.ts",
      "tsconfig.tsbuildinfo",
      "build.log",
    ],
  },
  ...nextVitals,
  {
    rules: {
      "no-console": ["warn", { allow: ["warn", "error", "info"] }],
      eqeqeq: ["error", "smart"],
      "prefer-const": "error",
      "no-var": "error",
      // React 19 新增的严格规则：拦截「effect 内同步 setState」。
      // 现有 6 处均为异步加载器的同步 loading 态，功能正确；
      // TODO(第二阶段 T03 前端收敛)：迁移至 react-query 后重新开启。
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default eslintConfig;
