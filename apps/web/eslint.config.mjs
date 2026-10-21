import nextVitals from "eslint-config-next/core-web-vitals";

// Next 16 起 `next lint` 移除，ESLint 9 使用扁平配置（2026-10-21 升级迁移）。
// eslint-config-next 16 原生导出扁平配置数组（Linter.Config[]），直接展开。
const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "tsconfig.tsbuildinfo"],
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
